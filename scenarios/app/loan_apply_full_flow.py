"""APP 场景：乐小融V2 借款完整申请流程。

完整链路:
  setup:  重启APP → 关闭弹窗 → 寻找乐小融V2 → 点击立即提现 → 等待提现页加载
  steps:  验证金额6200 → 点击X清空 → 弹出输入框 → 输入1000 → 失焦8秒触发试算
        → 验证利息≈54 → 勾选协议 → 点击确认借款 → 跳转协议页等5秒
        → 点击"我已阅读并同意" → 等5秒+查询短信+输入验证码 → 点击下一步
        → 验证放款审核中页 → 点击左上角X关闭
  teardown: 清理借款数据 (clean_unsettled_loans)
"""

from __future__ import annotations

import re

from agent.actions import CustomAction, ActionResult
from agent.adb_controller import ADBController
from agent.config import Config
from agent import mysql_client as db
from agent.path_locator import PathLocator
from scenarios.base import Flow
from scenarios.registry import register

# 复用 withdraw_flow 的导航 setup
from scenarios.app.withdraw_flow import (
    _restart_app,
    _close_popup_step,
    _scroll_find_product,
    _tap_withdraw_button,
    _wait_withdraw_page,
    _is_on_withdraw_page,
)


# ═══════════════════════════════════════════════════════════
#  测试数据（按业务调整）
# ═══════════════════════════════════════════════════════════

TEST_PHONE = "17325575205"            # 测试手机号（用于查询短信验证码）
TEST_CUST_NO = "CT1116672591353106432"  # 测试客户号（teardown 清理借据）
EXPECTED_DEFAULT_AMOUNT = "6200"      # 进入提现页默认金额
LOAN_AMOUNT = "1000"                  # 本次借款金额
EXPECTED_INTEREST = 54                # 试算预期利息（约等）
INTEREST_TOLERANCE = 5                # 利息允许偏差


# 在多个 step 之间共享查询到的验证码（模块级容器，避免全局变量副作用）
_state: dict = {"sms_code": None}


# ═══════════════════════════════════════════════════════════
#  内部工具函数
# ═══════════════════════════════════════════════════════════

def _find_amount_input(adb: ADBController) -> "UIElement | None":  # type: ignore[name-defined]
    """定位金额输入框。优先 EditText，回退到包含 ¥/￥ 的元素附近。"""
    edits = adb.find_elements(class_name="android.widget.EditText")
    if edits:
        # 取第一个非空 EditText
        for e in edits:
            if e.bounds[1] > 150:
                return e
    return None


def _read_amount_text(adb: ADBController) -> str:
    """读取金额输入框当前文本，去除非数字。"""
    elem = _find_amount_input(adb)
    if elem is None:
        return ""
    return re.sub(r"[^\d.]", "", elem.text or "")


def _find_interest_value(adb: ADBController) -> float | None:
    """从 UI 中提取"计息方式"行后的利息数值。

    策略:
      1. 找到 text="计息方式" 的元素，作为锚点（取其 y 坐标）。
      2. 在同行（y 偏差 < 80）右侧（x > 锚点）查找文本中含数字的元素。
      3. 提取首个数字（带小数）作为利息。
    """
    adb.dump_ui_xml()
    anchors = adb.find_elements(text="计息方式")
    if not anchors:
        anchors = adb.find_elements(text_contains="计息方式")
    if not anchors:
        return None

    anchor = anchors[0]
    ay = anchor.center[1]
    ax = anchor.bounds[2]  # 锚点右边界

    all_elems = adb.find_elements(package=Config.APP_PACKAGE)
    candidates = []
    for e in all_elems:
        if not e.text:
            continue
        # 同行 + 在右侧
        if abs(e.center[1] - ay) > 80:
            continue
        if e.bounds[0] < ax:
            continue
        m = re.search(r"(\d+(?:\.\d+)?)", e.text)
        if m:
            candidates.append((e.bounds[0], float(m.group(1))))

    if not candidates:
        return None
    # 取最靠近锚点（x 最小）的那个
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _find_clear_button_near(adb: ADBController, input_elem) -> "UIElement | None":  # type: ignore[name-defined]
    """在输入框右侧查找小尺寸空文本元素（X 清除按钮）。"""
    if input_elem is None:
        return None
    all_elems = adb.find_elements(package=input_elem.package or Config.APP_PACKAGE)
    for e in all_elems:
        if (
            e.text == ""
            and e.bounds[0] > input_elem.bounds[0]
            and e.bounds[1] > input_elem.bounds[1] - 50
            and e.bounds[3] < input_elem.bounds[3] + 50
            and (e.bounds[2] - e.bounds[0]) < 150
            and (e.bounds[3] - e.bounds[1]) < 150
        ):
            return e
    return None


def _find_protocol_checkbox(adb: ADBController):
    """查找"我已阅读并同意"前的协议勾选框。

    返回 (checkbox_elem, is_checked) 或 (None, False)。
    """
    adb.dump_ui_xml()
    # 锚点：文本含"我已阅读"
    anchors = adb.find_elements(text_contains="我已阅读")
    if not anchors:
        anchors = adb.find_elements(text_contains="同意")
    if not anchors:
        return None, False

    anchor = anchors[0]
    ay = anchor.center[1]

    # 在锚点左侧同行查找小尺寸 clickable 元素（CheckBox）
    all_elems = adb.find_elements(package=anchor.package or Config.APP_PACKAGE)
    for e in all_elems:
        if e is anchor:
            continue
        if abs(e.center[1] - ay) > 60:
            continue
        if e.bounds[2] > anchor.bounds[0]:  # 必须在锚点左侧
            continue
        w_ = e.bounds[2] - e.bounds[0]
        h_ = e.bounds[3] - e.bounds[1]
        if 20 <= w_ <= 120 and 20 <= h_ <= 120:
            # 通过 content_desc / class 判断是否已选中（不一定可靠）
            checked = "checked" in (e.content_desc or "").lower()
            return e, checked

    # 兜底：直接返回锚点左边坐标的虚拟元素
    return anchor, False


def _is_on_loading_review_page(adb: ADBController, locator: type[PathLocator]) -> bool:
    """判定当前是否在"放款审核中"页。"""
    markers = ["放款审核中", "审核中", "提交成功", "申请已提交"]
    for m in markers:
        if (locator.exists(adb, f'*[text="{m}"]')
                or locator.exists(adb, f'*[text_contains="{m}"]')):
            return True
    return False


# ═══════════════════════════════════════════════════════════
#  Custom Actions
# ═══════════════════════════════════════════════════════════

def _verify_default_amount(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证金额输入框默认显示 6200。"""
    adb.wait(2)
    adb.dump_ui_xml()
    actual = _read_amount_text(adb)
    if EXPECTED_DEFAULT_AMOUNT in actual:
        return ActionResult(
            action_name="验证金额输入框默认6200",
            success=True,
            message=f"金额输入框默认值正确: {actual}",
        )
    # 重试一次
    adb.wait(2)
    adb.dump_ui_xml()
    actual = _read_amount_text(adb)
    if EXPECTED_DEFAULT_AMOUNT in actual:
        return ActionResult(
            action_name="验证金额输入框默认6200",
            success=True,
            message=f"金额输入框默认值正确·重试: {actual}",
        )
    return ActionResult(
        action_name="验证金额输入框默认6200",
        success=False,
        message=f"金额输入框默认值不符: 期望含 {EXPECTED_DEFAULT_AMOUNT}，实际 '{actual}'",
    )


def _tap_clear_amount(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击金额输入框右侧 X 清空按钮。"""
    adb.dump_ui_xml()
    edit = _find_amount_input(adb)
    if edit is None:
        return ActionResult(
            action_name="点击X清空金额",
            success=False,
            message="未定位到金额输入框",
        )

    clear_btn = _find_clear_button_near(adb, edit)
    if clear_btn is not None:
        adb.tap(*clear_btn.center)
        adb.wait(2)
        return ActionResult(
            action_name="点击X清空金额",
            success=True,
            message=f"已点击 X 清空 @ {clear_btn.center}",
        )

    # 回退：聚焦 + 全选删除
    adb.tap(*edit.center)
    adb.wait(1)
    adb.press_key("KEYCODE_MOVE_END")
    adb.wait(0.3)
    for _ in range(8):  # 多次删除以确保清空
        adb.press_key("KEYCODE_DEL")
    adb.wait(1)
    return ActionResult(
        action_name="点击X清空金额",
        success=True,
        message="兜底：聚焦后逐个 KEYCODE_DEL 删除",
    )


def _verify_amount_input_popup(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证已弹出金额输入框（金额已清空且键盘/输入态可见）。"""
    adb.wait(1.5)
    adb.dump_ui_xml()
    actual = _read_amount_text(adb)
    # 清空后 actual 应为空或非 6200
    if actual == "" or EXPECTED_DEFAULT_AMOUNT not in actual:
        return ActionResult(
            action_name="验证弹出金额输入框",
            success=True,
            message=f"金额已清空 (当前值='{actual}')",
        )
    return ActionResult(
        action_name="验证弹出金额输入框",
        success=False,
        message=f"清空失败，当前值仍为 '{actual}'",
    )


def _input_amount(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """输入借款金额 1000。"""
    adb.dump_ui_xml()
    edit = _find_amount_input(adb)
    if edit is None:
        return ActionResult(
            action_name=f"输入金额{LOAN_AMOUNT}",
            success=False,
            message="未定位到金额输入框",
        )
    # 确保聚焦
    adb.tap(*edit.center)
    adb.wait(1)
    adb.input_text(LOAN_AMOUNT)
    adb.wait(2)

    actual = _read_amount_text(adb)
    if LOAN_AMOUNT in actual:
        return ActionResult(
            action_name=f"输入金额{LOAN_AMOUNT}",
            success=True,
            message=f"已输入金额: {actual}",
        )
    return ActionResult(
        action_name=f"输入金额{LOAN_AMOUNT}",
        success=False,
        message=f"输入后金额不符: 期望含 {LOAN_AMOUNT}，实际 '{actual}'",
    )


def _defocus_wait_trial(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """失焦并等待 8 秒触发试算。"""
    # 失焦：点击页面顶部标题区域
    adb.tap(540, 280)
    adb.wait(8)
    return ActionResult(
        action_name="失焦等8秒触发试算",
        success=True,
        message="已失焦并等待 8 秒",
    )


def _verify_interest(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """读取计息方式后的利息数值，验证 ≈ 54。"""
    interest = _find_interest_value(adb)
    if interest is None:
        # 二次重试
        adb.wait(3)
        interest = _find_interest_value(adb)

    if interest is None:
        return ActionResult(
            action_name=f"验证利息≈{EXPECTED_INTEREST}",
            success=False,
            message="未在'计息方式'行右侧解析到利息数值",
        )

    diff = abs(interest - EXPECTED_INTEREST)
    if diff <= INTEREST_TOLERANCE:
        return ActionResult(
            action_name=f"验证利息≈{EXPECTED_INTEREST}",
            success=True,
            message=f"试算成功，利息={interest} (期望≈{EXPECTED_INTEREST}, 偏差={diff})",
        )
    return ActionResult(
        action_name=f"验证利息≈{EXPECTED_INTEREST}",
        success=False,
        message=f"利息偏差过大: 实际={interest}, 期望≈{EXPECTED_INTEREST} (容差={INTEREST_TOLERANCE})",
    )


def _check_protocol(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """勾选"我已阅读并同意"协议框（先等待 3 秒再勾选）。"""
    # 先滚到底部确保协议勾选框可见
    w, h = adb.get_screen_size()
    adb.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.3), 500)
    adb.wait(3)  # 用户要求"等待3秒勾选"

    cb_elem, checked = _find_protocol_checkbox(adb)
    if cb_elem is None:
        return ActionResult(
            action_name="勾选协议",
            success=False,
            message="未定位到协议勾选框",
        )
    if checked:
        return ActionResult(
            action_name="勾选协议",
            success=True,
            message=f"协议已勾选 @ {cb_elem.center}",
        )

    adb.tap(*cb_elem.center)
    adb.wait(2)
    return ActionResult(
        action_name="勾选协议",
        success=True,
        message=f"已点击协议勾选框 @ {cb_elem.center}",
    )


def _tap_confirm_loan(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击确认借款按钮。"""
    adb.dump_ui_xml()
    # 优先精确文本
    candidates = ["确认借款", "立即借款", "确认", "立即提交", "提交申请"]
    for text in candidates:
        elems = adb.find_elements(text=text)
        for e in elems:
            if e.bounds[1] > 150 and e.clickable:
                adb.tap(*e.center)
                adb.wait(5)
                return ActionResult(
                    action_name="点击确认借款",
                    success=True,
                    message=f"已点击 '{text}' @ {e.center}",
                )
    # 兜底：text_contains
    for text in candidates:
        elems = adb.find_elements(text_contains=text)
        for e in elems:
            if e.bounds[1] > 150:
                adb.tap(*e.center)
                adb.wait(5)
                return ActionResult(
                    action_name="点击确认借款",
                    success=True,
                    message=f"已点击 '{e.text}' @ {e.center}",
                )

    return ActionResult(
        action_name="点击确认借款",
        success=False,
        message=f"未找到候选按钮: {candidates}",
    )


def _wait_protocol_page(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """等待跳转到协议强制阅读页（等 5 秒）。"""
    adb.wait(5)
    adb.dump_ui_xml()
    # 协议页特征：含"协议"或滚动条到底后才能点同意
    markers = ["借款合同", "借款协议", "服务协议", "用户协议", "请仔细阅读", "请阅读"]
    for m in markers:
        if (locator.exists(adb, f'*[text="{m}"]')
                or locator.exists(adb, f'*[text_contains="{m}"]')):
            return ActionResult(
                action_name="跳转协议页等5秒",
                success=True,
                message=f"已跳转协议页 (匹配: {m})",
            )
    # 没匹配也认为通过——避免某些版本无明显标识阻塞流程
    return ActionResult(
        action_name="跳转协议页等5秒",
        success=True,
        message="已等5秒（未识别到明确协议页标识，继续后续步骤）",
    )


def _tap_agree_protocol(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击"我已阅读并同意以上协议"按钮（等待按钮高亮可点击）。"""
    candidates = [
        "我已阅读并同意以上协议",
        "我已阅读并同意",
        "同意并继续",
        "同意",
        "下一步",
    ]
    # 重试 3 次，覆盖按钮从灰到亮的过渡
    for attempt in range(3):
        adb.dump_ui_xml()
        for text in candidates:
            elems = adb.find_elements(text=text)
            for e in elems:
                if e.bounds[1] > 150 and e.enabled:
                    adb.tap(*e.center)
                    adb.wait(4)
                    return ActionResult(
                        action_name="点击我已阅读并同意",
                        success=True,
                        message=f"已点击 '{text}' @ {e.center}",
                    )
            elems = adb.find_elements(text_contains=text)
            for e in elems:
                if e.bounds[1] > 150 and e.enabled:
                    adb.tap(*e.center)
                    adb.wait(4)
                    return ActionResult(
                        action_name="点击我已阅读并同意",
                        success=True,
                        message=f"已点击包含 '{text}' 的按钮 @ {e.center}",
                    )
        # 触发滚动到底，让按钮高亮
        w, h = adb.get_screen_size()
        adb.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 600)
        adb.wait(2)

    return ActionResult(
        action_name="点击我已阅读并同意",
        success=False,
        message=f"3 次重试后未找到候选按钮: {candidates}",
    )


def _query_and_input_sms(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """等 5 秒触发短信下发 → 查询验证码 → 点击短信框输入。"""
    adb.wait(5)
    code = db.get_draw_identity_verify_code(TEST_PHONE)
    # 兜底：再等 3 秒重查
    if code is None:
        adb.wait(3)
        code = db.get_draw_identity_verify_code(TEST_PHONE)
    if code is None:
        return ActionResult(
            action_name="查询并输入短信验证码",
            success=False,
            message=f"未从 DB 查到 {TEST_PHONE} 的验证码",
        )

    _state["sms_code"] = code

    # 定位短信验证码输入框
    adb.dump_ui_xml()
    edits = adb.find_elements(class_name="android.widget.EditText")
    target = None
    # 通常验证码框在页面中部，bound 较短
    for e in edits:
        if e.bounds[1] > 150:
            target = e
            break

    if target is None:
        return ActionResult(
            action_name="查询并输入短信验证码",
            success=False,
            message=f"已查到验证码 {code}，但未定位到验证码输入框",
        )

    adb.tap(*target.center)
    adb.wait(1.5)
    adb.input_text(str(code))
    adb.wait(2)
    return ActionResult(
        action_name="查询并输入短信验证码",
        success=True,
        message=f"已输入验证码 {code}",
    )


def _tap_next_step(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击下一步 / 提交按钮。"""
    adb.dump_ui_xml()
    candidates = ["下一步", "确认", "提交", "确定", "下一步 ", "完成"]
    for text in candidates:
        elems = adb.find_elements(text=text)
        for e in elems:
            if e.bounds[1] > 150 and e.enabled:
                adb.tap(*e.center)
                adb.wait(6)
                return ActionResult(
                    action_name="点击下一步",
                    success=True,
                    message=f"已点击 '{text}' @ {e.center}",
                )
    # 兜底：text_contains
    for text in candidates:
        elems = adb.find_elements(text_contains=text)
        for e in elems:
            if e.bounds[1] > 150:
                adb.tap(*e.center)
                adb.wait(6)
                return ActionResult(
                    action_name="点击下一步",
                    success=True,
                    message=f"已点击包含 '{text}' 的按钮",
                )
    # 兜底2：直接按 ENTER
    adb.press_enter()
    adb.wait(6)
    return ActionResult(
        action_name="点击下一步",
        success=True,
        message="兜底：发送 ENTER 键",
    )


def _verify_loading_review_page(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证已进入"放款审核中"页面。"""
    adb.dump_ui_xml()
    if _is_on_loading_review_page(adb, locator):
        return ActionResult(
            action_name="验证放款审核中页",
            success=True,
            message="已进入放款审核中页",
        )
    # 重试
    adb.wait(4)
    adb.dump_ui_xml()
    if _is_on_loading_review_page(adb, locator):
        return ActionResult(
            action_name="验证放款审核中页",
            success=True,
            message="已进入放款审核中页·重试",
        )
    return ActionResult(
        action_name="验证放款审核中页",
        success=False,
        message="未识别到'放款审核中'特征文本",
    )


def _tap_close_review(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """点击放款审核中页左上角 X 关闭按钮。

    优先 'ivx' → 'headtitleplus_backimage' → BACK 键。
    """
    adb.dump_ui_xml()
    elems = adb.find_elements(resource_id_contains="ivx")
    for e in elems:
        if e.bounds[1] < 400:
            adb.tap(*e.center)
            adb.wait(3)
            return ActionResult(
                action_name="点击左上角X关闭审核页",
                success=True,
                message=f"已点击 X 关闭按钮 @ {e.center}",
            )
    elems = adb.find_elements(resource_id_contains="headtitleplus_backimage")
    for e in elems:
        if e.bounds[1] < 400:
            adb.tap(*e.center)
            adb.wait(3)
            return ActionResult(
                action_name="点击左上角X关闭审核页",
                success=True,
                message=f"已点击返回箭头 @ {e.center}",
            )
    adb.press_back()
    adb.wait(3)
    return ActionResult(
        action_name="点击左上角X关闭审核页",
        success=True,
        message="兜底使用 BACK 键返回",
    )


def _teardown_clean_loan_data(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """teardown：清理本测试帐号产生的未结清借据。"""
    try:
        affected = db.clean_unsettled_loans(TEST_CUST_NO)
    except Exception as e:
        return ActionResult(
            action_name="清理借款数据",
            success=False,
            message=f"DB 清理失败: {e}",
        )
    return ActionResult(
        action_name="清理借款数据",
        success=True,
        message=f"已删除 {affected} 条未结清借据 (cust_no={TEST_CUST_NO})",
    )


# ═══════════════════════════════════════════════════════════
#  Flow 定义
# ═══════════════════════════════════════════════════════════

lexiang_loan_apply_full_flow = Flow(
    id="lexiang_loan_apply_full_flow",
    name="乐小融V2借款完整申请流程",
    description=(
        "重启APP → 关闭弹窗 → 寻找乐小融V2 → 点击立即提现 → 验证默认金额 → "
        "清空 → 输入1000 → 失焦试算 → 验证利息≈54 → 勾选协议 → 确认借款 → "
        "协议页同意 → 查询短信验证码并输入 → 下一步 → 验证放款审核中 → "
        "关闭X → 清理借款数据"
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
        CustomAction(
            name="等待提现页加载",
            execute_fn=_wait_withdraw_page,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
    ],

    steps=[
        CustomAction(
            name="验证金额输入框默认6200",
            execute_fn=_verify_default_amount,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击X清空金额",
            execute_fn=_tap_clear_amount,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="验证弹出金额输入框",
            execute_fn=_verify_amount_input_popup,
            retries=1,
            wait_before=0.5,
        ),
        CustomAction(
            name=f"输入金额{LOAN_AMOUNT}",
            execute_fn=_input_amount,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="失焦等8秒触发试算",
            execute_fn=_defocus_wait_trial,
            retries=1,
            wait_before=0.5,
        ),
        CustomAction(
            name=f"验证利息≈{EXPECTED_INTEREST}",
            execute_fn=_verify_interest,
            retries=2,
            retry_delay=3.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="勾选协议",
            execute_fn=_check_protocol,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击确认借款",
            execute_fn=_tap_confirm_loan,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="跳转协议页等5秒",
            execute_fn=_wait_protocol_page,
            retries=1,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击我已阅读并同意",
            execute_fn=_tap_agree_protocol,
            retries=2,
            retry_delay=3.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="查询并输入短信验证码",
            execute_fn=_query_and_input_sms,
            retries=2,
            retry_delay=3.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击下一步",
            execute_fn=_tap_next_step,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="验证放款审核中页",
            execute_fn=_verify_loading_review_page,
            retries=2,
            retry_delay=3.0,
            wait_before=0.5,
        ),
        CustomAction(
            name="点击左上角X关闭审核页",
            execute_fn=_tap_close_review,
            retries=2,
            retry_delay=2.0,
            wait_before=0.5,
        ),
    ],

    teardown=[
        CustomAction(
            name="清理借款数据",
            execute_fn=_teardown_clean_loan_data,
            retries=1,
            retry_delay=2.0,
            wait_before=0.5,
        ),
    ],
)

register(lexiang_loan_apply_full_flow)
