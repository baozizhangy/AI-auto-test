"""流程采集工具：逐步执行操作，每步保存截图 + UI树XML。

用法:
    python tools/flow_capture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.adb_controller import ADBController
from agent.config import Config


class FlowCapture:
    """逐步执行操作并采集截图+UI树。"""

    def __init__(self, flow_name: str):
        self.ctrl = ADBController(serial=Config.ANDROID_SERIAL)
        self.out_dir = Path(f"D:/UI_Agent/screenshots/capture_{flow_name}")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._step = 0

    def capture(self, label: str, dump_ui: bool = True) -> None:
        """截图 + dump UI树，保存到文件。"""
        self._step += 1
        prefix = f"{self._step:02d}_{label}"

        # 截图
        img_path = self.out_dir / f"{prefix}.png"
        self.ctrl.screenshot(img_path)
        print(f"  📷 截图: {img_path.name}")

        if dump_ui:
            # UI树
            xml_path = self.out_dir / f"{prefix}_ui.xml"
            xml_text = self.ctrl._dump_ui()
            xml_path.write_text(xml_text, encoding="utf-8")
            print(f"  🌳 UI树: {xml_path.name}")

    # ── 首页完整扫描 ────────────────────────────────

    def _scan_homepage(self) -> list[str]:
        """逐屏滚动首页，采集全部 UI 树，返回所有产品名。"""
        print("\n[Step 1.5] 首页完整扫描（逐屏滚动）")
        products: list[str] = []
        w, h = self.ctrl.get_screen_size()

        # 先回到顶部
        self.ctrl.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.8), 300)
        self.ctrl.wait(1)
        self.capture("首页_顶部", dump_ui=False)

        # 滚动采集，最多 6 屏
        for i in range(6):
            # dump UI 树
            xml_path = self.out_dir / f"scan_{i+1:02d}_ui.xml"
            xml_text = self.ctrl._dump_ui()
            xml_path.write_text(xml_text, encoding="utf-8")

            # 提取产品名
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            for node in root.iter("node"):
                text = node.get("text", "")
                if "额度由" in text and "提供" in text:
                    # "总额度80000 额度由时光分期提供" → "时光分期"
                    name = text.split("额度由")[-1].replace("提供", "").strip()
                    if name not in products:
                        products.append(name)

            print(f"  第{i+1}屏: UI树已保存, 累计产品 {len(products)} 个")

            # 滑动一屏
            self.ctrl.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
            self.ctrl.wait(1.5)

        # 截取最后一屏截图
        self.capture("首页_底部", dump_ui=False)

        # 滚回顶部
        for _ in range(6):
            self.ctrl.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.8), 300)
            self.ctrl.wait(0.5)

        print(f"\n  首页全部产品 ({len(products)} 个):")
        for idx, name in enumerate(products, 1):
            print(f"    {idx}. {name}")
        return products

    # ── 弹窗处理 ────────────────────────────────────

    def _find_close_btn(self) -> tuple[int, int] | None:
        """从UI树中查找弹窗关闭按钮。"""
        try:
            elems = self.ctrl.find_elements(resource_id_contains="home-pop")
            if not elems:
                return None
            pop = elems[0]
            all_elems = self.ctrl.find_elements(package=Config.APP_PACKAGE)
            for e in all_elems:
                if (e.text == ""
                    and e.bounds[0] > pop.bounds[0]
                    and e.bounds[2] < pop.bounds[2]
                    and e.bounds[1] > 1900
                    and (e.bounds[2] - e.bounds[0]) < 200
                    and (e.bounds[3] - e.bounds[1]) < 200):
                    return e.center
            return (pop.bounds[0] + pop.bounds[2]) // 2, pop.bounds[3] - 40
        except Exception:
            return None

    # ── 主流程 ─────────────────────────────────────

    def run(self) -> None:
        """执行采集流程。"""
        print(f"=== 流程采集开始 ===")
        print(f"设备: {Config.ANDROID_SERIAL}")
        print(f"APP:  {Config.APP_NAME} ({Config.APP_PACKAGE})")
        print(f"输出: {self.out_dir}\n")

        # ── Step 1: 启动APP ──
        print("[Step 1] 唤醒屏幕并启动APP")
        self.ctrl.wake_screen()
        self.ctrl.wait(1)
        self.ctrl.stop_test_app()
        self.ctrl.wait(1)
        self.ctrl.start_test_app()
        self.ctrl.wait(4)
        self.capture("启动APP进入首页")

        # ── Step 2: 关闭弹窗 ──
        print("\n[Step 2] 查找弹窗关闭按钮")
        close_pos = self._find_close_btn()
        if close_pos:
            x, y = close_pos
            print(f"  找到弹窗×按钮: ({x}, {y})")
            self.ctrl.tap(x, y)
            self.ctrl.wait(2)
        else:
            print("  未发现弹窗，跳过")

        # 验证弹窗关闭
        try:
            pops = self.ctrl.find_elements(resource_id_contains="home-pop")
            print(f"  {'⚠️ 弹窗仍然存在' if pops else '✔ 弹窗已关闭'}")
        except Exception:
            pass
        self.capture("关闭弹窗后")

        # ── Step 3: 首页完整扫描 ──
        products = self._scan_homepage()

        # ── Step 4: 滑动寻找「乐小融V2」并点击立即提现 ──
        print("\n[Step 4] 寻找乐小融V2")
        target = "乐小融V2"
        found = False
        w, h = self.ctrl.get_screen_size()

        for scroll_i in range(6):
            # dump UI 查找
            xml_text = self.ctrl._dump_ui()
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            withdraw_btn_bounds = None
            product_visible = False

            for node in root.iter("node"):
                text = node.get("text", "")
                bounds_raw = node.get("bounds", "[0,0][0,0]")
                import re
                nums = [int(n) for n in re.findall(r"\d+", bounds_raw)]
                l, t, r, b = nums[0], nums[1], nums[2], nums[3]

                if target.lower() in text.lower() and "额度由" in text:
                    product_visible = True
                    print(f"  找到产品: '{text}' bounds=[{l},{t}][{r},{b}]")

                # 找到该产品同行的「立即提现」按钮
                if product_visible and text == "立即提现" and r > 0:
                    withdraw_btn_bounds = (l, t, r, b)
                    break

            if withdraw_btn_bounds:
                cx = (withdraw_btn_bounds[0] + withdraw_btn_bounds[2]) // 2
                cy = (withdraw_btn_bounds[1] + withdraw_btn_bounds[3]) // 2
                print(f"  点击立即提现: ({cx}, {cy})")
                self.capture(f"找到{target}")
                self.ctrl.tap(cx, cy)
                self.ctrl.wait(3)
                found = True
                break
            else:
                # 没找到，继续滚动
                self.ctrl.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.3), 500)
                self.ctrl.wait(1.5)
                print(f"  第{scroll_i+1}次滚动...")

        if not found:
            print(f"  ⚠️ 未找到 {target}，可能不在产品列表中")
            self.capture("未找到乐小融V2")

        # ── Step 5: 进入提现页 ──
        print("\n[Step 5] 进入提现页")
        self.ctrl.wait(2)
        self.capture("进入提现页")

        # ── Step 6: 查找金额输入框 ──
        print("\n[Step 6] 查找金额输入框")
        self.capture("提现页_查找输入框")

        # 打印当前UI树中所有含数字的元素
        try:
            xml_text = self.ctrl._dump_ui()
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            print("  提现页关键元素:")
            for node in root.iter("node"):
                text = node.get("text", "")
                rid = node.get("resource-id", "")
                cls = node.get("class", "")
                bounds_raw = node.get("bounds", "[0,0][0,0]")
                import re
                nums = [int(n) for n in re.findall(r"\d+", bounds_raw)]
                l, t, r, b = nums[0], nums[1], nums[2], nums[3]
                if r > 0 and (text or "edit" in cls.lower() or "input" in rid.lower()):
                    print(f"    text='{text}' class={cls.split('.')[-1]} rid='{rid}' [{l},{t}][{r},{b}]")
        except Exception as e:
            print(f"  UI dump 异常: {e}")

        print(f"\n=== 流程采集完成 ===")
        print(f"截图和UI树保存在: {self.out_dir}")
        print(f"共 {self._step} 步截图")
        print(f"\n首页产品: {', '.join(products)}")


if __name__ == "__main__":
    fc = FlowCapture("popup_close")
    fc.run()
