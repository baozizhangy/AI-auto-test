"""快速执行测试场景并打印报告。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.adb_controller import ADBController
from agent.config import Config
from agent.test_runner import TestRunner, StepResult, TestReport
from scenarios.registry import get as get_scenario
import scenarios  # 触发注册

from datetime import datetime, timezone


def run_scenario(scenario_id: str) -> None:
    scenario = get_scenario(scenario_id)
    if not scenario:
        print(f"场景 {scenario_id} 不存在")
        return

    print(f"=== {scenario.name} ({scenario.step_count}步) ===\n")

    ctrl = ADBController(serial=Config.ANDROID_SERIAL)
    runner = TestRunner(test_name=scenario_id, controller=ctrl)

    run_id = "manual"
    runner.screenshot_dir = Config.SCREENSHOT_DIR / f"{scenario_id}_{run_id}"
    runner.screenshot_dir.mkdir(parents=True, exist_ok=True)

    step_results = []
    started_at = datetime.now(timezone.utc)

    for i, sd in enumerate(scenario.steps):
        step_no = i + 1
        print(f"  [{step_no}/{scenario.step_count}] {sd.name}...", end=" ", flush=True)

        try:
            result = runner.step(
                name=sd.name,
                action=sd.action,
                baseline=sd.baseline,
                assertion_prompt=sd.assertion_prompt,
                assertion_type=sd.assertion_type,
                wait_after=2.0,
            )
            step_results.append(result)
            dur_s = result.duration_ms / 1000
            status = "\033[32m✓\033[0m" if result.passed else "\033[31m✗\033[0m"
            print(f"{status} ({dur_s:.1f}s) {result.summary}")
        except Exception as exc:
            error_msg = str(exc)
            print(f"\033[31m✗\033[0m {error_msg}")

            error_screenshot = None
            try:
                shot_path = runner.screenshot_dir / f"{step_no:02d}_{sd.name}_error.png"
                ctrl.screenshot(str(shot_path))
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

    # 保存报告
    report = TestReport(
        test_name=scenario_id,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        steps=step_results,
    )
    report_path = Config.REPORT_DIR / f"{scenario_id}_{run_id}.json"
    report.save(report_path)

    passed_count = sum(1 for s in step_results if s.passed)
    total = len(scenario.steps)
    print(f"\n=== 结果: {passed_count}/{total} 通过 ===")
    print(f"报告: {report_path}")

    # 打印失败详情
    failures = [s for s in step_results if not s.passed]
    if failures:
        print(f"\n--- 失败详情 ({len(failures)}) ---")
        for f in failures:
            print(f"  ❌ {f.step_name}: {f.summary}")


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "lexiang_withdraw_flow"
    run_scenario(sid)
