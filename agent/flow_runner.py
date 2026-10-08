"""Flow 执行引擎 —— setup → steps → teardown，Action 级 Retry，支持实时 callback。

setup 失败会中止后续流程；step 失败默认继续执行后续步骤，仅标记失败。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from agent.config import Config
from agent.reporter import ActionReport, FlowReport

if TYPE_CHECKING:
    from agent.adb_controller import ADBController
    from agent.actions import Action, ActionResult


OnActionComplete = Callable[[ActionReport, str, int], None]
# (action_report, phase, total_actions) -> None


class FlowRunner:
    """流程执行器 —— 按 setup → steps → teardown 顺序执行。

    Args:
        on_action_complete: 每个 Action 执行完成后的回调，用于实时推送进度。
        continue_on_step_fail: step 阶段某步失败后是否继续后续步骤。
    """

    def __init__(
        self,
        adb: "ADBController",
        flow: "object",  # Flow 类型（避免循环导入）
        screenshot_base: Path | None = None,
        on_action_complete: OnActionComplete | None = None,
        continue_on_step_fail: bool = True,
    ) -> None:
        self._adb = adb
        self._flow = flow
        self._run_tag = uuid.uuid4().hex[:8]
        self._screenshot_dir = screenshot_base or (
            Config.SCREENSHOT_DIR / f"{flow.id}_{self._run_tag}"
        )
        self._action_index = 0
        self._on_action_complete = on_action_complete
        self._continue_on_step_fail = continue_on_step_fail
        self._total_actions = (
            len(flow.setup) + len(flow.steps) + len(flow.teardown)
        )

    # ── Public ──────────────────────────────────────────

    def run(self) -> FlowReport:
        """执行完整流程并返回报告。"""
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(timezone.utc)

        report = FlowReport(
            flow_id=self._flow.id,
            flow_name=self._flow.name,
            run_tag=self._run_tag,
            started_at=started_at,
            total_actions=(
                len(self._flow.setup) + len(self._flow.steps) + len(self._flow.teardown)
            ),
        )

        # ── Setup（失败则 abort）──
        for action in self._flow.setup:
            result = self._execute_action(action, "setup")
            report.actions.append(result)
            if not result.passed:
                report.aborted_at_phase = "setup"
                report.passed = False
                report.finished_at = datetime.now(timezone.utc)
                self._run_teardown(report)
                self._finalize_report(report)
                return report

        # ── Steps（默认失败后继续，只标记并记录）──
        any_step_failed = False
        for action in self._flow.steps:
            result = self._execute_action(action, "step")
            report.actions.append(result)
            if not result.passed:
                any_step_failed = True
                if not self._continue_on_step_fail:
                    report.aborted_at_phase = "step"
                    report.passed = False
                    report.finished_at = datetime.now(timezone.utc)
                    self._run_teardown(report)
                    self._finalize_report(report)
                    return report

        # ── Finalize ──
        report.passed = not any_step_failed
        report.finished_at = datetime.now(timezone.utc)
        self._run_teardown(report)
        self._finalize_report(report)
        return report

    # ── Internal ─────────────────────────────────────────

    def _execute_action(self, action: "Action", phase: str) -> ActionReport:
        """执行单个 Action 并返回报告。完成后调用 on_action_complete 回调。"""
        self._action_index += 1
        idx = self._action_index

        result: "ActionResult" = action.execute(
            self._adb,
            screenshot_dir=self._screenshot_dir,
            action_index=idx,
        )

        action_report = ActionReport(
            index=idx,
            name=action.name,
            phase=phase,
            passed=result.success,
            error=None if result.success else result.message,
            retries_used=result.retries_used,
            duration_ms=result.duration_ms,
            screenshot_before=_rel_path(result.screenshot_before),
            screenshot_after=_rel_path(result.screenshot_after),
        )

        # 实时回调，用于推送进度到前端
        if self._on_action_complete is not None:
            try:
                self._on_action_complete(action_report, phase, self._total_actions)
            except Exception as e:
                print(f"[FlowRunner] on_action_complete 回调异常: {e}")

        return action_report

    def _run_teardown(self, report: FlowReport) -> None:
        """执行 teardown，异常全部吞掉。"""
        for action in self._flow.teardown:
            try:
                result = self._execute_action(action, "teardown")
                report.actions.append(result)
            except Exception as e:
                self._action_index += 1
                report.actions.append(
                    ActionReport(
                        index=self._action_index,
                        name=action.name,
                        phase="teardown",
                        passed=False,
                        error=str(e),
                    )
                )

    def _finalize_report(self, report: FlowReport) -> None:
        """汇总报告统计并保存。"""
        report.passed_actions = sum(
            1 for a in report.actions if a.passed and a.phase != "teardown"
        )
        report_path = Config.REPORT_DIR / f"{self._flow.id}_{self._run_tag}.json"
        report.save(report_path)


def _rel_path(p: str | None) -> str | None:
    """将绝对路径转为相对于 SCREENSHOT_DIR 的相对路径。"""
    if p is None:
        return None
    try:
        return str(Path(p).absolute().relative_to(Config.SCREENSHOT_DIR.absolute()).as_posix())
    except (ValueError, OSError):
        return str(p)
