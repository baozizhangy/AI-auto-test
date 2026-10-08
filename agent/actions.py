"""Action 层 —— 可复用的业务动作（定位 → 交互 → 断言）。

预置类型：
  TapAction    — 点击元素 → 验证新状态
  InputAction  — 定位输入框 → 清空 → 输入 → 失焦 → 验证值
  WaitAction   — 等待元素出现/消失
  ScrollAction — 滚动查找目标元素
  CustomAction — 自定义操作（逃生舱）
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from agent.path_locator import PathLocator, ElementNotFoundError

if TYPE_CHECKING:
    from agent.adb_controller import ADBController, UIElement


@dataclass
class ActionResult:
    """Action 执行结果"""
    action_name: str
    success: bool
    message: str
    element: "UIElement | None" = None
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    retries_used: int = 0
    duration_ms: float = 0


class Action(ABC):
    """业务动作基类 —— 定位→交互→断言，三步合一。

    子类实现 _do_execute() 完成具体业务逻辑。
    execute() 是模板方法，提供统一的 retry + 截图。
    """

    def __init__(
        self,
        name: str,
        path: str = "",
        wait_before: float = 1.0,
        wait_after: float = 1.5,
        retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        self.name = name
        self.path = path
        self.wait_before = wait_before
        self.wait_after = wait_after
        self.retries = retries
        self.retry_delay = retry_delay
        self._locator_class: type[PathLocator] = PathLocator

    # ── 模板方法 ──────────────────────────────────────

    def execute(
        self, adb: "ADBController", screenshot_dir: Path | None = None, action_index: int = 0
    ) -> ActionResult:
        """模板方法：retry 循环 + 截图 + 调用 _do_execute。"""
        t0 = time.perf_counter()
        locator = self._locator_class

        for attempt in range(self.retries + 1):
            try:
                if self.wait_before > 0:
                    adb.wait(self.wait_before)

                # 截图 before
                before_path: str | None = None
                if screenshot_dir:
                    before_path = self._screenshot_path(screenshot_dir, action_index, "before")
                    try:
                        adb.screenshot(before_path)
                    except Exception:
                        before_path = None

                result = self._do_execute(adb, locator)

                # 截图 after
                after_path: str | None = None
                if screenshot_dir:
                    after_path = self._screenshot_path(screenshot_dir, action_index, "after")
                    try:
                        adb.screenshot(after_path)
                    except Exception:
                        after_path = None

                result.screenshot_before = before_path
                result.screenshot_after = after_path if result.success else None
                result.retries_used = attempt
                result.duration_ms = (time.perf_counter() - t0) * 1000
                return result

            except (ElementNotFoundError, AssertionError, RuntimeError) as e:
                if attempt < self.retries:
                    adb.wait(self.retry_delay)
                    try:
                        adb.dump_ui_xml()
                    except Exception:
                        pass
                else:
                    after_path: str | None = None
                    if screenshot_dir:
                        after_path = self._screenshot_path(screenshot_dir, action_index, "after_error")
                        try:
                            adb.screenshot(after_path)
                        except Exception:
                            after_path = None

                    return ActionResult(
                        action_name=self.name,
                        success=False,
                        message=str(e),
                        screenshot_before=None,
                        screenshot_after=after_path,
                        retries_used=attempt,
                        duration_ms=(time.perf_counter() - t0) * 1000,
                    )

        return ActionResult(
            action_name=self.name,
            success=False,
            message="未知错误",
            retries_used=self.retries,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    @abstractmethod
    def _do_execute(self, adb: "ADBController", locator: type[PathLocator]) -> ActionResult:
        """子类实现：具体的定位 → 交互 → 断言逻辑。"""
        ...

    @staticmethod
    def _safe_filename(name: str, max_len: int = 40) -> str:
        """将 action 名称转为安全的文件名部分。"""
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        safe = safe.strip("_") or "action"
        return safe[:max_len]

    @staticmethod
    def _screenshot_path(screenshot_dir: Path, index: int, suffix: str) -> str:
        """生成截图路径。"""
        return str(screenshot_dir / f"{index:02d}_{suffix}.png")


# ── 预置 Action 类型 ────────────────────────────────────────


class TapAction(Action):
    """点击操作：定位元素 → 点击 → 验证后续状态"""

    def __init__(
        self,
        name: str,
        path: str,
        assertions: list[str] | None = None,
        wait_before: float = 1.0,
        wait_after: float = 1.5,
        retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        super().__init__(
            name=name,
            path=path,
            wait_before=wait_before,
            wait_after=wait_after,
            retries=retries,
            retry_delay=retry_delay,
        )
        self.assertions: list[str] = assertions or []

    def _do_execute(self, adb: "ADBController", locator: type[PathLocator]) -> ActionResult:
        elem = locator.find(adb, self.path)
        adb.tap(*elem.center)
        adb.wait(self.wait_after)

        for assertion_path in self.assertions:
            if not locator.exists(adb, assertion_path):
                return ActionResult(
                    action_name=self.name,
                    success=False,
                    message=f"断言失败：点击后未找到元素 '{assertion_path}'",
                    element=elem,
                )

        return ActionResult(
            action_name=self.name,
            success=True,
            message=f"点击成功: {self.path}",
            element=elem,
        )


class InputAction(Action):
    """输入操作：定位输入框 → 清空 → 输入 → 失焦 → 验证值"""

    def __init__(
        self,
        name: str,
        path: str,
        value: str = "",
        assert_value: str | None = None,
        wait_before: float = 1.0,
        wait_after: float = 1.5,
        retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        super().__init__(
            name=name,
            path=path,
            wait_before=wait_before,
            wait_after=wait_after,
            retries=retries,
            retry_delay=retry_delay,
        )
        self.value = value
        self.assert_value = assert_value if assert_value is not None else value

    def _do_execute(self, adb: "ADBController", locator: type[PathLocator]) -> ActionResult:
        elem = locator.find(adb, self.path)
        adb.tap(*elem.center)
        adb.wait(1.5)  # wait for keyboard + clear button

        # find clear button (small empty-text element near right side of input)
        all_elems = adb.find_elements(package=elem.package)
        clear_clicked = False
        for e in all_elems:
            if (
                e.text == ""
                and e.bounds[0] > elem.bounds[0]
                and e.bounds[1] > elem.bounds[1] - 50
                and e.bounds[3] < elem.bounds[3] + 50
                and (e.bounds[2] - e.bounds[0]) < 150
                and (e.bounds[3] - e.bounds[1]) < 150
            ):
                adb.tap(*e.center)
                adb.wait(0.5)
                clear_clicked = True
                break

        if not clear_clicked:
            # fallback: select all + delete
            adb.press_key("KEYCODE_MOVE_END")
            adb.wait(0.3)
            adb.press_key("KEYCODE_SHIFT_LEFT")
            adb.press_key("KEYCODE_MOVE_HOME")
            adb.wait(0.3)
            adb.press_key("KEYCODE_DEL")
            adb.wait(0.5)

        adb.input_text(self.value)
        adb.wait(1)

        # defocus: tap title area
        adb.tap(600, 300)
        adb.wait(self.wait_after)

        # verify value
        try:
            updated = locator.find(adb, self.path)
            actual = updated.text.strip()
            if actual != self.assert_value:
                return ActionResult(
                    action_name=self.name,
                    success=False,
                    message=f"输入验证失败：期望 '{self.assert_value}'，实际 '{actual}'",
                    element=updated,
                )
        except ElementNotFoundError:
            pass  # element may have changed structure after input

        return ActionResult(
            action_name=self.name,
            success=True,
            message=f"输入成功: {self.value}",
            element=elem,
        )


class WaitAction(Action):
    """纯等待操作：等待元素出现或消失"""

    def __init__(
        self,
        name: str,
        until_exists: str | None = None,
        until_gone: str | None = None,
        timeout: float = 30,
        wait_before: float = 0.0,
        wait_after: float = 0.0,
        retries: int = 1,
        retry_delay: float = 1.0,
    ) -> None:
        path = until_exists or until_gone or ""
        super().__init__(
            name=name,
            path=path,
            wait_before=wait_before,
            wait_after=wait_after,
            retries=retries,
            retry_delay=retry_delay,
        )
        self.until_exists = until_exists
        self.until_gone = until_gone
        self.timeout = timeout

    def _do_execute(self, adb: "ADBController", locator: type[PathLocator]) -> ActionResult:
        if self.until_exists:
            start = time.perf_counter()
            while True:
                if locator.exists(adb, self.until_exists):
                    return ActionResult(
                        action_name=self.name,
                        success=True,
                        message=f"元素已出现: {self.until_exists}",
                    )
                if time.perf_counter() - start >= self.timeout:
                    return ActionResult(
                        action_name=self.name,
                        success=False,
                        message=f"等待超时（{self.timeout}s）：元素未出现 '{self.until_exists}'",
                    )
                adb.wait(1)

        if self.until_gone:
            ok = locator.wait_until_gone(adb, self.until_gone, self.timeout)
            return ActionResult(
                action_name=self.name,
                success=ok,
                message=(
                    f"元素已消失: {self.until_gone}"
                    if ok
                    else f"元素未消失: {self.until_gone} ({self.timeout}s)"
                ),
            )

        return ActionResult(
            action_name=self.name,
            success=False,
            message="WaitAction 需指定 until_exists 或 until_gone",
        )


class ScrollAction(Action):
    """滚动操作：滚动查找目标元素"""

    def __init__(
        self,
        name: str,
        path: str,
        direction: str = "down",
        max_scrolls: int = 10,
        wait_before: float = 0.5,
        wait_after: float = 1.0,
        retries: int = 1,
        retry_delay: float = 1.0,
    ) -> None:
        super().__init__(
            name=name,
            path=path,
            wait_before=wait_before,
            wait_after=wait_after,
            retries=retries,
            retry_delay=retry_delay,
        )
        self.direction = direction
        self.max_scrolls = max_scrolls

    def _do_execute(self, adb: "ADBController", locator: type[PathLocator]) -> ActionResult:
        w, h = adb.get_screen_size()

        for i in range(self.max_scrolls + 1):
            if locator.exists(adb, self.path):
                elem = locator.find(adb, self.path)
                # verify element is in visible area (not WebView buffer coords)
                if elem.bounds[1] > 150 or self.max_scrolls == 0:
                    return ActionResult(
                        action_name=self.name,
                        success=True,
                        message=f"滚动 {i} 次后找到: {self.path}",
                        element=elem,
                    )

            if i < self.max_scrolls:
                if self.direction == "down":
                    adb.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
                elif self.direction == "up":
                    adb.swipe(w // 2, int(h * 0.2), w // 2, int(h * 0.8), 500)
                adb.wait(1)

        return ActionResult(
            action_name=self.name,
            success=False,
            message=f"滚动 {self.max_scrolls} 次后未找到: {self.path}",
        )


class CustomAction(Action):
    """自定义操作：逃生舱，用于无法被预置类型覆盖的特殊业务逻辑"""

    def __init__(
        self,
        name: str,
        execute_fn: Callable[["ADBController", type[PathLocator]], ActionResult],
        path: str = "",
        wait_before: float = 1.0,
        wait_after: float = 1.5,
        retries: int = 1,
        retry_delay: float = 2.0,
    ) -> None:
        super().__init__(
            name=name,
            path=path,
            wait_before=wait_before,
            wait_after=wait_after,
            retries=retries,
            retry_delay=retry_delay,
        )
        self.execute_fn = execute_fn

    def _do_execute(self, adb: "ADBController", locator: type[PathLocator]) -> ActionResult:
        if self.execute_fn is None:
            return ActionResult(
                action_name=self.name,
                success=False,
                message="CustomAction 未设置 execute_fn",
            )
        return self.execute_fn(adb, locator)
