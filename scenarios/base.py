"""场景定义基类。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# Action 不再锁死 ADBController，接受任意 controller 对象（鸭子类型）
ActionFn = Callable[[Any], None]


@dataclass
class StepDef:
    """单个测试步骤的声明式定义。"""
    name: str
    action: ActionFn
    baseline: str | None = None
    assertion_prompt: str | None = None
    assertion_type: str = "visual"  # "visual"=截图AI对比, "dom"=DOM断言, "text"=文字断言


@dataclass
class Scenario:
    """可被发现和执行的一个测试场景。"""
    id: str
    name: str
    description: str = ""
    category: str = "general"
    driver_type: str = "app"  # "app"=ADB设备, "web"=Playwright浏览器
    config: dict = field(default_factory=dict)  # 场景级配置（如 base_url）
    steps: list[StepDef] = field(default_factory=list)

    @property
    def step_count(self) -> int:
        return len(self.steps)


# ── Flow 类型定义 ───────────────────────────────────────────


@dataclass
class Flow:
    """业务流程编排 —— Action 的有序依赖链"""

    id: str
    name: str
    description: str = ""
    category: str = "app"
    driver_type: str = "app"

    setup: list[Any] = field(default_factory=list)       # list[Action]
    steps: list[Any] = field(default_factory=list)       # list[Action]
    teardown: list[Any] = field(default_factory=list)    # list[Action]

    config: dict = field(default_factory=dict)

    @property
    def step_count(self) -> int:
        return len(self.setup) + len(self.steps) + len(self.teardown)
