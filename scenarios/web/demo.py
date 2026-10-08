"""Web 示例场景：访问 example.com，验证首页内容。"""

from scenarios.base import Scenario, StepDef
from scenarios.registry import register


def _open_home(web) -> None:
    """打开首页。"""
    web.navigate("/")


def _check_title(web) -> None:
    """检查页面标题。"""
    title = web.title()
    assert "Example" in title, f"标题不符合预期: {title}"


def _check_header(web) -> None:
    """检查关键元素存在。"""
    assert web.is_visible("h1"), "h1 标题元素不存在"


demo_web = Scenario(
    id="demo_web",
    name="示例网站首页检查",
    description="打开 example.com，验证首页标题和 h1 元素存在",
    category="web",
    driver_type="web",
    config={"base_url": "https://www.example.com"},
    steps=[
        StepDef(name="打开首页", action=_open_home, baseline=None, assertion_type="dom"),
        StepDef(name="验证标题", action=_check_title, baseline=None, assertion_type="dom"),
        StepDef(name="验证 h1 元素", action=_check_header, baseline=None, assertion_type="dom"),
    ],
)

register(demo_web)
