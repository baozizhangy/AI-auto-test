"""导入所有场景模块以触发注册。"""

from scenarios.app import lexiang_borrow_home  # noqa: F401
from scenarios.app import lexiang_withdraw_flow  # noqa: F401
from scenarios.base import Scenario, StepDef, Flow  # noqa: F401
from scenarios.registry import get, get_flow, list_all, list_flows  # noqa: F401
from scenarios.web import demo_web  # noqa: F401
