"""报告模块 —— FlowReport / ActionReport 数据类 + JSON 序列化。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ActionReport:
    """单个 Action 的执行报告"""
    index: int
    name: str
    phase: str                     # setup / step / teardown
    passed: bool
    error: str | None = None
    retries_used: int = 0
    duration_ms: float = 0
    screenshot_before: str | None = None
    screenshot_after: str | None = None


@dataclass
class FlowReport:
    """一次 Flow 执行的完整报告"""

    flow_id: str
    flow_name: str
    run_tag: str                   # 8位随机标识
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    passed: bool = False
    total_actions: int = 0
    passed_actions: int = 0
    actions: list[ActionReport] = field(default_factory=list)
    aborted_at_phase: str | None = None  # setup / step / None

    @property
    def total_duration_ms(self) -> float:
        return sum(a.duration_ms for a in self.actions)

    def to_dict(self) -> dict:
        return {
            "flow_id": self.flow_id,
            "flow_name": self.flow_name,
            "run_tag": self.run_tag,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "passed": self.passed,
            "total_actions": self.total_actions,
            "passed_actions": self.passed_actions,
            "total_duration_ms": self.total_duration_ms,
            "aborted_at_phase": self.aborted_at_phase,
            "actions": [
                {
                    "index": a.index,
                    "name": a.name,
                    "phase": a.phase,
                    "passed": a.passed,
                    "error": a.error,
                    "retries_used": a.retries_used,
                    "duration_ms": a.duration_ms,
                    "screenshot_before": a.screenshot_before,
                    "screenshot_after": a.screenshot_after,
                }
                for a in self.actions
            ],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
