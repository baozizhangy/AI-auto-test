"""流程采集 - 提现页操作：修改金额、期数、计息方式、还款计划。

重点：
  - 每步操作后充分等待，确保页面渲染完成
  - 弹窗关闭通过查找弹窗内的关闭按钮，不用 press_back
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.adb_controller import ADBController
from agent.config import Config


class WithdrawCapture:
    def __init__(self):
        self.ctrl = ADBController(serial=Config.ANDROID_SERIAL)
        self.out_dir = Path("D:/UI_Agent/screenshots/capture_withdraw")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._step = 0

    def capture(self, label: str) -> None:
        """截图 + UI树保存。"""
        self._step += 1
        prefix = f"{self._step:02d}_{label}"
        img = self.out_dir / f"{prefix}.png"
        self.ctrl.screenshot(img)
        print(f"  📷 {img.name}")
        xml = self.out_dir / f"{prefix}_ui.xml"
        xml_text = self.ctrl._dump_ui()
        xml.write_text(xml_text, encoding="utf-8")
        print(f"  🌳 {xml.name}")

    def _dump_and_list(self) -> list[dict]:
        """dump UI树，返回所有可见元素。"""
        xml_text = self.ctrl._dump_ui()
        root = ET.fromstring(xml_text)
        elems = []
        for node in root.iter("node"):
            text = node.get("text", "")
            cls = node.get("class", "")
            bounds_raw = node.get("bounds", "[0,0][0,0]")
            nums = [int(n) for n in re.findall(r"\d+", bounds_raw)]
            l, t, r, b = nums[0], nums[1], nums[2], nums[3]
            if r > 0:
                elems.append({"text": text, "class": cls, "bounds": (l, t, r, b),
                              "center": ((l + r) // 2, (t + b) // 2)})
        return elems

    def _find(self, elems: list[dict], **kwargs) -> dict | None:
        for e in elems:
            match = True
            if "text" in kwargs and e["text"] != kwargs["text"]:
                match = False
            if "text_contains" in kwargs and kwargs["text_contains"] not in e["text"]:
                match = False
            if "class_contains" in kwargs and kwargs["class_contains"] not in e["class"]:
                match = False
            if match:
                return e
        return None

    def _close_popup(self) -> bool:
        """查找并点击弹窗内的关闭按钮（非 press_back）。

        策略：
          1. 查找文字为 "关闭"/"确定"/"我知道了"/"×"/"X" 的按钮
          2. 查找弹窗内顶部或底部的空文字小元素（×图标）
        """
        self.ctrl.wait(1)  # 等弹窗稳定
        elems = self._dump_and_list()

        # 策略1: 文字匹配关闭按钮
        close_texts = ["关闭", "确定", "我知道了", "知道了", "确认", "×", "X"]
        for ct in close_texts:
            for e in elems:
                if e["text"] == ct:
                    cx, cy = e["center"]
                    print(f"  找到关闭按钮: '{ct}' @ ({cx},{cy})")
                    self.ctrl.tap(cx, cy)
                    self.ctrl.wait(2)
                    return True

        # 策略2: 查找弹窗覆盖层中的 × 图标
        # 弹窗通常覆盖屏幕中部，查找居中且文字为空的小方块
        screen_w, screen_h = self.ctrl.get_screen_size()
        for e in elems:
            if (e["text"] == ""
                and 100 < e["bounds"][0] < screen_w - 100
                and e["bounds"][1] > screen_h * 0.3  # 不在顶部
                and (e["bounds"][2] - e["bounds"][0]) < 120  # 小元素
                and (e["bounds"][3] - e["bounds"][1]) < 120
                and "Image" not in e["class"]):
                # 可能是 × 图标
                cx, cy = e["center"]
                # 确保不在屏幕边缘（排除返回按钮等）
                if cx > 100 and cy > 300:
                    print(f"  找到可能的×图标: bounds={e['bounds']} class={e['class']}")
                    self.ctrl.tap(cx, cy)
                    self.ctrl.wait(2)
                    return True

        print("  ⚠️ 未找到弹窗关闭按钮")
        return False

    def _navigate_to_withdraw(self, product_name: str = "乐小融V2") -> bool:
        """从首页导航到指定产品的提现页。"""
        w, h = self.ctrl.get_screen_size()
        print(f"  导航到 {product_name} 提现页...")

        for scroll_i in range(8):
            xml_text = self.ctrl._dump_ui()
            root = ET.fromstring(xml_text)
            product_found = False
            withdraw_btn = None

            for node in root.iter("node"):
                text = node.get("text", "")
                bounds_raw = node.get("bounds", "[0,0][0,0]")
                nums = [int(n) for n in re.findall(r"\d+", bounds_raw)]
                l, t, r, b = nums[0], nums[1], nums[2], nums[3]

                if product_name.lower() in text.lower() and "额度由" in text and r > 0:
                    product_found = True
                if product_found and text == "立即提现" and r > 0:
                    withdraw_btn = ((l + r) // 2, (t + b) // 2)
                    break

            if withdraw_btn:
                print(f"  找到 {product_name} 的立即提现: {withdraw_btn}")
                self.ctrl.tap(*withdraw_btn)
                self.ctrl.wait(5)  # 等待提现页加载
                return True

            self.ctrl.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.3), 500)
            self.ctrl.wait(2)

        print(f"  ⚠️ 未找到 {product_name}")
        return False

    def run(self):
        w, h = self.ctrl.get_screen_size()
        print("=== 提现页操作采集 ===\n")

        # ── Step 0: 确保在提现页 ──
        print("[Step 0] 检查当前页面")
        self.ctrl.wake_screen()
        self.ctrl.wait(2)
        elems = self._dump_and_list()
        amount_input = self._find(elems, class_contains="EditText")
        if not amount_input:
            print("  不在提现页，导航中...")
            if not self._navigate_to_withdraw():
                print("  ❗ 导航失败")
                return
            self.ctrl.wait(3)  # 额外等待页面加载
            elems = self._dump_and_list()
            amount_input = self._find(elems, class_contains="EditText")
        if amount_input:
            print(f"  ✔ 已在提现页，当前金额: {amount_input['text']}")

        # ── Step 1: 点击金额输入框 ──
        print("\n[Step 1] 点击金额输入框")
        self.ctrl.wait(1)
        cx, cy = amount_input["center"]
        self.ctrl.tap(cx, cy)
        self.ctrl.wait(2)  # 等待键盘弹出
        self.capture("点击金额输入框")

        # ── Step 2: 清空并输入3000 ──
        print("\n[Step 2] 清空并输入3000")
        # 先点击清除按钮(×)
        elems = self._dump_and_list()
        clear_btn = None
        for e in elems:
            if (e["text"] == ""
                and 1000 < e["bounds"][0] < 1120
                and 790 < e["bounds"][1] < 930
                and (e["bounds"][2] - e["bounds"][0]) < 150):
                clear_btn = e
                break

        if clear_btn:
            cx, cy = clear_btn["center"]
            print(f"  点击清除按钮: ({cx},{cy})")
            self.ctrl.tap(cx, cy)
            self.ctrl.wait(1)
        else:
            # 全选删除
            print("  未找到清除按钮，全选删除")
            self.ctrl.press_key("KEYCODE_MOVE_END")
            self.ctrl.wait(0.5)
            self.ctrl.press_key("KEYCODE_SHIFT_LEFT")  # 开始选择
            self.ctrl.press_key("KEYCODE_MOVE_HOME")
            self.ctrl.wait(0.5)
            self.ctrl.press_key("KEYCODE_DEL")
            self.ctrl.wait(1)

        # 输入新金额
        self.ctrl.input_text("3000")
        self.ctrl.wait(2)
        self.capture("输入3000")

        # ── Step 3: 失焦确认 ──
        print("\n[Step 3] 失焦触发确认")
        # 点击页面标题区域失焦
        self.ctrl.tap(600, 300)
        self.ctrl.wait(3)
        self.capture("失焦确认金额")

        # 验证金额
        elems = self._dump_and_list()
        new_amount = self._find(elems, class_contains="EditText")
        if new_amount:
            print(f"  ✔ 金额: {new_amount['text']}")

        # ── Step 4: 点击期数下拉 ──
        print("\n[Step 4] 点击借款期限下拉框")
        self.ctrl.wait(1)
        elems = self._dump_and_list()
        period_val = None
        for e in elems:
            if re.match(r"\d+期", e["text"]):
                period_val = e
                break
        if period_val:
            cx, cy = period_val["center"]
            print(f"  当前期数: '{period_val['text']}' @ ({cx},{cy})")
            self.ctrl.tap(cx, cy)
            self.ctrl.wait(3)  # 等待下拉展开
        self.capture("展开期数下拉")

        # ── Step 5: 选择9期 ──
        print("\n[Step 5] 选择9期")
        elems = self._dump_and_list()
        print("  可见选项:")
        for e in elems:
            if re.match(r"\d+期", e["text"]) and e["bounds"][2] > 0:
                print(f"    '{e['text']}' @ {e['bounds']}")

        target_9 = None
        for e in elems:
            if e["text"] == "9期":
                target_9 = e
                break
        if target_9:
            cx, cy = target_9["center"]
            print(f"  点击: '{target_9['text']}' @ ({cx},{cy})")
            self.ctrl.tap(cx, cy)
            self.ctrl.wait(3)
        else:
            print("  ⚠️ 未找到9期，查看截图确认")
            self.capture("期数下拉截图")
        self.capture("选择9期后")

        # 验证期数
        self.ctrl.wait(1)
        elems = self._dump_and_list()
        for e in elems:
            if re.match(r"\d+期", e["text"]):
                print(f"  ✔ 期数: {e['text']}")
                break

        # ── Step 6: 点击计息方式详情 ──
        print("\n[Step 6] 点击计息方式详情")
        self.ctrl.wait(1)
        elems = self._dump_and_list()
        interest_val = self._find(elems, text_contains="按日计息")
        if interest_val:
            cx, cy = interest_val["center"]
            print(f"  计息: '{interest_val['text']}' @ ({cx},{cy})")
            self.ctrl.tap(cx, cy)
            self.ctrl.wait(3)  # 等待弹窗出现
        self.capture("计息方式弹窗")

        # ── Step 7: 关闭计息方式弹窗 ──
        print("\n[Step 7] 关闭计息方式弹窗")
        if self._close_popup():
            print("  ✔ 弹窗已关闭")
        else:
            print("  ⚠️ 弹窗关闭失败，继续下一步")
        self.ctrl.wait(2)
        self.capture("关闭计息方式弹窗后")

        # ── Step 8: 点击还款计划详情 ──
        print("\n[Step 8] 点击还款计划详情")
        self.ctrl.wait(1)
        elems = self._dump_and_list()
        repay_val = self._find(elems, text_contains="首期")
        if not repay_val:
            repay_val = self._find(elems, text_contains="应还")
        if repay_val:
            cx, cy = repay_val["center"]
            print(f"  还款: '{repay_val['text']}' @ ({cx},{cy})")
            self.ctrl.tap(cx, cy)
            self.ctrl.wait(3)  # 等待弹窗出现
        self.capture("还款计划弹窗")

        # ── Step 9: 关闭还款计划弹窗 ──
        print("\n[Step 9] 关闭还款计划弹窗")
        if self._close_popup():
            print("  ✔ 弹窗已关闭")
        else:
            print("  ⚠️ 弹窗关闭失败")
        self.ctrl.wait(2)
        self.capture("关闭还款计划弹窗后")

        print(f"\n=== 采集完成 ===")
        print(f"输出: {self.out_dir}")
        print(f"共 {self._step} 步")


if __name__ == "__main__":
    WithdrawCapture().run()
