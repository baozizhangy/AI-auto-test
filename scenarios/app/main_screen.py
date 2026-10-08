"""APP 场景：乐享借 APP 启动与主页面验证（新 Flow 架构）。"""

from agent.actions import CustomAction, ActionResult
from agent.adb_controller import ADBController
from agent.config import Config
from agent.path_locator import PathLocator
from scenarios.base import Flow
from scenarios.registry import register


# ── 自定义 Action 函数 ──────────────────────────────────

def _wake_and_launch(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """唤醒屏幕并启动乐享借。"""
    adb.wake_screen()
    adb.wait(1)
    adb.stop_test_app()
    adb.wait(1)
    adb.start_test_app()
    adb.wait(4)

    if not locator.exists(adb, f"#{Config.APP_PACKAGE}:id/webview_fl"):
        return ActionResult(
            action_name="唤醒屏幕并启动乐享借",
            success=False,
            message="WebView 容器未加载",
        )
    return ActionResult(
        action_name="唤醒屏幕并启动乐享借",
        success=True,
        message="APP 启动成功，WebView 容器已加载",
    )


def _verify_webview_loaded(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证 WebView 已加载且在前台。"""
    elem = locator.find(adb, "WebView")
    if not elem.enabled:
        return ActionResult(
            action_name="验证 WebView 已加载",
            success=False,
            message="WebView 未启用",
            element=elem,
        )

    elems = adb.find_elements(package=Config.APP_PACKAGE)
    if len(elems) == 0:
        return ActionResult(
            action_name="验证 WebView 已加载",
            success=False,
            message=f"未找到属于 {Config.APP_PACKAGE} 的 UI 元素",
        )

    return ActionResult(
        action_name="验证 WebView 已加载",
        success=True,
        message="WebView 已加载且在前台",
        element=elem,
    )


def _verify_main_container(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证主页面容器结构完整。"""
    checks = [
        f"#{Config.APP_PACKAGE}:id/action_bar_root",
        f"#{Config.APP_PACKAGE}:id/pre_loan_check_fl",
        f"#{Config.APP_PACKAGE}:id/webview_fl",
    ]
    for path in checks:
        if not locator.exists(adb, path):
            return ActionResult(
                action_name="验证主页面容器结构",
                success=False,
                message=f"容器不存在: {path}",
            )
    return ActionResult(
        action_name="验证主页面容器结构",
        success=True,
        message="主页面容器结构完整",
    )


def _verify_webview_interactive(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证 WebView 可交互。"""
    elem = locator.find(adb, "WebView")
    l, t, r, b = elem.bounds
    if (r - l) <= 100 or (b - t) <= 100:
        return ActionResult(
            action_name="验证 WebView 可交互",
            success=False,
            message=f"WebView 尺寸异常: {elem.bounds}",
            element=elem,
        )
    return ActionResult(
        action_name="验证 WebView 可交互",
        success=True,
        message="WebView 可交互",
        element=elem,
    )


# ── Flow 定义 ─────────────────────────────────────────

lexiang_borrow_home = Flow(
    id="lexiang_borrow_home",
    name="乐享借主页验证",
    description="启动乐享借 APP，验证 WebView 加载、主容器结构、WebView 可交互",
    category="app",
    driver_type="app",
    setup=[
        CustomAction(
            name="唤醒屏幕并启动乐享借",
            execute_fn=_wake_and_launch,
            retries=2,
            retry_delay=5.0,
        ),
    ],
    steps=[
        CustomAction(
            name="验证 WebView 已加载",
            execute_fn=_verify_webview_loaded,
        ),
        CustomAction(
            name="验证主页面容器结构",
            execute_fn=_verify_main_container,
        ),
        CustomAction(
            name="验证 WebView 可交互",
            execute_fn=_verify_webview_interactive,
        ),
    ],
    teardown=[],
)

register(lexiang_borrow_home)
