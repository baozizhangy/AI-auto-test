"""APP 场景：乐享借首页完整交互流程。

11 步流程：
  setup:
    1. 重启APP（验证启动成功）
    2. 验证进入首页
  steps:
    3. 验证首页弹窗正常展示
    4. 关闭弹窗
    5. 验证产品列表包含乐小融V2
    6. 首页查找"我的账单"
    7. 点击"最近还款日"
    8. 验证跳转到待还账单列表
    9. 点击左上角返回首页
   10. 下拉刷新，验证页面刷新成功
   11. 切到后台
   12. 切回应用首页
"""

from __future__ import annotations

from agent.actions import CustomAction, ActionResult
from agent.adb_controller import ADBController
from agent.config import Config
from agent.path_locator import PathLocator
from scenarios.base import Flow
from scenarios.registry import register

# 复用 withdraw_flow 中已有的工具函数
from scenarios.app.withdraw_flow import (
    _restart_app,
    _close_popup_step,
    _scroll_to_product,
)


# ═══════════════════════════════════════════════════════════
#  内部工具
# ═══════════════════════════════════════════════════════════

def _is_on_homepage(adb: ADBController, locator: type[PathLocator]) -> bool:
    """判定当前是否在乐享借首页。

    特征：存在 "立即提现" 按钮 或 "额度由...提供" 文本。
    """
    return (
        locator.exists(adb, '*[text="立即提现"]')
        or locator.exists(adb, '*[text_contains="额度由"]')
    )


# ═══════════════════════════════════════════════════════════
#  Custom Actions
# ═══════════════════════════════════════════════════════════

def _verify_on_homepage(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证 APP 已进入首页。"""
    adb.dump_ui_xml()
    if _is_on_homepage(adb, locator):
        return ActionResult(
            action_name="验证进入首页",
            success=True,
            message="已进入乐享借首页",
        )
    adb.wait(3)
    adb.dump_ui_xml()
    if _is_on_homepage(adb, locator):
        return ActionResult(
            action_name="验证进入首页",
            success=True,
            message="已进入乐享借首页·重试",
        )
    return ActionResult(
        action_name="验证进入首页",
        success=False,
        message="未识别到首页特征元素",
    )


def _verify_homepage_popup(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证首页启动弹窗正常展示（home-pop）。"""
    adb.dump_ui_xml()
    pop_elems = adb.find_elements(resource_id_contains="home-pop")
    if pop_elems:
        return ActionResult(
            action_name="验证首页弹窗正常展示",
            success=True,
            message=f"首页弹窗正常展示 (数量={len(pop_elems)})",
        )
    # 二次重试，弹窗可能延迟出现
    adb.wait(2)
    adb.dump_ui_xml()
    pop_elems = adb.find_elements(resource_id_contains="home-pop")
    if pop_elems:
        return ActionResult(
            action_name="验证首页弹窗正常展示",
            success=True,
            message=f"首页弹窗正常展示·重试 (数量={len(pop_elems)})",
        )
    return ActionResult(
        action_name="验证首页弹窗正常展示",
        success=False,
        message="未检测到首页 home-pop 弹窗",
    )


def _verify_product_lexiaorong(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证产品列表中包含乐小融V2（最多滚动 12 屏查找）。"""
    result = _scroll_to_product(adb, "乐小融V2")
    if result is None:
        return ActionResult(
            action_name="验证产品列表包含乐小融V2",
            success=False,
            message="滚动后仍未找到乐小融V2产品",
        )
    return ActionResult(
        action_name="验证产品列表包含乐小融V2",
        success=True,
        message=f"产品列表包含乐小融V2 (定位 y={result['product_y']})",
    )


def _scroll_find_my_bills(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """首页滚动查找"我的账单"区块。"""
    w, h = adb.get_screen_size()

    # 已经在乐小融V2附近，先回到顶部再向下找"我的账单"
    for _ in range(8):
        adb.dump_ui_xml()
        # 优先精确匹配"我的账单"
        elems = adb.find_elements(text="我的账单")
        if not elems:
            elems = adb.find_elements(text_contains="我的账单")
        for e in elems:
            if e.bounds[1] > 150 and e.bounds[3] < h - 100:
                return ActionResult(
                    action_name="首页查找我的账单",
                    success=True,
                    message=f"已定位'我的账单' @ {e.center}",
                )
        # 向下滚一屏继续找
        adb.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
        adb.wait(1.5)

    return ActionResult(
        action_name="首页查找我的账单",
        success=False,
        message="滚动后未找到'我的账单'区块",
    )


def _tap_recent_repay_date(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击"最近还款日"。"""
    adb.dump_ui_xml()

    # 候选文本（容错不同 UI 版本）
    candidates = ["最近还款日", "最近还款", "近期还款日", "下一期还款日"]
    for text in candidates:
        elems = adb.find_elements(text=text)
        if not elems:
            elems = adb.find_elements(text_contains=text)
        for e in elems:
            if e.bounds[1] > 150:
                adb.tap(*e.center)
                adb.wait(4)
                return ActionResult(
                    action_name="点击最近还款日",
                    success=True,
                    message=f"已点击 '{text}' @ {e.center}",
                )

    return ActionResult(
        action_name="点击最近还款日",
        success=False,
        message=f"未找到候选文本之一: {candidates}",
    )


def _verify_bill_list_page(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证已跳转到待还账单列表页。"""
    adb.dump_ui_xml()

    # 待还账单页特征文本（多候选容错）
    markers = ["待还账单", "账单详情", "账单列表", "应还金额", "还款记录", "我的账单"]
    for m in markers:
        if (locator.exists(adb, f'*[text="{m}"]')
                or locator.exists(adb, f'*[text_contains="{m}"]')):
            return ActionResult(
                action_name="验证跳转到待还账单列表",
                success=True,
                message=f"已跳转到账单页 (匹配标识: {m})",
            )

    # 二次重试
    adb.wait(2)
    adb.dump_ui_xml()
    for m in markers:
        if (locator.exists(adb, f'*[text="{m}"]')
                or locator.exists(adb, f'*[text_contains="{m}"]')):
            return ActionResult(
                action_name="验证跳转到待还账单列表",
                success=True,
                message=f"已跳转到账单页·重试 (匹配标识: {m})",
            )

    return ActionResult(
        action_name="验证跳转到待还账单列表",
        success=False,
        message=f"未匹配到账单页特征: {markers}",
    )


def _tap_back_to_home(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击左上角返回按钮，返回首页。

    优先级: ivx (X) → headtitleplus_backimage (←) → 系统 BACK 键。
    点击后验证已返回首页。
    """
    adb.dump_ui_xml()

    tapped = False
    # 优先 ivx
    elems = adb.find_elements(resource_id_contains="ivx")
    for e in elems:
        if e.bounds[1] < 400:
            adb.tap(*e.center)
            tapped = True
            break
    # 回退 headtitleplus_backimage
    if not tapped:
        elems = adb.find_elements(resource_id_contains="headtitleplus_backimage")
        for e in elems:
            if e.bounds[1] < 400:
                adb.tap(*e.center)
                tapped = True
                break
    # 兜底 BACK 键
    if not tapped:
        adb.press_back()

    adb.wait(3)
    adb.dump_ui_xml()
    if _is_on_homepage(adb, locator):
        return ActionResult(
            action_name="点击左上角返回首页",
            success=True,
            message="已返回首页",
        )

    # 可能是多层 H5，再按一次 BACK
    adb.press_back()
    adb.wait(2)
    adb.dump_ui_xml()
    if _is_on_homepage(adb, locator):
        return ActionResult(
            action_name="点击左上角返回首页",
            success=True,
            message="已返回首页·二次返回",
        )

    return ActionResult(
        action_name="点击左上角返回首页",
        success=False,
        message="点击返回后未回到首页",
    )


def _pull_to_refresh(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """下拉刷新首页，验证刷新后仍在首页。"""
    w, h = adb.get_screen_size()

    # 从屏幕上部向下大幅滑动触发下拉刷新
    adb.swipe(w // 2, int(h * 0.25), w // 2, int(h * 0.75), 600)
    adb.wait(3)  # 等待加载动画
    adb.swipe(w // 2, int(h * 0.25), w // 2, int(h * 0.75), 600)
    adb.wait(2)

    # 刷新结束后仍应在首页
    adb.dump_ui_xml()
    if _is_on_homepage(adb, locator):
        return ActionResult(
            action_name="下拉刷新首页",
            success=True,
            message="下拉刷新成功，仍在首页",
        )
    return ActionResult(
        action_name="下拉刷新首页",
        success=False,
        message="刷新后页面状态异常，未识别到首页",
    )


def _send_to_background(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """切到后台（按 HOME 键）。"""
    adb.press_home()
    adb.wait(5)
    current = adb.get_current_activity()
    # APP 已被切到后台 → 当前 activity 不再是测试 APP 包名
    if Config.APP_PACKAGE and Config.APP_PACKAGE in current:
        return ActionResult(
            action_name="切到后台",
            success=False,
            message=f"按HOME后APP仍在前台: {current}",
        )
    return ActionResult(
        action_name="切到后台",
        success=True,
        message=f"已切到后台 (当前 Activity: {current or '未知'})",
    )


def _bring_to_foreground(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """切回 APP 首页。"""
    adb.start_test_app()
    adb.wait(5)
    adb.dump_ui_xml()
    if _is_on_homepage(adb, locator):
        return ActionResult(
            action_name="切回应用首页",
            success=True,
            message="已切回首页",
        )
    # 可能停留在弹窗页面，关掉再判定
    pop_elems = adb.find_elements(resource_id_contains="home-pop")
    if pop_elems:
        return ActionResult(
            action_name="切回应用首页",
            success=True,
            message="已切回首页（带启动弹窗）",
        )
    adb.wait(3)
    adb.dump_ui_xml()
    if _is_on_homepage(adb, locator):
        return ActionResult(
            action_name="切回应用首页",
            success=True,
            message="已切回首页·重试",
        )
    return ActionResult(
        action_name="切回应用首页",
        success=False,
        message="切回后未识别到首页特征",
    )


# ═══════════════════════════════════════════════════════════
#  Flow 定义
# ═══════════════════════════════════════════════════════════

lexiang_home_full_flow = Flow(
    id="lexiang_home_full_flow",
    name="乐享借首页完整交互流程",
    description=(
        "重启APP → 进入首页 → 验证弹窗 → 关闭弹窗 → 验证产品列表 → "
        "找我的账单 → 点最近还款日 → 验证账单页 → 返回首页 → 下拉刷新 → "
        "切后台 → 切回首页"
    ),
    category="app",
    driver_type="app",

    setup=[
        CustomAction(
            name="重启APP并验证启动成功",
            execute_fn=_restart_app,
            retries=1,
            retry_delay=3.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="验证进入首页",
            execute_fn=_verify_on_homepage,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
    ],

    steps=[
        CustomAction(
            name="验证首页弹窗正常展示",
            execute_fn=_verify_homepage_popup,
            retries=1,
            wait_before=0.5,
        ),
        CustomAction(
            name="关闭首页弹窗",
            execute_fn=_close_popup_step,
            retries=2,
            retry_delay=1.5,
            wait_before=0.5,
        ),
        CustomAction(
            name="验证产品列表包含乐小融V2",
            execute_fn=_verify_product_lexiaorong,
            retries=1,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="首页查找我的账单",
            execute_fn=_scroll_find_my_bills,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击最近还款日",
            execute_fn=_tap_recent_repay_date,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="验证跳转到待还账单列表",
            execute_fn=_verify_bill_list_page,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击左上角返回首页",
            execute_fn=_tap_back_to_home,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="下拉刷新首页",
            execute_fn=_pull_to_refresh,
            retries=1,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="切到后台",
            execute_fn=_send_to_background,
            retries=1,
            wait_before=0.5,
        ),
        CustomAction(
            name="切回应用首页",
            execute_fn=_bring_to_foreground,
            retries=2,
            retry_delay=3.0,
            wait_before=0.5,
        ),
    ],

    teardown=[],
)

register(lexiang_home_full_flow)
