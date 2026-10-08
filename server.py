"""UI 自动化测试后台 — FastAPI 应用。

提供 REST API + WebSocket 实时推送 + 静态文件服务。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent.adb_controller import ADBController
from agent.config import Config
from agent.flow_runner import FlowRunner
from agent.test_runner import StepResult, TestReport, TestRunner
from scenarios.base import Flow
from scenarios.registry import get as get_scenario, list_all

BASE_DIR = Path(__file__).resolve().parent

# ── 线程执行器 ──────────────────────────────────────────

_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="test-runner",
)
_main_loop: asyncio.AbstractEventLoop | None = None
_active_run: str | None = None  # 当前正在运行的 run_id
_active_controller = None        # 当前运行中的 controller 引用，用于强制中止
_runs_queues: dict[str, asyncio.Queue] = {}


def _send_to_queue(queue: asyncio.Queue, msg: dict) -> None:
    """从同步线程安全地向 asyncio.Queue 投递消息。"""
    if _main_loop is not None:
        _main_loop.call_soon_threadsafe(queue.put_nowait, msg)
    else:
        print(f"[WARN] _main_loop 未初始化，消息丢弃: {msg.get('type', '?')}")


# ── 生命周期 ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    import scenarios  # noqa: F401
    print(f"[Dashboard] 已加载 {len(list_all())} 个场景")
    print(f"[Dashboard] 事件循环已就绪: {id(_main_loop)}")
    yield
    # cancel_futures=True: 取消队列中尚未执行的任务，避免阻塞关闭
    _executor.shutdown(wait=False, cancel_futures=True)


# ── FastAPI 应用 ────────────────────────────────────────

app = FastAPI(title="UI Automation Dashboard", lifespan=lifespan)

app.mount("/screenshots", StaticFiles(directory=str(Config.SCREENSHOT_DIR)), name="screenshots")
app.mount("/baselines", StaticFiles(directory=str(Config.BASELINE_DIR)), name="baselines")


# ── REST API 路由 ───────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/config")
async def api_config():
    return {
        "serial": Config.ANDROID_SERIAL,
        "model": Config.ANTHROPIC_MODEL,
        "has_api_key": bool(Config.ANTHROPIC_API_KEY),
        "browser_type": Config.BROWSER_TYPE,
        "headless": Config.BROWSER_HEADLESS,
        "base_url": Config.DEFAULT_BASE_URL,
    }


@app.get("/api/scenarios")
async def api_scenarios():
    results = []
    for s in list_all():
        item = {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "category": s.category,
            "driver_type": s.driver_type,
            "step_count": s.step_count,
            "config": s.config,
        }
        if isinstance(s, Flow):
            # Flow: list Action names
            all_actions = list(s.setup) + list(s.steps) + list(s.teardown)
            item["steps"] = [{"name": a.name, "assertion_type": "dom"} for a in all_actions]
        else:
            # Scenario: list StepDef details
            item["steps"] = [
                {"name": sd.name, "baseline": sd.baseline, "assertion_type": sd.assertion_type}
                for sd in s.steps
            ]
        results.append(item)
    return results


@app.get("/api/scenarios/{scenario_id}")
async def api_scenario_detail(scenario_id: str):
    s = get_scenario(scenario_id)
    if s is None:
        return JSONResponse({"error": "场景不存在"}, status_code=404)
    if isinstance(s, Flow):
        all_actions = list(s.setup) + list(s.steps) + list(s.teardown)
        steps_data = [{"name": a.name, "assertion_type": "dom", "has_baseline": False} for a in all_actions]
    else:
        steps_data = [
            {"name": sd.name, "baseline": sd.baseline, "assertion_type": sd.assertion_type,
             "has_baseline": (Config.BASELINE_DIR / s.id / (sd.baseline or f"{sd.name}.png")).exists()}
            for sd in s.steps
        ]
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "category": s.category,
        "driver_type": s.driver_type,
        "config": s.config,
        "steps": steps_data,
    }


@app.post("/api/run")
async def api_start_run(payload: dict):
    global _active_run

    scenario_id = payload.get("scenario_id", "")
    scenario = get_scenario(scenario_id)
    if scenario is None:
        return JSONResponse({"error": f"场景不存在: {scenario_id}"}, status_code=404)

    if _active_run is not None:
        return JSONResponse({"error": "已有测试正在运行中", "run_id": _active_run}, status_code=409)

    run_id = uuid.uuid4().hex
    serial = payload.get("serial") or Config.ANDROID_SERIAL
    model = payload.get("model") or Config.ANTHROPIC_MODEL

    # 只有旧 Scenario 存在 visual 断言步骤时才需要 API key
    # Flow 在第一版中不使用 visual 断言
    has_visual = False
    if not isinstance(scenario, Flow):
        has_visual = any(sd.assertion_type == "visual" for sd in scenario.steps)
    if has_visual and not Config.ANTHROPIC_API_KEY:
        return JSONResponse({"error": "该场景包含 AI 视觉断言，需要配置 ANTHROPIC_API_KEY"}, status_code=400)

    _active_run = run_id
    queue: asyncio.Queue = asyncio.Queue()
    _runs_queues[run_id] = queue

    driver_type = scenario.driver_type

    def _sync_runner() -> None:
        """在后台线程中执行测试。"""
        global _active_run, _active_controller
        controller = None
        print(f"[Runner] 线程启动, main_loop={id(_main_loop) if _main_loop else 'None'}")
        try:
            if driver_type == "web":
                from agent.web_controller import WebController
                browser_type = payload.get("browser_type") or Config.BROWSER_TYPE
                headless = payload.get("headless", Config.BROWSER_HEADLESS)
                base_url = scenario.config.get("base_url", Config.DEFAULT_BASE_URL)
                _send_to_queue(queue, {"type": "log", "message": f"启动浏览器 {browser_type} [headless={headless}]"})
                controller = WebController(browser_type=browser_type, headless=headless, base_url=base_url)
                _active_controller = controller  # 存储引用，供 force-reset 使用
            else:
                _send_to_queue(queue, {"type": "log", "message": f"连接设备 {serial}，模型 {model}"})
                controller = ADBController(serial or Config.ANDROID_SERIAL)

            # ── Flow / Scenario 分支 ──────────────────────────
            if isinstance(scenario, Flow):
                # 新 Flow → FlowRunner（实时推送每一步）
                screenshot_dir = Config.SCREENSHOT_DIR / f"{scenario.id}_{run_id[:8]}"

                # 先推送 run_starting，告知前端总步数
                _total = len(scenario.setup) + len(scenario.steps) + len(scenario.teardown)
                _send_to_queue(queue, {
                    "type": "run_starting",
                    "total_steps": _total,
                    "flow_id": scenario.id,
                    "flow_name": scenario.name,
                })

                def _on_action_done(action_report, phase, total_actions):
                    """每个 Action 完成后立即推送实时进度。"""
                    step_index = action_report.index - 1
                    _send_to_queue(queue, {
                        "type": "step_starting",
                        "step_index": step_index,
                        "step_name": action_report.name,
                        "total_steps": total_actions,
                        "screenshot_before": action_report.screenshot_before,
                    })
                    _send_to_queue(queue, {
                        "type": "step_completed",
                        "step_index": step_index,
                        "step_name": action_report.name,
                        "result": {
                            "step_name": action_report.name,
                            "screenshot": action_report.screenshot_after or action_report.screenshot_before,
                            "screenshot_before": action_report.screenshot_before,
                            "screenshot_after": action_report.screenshot_after,
                            "passed": action_report.passed,
                            "confidence": 1.0 if action_report.passed else 0.0,
                            "summary": action_report.error or f"{action_report.phase} 完成",
                            "differences": [] if action_report.passed else [action_report.error or ""],
                            "duration_ms": action_report.duration_ms,
                        },
                    })

                flow_runner = FlowRunner(
                    adb=controller,
                    flow=scenario,
                    screenshot_base=screenshot_dir,
                    on_action_complete=_on_action_done,
                    continue_on_step_fail=True,
                )
                report = flow_runner.run()

                _send_to_queue(queue, {
                    "type": "run_completed",
                    "report": {
                        "test_name": report.flow_id,
                        "started_at": report.started_at.isoformat(),
                        "finished_at": report.finished_at.isoformat(),
                        "passed": report.passed,
                        "total_duration_ms": report.total_duration_ms,
                        "steps": [
                            {
                                "step_name": a.name,
                                "screenshot": a.screenshot_after or a.screenshot_before,
                                "screenshot_before": a.screenshot_before,
                                "screenshot_after": a.screenshot_after,
                                "passed": a.passed,
                                "confidence": 1.0 if a.passed else 0.0,
                                "summary": a.error or f"{a.phase} 完成",
                                "differences": [] if a.passed else [a.error or ""],
                                "duration_ms": a.duration_ms,
                            }
                            for a in report.actions
                        ],
                    },
                    "report_path": str(Config.REPORT_DIR / f"{report.flow_id}_{report.run_tag}.json"),
                })

                # teardown
                if controller is not None and hasattr(controller, "teardown"):
                    try:
                        controller.teardown()
                    except Exception:
                        pass
                _active_run = None
                _active_controller = None
                return

            # 旧 Scenario → TestRunner（保留原逻辑）
            runner = TestRunner(test_name=scenario_id, controller=controller, model=model)
            runner.screenshot_dir = Config.SCREENSHOT_DIR / f"{scenario_id}_{run_id[:8]}"
            runner.screenshot_dir.mkdir(parents=True, exist_ok=True)

            step_defs = scenario.steps
            total = len(step_defs)
            step_results = []
            started_at = datetime.now(timezone.utc)

            print(f"\n{'='*50}")
            print(f"[{scenario.name}] 开始执行 (共 {total} 步)")
            print(f"{'='*50}")

            for i, sd in enumerate(step_defs):
                step_no = i + 1
                print(f"  [{step_no}/{total}] {sd.name}...", end=" ", flush=True)

                _send_to_queue(queue, {
                    "type": "step_starting",
                    "step_index": i,
                    "step_name": sd.name,
                    "total_steps": total,
                })

                try:
                    result = runner.step(
                        name=sd.name,
                        action=sd.action,
                        baseline=sd.baseline,
                        assertion_prompt=sd.assertion_prompt,
                        assertion_type=sd.assertion_type,
                        wait_after=1.0,
                    )
                    step_results.append(result)
                    dur_s = result.duration_ms / 1000
                    print(f"\033[32m✓\033[0m ({dur_s:.1f}s)")

                    _send_to_queue(queue, {
                        "type": "step_completed",
                        "step_index": i,
                        "step_name": sd.name,
                        "result": result.to_dict(),
                    })
                except Exception as exc:
                    error_msg = str(exc)
                    print(f"\033[31m✗\033[0m {error_msg}")

                    # 尝试截取失败时的页面状态
                    error_screenshot = None
                    try:
                        shot_path = runner.screenshot_dir / f"{step_no:02d}_{sd.name}_error.png"
                        controller.screenshot(str(shot_path))
                        error_screenshot = shot_path
                    except Exception:
                        pass

                    fail_result = StepResult(
                        step_name=sd.name,
                        screenshot_path=error_screenshot,
                        passed=False,
                        confidence=0.0,
                        summary=error_msg,
                        differences=[error_msg],
                        duration_ms=0,
                    )
                    step_results.append(fail_result)

                    _send_to_queue(queue, {
                        "type": "step_completed",
                        "step_index": i,
                        "step_name": sd.name,
                        "result": fail_result.to_dict(),
                    })
                    # 步骤失败后继续执行后续步骤

            # 始终保存报告（包括部分执行的）
            report = TestReport(
                test_name=scenario_id,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                steps=step_results,
            )
            report_path = Config.REPORT_DIR / f"{scenario_id}_{run_id[:8]}.json"
            report.save(report_path)

            passed_count = sum(1 for s in step_results if s.passed)
            total_dur = report.total_duration_ms / 1000
            status = "\033[32m全部通过\033[0m" if report.passed else f"\033[31m{total - passed_count} 步失败\033[0m"
            print(f"{'='*50}")
            print(f"[{scenario.name}] 完成: {passed_count}/{total} 通过, 耗时 {total_dur:.1f}s  → {status}")
            print(f"  报告: {report_path}")
            print(f"{'='*50}\n")

            _send_to_queue(queue, {
                "type": "run_completed",
                "report": report.to_dict(),
                "report_path": str(report_path),
            })
        except Exception as exc:
            _send_to_queue(queue, {
                "type": "run_error",
                "error": str(exc),
                "step_index": None,
            })
        finally:
            if controller is not None and hasattr(controller, "teardown"):
                try:
                    controller.teardown()
                except Exception:
                    pass
            _active_run = None
            _active_controller = None

    _executor.submit(_sync_runner)

    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/force-reset")
async def api_force_reset():
    """强制清除卡住的测试状态，杀死浏览器进程。

    当后台线程因页面加载挂起导致 _active_run 永远不被清空时，
    调用此接口可以强制中止浏览器进程，使卡住的线程抛出异常并退出。
    """
    global _active_run, _active_controller
    was_running = _active_run
    controller = _active_controller

    # 先清除状态，再杀进程（避免杀进程期间仍被拦截）
    _active_run = None
    _active_controller = None

    # 强制杀死浏览器进程
    killed = False
    if controller is not None and hasattr(controller, "force_kill"):
        try:
            controller.force_kill()
            killed = True
        except Exception as e:
            print(f"[ForceReset] 杀死浏览器进程时出错: {e}")

    if was_running:
        print(f"[ForceReset] 已强制中止运行中的测试: {was_running}")
        return {"status": "reset", "killed_browser": killed, "was_run_id": was_running}
    else:
        return {"status": "ok", "message": "当前无运行中的测试"}


@app.get("/api/runs")
async def api_runs():
    """列出所有历史报告。"""
    reports_dir = Config.REPORT_DIR
    runs = []
    for f in sorted(reports_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # 兼容 Flow 报告 (flow_name/flow_id) 与旧 Scenario 报告 (test_name)
            display_name = (
                data.get("flow_name")
                or data.get("test_name")
                or data.get("flow_id")
                or f.stem
            )
            runs.append({
                "run_id": f.stem,
                "test_name": display_name,
                "scenario_id": data.get("flow_id") or data.get("test_name", ""),
                "started_at": data.get("started_at", ""),
                "finished_at": data.get("finished_at", ""),
                "passed": data.get("passed", False),
                "total_duration_ms": data.get("total_duration_ms", 0),
                "step_count": len(data.get("steps", []) or data.get("actions", [])),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return runs


@app.get("/api/runs/{run_id}")
async def api_run_detail(run_id: str):
    """返回某次运行的完整报告。

    同时兼容 Flow 报告 (actions/name/error) 与旧 Scenario 报告 (steps/step_name/summary)：
    如果是 Flow 格式则在返回前标准化为前端预期的 steps 格式。
    """
    path = Config.REPORT_DIR / f"{run_id}.json"
    if not path.exists():
        return JSONResponse({"error": "报告不存在"}, status_code=404)

    data = json.loads(path.read_text(encoding="utf-8"))

    # 标准化 Flow 报告 → 前端统一格式
    if "actions" in data and "steps" not in data:
        data["test_name"] = data.get("flow_name") or data.get("flow_id", "")
        data["steps"] = [
            {
                "step_name": a.get("name", ""),
                "screenshot": a.get("screenshot_after") or a.get("screenshot_before"),
                "screenshot_before": a.get("screenshot_before"),
                "screenshot_after": a.get("screenshot_after"),
                "passed": a.get("passed", False),
                "confidence": 1.0 if a.get("passed") else 0.0,
                "summary": a.get("error") or f"{a.get('phase', 'step')} 完成",
                "differences": [] if a.get("passed") else [a.get("error") or ""],
                "duration_ms": a.get("duration_ms", 0),
                "phase": a.get("phase", ""),
                "retries_used": a.get("retries_used", 0),
            }
            for a in data["actions"]
        ]

    return data


@app.delete("/api/runs/{run_id}")
async def api_delete_run(run_id: str):
    """删除某次运行记录及关联截图。"""
    import shutil

    report_path = Config.REPORT_DIR / f"{run_id}.json"

    deleted = []
    if report_path.exists():
        report_path.unlink()
        deleted.append("report")

    # 截图目录名就是 run_id（即报告文件的 stem），如 loan_apply_flow_908b24a5
    screenshot_dir = Config.SCREENSHOT_DIR / run_id
    if screenshot_dir.is_dir():
        shutil.rmtree(screenshot_dir)
        deleted.append(f"screenshots/{run_id}")

    if not deleted:
        return JSONResponse({"error": "报告不存在"}, status_code=404)

    return {"deleted": deleted, "run_id": run_id}


# ── WebSocket ───────────────────────────────────────────

@app.websocket("/ws/run/{run_id}")
async def ws_run(websocket: WebSocket, run_id: str):
    await websocket.accept()

    if run_id not in _runs_queues:
        await websocket.send_json({"type": "run_error", "error": "无效的 run_id"})
        await websocket.close()
        return

    queue = _runs_queues[run_id]
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=120)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "run_error", "error": "运行超时（120秒无消息）"})
                break

            await websocket.send_json(msg)
            if msg["type"] in ("run_completed", "run_error"):
                break
    except WebSocketDisconnect:
        pass  # 客户端断开，测试在后台继续运行
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ── 入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    import socket
    import signal
    import sys

    PORT = 8000

    # 自行创建 socket 并设 SO_REUSEADDR，解决 Windows 幽灵端口占用
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", PORT))
    sock.listen(128)
    sock.setblocking(False)

    config = uvicorn.Config("server:app", timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)

    def _shutdown(*_):
        print("\n[关闭] 正在停止服务器...")
        server.should_exit = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        asyncio.run(server.serve(sockets=[sock]))
    finally:
        sock.close()
        print(f"[关闭] 端口 {PORT} 已释放")
