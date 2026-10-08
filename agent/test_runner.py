"""测试流程编排器。

执行测试用例：controller 执行操作 → 截图 → AI 验证 → 生成报告。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .ai_verifier import AIVerifier
from .config import Config


@dataclass
class StepResult:
    step_name: str
    screenshot_path: Path | None
    passed: bool
    confidence: float
    summary: str
    differences: list[str]
    duration_ms: float

    def to_dict(self) -> dict:
        screenshot = None
        if self.screenshot_path is not None:
            try:
                # 相对于 SCREENSHOT_DIR 的路径，前端通过 /screenshots/ 访问
                rel = self.screenshot_path.resolve().relative_to(Config.SCREENSHOT_DIR.resolve())
                screenshot = rel.as_posix()
            except ValueError:
                screenshot = str(self.screenshot_path)
        return {
            "step_name": self.step_name,
            "screenshot": screenshot,
            "passed": self.passed,
            "confidence": self.confidence,
            "summary": self.summary,
            "differences": self.differences,
            "duration_ms": self.duration_ms,
        }


@dataclass
class TestReport:
    test_name: str
    started_at: datetime
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.steps)

    @property
    def total_duration_ms(self) -> float:
        return sum(s.duration_ms for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "passed": self.passed,
            "total_duration_ms": self.total_duration_ms,
            "steps": [s.to_dict() for s in self.steps],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


class TestRunner:
    """编排执行一个测试用例的多个步骤。

    controller 只要实现了 screenshot(path) 和 wait(seconds) 即可。
    """

    ActionFn = Callable[[Any], None]

    def __init__(
        self,
        test_name: str,
        controller: Any,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.test_name = test_name
        self.controller = controller
        self.verifier = AIVerifier(
            api_key=api_key or Config.ANTHROPIC_API_KEY,
            model=model or Config.ANTHROPIC_MODEL,
        )
        self.baseline_dir = Config.BASELINE_DIR / test_name
        self.screenshot_dir = Config.SCREENSHOT_DIR / test_name
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.step_counter = 0

    # ── Step 定义 ────────────────────────────────────────

    def step(
        self,
        name: str,
        action: ActionFn,
        baseline: str | None = None,
        assertion_prompt: str | None = None,
        assertion_type: str = "visual",
        wait_after: float = 1.0,
    ) -> StepResult:
        self.step_counter += 1
        idx = self.step_counter

        t0 = time.perf_counter()
        action(self.controller)
        self.controller.wait(wait_after)

        # 截图
        filename = f"{idx:02d}_{name}.png"
        shot_path = self.screenshot_dir / filename
        self.controller.screenshot(str(shot_path))

        # 断言
        if assertion_type == "dom":
            # DOM 断言：action 内部自行检查，此处跳过 AI 对比
            result = StepResult(
                step_name=name,
                screenshot_path=shot_path,
                passed=True,
                confidence=1.0,
                summary="DOM 断言通过",
                differences=[],
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        else:
            # visual 断言：AI 截图对比
            baseline_file = baseline if baseline else f"{name}.png"
            baseline_path = self.baseline_dir / baseline_file

            if not baseline_path.exists():
                result = StepResult(
                    step_name=name,
                    screenshot_path=shot_path,
                    passed=False,
                    confidence=0,
                    summary=f"基线不存在 ({baseline_file})，请确认后放入 baselines/{self.test_name}/",
                    differences=[],
                    duration_ms=(time.perf_counter() - t0) * 1000,
                )
            else:
                verified = self.verifier.verify(baseline_path, shot_path, assertion_prompt)
                result = StepResult(
                    step_name=name,
                    screenshot_path=shot_path,
                    passed=verified["pass"],
                    confidence=verified["confidence"],
                    summary=verified["summary"],
                    differences=verified.get("differences", []),
                    duration_ms=(time.perf_counter() - t0) * 1000,
                )

        return result

    # ── 旧 Scenario 兼容包装 ────────────────────────────

    @staticmethod
    def wrap_legacy_scenario(scenario: "Scenario") -> "Flow":
        """将旧 Scenario 包装为临时 Flow。

        - setup = []
        - steps = 每个 StepDef 包装为 CustomAction
        - teardown = []
        """
        from agent.actions import CustomAction, ActionResult
        from scenarios.base import Flow

        def _make_action_wrapper(step_def):
            """将 StepDef.action 包装为 CustomAction"""

            def _wrap(adb, locator) -> ActionResult:
                try:
                    step_def.action(adb)
                    return ActionResult(
                        action_name=step_def.name,
                        success=True,
                        message="步骤执行成功",
                    )
                except Exception as e:
                    return ActionResult(
                        action_name=step_def.name,
                        success=False,
                        message=str(e),
                    )

            return CustomAction(
                name=step_def.name,
                execute_fn=_wrap,
                retries=1,  # 旧场景不改动原有行为
            )

        steps = [_make_action_wrapper(sd) for sd in scenario.steps]
        return Flow(
            id=scenario.id,
            name=scenario.name,
            description=scenario.description,
            category=scenario.category,
            driver_type=scenario.driver_type,
            steps=steps,
            config=scenario.config,
        )

    # ── 批量执行 ─────────────────────────────────────────

    def run(self, steps: list[tuple]) -> TestReport:
        """批量执行步骤。steps = [(name, action_fn, baseline_filename, assertion_type), ...]"""
        report = TestReport(test_name=self.test_name, started_at=datetime.now(timezone.utc))
        for item in steps:
            name = item[0]
            action = item[1]
            baseline = item[2] if len(item) > 2 else None
            assertion_type = item[3] if len(item) > 3 else "visual"
            result = self.step(name, action, baseline, assertion_type=assertion_type)
            report.steps.append(result)
        report.finished_at = datetime.now(timezone.utc)
        report.save(Config.REPORT_DIR / f"{self.test_name}.json")
        return report
