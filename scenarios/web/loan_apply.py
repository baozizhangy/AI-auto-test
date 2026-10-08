"""贷款申请页主流程测试。

步骤：
1. 导航到 H5 贷款申请页并等待加载（使用 networkidle 确保动态内容就绪）
2. 修改借款金额为 3000，失焦验证
3. 点击还款计划的"应还"，验证弹窗展示
4. 关闭弹窗，验证弹窗关闭
"""

from scenarios.base import Scenario, StepDef
from scenarios.registry import register


# ── 步骤 1：打开页面 ──────────────────────────

def _step_navigate(web) -> None:
    """导航到 H5 贷款申请页。"""
    # 使用 load 等待页面主体加载完成（不用 networkidle，避免 H5 埋点/长轮询导致永不返回）
    web.navigate(web.base_url, wait_until="load")
    # 等待关键元素出现，确认页面可用（Playwright 自动轮询，比 sleep 更可靠）
    web.page.wait_for_selector("input.f-input", timeout=20000)


# ── 步骤 2：修改金额为 3000 ────────────────────

def _step_modify_amount_to_3000(web) -> None:
    inp = web.page.locator("input.f-input")
    inp.click(force=True)
    # 用键盘操作清空并输入新值，模拟真实用户输入以触发 Vue 响应式更新
    web.page.keyboard.press("Control+a")
    web.page.keyboard.press("Backspace")
    web.page.keyboard.type("3000", delay=50)


def _step_verify_amount_3000(web) -> None:
    # 手动派发 blur + change 事件，确保 Vue 响应式触发还款计划重新计算
    # （Escape 键在部分 H5 框架中不会触发 input 的 change/blur 事件）
    web.page.evaluate("""
        const inp = document.querySelector('input.f-input');
        if (inp) {
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            inp.dispatchEvent(new Event('blur', { bubbles: true }));
            inp.blur();
        }
    """)
    # 等待还款计划重新计算完成（API 请求 + DOM 重渲染）
    web.wait(5)
    actual = web.page.input_value("input.f-input").strip()
    assert actual == "3000", f"金额修改失败，期望 3000，实际 {actual}"


# ── 步骤 3：点击应还 → 验证弹窗 ────────────────

def _step_click_due_and_verify_popup(web) -> None:
    """点击应还区域，验证弹窗和遮罩展示。"""
    # 确认页面已完成重计算：检查显示的应还金额是否已变化（非默认 9000 的值）
    due = web.page.locator(".select-wrap").filter(has_text="应还")
    due.first.wait_for(state="visible", timeout=10000)

    # 等待应还金额文本包含 3000 对应的值（¥301 左右），确认重计算完成
    # 如果还是旧值说明重计算未完成，继续等待
    for _ in range(10):
        text = due.first.text_content() or ""
        # 旧值 9000 对应 ¥904，新值 3000 对应约 ¥301
        if "904" not in text:
            break
        web.wait(1)

    web.wait(0.5)  # 额外稳定时间

    # 用 Playwright click(force=True) 触发完整事件链
    due.first.click(force=True)

    # 等待弹窗遮罩出现
    web.page.locator(".action-mask").first.wait_for(state="visible", timeout=10000)

    # 验证遮罩层可见
    mask = web.page.locator(".action-mask")
    assert mask.count() > 0, "遮罩 .action-mask 不存在"
    assert mask.first.is_visible(), "遮罩 .action-mask 不可见"

    # 验证还款弹窗内容可见
    wraps = web.page.locator(".action-wrap")
    visible = [w for w in wraps.all() if w.is_visible()]
    assert len(visible) > 0, "弹窗 .action-wrap 不存在或不可见"


# ── 步骤 4：关闭弹窗 ──────────────────────────

def _step_close_popup(web) -> None:
    """点击弹窗关闭按钮或遮罩。"""
    web.wait(1.0)  # 等待弹窗打开动画完成

    # 策略 1：点击关闭按钮 img.only-close
    close_btn = web.page.locator("img.only-close")
    if close_btn.count() > 0 and close_btn.first.is_visible():
        close_btn.first.click(force=True)
        web.wait(1.0)
        # 检查是否已关闭
        mask = web.page.locator(".action-mask")
        if mask.count() == 0 or not mask.first.is_visible():
            return

    # 策略 2：JS 触发关闭按钮
    if close_btn.count() > 0:
        close_btn.first.evaluate("el => el.click()")
        web.wait(1.0)
        mask = web.page.locator(".action-mask")
        if mask.count() == 0 or not mask.first.is_visible():
            return

    # 策略 3：点击遮罩层
    mask = web.page.locator(".action-mask")
    if mask.count() > 0 and mask.first.is_visible():
        mask.first.click(force=True)
        web.wait(1.0)

    raise RuntimeError("未找到弹窗关闭按钮或遮罩")


def _step_verify_popup_closed(web) -> None:
    """验证所有弹窗和遮罩均已不可见。"""
    web.wait(0.5)
    # action-mask 关闭后可能从 DOM 中移除（count==0）或变为不可见
    mask = web.page.locator(".action-mask")
    if mask.count() > 0:
        assert not mask.first.is_visible(), "遮罩 .action-mask 仍然可见"

    # action-wrap 关闭后 display:none 或从 DOM 移除
    wraps = web.page.locator(".action-wrap")
    visible = [w for w in wraps.all() if w.is_visible()]
    assert len(visible) == 0, f"仍有 {len(visible)} 个弹窗可见"


def _step_bank_check(web) -> None:
    """点击绑卡按钮（银行卡或去绑卡文本）。"""
    # 使用 get_by_text 纯文本匹配，不限定 HTML 标签类型
    bank_btn = web.page.get_by_text("银行")
    bind_btn = web.page.get_by_text("去绑卡")
    if bind_btn.count() > 0 and bind_btn.first.is_visible():
        bind_btn.first.click(force=True)
    elif bank_btn.count() > 0 and bank_btn.first.is_visible():
        bank_btn.first.click(force=True)
    else:
        raise RuntimeError("未找到绑卡按钮")
    web.wait(3.0)


def _set_bind_card_page(web) -> None:
    """进入绑卡页面。"""
    header = web.page.locator('div.header-name')
    header.wait_for(state="visible", timeout=10000)
    text = header.text_content() or ""
    assert "银行卡" in text, "绑卡页面未正常打开"


def _step_fill_card_and_agree(web) -> None:
    """绑卡页面输入银行卡号、手机号，并勾选协议。"""
    # 1. 输入银行卡号
    card_input = web.page.locator("input[placeholder*='银行卡'], input[placeholder*='卡号']")
    if card_input.count() == 0:
        # 回退：通过标签文本定位附近的 input
        card_input = web.page.get_by_text("银行卡").locator(".. >> input").first
    card_input.first.click(force=True)
    web.page.keyboard.press("Control+a")
    web.page.keyboard.press("Backspace")
    web.page.keyboard.type("620200177977484335", delay=30)
    web.wait(1)

    # 2. 输入手机号
    phone_input = web.page.locator("input[placeholder*='手机'], input[placeholder*='手机号']")
    if phone_input.count() == 0:
        phone_input = web.page.get_by_text("手机").locator(".. >> input").first
    phone_input.first.click(force=True)
    web.page.keyboard.press("Control+a")
    web.page.keyboard.press("Backspace")
    web.page.keyboard.type("18726016979", delay=30)
    web.wait(0.5)

    # 3. 勾选协议——自定义 iconfont 图标勾选框
    checkbox = web.page.locator("span.icon-checkbox")
    checkbox.first.wait_for(state="visible", timeout=5000)
    checkbox.first.click(force=True)
    web.wait(1.0)


# ── 场景注册 ────────────────────────────────────

loan_apply_flow = Scenario(
    id="loan_apply_flow",
    name="贷款申请主流程检查",
    description="打开贷款H5页面 → 修改借款金额为3000 → 查看还款计划应还详情弹窗 → 关闭弹窗",
    category="web",
    driver_type="web",
    config={
        "base_url": (
            "http://bm-sit.shangtoutech.com/clg/bcs/api/p/hub/uni/22588/SWpaaE1UVXhZV1kwTkdZNU9UbGtOVFF6WTJNMU9UTXpaQ0k6MXdSaXlLOmozZ3ZiWU1FeVpwWnN1MHRNZlBOT1lOXzVxQQ?channelId=HUB_LXJ&channelUid=2000&loadingPage=draw&channelApplyNo=BM1222221610623614976&applyNo=AP1222221615657500672#/loan-apply?loanFlowNo=LFN1222222030725824512"
        ),
    },
    steps=[
        StepDef(name="打开贷款申请页", action=_step_navigate, assertion_type="dom"),
        StepDef(name="修改金额为3000", action=_step_modify_amount_to_3000, assertion_type="dom"),
        StepDef(name="验证金额3000", action=_step_verify_amount_3000, assertion_type="dom"),
        StepDef(name="点击应还并验证弹窗", action=_step_click_due_and_verify_popup, assertion_type="dom"),
        StepDef(name="关闭弹窗", action=_step_close_popup, assertion_type="dom"),
        StepDef(name="验证弹窗已关闭", action=_step_verify_popup_closed, assertion_type="dom"),
        StepDef(name="点击绑卡按钮", action=_step_bank_check, assertion_type="dom"),
        StepDef(name="进入绑卡页面", action=_set_bind_card_page, assertion_type="dom"),
        StepDef(name="填写银行卡信息并勾选协议", action=_step_fill_card_and_agree, assertion_type="dom"),
    ],
)

register(loan_apply_flow)
