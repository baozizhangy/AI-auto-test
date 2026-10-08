"""APP 场景：乐小融V2 提现页短流程（新 Flow 架构）。

短流程:
  setup:   重启APP → 验证启动弹窗 → 关闭弹窗 → 下滑寻找乐小融V2 → 点击立即提现
  steps:   验证跳转借款申请页 → 点击左上角X关闭 → 验证返回首页
"""

from __future__ import annotations

import re

from agent.actions import TapAction, InputAction, WaitAction, CustomAction, ActionResult
from agent.adb_controller import ADBController
from agent.config import Config
from agent.path_locator import PathLocator
from scenarios.base import Flow
from scenarios.registry import register


# ═══════════════════════════════════════════════════════════
#  工具函数（从旧代码迁移，保持逻辑不变）
# ═══════════════════════════════════════════════════════════

def _close_homepage_popup(adb: ADBController) -> None:
    """关闭首页弹窗。"""
    adb.dump_ui_xml()
    pop_elems = adb.find_elements(resource_id_contains="home-pop")
    if not pop_elems:
        return

    pop = pop_elems[0]
    all_elems = adb.find_elements(package=Config.APP_PACKAGE)
    for e in all_elems:
        if (e.text == ""
                and e.bounds[0] > pop.bounds[0]
                and e.bounds[2] < pop.bounds[2]
                and e.bounds[1] > 1900
                and (e.bounds[2] - e.bounds[0]) < 200
                and (e.bounds[3] - e.bounds[1]) < 200):
            adb.tap(*e.center)
            adb.wait(2)
            return

    # fallback: tap popup bottom center
    x = (pop.bounds[0] + pop.bounds[2]) // 2
    y = pop.bounds[3] - 40
    adb.tap(x, y)
    adb.wait(2)


def _scroll_to_product(adb: ADBController, product_name: str = "乐小融V2") -> dict | None:
    """滚动首页，定位指定产品。

    返回: {'product_y': int, 'btn_elem': Element|None} 或 None。
    不再仅仅返回 bool，以便后续步骤可复用点击。
    """
    w, h = adb.get_screen_size()

    for _ in range(12):
        adb.dump_ui_xml()
        product_elems = adb.find_elements(text_contains=f"额度由{product_name}提供")
        real_product = None
        for pe in product_elems:
            if pe.bounds[1] > 150:
                real_product = pe
                break

        if real_product:
            product_y = real_product.center[1]
            withdraw_btns = adb.find_elements(text="立即提现")
            best_btn, best_dist = None, 999999
            for btn in withdraw_btns:
                if btn.bounds[1] > 150:
                    dist = abs(btn.center[1] - product_y)
                    if dist < best_dist:
                        best_dist = dist
                        best_btn = btn
            return {
                "product_y": product_y,
                "btn_elem": best_btn if (best_btn and best_dist < 300) else None,
            }

        adb.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
        adb.wait(2)

    return None


def _is_on_withdraw_page(adb: ADBController, locator: type[PathLocator]) -> bool:
    """检测当前是否在提现页。"""
    markers = ["借款申请", "可取现额度", "确认借款"]
    for marker in markers:
        if locator.exists(adb, f'*[text="{marker}"]'):
            return True
    for marker in ["可取现额度", "可借款金额范围"]:
        if locator.exists(adb, f'*[text_contains="{marker}"]'):
            return True
    return False


# ═══════════════════════════════════════════════════════════
#  Custom Actions（导航与复杂逻辑）
# ═══════════════════════════════════════════════════════════

def _restart_app(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """重启APP：停止 → 启动 → 等待首页加载。"""
    adb.stop_test_app()
    adb.wait(2)
    adb.start_test_app()
    adb.wait(8)
    adb.dump_ui_xml()
    return ActionResult(
        action_name="重启APP并加载首页",
        success=True,
        message="APP已启动并加载首页",
    )


def _close_popup_step(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """关闭首页弹窗（若存在）。"""
    adb.dump_ui_xml()
    pop_elems = adb.find_elements(resource_id_contains="home-pop")
    if not pop_elems:
        return ActionResult(
            action_name="关闭首页弹窗",
            success=True,
            message="无弹窗需关闭",
        )
    _close_homepage_popup(adb)
    adb.wait(1)
    return ActionResult(
        action_name="关闭首页弹窗",
        success=True,
        message="弹窗已关闭",
    )


def _verify_launch_popup(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证 APP 启动后的弹窗是否出现（首页 home-pop）。

    仅检测不点击。若弹窗不出现也视为通过（并非所有账号都会弹），但需在消息中着重标记。
    """
    adb.dump_ui_xml()
    pop_elems = adb.find_elements(resource_id_contains="home-pop")
    if pop_elems:
        return ActionResult(
            action_name="验证启动弹窗",
            success=True,
            message=f"检测到 home-pop 弹窗 (数量={len(pop_elems)})",
        )
    return ActionResult(
        action_name="验证启动弹窗",
        success=True,
        message="本次启动未出现弹窗",
    )


def _scroll_find_product(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """下滑首页，定位乐小融V2产品（不点击）。"""
    result = _scroll_to_product(adb, "乐小融V2")
    if result is None:
        return ActionResult(
            action_name="下滑寻找乐小融V2",
            success=False,
            message="未找到乐小融V2产品",
        )
    return ActionResult(
        action_name="下滑寻找乐小融V2",
        success=True,
        message=f"已定位乐小融V2 (y={result['product_y']})",
    )


def _tap_withdraw_button(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击乐小融V2 的“立即提现”按钮。"""
    adb.dump_ui_xml()
    product_elems = adb.find_elements(text_contains="额度由乐小融V2提供")
    real_product = None
    for pe in product_elems:
        if pe.bounds[1] > 150:
            real_product = pe
            break
    if not real_product:
        return ActionResult(
            action_name="点击立即提现",
            success=False,
            message="在首页未定位到乐小融V2产品卡",
        )

    product_y = real_product.center[1]
    withdraw_btns = adb.find_elements(text="立即提现")
    best_btn, best_dist = None, 999999
    for btn in withdraw_btns:
        if btn.bounds[1] > 150:
            dist = abs(btn.center[1] - product_y)
            if dist < best_dist:
                best_dist = dist
                best_btn = btn

    if best_btn and best_dist < 300:
        adb.tap(*best_btn.center)
        adb.wait(5)
        return ActionResult(
            action_name="点击立即提现",
            success=True,
            message="已点击乐小融V2的立即提现",
        )
    # 兑底：右侧坐标点击
    adb.tap(913, product_y)
    adb.wait(5)
    return ActionResult(
        action_name="点击立即提现",
        success=True,
        message="兑底坐标点击立即提现",
    )


def _wait_withdraw_page(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """等待提现页加载完成。"""
    adb.wait(5)
    adb.dump_ui_xml()
    if _is_on_withdraw_page(adb, locator):
        return ActionResult(
            action_name="等待提现页加载",
            success=True,
            message="已到达提现页",
        )
    adb.wait(5)
    adb.dump_ui_xml()
    if _is_on_withdraw_page(adb, locator):
        return ActionResult(
            action_name="等待提现页加载",
            success=True,
            message="已到达提现页·重试",
        )
    return ActionResult(
        action_name="等待提现页加载",
        success=False,
        message="导航后仍未到达提现页",
    )


def _exists_with_nudge(adb: ADBController, locator: type[PathLocator],
                      path: str, attempts: int = 3) -> bool:
    """检查元素是否存在，针对 WebView 不稳定元素。

    失败时主动做微幅滚动触发 WebView Accessibility Tree 重新计算。
    """
    w, h = adb.get_screen_size()
    for i in range(attempts):
        adb.dump_ui_xml()
        if locator.exists(adb, path):
            return True
        if i < attempts - 1:
            # 微幅上下滚动 50px 触发 WebView 重绘 + AC tree 刷新
            cy = int(h * 0.5)
            adb.swipe(w // 2, cy, w // 2, cy - 50, 200)
            adb.wait(0.8)
            adb.swipe(w // 2, cy - 50, w // 2, cy, 200)
            adb.wait(1.2)
    return False


def _verify_withdraw_page(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证提现页核心元素完整性。

    分为必备元素（稳定）与辅助元素（WebView 动态文本，可能不稳定）。
    - 必备全部存在则认为在提现页。
    - 辅助元素仅警告，不作为失败依据（WebView Accessibility Tree 偶发丢节点）。
    """
    required = [
        '*[text="借款申请"]',
        'EditText[0]',
        '*[text="借款期限"]',
        '*[text="计息方式"]',
        '*[text="还款计划"]',
    ]
    optional = [
        '*[text_contains="可取现额度"]',
        '*[text_contains="可借款金额范围"]',
    ]

    # 必备元素：逐个检查，允许微滚动触发重绘
    for path in required:
        if not _exists_with_nudge(adb, locator, path, attempts=3):
            return ActionResult(
                action_name="验证提现页元素完整性",
                success=False,
                message=f"必备元素不存在: {path}",
            )

    # 辅助元素：尽力检查，丢失不阻断流程
    missing = [p for p in optional if not _exists_with_nudge(adb, locator, p, attempts=2)]

    msg = "提现页核心元素完整"
    if missing:
        msg += f"（WebView 动态文本未捕获: {missing}）"

    return ActionResult(
        action_name="验证提现页元素完整性",
        success=True,
        message=msg,
    )


def _verify_bottom_fields(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """滚动到底部，验证表单字段完整性。

    同 _verify_withdraw_page，区分必备 / 辅助元素。
    """
    w, h = adb.get_screen_size()
    adb.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
    adb.wait(2)

    required = [
        '*[text="收款卡"]',
        '*[text="借款用途"]',
        '*[text="确认借款"]',
    ]
    optional = [
        '*[text_contains="放款机构"]',
        '*[text_contains="我已阅读并同意"]',
    ]

    for path in required:
        if not _exists_with_nudge(adb, locator, path, attempts=3):
            return ActionResult(
                action_name="验证表单字段完整性",
                success=False,
                message=f"必备元素不存在: {path}",
            )

    missing = [p for p in optional if not _exists_with_nudge(adb, locator, p, attempts=2)]
    msg = "表单字段完整"
    if missing:
        msg += f"（WebView 动态文本未捕获: {missing}）"

    return ActionResult(
        action_name="验证表单字段完整性",
        success=True,
        message=msg,
    )


def _close_popup_click(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """关闭弹窗：查找"我知道了"/"确定"等关闭按钮并点击。"""
    close_texts = ["我知道了", "确定", "关闭", "确认", "知道了"]
    for ct in close_texts:
        if locator.exists(adb, f'*[text="{ct}"]'):
            elem = locator.find(adb, f'*[text="{ct}"]')
            adb.tap(*elem.center)
            adb.wait(2)
            return ActionResult(
                action_name="关闭弹窗",
                success=True,
                message=f"点击 '{ct}' 关闭弹窗",
                element=elem,
            )
    return ActionResult(
        action_name="关闭弹窗",
        success=False,
        message="未找到弹窗关闭按钮",
    )


def _verify_loan_apply_page(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证已跳转进入借款申请页（提现页）。"""
    adb.dump_ui_xml()
    if _is_on_withdraw_page(adb, locator):
        return ActionResult(
            action_name="验证跳转借款申请页",
            success=True,
            message="已进入借款申请页",
        )
    adb.wait(2)
    adb.dump_ui_xml()
    if _is_on_withdraw_page(adb, locator):
        return ActionResult(
            action_name="验证跳转借款申请页",
            success=True,
            message="已进入借款申请页·重试",
        )
    return ActionResult(
        action_name="验证跳转借款申请页",
        success=False,
        message="未检测到借款申请页特征元素",
    )


def _tap_close_x_back(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击提现页左上角 X / 返回按钮，返回首页。

    优先 'ivx' (X 按钮) → 'headtitleplus_backimage' (箭头) → BACK 键。
    """
    adb.dump_ui_xml()

    # 优先：ivx 关闭 X
    elems = adb.find_elements(resource_id_contains="ivx")
    for e in elems:
        if e.bounds[1] < 400:
            adb.tap(*e.center)
            adb.wait(3)
            return ActionResult(
                action_name="点击左上角X关闭提现页",
                success=True,
                message=f"已点击 X 关闭按钮 @ {e.center}",
            )

    # 回退：headtitleplus_backimage 箭头
    elems = adb.find_elements(resource_id_contains="headtitleplus_backimage")
    for e in elems:
        if e.bounds[1] < 400:
            adb.tap(*e.center)
            adb.wait(3)
            return ActionResult(
                action_name="点击左上角X关闭提现页",
                success=True,
                message=f"已点击返回箭头 @ {e.center}",
            )

    # 兑底：BACK 键
    adb.press_back()
    adb.wait(3)
    return ActionResult(
        action_name="点击左上角X关闭提现页",
        success=True,
        message="兑底使用系统 BACK 键返回",
    )


def _verify_back_homepage(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证已返回乐享借首页。

    首页特征：存在 '立即提现' 按钮 或 '额度由...提供' 文本。
    如果仍在提现页（存在 '借款申请'）则判定为失败。
    """
    adb.dump_ui_xml()

    if locator.exists(adb, '*[text="借款申请"]'):
        return ActionResult(
            action_name="验证返回首页",
            success=False,
            message="仍停留在借款申请页，未返回首页",
        )

    if (locator.exists(adb, '*[text="立即提现"]')
            or locator.exists(adb, '*[text_contains="额度由"]')):
        return ActionResult(
            action_name="验证返回首页",
            success=True,
            message="已返回乐享借首页",
        )

    adb.wait(2)
    adb.dump_ui_xml()
    if (locator.exists(adb, '*[text="立即提现"]')
            or locator.exists(adb, '*[text_contains="额度由"]')):
        return ActionResult(
            action_name="验证返回首页",
            success=True,
            message="已返回乐享借首页·重试",
        )

    return ActionResult(
        action_name="验证返回首页",
        success=False,
        message="页面状态未知，未识别到首页特征元素",
    )


# ═══════════════════════════════════════════════════════════
#  Flow 定义
# ═══════════════════════════════════════════════════════════

lexiang_withdraw_flow = Flow(
    id="lexiang_withdraw_flow",
    name="APP启动定位产品",
    description=(
        "重启APP → 验证启动弹窗 → 关闭弹窗 → 下滑寻找乐小融V2 → "
        "点击立即提现 → 验证跳转借款申请页 → 点击左上角X关闭 → 验证返回首页"
    ),
    category="app",
    driver_type="app",

    setup=[
        CustomAction(
            name="重启APP并加载首页",
            execute_fn=_restart_app,
            retries=1,
            retry_delay=3.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="验证启动后弹窗",
            execute_fn=_verify_launch_popup,
            retries=1,
            wait_before=0.5,
        ),
        CustomAction(
            name="关闭首页弹窗",
            execute_fn=_close_popup_step,
            retries=1,
            wait_before=0.5,
        ),
        CustomAction(
            name="下滑寻找乐小融V2",
            execute_fn=_scroll_find_product,
            retries=2,
            retry_delay=3.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击立即提现",
            execute_fn=_tap_withdraw_button,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
    ],

    steps=[
        CustomAction(
            name="验证跳转借款申请页",
            execute_fn=_verify_loan_apply_page,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击左上角X关闭提现页",
            execute_fn=_tap_close_x_back,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="验证返回首页",
            execute_fn=_verify_back_homepage,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
    ],

    teardown=[],
)

register(lexiang_withdraw_flow)
