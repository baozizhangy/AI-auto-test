"""ADB 设备控制层：截图、点击、滑动、输入文字、UI 元素定位。

所有 ADB 操作通过 subprocess 调用 adb 命令完成。
UI 元素定位通过 uiautomator dump 解析 XML 层级树实现。
"""

from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from agent.config import Config


@dataclass
class UIElement:
    """uiautomator dump 解析出的一个 UI 元素。"""
    text: str
    resource_id: str
    class_name: str
    content_desc: str
    bounds: tuple[int, int, int, int]  # (left, top, right, bottom)
    clickable: bool
    enabled: bool
    package: str

    @property
    def center(self) -> tuple[int, int]:
        """返回元素中心坐标。"""
        return (self.bounds[0] + self.bounds[2]) // 2, (self.bounds[1] + self.bounds[3]) // 2


class ADBController:
    """封装 ADB 命令，操作 Android 设备。"""

    def __init__(self, serial: str = "emulator-5554") -> None:
        self.serial = serial
        self._adb_prefix = [Config.ADB_PATH, "-s", serial]

    # ── 底层 ──────────────────────────────────────────────

    def _run(self, *args: str, timeout: float = 30) -> bytes:
        """执行 adb 命令并返回 stdout bytes。"""
        cmd = [*self._adb_prefix, *args]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"ADB 命令失败: {' '.join(cmd)}\n{err}")
        return proc.stdout

    # ── 截图 ──────────────────────────────────────────────

    def screenshot(self, output_path: str | Path) -> Path:
        """截取设备当前画面，保存为 PNG 文件，返回文件路径。"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._run("exec-out", "screencap", "-p")
        output_path.write_bytes(data)
        return output_path

    # ── 触控操作 ──────────────────────────────────────────

    def tap(self, x: int, y: int) -> None:
        """点击屏幕坐标 (x, y)。"""
        self._run("shell", "input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """从 (x1, y1) 滑动到 (x2, y2)。"""
        self._run("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        """长按坐标。"""
        self.swipe(x, y, x, y, duration_ms)

    def input_text(self, text: str) -> None:
        """输入文字（需先聚焦输入框，中文需安装 ADBKeyboard）。"""
        # 对特殊字符做转义
        escaped = text.replace(" ", "%s").replace("&", "\\&")
        self._run("shell", "input", "text", escaped)

    # ── 按键 ──────────────────────────────────────────────

    def press_key(self, keycode: str) -> None:
        """发送按键事件。常用: KEYCODE_BACK, KEYCODE_HOME, KEYCODE_ENTER。"""
        self._run("shell", "input", "keyevent", keycode)

    def press_back(self) -> None:
        self.press_key("KEYCODE_BACK")

    def press_home(self) -> None:
        self.press_key("KEYCODE_HOME")

    def press_enter(self) -> None:
        self.press_key("KEYCODE_ENTER")

    def wake_screen(self) -> None:
        """唤醒屏幕并上滑解锁（无密码情况）。"""
        # 先检查屏幕是否已亮
        raw = self._run("shell", "dumpsys", "power", timeout=10).decode()
        if "mWakefulness=Asleep" in raw:
            self.press_key("KEYCODE_WAKEUP")
            self.wait(1)
            # 上滑解锁
            w, h = self.get_screen_size()
            self.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.3), 300)
            self.wait(1)

    # ── 应用管理 ──────────────────────────────────────────

    def start_app(self, package: str, activity: str | None = None) -> None:
        """启动应用。如果不指定 activity，让系统默认启动。"""
        if activity:
            self._run("shell", "am", "start", "-n", f"{package}/{activity}")
        else:
            self._run("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")

    def start_test_app(self) -> None:
        """启动配置中的测试APP。"""
        if not Config.APP_PACKAGE:
            raise ValueError("未配置 APP_PACKAGE，请在 .env 中设置")
        self.start_app(Config.APP_PACKAGE, Config.APP_LAUNCH_ACTIVITY or None)

    def stop_app(self, package: str) -> None:
        self._run("shell", "am", "force-stop", package)

    def stop_test_app(self) -> None:
        """停止配置中的测试APP。"""
        if Config.APP_PACKAGE:
            self.stop_app(Config.APP_PACKAGE)

    # ── UI 元素定位 ────────────────────────────────────

    _REMOTE_XML = "/sdcard/window_dump.xml"

    def dump_ui_xml(self) -> str:
        """执行 uiautomator dump 并返回 XML 文本，带重试机制。"""
        last_err = None
        for attempt in range(3):
            try:
                self._run("shell", "uiautomator", "dump", self._REMOTE_XML, timeout=15)
                return self._run("shell", "cat", self._REMOTE_XML, timeout=10).decode(errors="replace")
            except Exception as e:
                last_err = e
                import time
                time.sleep(1)
        raise last_err  # type: ignore[misc]

    def get_page_source(self) -> str:
        """别名，语义更清晰"""
        return self.dump_ui_xml()

    def _dump_ui(self) -> str:
        """兼容旧代码的委托方法"""
        return self.dump_ui_xml()

    @staticmethod
    def _parse_bounds(raw: str) -> tuple[int, int, int, int]:
        """解析 bounds="[l,t][r,b]" 格式。"""
        nums = re.findall(r"\d+", raw)
        return int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3])

    def _node_to_element(self, node: ET.Element) -> UIElement:
        return UIElement(
            text=node.get("text", ""),
            resource_id=node.get("resource-id", ""),
            class_name=node.get("class", ""),
            content_desc=node.get("content-desc", ""),
            bounds=self._parse_bounds(node.get("bounds", "[0,0][0,0]")),
            clickable=node.get("clickable", "false") == "true",
            enabled=node.get("enabled", "true") == "true",
            package=node.get("package", ""),
        )

    def find_elements(self, **kwargs) -> list[UIElement]:
        """按条件查找 UI 元素。

        支持的参数: text, text_contains, resource_id, resource_id_contains,
                    class_name, content_desc, package, clickable
        返回所有匹配的元素列表。
        """
        xml_text = self._dump_ui()
        root = ET.fromstring(xml_text)
        results: list[UIElement] = []

        for node in root.iter("node"):
            elem = self._node_to_element(node)
            match = True

            if "text" in kwargs and elem.text != kwargs["text"]:
                match = False
            if "text_contains" in kwargs and kwargs["text_contains"] not in elem.text:
                match = False
            if "resource_id" in kwargs and elem.resource_id != kwargs["resource_id"]:
                match = False
            if "resource_id_contains" in kwargs and kwargs["resource_id_contains"] not in elem.resource_id:
                match = False
            if "class_name" in kwargs and elem.class_name != kwargs["class_name"]:
                match = False
            if "content_desc" in kwargs and elem.content_desc != kwargs["content_desc"]:
                match = False
            if "package" in kwargs and elem.package != kwargs["package"]:
                match = False
            if "clickable" in kwargs and elem.clickable != kwargs["clickable"]:
                match = False

            if match:
                results.append(elem)

        return results

    def find_element(self, **kwargs) -> UIElement:
        """查找单个 UI 元素，找不到则抛出 AssertionError。"""
        elems = self.find_elements(**kwargs)
        if not elems:
            desc = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
            raise AssertionError(f"找不到 UI 元素: {desc}")
        return elems[0]

    def assert_element_exists(self, **kwargs) -> UIElement:
        """断言某个 UI 元素存在，用于测试步骤的 DOM 断言。"""
        return self.find_element(**kwargs)

    def tap_element(self, **kwargs) -> None:
        """查找元素并点击其中心。"""
        elem = self.find_element(**kwargs)
        x, y = elem.center
        self.tap(x, y)

    def get_current_activity(self) -> str:
        """获取当前前台 Activity 名称。"""
        raw = self._run("shell", "dumpsys", "activity", "top", timeout=10).decode()
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("ACTIVITY"):
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1]
        return ""

    # ── 设备信息 ──────────────────────────────────────────

    def get_screen_size(self) -> tuple[int, int]:
        """返回屏幕分辨率 (width, height)。"""
        raw = self._run("shell", "wm", "size").decode()
        # 输出格式: Physical size: 1080x2400
        size_str = raw.strip().split(":")[-1].strip()
        w, h = size_str.split("x")
        return int(w), int(h)

    def get_device_info(self) -> dict[str, str]:
        """获取设备基本信息。"""
        props = {
            "brand": "ro.product.brand",
            "model": "ro.product.model",
            "android_version": "ro.build.version.release",
            "sdk_version": "ro.build.version.sdk",
        }
        info = {}
        for key, prop in props.items():
            info[key] = self._run("shell", "getprop", prop).decode().strip()
        w, h = self.get_screen_size()
        info["screen_size"] = f"{w}x{h}"
        return info

    def wait(self, seconds: float) -> None:
        """等待指定秒数（用于等待 UI 渲染）。"""
        time.sleep(seconds)
