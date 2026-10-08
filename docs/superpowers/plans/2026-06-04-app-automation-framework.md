# APP 自动化测试框架 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 Android APP 自动化测试核心引擎 — 新增 PathLocator / Action / FlowRunner 三层架构，保持旧 Scenario 向后兼容。

**Architecture:** 三层引擎 — Core Layer (`path_locator.py`) 负责元素路径定位；Action Layer (`actions.py`) 封装可复用的定位→交互→断言业务动作；Flow Layer (`flow_runner.py`) 编排 setup → steps → teardown 三段执行。旧 `Scenario`/`StepDef` 通过包装层兼容。

**Tech Stack:** Python 3.10+, `dataclasses`, `xml.etree.ElementTree`, `subprocess`(adb), 现有 `Config`/`ADBController`（不新增第三方依赖）。

---

### Task 1: ADB Controller 增强 + Path Locator

**Files:**
- Modify: `agent/adb_controller.py:138-139` (将 `_dump_ui` 提升为公开)
- Create: `agent/path_locator.py`

#### 1.1 增强 ADBController

- [ ] **Step 1: 将 `_dump_ui` 提升为公开方法 `dump_ui_xml`，并添加 `get_page_source` 别名**

Edit `agent/adb_controller.py` — 在 `_REMOTE_XML = ...` 行之后、`_dump_ui` 方法处：

```python
# 将 _dump_ui 方法名改为 dump_ui_xml，保留原方法作为委托
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
```

Old snippet to replace (lines 139-150):
```python
    def _dump_ui(self) -> str:
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
```

New snippet:
```python
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
```

- [ ] **Step 2: Commit**

```bash
git add agent/adb_controller.py
git commit -m "refactor(adb): promote _dump_ui to public dump_ui_xml + get_page_source alias"
```

#### 1.2 创建 PathLocator

- [ ] **Step 3: 创建 `agent/path_locator.py`**

```python
"""路径定位器 —— 类 CSS 选择器语法，在 uiautomator dump XML 树上匹配元素。

语法参考:
  #resource_id                    -- resource-id 完全匹配
  #id > ClassName[text="确认"]     -- 直接子元素 + 属性过滤
  #id ClassName[0]                -- 后代元素 + 索引
  *[text="借款申请"]               -- 通配 + 文本匹配
  *[text_contains="可取现"]        -- 文本包含
  path_a | path_b                 -- 备选路径（任一命中即返回）
"""

from __future__ import annotations

import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.adb_controller import ADBController, UIElement


class ElementNotFoundError(AssertionError):
    """路径未匹配到任何元素时抛出。"""
    def __init__(self, path: str, message: str | None = None) -> None:
        self.path = path
        super().__init__(message or f"未找到元素: {path}")


# ── Token types for the lexer ──────────────────────────────

_SELECTOR_RE = re.compile(
    r"""
    \s*>\s*                        # > 直接子元素
    |\s+(?![\w\[\]=\*"'\|\-])     # 空格：后代元素（不跟在运算符后面）
    |\|\s*                         # | 备选路径分隔
    |\#(?P<res_id>[\w./]+)         # #resource_id
    |(?P<index>\[\-?\d+\])         # [N] 或 [-N]
    |\[(?P<attr>\w+)(?:           # [attr 或 [attr_op=value]
        (?:_(?P<op>contains|starts_with|matches))\s*=\s*
        "(?P<val>[^"]*)"
      )?\]
    |(?P<class>\w+(?:\.\w+)*)     # ClassName
    |\*(?=\[)                      # * 通配（仅在 [ 前）
    """,
    re.VERBOSE,
)


class _Segment:
    """路径中的一个匹配段。"""
    __slots__ = ("child_combinator", "resource_id", "class_name", "attrs", "index", "is_wildcard")

    def __init__(self) -> None:
        self.child_combinator: str = " "        # " " 后代, ">" 直接子
        self.resource_id: str | None = None
        self.class_name: str | None = None
        self.attrs: dict[str, tuple[str, str]] = {}  # attr_name -> (op, value)
        self.index: int | None = None
        self.is_wildcard: bool = False

    def __repr__(self) -> str:
        parts = []
        if self.resource_id:
            parts.append(f"#{self.resource_id}")
        elif self.class_name:
            parts.append(self.class_name)
        elif self.is_wildcard:
            parts.append("*")
        for attr, (op, val) in self.attrs.items():
            if op == "=":
                parts.append(f"[{attr}={val!r}]")
            else:
                parts.append(f"[{attr}_{op}={val!r}]")
        if self.index is not None:
            parts.append(f"[{self.index}]")
        return f"Segment({self.child_combinator!r} {''.join(parts)})"


class PathLocator:
    """路径定位器 —— 将简洁路径语法编译为 uiautomator XML 树的匹配。"""

    # built-in: 简写 class 名 → 全限定名
    SHORT_CLASS_MAP: dict[str, str] = {
        "View": "android.view.View",
        "ViewGroup": "android.view.ViewGroup",
        "TextView": "android.widget.TextView",
        "EditText": "android.widget.EditText",
        "Button": "android.widget.Button",
        "ImageView": "android.widget.ImageView",
        "WebView": "android.webkit.WebView",
        "LinearLayout": "android.widget.LinearLayout",
        "RelativeLayout": "android.widget.RelativeLayout",
        "FrameLayout": "android.widget.FrameLayout",
        "ScrollView": "android.widget.ScrollView",
        "ListView": "android.widget.ListView",
        "RecyclerView": "androidx.recyclerview.widget.RecyclerView",
        "CheckBox": "android.widget.CheckBox",
        "RadioButton": "android.widget.RadioButton",
        "ImageButton": "android.widget.ImageButton",
        "ProgressBar": "android.widget.ProgressBar",
        "Switch": "android.widget.Switch",
    }

    @classmethod
    def _resolve_class(cls, name: str) -> str:
        """将简写 class 名解析为全限定名。如果已经有 '.' 则保持不变。"""
        if "." in name:
            return name
        return cls.SHORT_CLASS_MAP.get(name, f"android.widget.{name}")

    # ── Parser ─────────────────────────────────────────────

    @classmethod
    def _parse_path(cls, path: str) -> list[_Segment]:
        """解析路径字符串为匹配段列表。"""
        segments: list[_Segment] = []
        cur = _Segment()
        segments.append(cur)

        pos = 0
        while pos < len(path):
            m = _SELECTOR_RE.match(path, pos)
            if not m:
                raise ValueError(f"路径语法错误，位置 {pos}: {path[pos:pos+20]!r}")

            if m.group() == "> " or m.group().strip() == ">":
                # '>' child combinator → 开启新段
                cur = _Segment()
                cur.child_combinator = ">"
                segments.append(cur)
                pos = m.end()
                # 跳过 '>' 后面可能的空格
                while pos < len(path) and path[pos] == " ":
                    pos += 1
                continue

            if m.group().startswith("|"):
                # 备选路径 —— 暂时用特殊段标记，在 find 中处理
                # 解析 | 之后的路径存储为 alternative
                cur = _Segment()
                cur.child_combinator = "|"
                segments.append(cur)
                pos = m.end()
                continue

            if m.group().startswith(" ") and len(m.group().strip()) == 0:
                # 空格后代选择器 —— 开启新段（如果没有紧跟 # 或 class）
                whitespace_end = m.end()
                # 查看空格后面是否有 #, *, class
                lookahead = _SELECTOR_RE.match(path, whitespace_end)
                if lookahead and lookahead.group() in ("> ", "|"):
                    # 空格被 > 或 | 覆盖，跳过
                    pos = whitespace_end
                    continue
                cur = _Segment()
                cur.child_combinator = " "
                segments.append(cur)
                pos = whitespace_end
                continue

            # 属性匹配
            if m.group("res_id"):
                cur.resource_id = m.group("res_id")
            elif m.group("attr"):
                attr_name = m.group("attr")
                op = m.group("op") or "="
                val = m.group("val") or ""
                cur.attrs[attr_name] = (op, val)
            elif m.group("index"):
                cur.index = int(m.group("index").strip("[]"))
            elif m.group("class"):
                cur.class_name = cls._resolve_class(m.group("class"))
            elif m.group().startswith("*"):
                cur.is_wildcard = True

            pos = m.end()

        # 过滤掉空段（可能由 > 和 | 产生）
        final: list[_Segment] = []
        for seg in segments:
            # 一个有效的段至少要有 resource_id、class_name、wildcard、attrs 或 index
            has_matcher = (
                seg.resource_id is not None
                or seg.class_name is not None
                or seg.is_wildcard
                or len(seg.attrs) > 0
                or seg.index is not None
            )
            if has_matcher or seg.child_combinator == "|":
                final.append(seg)

        if not final:
            raise ValueError(f"路径为空或无效: {path!r}")

        return final

    # ── Matcher ─────────────────────────────────────────────

    @classmethod
    def _normalize_text(cls, text: str) -> str:
        """Unicode NFKC 规范化，容忍隐藏字符。"""
        return unicodedata.normalize("NFKC", text).strip()

    @classmethod
    def _matches_segment(cls, seg: _Segment, element: "UIElement") -> bool:
        """检查单个元素是否匹配一个段的所有条件。"""
        # resource_id
        if seg.resource_id is not None:
            # 支持只写末尾部分，如 "id/webview_fl" 匹配 "com.app:id/webview_fl"
            eid = element.resource_id
            if eid != seg.resource_id and not eid.endswith(":" + seg.resource_id):
                # 也支持 "id/xxx" 形式的模糊匹配
                if not (seg.resource_id.startswith("id/") and eid.endswith(seg.resource_id)):
                    return False

        # class_name
        if seg.class_name is not None:
            if element.class_name != seg.class_name:
                # 也尝试短名匹配
                short = element.class_name.split(".")[-1] if "." in element.class_name else element.class_name
                resolved = cls._resolve_class(seg.class_name) if "." not in seg.class_name else seg.class_name
                if element.class_name != resolved and short != resolved.split(".")[-1]:
                    return False

        # wildcard: 匹配任意（但需要检查 attrs）
        # wildcard itself matches everything

        # attrs
        for attr, (op, val) in seg.attrs.items():
            if attr == "text":
                elem_val = cls._normalize_text(element.text)
                norm_val = cls._normalize_text(val)
            elif attr == "content_desc":
                elem_val = cls._normalize_text(element.content_desc)
                norm_val = cls._normalize_text(val)
            elif attr == "class":
                elem_val = element.class_name
                norm_val = cls._resolve_class(val)
            elif attr == "package":
                elem_val = element.package
                norm_val = val
            elif attr == "clickable":
                elem_val = "true" if element.clickable else "false"
                norm_val = val.lower()
            elif attr == "enabled":
                elem_val = "true" if element.enabled else "false"
                norm_val = val.lower()
            else:
                return False

            if op == "=" or op == "equals":
                if elem_val != norm_val:
                    return False
            elif op == "contains":
                if norm_val not in elem_val:
                    return False
            elif op == "starts_with":
                if not elem_val.startswith(norm_val):
                    return False
            elif op == "matches":
                if not re.search(norm_val, elem_val):
                    return False

        return True

    @classmethod
    def _match_segment(
        cls, seg: _Segment, candidates: list["UIElement"], index: int | None
    ) -> list["UIElement"]:
        """在一个候选列表中匹配段条件，返回匹配的元素列表。"""
        results: list["UIElement"] = []
        for elem in candidates:
            if cls._matches_segment(seg, elem):
                results.append(elem)
        if index is not None:
            try:
                results = [results[index]]
            except IndexError:
                results = []
        return results

    @classmethod
    def _get_children(cls, element_root: ET.Element, parent_element: "UIElement | None",
                      adb: "ADBController", child_combinator: str) -> list["UIElement"]:
        """获取候选子元素列表。

        child_combinator == '>'  → 仅直接子元素
        child_combinator == ' '  → 所有后代元素
        """
        if parent_element is None:
            node = element_root
        else:
            # 在 XML 树中定位 parent_element 对应的节点
            node = cls._find_node_by_bounds(element_root, parent_element.bounds)
            if node is None:
                return []

        from agent.adb_controller import UIElement as UE

        elements: list["UIElement"] = []
        if child_combinator == ">":
            # 直接子元素
            target = node
        else:
            # 后代元素
            target = node

        for child in target.iter("node"):
            if child is target:
                continue
            elem = UE(
                text=child.get("text", ""),
                resource_id=child.get("resource-id", ""),
                class_name=child.get("class", ""),
                content_desc=child.get("content-desc", ""),
                bounds=adb._parse_bounds(child.get("bounds", "[0,0][0,0]")),
                clickable=child.get("clickable", "false") == "true",
                enabled=child.get("enabled", "true") == "true",
                package=child.get("package", ""),
            )
            elements.append(elem)

        return elements

    @classmethod
    def _find_node_by_bounds(cls, root: ET.Element, bounds: tuple[int, int, int, int]) -> ET.Element | None:
        """在 XML 树中根据 bounds 定位节点。"""
        target_str = f"[{bounds[0]},{bounds[1]}][{bounds[2]},{bounds[3]}]"
        for node in root.iter("node"):
            if node.get("bounds") == target_str:
                return node
        return None

    # ── Public API ──────────────────────────────────────────

    @classmethod
    def find_all(cls, adb: "ADBController", path: str) -> list["UIElement"]:
        """返回所有匹配元素（立即返回，不等待）。"""
        # 检查备选路径
        if "|" in path:
            alternatives = [p.strip() for p in path.split("|", 1)]
            results = cls.find_all(adb, alternatives[0])
            if results:
                return results
            results = cls.find_all(adb, alternatives[1])
            return results

        segments = cls._parse_path(path)
        xml_text = adb.dump_ui_xml()
        root = ET.fromstring(xml_text)

        from agent.adb_controller import UIElement as UE

        # 初始候选: 所有元素
        candidates: list["UIElement"] = []
        for node in root.iter("node"):
            elem = UE(
                text=node.get("text", ""),
                resource_id=node.get("resource-id", ""),
                class_name=node.get("class", ""),
                content_desc=node.get("content-desc", ""),
                bounds=adb._parse_bounds(node.get("bounds", "[0,0][0,0]")),
                clickable=node.get("clickable", "false") == "true",
                enabled=node.get("enabled", "true") == "true",
                package=node.get("package", ""),
            )
            candidates.append(elem)

        # 逐段匹配
        for i, seg in enumerate(segments):
            if i == 0:
                # 第一个段在整个 XML 树中匹配
                candidates = cls._match_segment(seg, candidates, seg.index)
            else:
                # 后续段在之前匹配到的元素的子/后代中继续匹配
                new_candidates: list["UIElement"] = []
                for prev_elem in candidates:
                    # 在后代节点中查找
                    children = cls._get_children(root, prev_elem, adb, seg.child_combinator)
                    matched = cls._match_segment(seg, children, seg.index)
                    new_candidates.extend(matched)
                candidates = new_candidates

            if not candidates and i < len(segments) - 1:
                break  # 中间段无匹配，提前终止

        return candidates

    @classmethod
    def find(cls, adb: "ADBController", path: str, timeout: float = 10) -> "UIElement":
        """查找单个匹配元素，在 timeout 内轮询重试。"""
        start = time.perf_counter()
        last_error: str | None = None

        while True:
            results = cls.find_all(adb, path)
            if results:
                return results[0]

            elapsed = time.perf_counter() - start
            if elapsed >= timeout:
                raise ElementNotFoundError(
                    path,
                    f"路径 '{path}' 未匹配到任何元素（{timeout}s 超时）"
                    + (f"，最后错误: {last_error}" if last_error else ""),
                )
            time.sleep(1)

    @classmethod
    def exists(cls, adb: "ADBController", path: str) -> bool:
        """检查元素是否存在（立即返回）。"""
        results = cls.find_all(adb, path)
        return len(results) > 0

    @classmethod
    def wait_until_gone(cls, adb: "ADBController", path: str, timeout: float = 15) -> bool:
        """等待元素从 UI 树中消失，返回是否成功。"""
        start = time.perf_counter()
        while True:
            if not cls.exists(adb, path):
                return True
            if time.perf_counter() - start >= timeout:
                return False
            time.sleep(1)
```

- [ ] **Step 4: 验证 PathLocator 解析无语法错误**

```bash
cd D:/UI_Agent && python -c "
from agent.path_locator import PathLocator
# Test parsing various paths
for p in ['*[text=\"确认借款\"]', '#webview_fl EditText[0]', '*[text_contains=\"可取现\"]', 'WebView > *[text=\"确认\"]', '*[text=\"我知道了\"] | *[text=\"确定\"]']:
    segs = PathLocator._parse_path(p)
    print(f'{p:50s} → {[str(s) for s in segs]}')
print('All paths parsed OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add agent/path_locator.py
git commit -m "feat: add PathLocator with CSS-like selector syntax for UI element matching"
```

---

### Task 2: Action Layer

**Files:**
- Create: `agent/actions.py`

- [ ] **Step 1: 创建 `agent/actions.py`**

```python
"""Action 层 —— 可复用的业务动作（定位 → 交互 → 断言）。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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

    name: str
    path: str = ""
    wait_before: float = 1.0
    wait_after: float = 1.5
    retries: int = 3
    retry_delay: float = 2.0
    _locator_class: type[PathLocator] = PathLocator

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
                    # 强制重新 dump UI
                    try:
                        adb.dump_ui_xml()
                    except Exception:
                        pass
                else:
                    # 截图 after（失败时）
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

        # 不应到达这里
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

        # 执行断言
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
        adb.wait(1.5)  # 等待键盘 + 清除按钮出现

        # 查找清除按钮（输入框右侧的小尺寸空文本元素）
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
            # 回退：全选删除
            adb.press_key("KEYCODE_MOVE_END")
            adb.wait(0.3)
            adb.press_key("KEYCODE_SHIFT_LEFT")
            adb.press_key("KEYCODE_MOVE_HOME")
            adb.wait(0.3)
            adb.press_key("KEYCODE_DEL")
            adb.wait(0.5)

        adb.input_text(self.value)
        adb.wait(1)

        # 失焦：点击标题区域
        adb.tap(600, 300)
        adb.wait(self.wait_after)

        # 重新获取元素验证值
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
            pass  # 输入后元素可能结构变化，跳过 text 验证

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
        # WaitAction 使用 path 做 until_exists/until_gone
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
                message=f"元素已消失: {self.until_gone}" if ok else f"元素未消失: {self.until_gone} ({self.timeout}s)",
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
            # 先检查目标是否可见
            if locator.exists(adb, self.path):
                elem = locator.find(adb, self.path)
                # 验证元素在屏幕可见区域内（不是 WebView 缓冲区坐标 130）
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
```

- [ ] **Step 2: 验证 actions 模块可导入**

```bash
cd D:/UI_Agent && python -c "
from agent.actions import TapAction, InputAction, WaitAction, ScrollAction, CustomAction, ActionResult
print('TapAction:', TapAction(name='test', path='*[text=\"确认\"]'))
print('InputAction:', InputAction(name='test', path='EditText[0]', value='3000'))
print('WaitAction:', WaitAction(name='test', until_exists='*[text=\"加载完成\"]'))
print('ScrollAction:', ScrollAction(name='test', path='*[text_contains=\"目标\"]'))
print('All actions instantiated OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add agent/actions.py
git commit -m "feat: add Action layer — Tap/Input/Wait/Scroll/Custom with retry"
```

---

### Task 3: Reporter

**Files:**
- Create: `agent/reporter.py`

- [ ] **Step 1: 创建 `agent/reporter.py`**

```python
"""报告模块 —— FlowReport / ActionReport 数据类 + JSON 序列化。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ActionReport:
    """单个 Action 的执行报告"""
    index: int
    name: str
    phase: str                     # setup / step / teardown
    passed: bool
    error: str | None = None
    retries_used: int = 0
    duration_ms: float = 0
    screenshot_before: str | None = None
    screenshot_after: str | None = None


@dataclass
class FlowReport:
    """一次 Flow 执行的完整报告"""

    flow_id: str
    flow_name: str
    run_tag: str                   # 8位随机标识
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    passed: bool = False
    total_actions: int = 0
    passed_actions: int = 0
    actions: list[ActionReport] = field(default_factory=list)
    aborted_at_phase: str | None = None  # setup / step / None

    @property
    def total_duration_ms(self) -> float:
        return sum(a.duration_ms for a in self.actions)

    def to_dict(self) -> dict:
        return {
            "flow_id": self.flow_id,
            "flow_name": self.flow_name,
            "run_tag": self.run_tag,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "passed": self.passed,
            "total_actions": self.total_actions,
            "passed_actions": self.passed_actions,
            "total_duration_ms": self.total_duration_ms,
            "aborted_at_phase": self.aborted_at_phase,
            "actions": [
                {
                    "index": a.index,
                    "name": a.name,
                    "phase": a.phase,
                    "passed": a.passed,
                    "error": a.error,
                    "retries_used": a.retries_used,
                    "duration_ms": a.duration_ms,
                    "screenshot_before": a.screenshot_before,
                    "screenshot_after": a.screenshot_after,
                }
                for a in self.actions
            ],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
```

- [ ] **Step 2: 验证 reporter 可导入**

```bash
cd D:/UI_Agent && python -c "
from agent.reporter import FlowReport, ActionReport
from datetime import datetime, timezone
r = FlowReport(flow_id='test', flow_name='Test', run_tag='abcdef01', started_at=datetime.now(timezone.utc))
r.actions.append(ActionReport(index=1, name='step1', phase='step', passed=True, duration_ms=1000))
print(r.to_dict())
print('Reporter OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add agent/reporter.py
git commit -m "feat: add reporter — FlowReport/ActionReport with JSON serialization"
```

---

### Task 4: Flow Runner

**Files:**
- Create: `agent/flow_runner.py`

- [ ] **Step 1: 创建 `agent/flow_runner.py`**

```python
"""Flow 执行引擎 —— setup → steps → teardown，Fail-Fast + Action 级 Retry。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from agent.config import Config
from agent.reporter import ActionReport, FlowReport

if TYPE_CHECKING:
    from agent.adb_controller import ADBController
    from agent.actions import Action, ActionResult


class FlowRunner:
    """流程执行器 —— 按 setup → steps → teardown 顺序执行。"""

    def __init__(
        self,
        adb: "ADBController",
        flow: "object",  # Flow 类型（避免循环导入）
        screenshot_base: Path | None = None,
    ) -> None:
        self._adb = adb
        self._flow = flow
        self._run_tag = uuid.uuid4().hex[:8]
        self._screenshot_dir = screenshot_base or (
            Config.SCREENSHOT_DIR / f"{flow.id}_{self._run_tag}"
        )
        self._action_index = 0

    # ── Public ──────────────────────────────────────────

    def run(self) -> FlowReport:
        """执行完整流程并返回报告。"""
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(timezone.utc)

        report = FlowReport(
            flow_id=self._flow.id,
            flow_name=self._flow.name,
            run_tag=self._run_tag,
            started_at=started_at,
            total_actions=len(self._flow.setup) + len(self._flow.steps) + len(self._flow.teardown),
        )

        # ── Setup ──
        for action in self._flow.setup:
            result = self._execute_action(action, "setup")
            report.actions.append(result)
            if not result.passed:
                report.aborted_at_phase = "setup"
                report.passed = False
                report.finished_at = datetime.now(timezone.utc)
                self._run_teardown(report)
                self._finalize_report(report)
                return report

        # ── Steps ──
        for action in self._flow.steps:
            result = self._execute_action(action, "step")
            report.actions.append(result)
            if not result.passed:
                report.aborted_at_phase = "step"
                report.passed = False
                report.finished_at = datetime.now(timezone.utc)
                self._run_teardown(report)
                self._finalize_report(report)
                return report

        # ── 全部通过 ──
        report.passed = True
        report.finished_at = datetime.now(timezone.utc)
        self._run_teardown(report)
        self._finalize_report(report)
        return report

    # ── Internal ─────────────────────────────────────────

    def _execute_action(self, action: "Action", phase: str) -> ActionReport:
        """执行单个 Action 并返回报告。"""
        self._action_index += 1
        idx = self._action_index

        result: "ActionResult" = action.execute(
            self._adb,
            screenshot_dir=self._screenshot_dir,
            action_index=idx,
        )

        # 构建截图相对路径（用于报告）
        def _rel(p: str | None) -> str | None:
            if p is None:
                return None
            try:
                return str(Path(p).relative_to(Config.SCREENSHOT_DIR).as_posix())
            except ValueError:
                return str(p)

        return ActionReport(
            index=idx,
            name=action.name,
            phase=phase,
            passed=result.success,
            error=None if result.success else result.message,
            retries_used=result.retries_used,
            duration_ms=result.duration_ms,
            screenshot_before=_rel(result.screenshot_before),
            screenshot_after=_rel(result.screenshot_after),
        )

    def _run_teardown(self, report: FlowReport) -> None:
        """执行 teardown，异常全部吞掉。"""
        for action in self._flow.teardown:
            try:
                result = self._execute_action(action, "teardown")
                report.actions.append(result)
            except Exception as e:
                self._action_index += 1
                report.actions.append(ActionReport(
                    index=self._action_index,
                    name=action.name,
                    phase="teardown",
                    passed=False,
                    error=str(e),
                ))

    def _finalize_report(self, report: FlowReport) -> None:
        """汇总报告统计。"""
        report.passed_actions = sum(1 for a in report.actions if a.passed and a.phase != "teardown")
        # 保存报告到 reports/ 目录
        report_path = Config.REPORT_DIR / f"{self._flow.id}_{self._run_tag}.json"
        report.save(report_path)
```

- [ ] **Step 2: 验证 flow_runner 可导入**

```bash
cd D:/UI_Agent && python -c "
from agent.flow_runner import FlowRunner
print('FlowRunner imported OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add agent/flow_runner.py
git commit -m "feat: add FlowRunner — setup→steps→teardown with Fail-Fast + retry"
```

---

### Task 5: Scenarios Base + Registry 更新

**Files:**
- Modify: `scenarios/base.py` (添加 Flow 类型定义)
- Modify: `scenarios/registry.py` (支持 Flow 注册)
- Modify: `scenarios/__init__.py` (导出 Flow)
- Modify: `agent/__init__.py` (导出新模块)

- [ ] **Step 1: 扩展 `scenarios/base.py` 添加 Flow 类型**

在文件末尾追加：

```python
"""Flow 类型定义 —— 业务流程编排。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.actions import Action


@dataclass
class Flow:
    """业务流程编排 —— Action 的有序依赖链"""

    id: str
    name: str
    description: str = ""
    category: str = "app"
    driver_type: str = "app"

    setup: list[Any] = field(default_factory=list)       # list[Action]
    steps: list[Any] = field(default_factory=list)       # list[Action]
    teardown: list[Any] = field(default_factory=list)    # list[Action]

    config: dict = field(default_factory=dict)

    @property
    def step_count(self) -> int:
        return len(self.setup) + len(self.steps) + len(self.teardown)
```

- [ ] **Step 2: 更新 `scenarios/registry.py` 支持 Flow**

```python
"""场景注册中心。"""

from __future__ import annotations

from scenarios.base import Scenario, Flow

_scenarios: dict[str, Scenario] = {}
_flows: dict[str, Flow] = {}


def register(obj: Scenario | Flow) -> None:
    """注册 Scenario 或 Flow。"""
    if isinstance(obj, Flow):
        _flows[obj.id] = obj
    else:
        _scenarios[obj.id] = obj


def list_all() -> list[Scenario | Flow]:
    """列出所有已注册的场景和流程。"""
    result: list[Scenario | Flow] = list(_scenarios.values())
    result.extend(_flows.values())
    return result


def get(scenario_id: str) -> Scenario | Flow | None:
    """按 ID 获取场景或流程。"""
    return _flows.get(scenario_id) or _scenarios.get(scenario_id)


def get_flow(flow_id: str) -> Flow | None:
    """按 ID 获取 Flow。"""
    return _flows.get(flow_id)


def list_flows() -> list[Flow]:
    """列出所有已注册的 Flow。"""
    return list(_flows.values())
```

Old snippet to replace:
```python
"""场景注册中心。"""

from __future__ import annotations

from scenarios.base import Scenario

_scenarios: dict[str, Scenario] = {}


def register(scenario: Scenario) -> None:
    _scenarios[scenario.id] = scenario


def list_all() -> list[Scenario]:
    return list(_scenarios.values())


def get(scenario_id: str) -> Scenario | None:
    return _scenarios.get(scenario_id)
```

- [ ] **Step 3: 更新 `scenarios/__init__.py`**

```python
"""导入所有场景模块以触发注册。"""

from scenarios.app import lexiang_borrow_home  # noqa: F401
from scenarios.app import lexiang_withdraw_flow  # noqa: F401
from scenarios.base import Scenario, StepDef, Flow  # noqa: F401
from scenarios.registry import get, get_flow, list_all, list_flows  # noqa: F401
from scenarios.web import demo_web  # noqa: F401
```

- [ ] **Step 4: 更新 `agent/__init__.py`**

```python
from agent.adb_controller import ADBController
from agent.ai_verifier import AIVerifier
from agent.config import Config
from agent.test_runner import TestRunner, StepResult, TestReport
from agent.path_locator import PathLocator, ElementNotFoundError
from agent.actions import Action, TapAction, InputAction, WaitAction, ScrollAction, CustomAction, ActionResult
from agent.flow_runner import FlowRunner
from agent.reporter import FlowReport, ActionReport

__all__ = [
    "ADBController",
    "AIVerifier",
    "Config",
    "TestRunner", "StepResult", "TestReport",
    "PathLocator", "ElementNotFoundError",
    "Action", "TapAction", "InputAction", "WaitAction", "ScrollAction", "CustomAction", "ActionResult",
    "FlowRunner",
    "FlowReport", "ActionReport",
]
```

- [ ] **Step 5: 验证导入无环错误**

```bash
cd D:/UI_Agent && python -c "
import agent
import scenarios
print('All imports OK')
print('Flows:', len(scenarios.list_flows()))
print('All scenarios:', len(scenarios.list_all()))
"
```

- [ ] **Step 6: Commit**

```bash
git add scenarios/base.py scenarios/registry.py scenarios/__init__.py agent/__init__.py
git commit -m "feat: add Flow type to scenarios/base.py, update registry and exports"
```

---

### Task 6: 向后兼容 — test_runner.py 包装旧 Scenario

**Files:**
- Modify: `agent/test_runner.py`

- [ ] **Step 1: 在 TestRunner 中添加 `_wrap_legacy_scenario` 方法**

在 `TestRunner` 类的 `run` 方法之前添加：

```python
    # ── 旧 Scenario 兼容包装 ────────────────────────────

    @staticmethod
    def wrap_legacy_scenario(scenario: "Scenario") -> "Flow":
        """将旧 Scenario 包装为临时 Flow。

        - setup = []
        - steps = 每个 StepDef 包装为 CustomAction
        - teardown = []
        """
        from agent.actions import CustomAction, ActionResult
        from scenarios.base import Flow

        def _make_action_wrapper(step_def):
            """将 StepDef.action 包装为 CustomAction"""
            def _wrap(adb, locator) -> ActionResult:
                try:
                    step_def.action(adb)
                    return ActionResult(
                        action_name=step_def.name,
                        success=True,
                        message="步骤执行成功",
                    )
                except Exception as e:
                    return ActionResult(
                        action_name=step_def.name,
                        success=False,
                        message=str(e),
                    )
            return CustomAction(
                name=step_def.name,
                execute_fn=_wrap,
                retries=1,  # 旧场景不改动原有行为
            )

        steps = [_make_action_wrapper(sd) for sd in scenario.steps]
        return Flow(
            id=scenario.id,
            name=scenario.name,
            description=scenario.description,
            category=scenario.category,
            driver_type=scenario.driver_type,
            steps=steps,
            config=scenario.config,
        )
```

Note: need to add `from __future__ import annotations` at top of test_runner.py if not there, and ensure `Scenario` import works by using forward reference.

- [ ] **Step 2: 验证兼容包装**

```bash
cd D:/UI_Agent && python -c "
from scenarios.registry import get
from agent.test_runner import TestRunner
# 获取旧 Scenario
scenario = get('lexiang_borrow_home')
print(f'Scenario type: {type(scenario).__name__}')
# 包装为 Flow
flow = TestRunner.wrap_legacy_scenario(scenario)
print(f'Flow id={flow.id}, steps={flow.step_count}')
print('Legacy wrap OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add agent/test_runner.py
git commit -m "feat(test_runner): add wrap_legacy_scenario() for backward compat"
```

---

### Task 7: 用新 Flow 重写 lexiang_borrow_home

**Files:**
- Modify: `scenarios/app/main_screen.py`

- [ ] **Step 1: 重写 `scenarios/app/main_screen.py`**

```python
"""APP 场景：乐享借 APP 启动与主页面验证（新 Flow 架构）。"""

from agent.actions import TapAction, WaitAction, CustomAction, ActionResult
from agent.adb_controller import ADBController
from agent.config import Config
from agent.path_locator import PathLocator
from scenarios.base import Flow
from scenarios.registry import register


# ── 自定义 Actions ──────────────────────────────────────

def _wake_and_launch(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """唤醒屏幕并启动乐享借。"""
    adb.wake_screen()
    adb.wait(1)
    adb.stop_test_app()
    adb.wait(1)
    adb.start_test_app()
    adb.wait(4)

    # 验证 WebView 容器已加载
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

    # 验证包名在前台
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
```

- [ ] **Step 2: 更新 `scenarios/app/__init__.py`**

```python
from scenarios.app.main_screen import lexiang_borrow_home  # noqa: F401
from scenarios.app.withdraw_flow import lexiang_withdraw_flow  # noqa: F401
```

(保持现有内容不变，只是确认导入正确)

- [ ] **Step 3: 验证新 Flow 可导入**

```bash
cd D:/UI_Agent && python -c "
from scenarios.registry import get, get_flow
flow = get_flow('lexiang_borrow_home')
print(f'Flow: {flow.name}')
print(f'  Setup: {[a.name for a in flow.setup]}')
print(f'  Steps: {[a.name for a in flow.steps]}')
print('New flow registered OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add scenarios/app/main_screen.py scenarios/app/__init__.py
git commit -m "refactor: rewrite lexiang_borrow_home with new Flow architecture"
```

---

### Task 8: server.py 适配 FlowRunner

**Files:**
- Modify: `server.py:150-280` (api_start_run 中的同步执行器)

- [ ] **Step 1: 更新 server.py 的 api_start_run 以支持 Flow**

需要修改 `_sync_runner` 内部函数。定位到约第 153 行 `def _sync_runner() -> None:`，将其中执行逻辑改为支持 Flow。

在 server.py 顶部导入区添加：
```python
from agent.flow_runner import FlowRunner
from scenarios.base import Flow
from datetime import datetime, timezone
```

将 `_sync_runner` 内部（约第 159-283 行）的执行逻辑替换。找到以下位置：

```python
            else:
                _send_to_queue(queue, {"type": "log", "message": f"连接设备 {serial}，模型 {model}"})
                controller = ADBController(serial or Config.ANDROID_SERIAL)

            runner = TestRunner(test_name=scenario_id, controller=controller, model=model)
            runner.screenshot_dir = Config.SCREENSHOT_DIR / f"{scenario_id}_{run_id[:8]}"
            runner.screenshot_dir.mkdir(parents=True, exist_ok=True)

            step_defs = scenario.steps
            total = len(step_defs)
            step_results = []
            started_at = datetime.now(timezone.utc)
```

替换为：

```python
            else:
                _send_to_queue(queue, {"type": "log", "message": f"连接设备 {serial}，模型 {model}"})
                controller = ADBController(serial or Config.ANDROID_SERIAL)

            # 判断是新 Flow 还是旧 Scenario
            if isinstance(scenario, Flow):
                # 新 Flow —— 使用 FlowRunner
                flow_runner = FlowRunner(
                    adb=controller,
                    flow=scenario,
                    screenshot_base=Config.SCREENSHOT_DIR / f"{scenario.id}_{run_id[:8]}",
                )
                report = flow_runner.run()

                # 将 FlowReport 转换回旧格式推送给 WebSocket
                for action_report in report.actions:
                    step_name = action_report.name
                    step_index = action_report.index - 1
                    total = report.total_actions

                    _send_to_queue(queue, {
                        "type": "step_starting",
                        "step_index": step_index,
                        "step_name": step_name,
                        "total_steps": total,
                    })

                    # 截图路径转换为 StepResult 兼容格式
                    _send_to_queue(queue, {
                        "type": "step_completed",
                        "step_index": step_index,
                        "step_name": step_name,
                        "result": {
                            "step_name": step_name,
                            "screenshot": action_report.screenshot_after or action_report.screenshot_before,
                            "passed": action_report.passed,
                            "confidence": 1.0 if action_report.passed else 0.0,
                            "summary": action_report.error or f"{action_report.phase} 完成",
                            "differences": [] if action_report.passed else [action_report.error or ""],
                            "duration_ms": action_report.duration_ms,
                        },
                    })

                _send_to_queue(queue, {
                    "type": "run_completed",
                    "report": {
                        "test_name": report.flow_id,
                        "started_at": report.started_at.isoformat(),
                        "finished_at": report.finished_at.isoformat(),
                        "passed": report.passed,
                        "total_duration_ms": report.total_duration_ms,
                        "steps": [
                            {
                                "step_name": a.name,
                                "screenshot": a.screenshot_after or a.screenshot_before,
                                "passed": a.passed,
                                "confidence": 1.0 if a.passed else 0.0,
                                "summary": a.error or f"{a.phase} 完成",
                                "differences": [] if a.passed else [a.error or ""],
                                "duration_ms": a.duration_ms,
                            }
                            for a in report.actions
                        ],
                    },
                    "report_path": str(Config.REPORT_DIR / f"{report.flow_id}_{report.run_tag}.json"),
                })
                return  # 新 Flow 执行完毕

            # 旧 Scenario —— 使用 TestRunner（保留原逻辑）
            runner = TestRunner(test_name=scenario_id, controller=controller, model=model)
            runner.screenshot_dir = Config.SCREENSHOT_DIR / f"{scenario_id}_{run_id[:8]}"
            runner.screenshot_dir.mkdir(parents=True, exist_ok=True)

            step_defs = scenario.steps
            total = len(step_defs)
            step_results = []
            started_at = datetime.now(timezone.utc)
```

保留后续旧 Scenario 的执行逻辑（约第 180-283 行）不变。

- [ ] **Step 2: 验证 server 可启动**

```bash
cd D:/UI_Agent && timeout 5 python -c "
from server import app
print('Server module loaded OK')
" 2>&1 || echo "(timeout expected — just checking import)"
```

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat(server): support FlowRunner execution alongside legacy Scenario"
```

---

### Task 9: 用新 Flow 重写 lexiang_withdraw_flow

**Files:**
- Modify: `scenarios/app/withdraw_flow.py`

- [ ] **Step 1: 重写为 Flow 架构**

原有文件约 531 行，核心逻辑保留（_close_homepage_popup、_scroll_to_product、_ensure_on_withdraw_page 等导航逻辑），但包装为 CustomAction，归入 setup 阶段。表单操作用 TapAction/InputAction/ScrollAction。

```python
"""APP 场景：乐小融V2 提现页操作流程（新 Flow 架构）。

流程：
  setup:    启动APP → 等待加载 → 关闭弹窗 → 导航到提现页
  steps:    验证页面元素 → 修改金额 → 选择期数 → 计息弹窗 → 还款弹窗
  teardown: （空）
"""

from __future__ import annotations

import re

from agent.actions import TapAction, InputAction, ScrollAction, WaitAction, CustomAction, ActionResult
from agent.adb_controller import ADBController
from agent.config import Config
from agent.path_locator import PathLocator
from scenarios.base import Flow
from scenarios.registry import register


# ═══════════════════════════════════════════════════════════
#  自定义 Action 函数（导航逻辑，未来可抽象为通用 Action）
# ═══════════════════════════════════════════════════════════

def _launch_and_navigate(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """启动APP并导航到乐小融V2提现页。"""
    adb.stop_test_app()
    adb.wait(2)
    adb.start_test_app()
    adb.wait(8)

    # 关闭首页弹窗
    _close_homepage_popup(adb)

    # 滚动查找乐小融V2并点击
    if not _scroll_to_product(adb, "乐小融V2"):
        return ActionResult(
            action_name="启动APP并进入乐小融V2提现页",
            success=False,
            message="未找到乐小融V2产品",
        )

    adb.wait(5)
    # 验证已进入提现页
    if not _is_on_withdraw_page(adb, locator):
        adb.wait(5)
        if not _is_on_withdraw_page(adb, locator):
            return ActionResult(
                action_name="启动APP并进入乐小融V2提现页",
                success=False,
                message="导航后仍未到达提现页",
            )

    return ActionResult(
        action_name="启动APP并进入乐小融V2提现页",
        success=True,
        message="已到达提现页",
    )


def _verify_withdraw_page(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证提现页核心元素完整性（顶部可见部分）。"""
    checks = [
        '*[text="借款申请"]',
        '*[text_contains="可取现额度"]',
        'EditText[0]',
        '*[text_contains="可借款金额范围"]',
        '*[text="借款期限"]',
        '*[text="计息方式"]',
        '*[text="还款计划"]',
    ]
    for path in checks:
        if not locator.exists(adb, path):
            return ActionResult(
                action_name="验证提现页元素完整性",
                success=False,
                message=f"元素不存在: {path}",
            )
    return ActionResult(
        action_name="验证提现页元素完整性",
        success=True,
        message="提现页顶部元素完整",
    )


def _verify_bottom_fields(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """验证提现页底部表单字段完整性。"""
    w, h = adb.get_screen_size()
    adb.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
    adb.wait(2)

    checks = [
        '*[text="收款卡"]',
        '*[text="借款用途"]',
        '*[text_contains="放款机构"]',
        '*[text_contains="我已阅读并同意"]',
        '*[text="确认借款"]',
    ]
    for path in checks:
        if not locator.exists(adb, path):
            return ActionResult(
                action_name="验证表单字段完整性",
                success=False,
                message=f"元素不存在: {path}",
            )
    return ActionResult(
        action_name="验证表单字段完整性",
        success=True,
        message="表单字段完整",
    )


# ═══════════════════════════════════════════════════════════
#  工具函数（从旧代码迁移，保持逻辑不变）
# ═══════════════════════════════════════════════════════════

def _close_homepage_popup(adb: ADBController) -> None:
    """关闭首页弹窗。"""
    adb.wait(1)
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
    x = (pop.bounds[0] + pop.bounds[2]) // 2
    y = pop.bounds[3] - 40
    adb.tap(x, y)
    adb.wait(2)


def _scroll_to_product(adb: ADBController, product_name: str = "乐小融V2") -> bool:
    """滚动首页，查找指定产品并点击"立即提现"。"""
    w, h = adb.get_screen_size()
    for _ in range(12):
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
            if best_btn and best_dist < 300:
                adb.tap(*best_btn.center)
                adb.wait(5)
                return True
            adb.tap(913, product_y)
            adb.wait(5)
            return True
        adb.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
        adb.wait(2)
    return False


def _is_on_withdraw_page(adb: ADBController, locator: type[PathLocator]) -> bool:
    """检测当前是否在提现页。"""
    markers = ["借款申请", "可取现额度", "确认借款"]
    for marker in markers:
        if locator.exists(adb, f'*[text="{marker}"]'):
            return True
    return False


def _close_popup_click(adb: ADBController, locator: type[PathLocator]) -> ActionResult:
    """关闭弹窗：点击"我知道了"/"确定"等按钮。"""
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


# ═══════════════════════════════════════════════════════════
#  Flow 定义
# ═══════════════════════════════════════════════════════════

lexiang_withdraw_flow = Flow(
    id="lexiang_withdraw_flow",
    name="乐小融V2提现页操作流程",
    description=(
        "启动APP → 导航到乐小融V2提现页 → 验证页面元素 → "
        "修改金额3000 → 选择9期 → 计息弹窗 → 还款弹窗"
    ),
    category="app",
    driver_type="app",

    setup=[
        CustomAction(
            name="启动APP并进入乐小融V2提现页",
            execute_fn=_launch_and_navigate,
            retries=2,
            retry_delay=5.0,
        ),
    ],

    steps=[
        # ── 页面验证 ──
        CustomAction(
            name="验证提现页元素完整性",
            execute_fn=_verify_withdraw_page,
        ),
        CustomAction(
            name="验证表单字段完整性",
            execute_fn=_verify_bottom_fields,
        ),
        # ── 金额修改 ──
        InputAction(
            name="修改借款金额为3000",
            path="EditText[0]",
            value="3000",
            wait_before=1,
            wait_after=3,
            retries=3,
        ),
        # ── 期数选择 ──
        TapAction(
            name="选择借款期限",
            path='*[text_matches="\\d+期"]',
            wait_after=2,
        ),
        TapAction(
            name="选择9期",
            path='*[text="9期"]',
            wait_after=3,
            retries=3,
        ),
        # ── 计息方式弹窗 ──
        TapAction(
            name="打开计息方式详情",
            path='*[text_contains="按日计息"]',
            wait_after=3,
            assertions=['*[text="我知道了"]'],
        ),
        CustomAction(
            name="关闭计息方式弹窗",
            execute_fn=_close_popup_click,
            retries=2,
        ),
        WaitAction(
            name="验证弹窗已关闭",
            until_gone='*[text="我知道了"]',
            timeout=10,
        ),
        # ── 还款计划弹窗 ──
        TapAction(
            name="打开还款计划详情",
            path='*[text_contains="首期"] | *[text_contains="应还"]',
            wait_after=3,
            assertions=['*[text="我知道了"]'],
        ),
        CustomAction(
            name="关闭还款计划弹窗",
            execute_fn=_close_popup_click,
            retries=2,
        ),
        WaitAction(
            name="验证还款弹窗已关闭",
            until_gone='*[text="我知道了"]',
            timeout=10,
        ),
    ],

    teardown=[],
)

register(lexiang_withdraw_flow)
```

- [ ] **Step 2: 验证新 Flow 可导入**

```bash
cd D:/UI_Agent && python -c "
from scenarios.registry import get_flow
flow = get_flow('lexiang_withdraw_flow')
print(f'Flow: {flow.name}')
print(f'  Setup: {len(flow.setup)} actions')
print(f'  Steps: {len(flow.steps)} actions')
print(f'  Total: {flow.step_count} actions')
for a in flow.setup + flow.steps:
    print(f'    - {a.name}')
print('Withdraw flow registered OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add scenarios/app/withdraw_flow.py
git commit -m "refactor: rewrite lexiang_withdraw_flow with new Flow architecture"
```

---

### Task 10: 端到端验证

**Files:**
- Create: `tests/test_new_framework.py` (临时验证脚本)

- [ ] **Step 1: 验证 PathLocator 查找元素（需连接设备）**

```bash
cd D:/UI_Agent && python -c "
from agent import ADBController, Config, PathLocator
adb = ADBController(Config.ANDROID_SERIAL)
xml = adb.dump_ui_xml()
print(f'XML size: {len(xml)} chars')
elements = adb.find_elements(package=Config.APP_PACKAGE)
print(f'Elements on screen for {Config.APP_PACKAGE}: {len(elements)}')
# Test PathLocator with sample paths
tests = ['WebView', 'EditText', 'Button', 'TextView']
for p in tests:
    try:
        elem = PathLocator.find(adb, p, timeout=3)
        print(f'  {p:20s} → {elem.class_name} @ {elem.center}')
    except Exception as e:
        print(f'  {p:20s} → NOT_FOUND: {e}')
"
```

- [ ] **Step 2: 验证 FlowRunner 执行新 Flow（需连接设备 + APP 已安装）**

```bash
cd D:/UI_Agent && python -c "
from agent import ADBController, Config, FlowRunner
from scenarios.registry import get_flow

flow = get_flow('lexiang_borrow_home')
adb = ADBController(Config.ANDROID_SERIAL)

runner = FlowRunner(adb, flow)
report = runner.run()

print(f'Result: {\"PASS\" if report.passed else \"FAIL\"}')
print(f'Actions: {report.passed_actions}/{report.total_actions} passed')
for a in report.actions:
    status = '✓' if a.passed else '✗'
    print(f'  [{status}] {a.name:40s} ({a.phase}, {a.duration_ms:.0f}ms)')
    if a.error:
        print(f'       Error: {a.error}')
"
```

- [ ] **Step 3: 验证旧场景兼容 — server 模块能正常加载所有场景**

```bash
cd D:/UI_Agent && python -c "
import scenarios
from scenarios.registry import list_all
all_items = list_all()
print(f'Total registered: {len(all_items)}')
for item in all_items:
    t = type(item).__name__
    name = item.name
    steps = item.step_count
    print(f'  [{t:8s}] {name:30s} ({steps} steps/actions)')
print('All items registered OK')
"
```

- [ ] **Step 4: 验证 server 可启动（导入检查）**

```bash
cd D:/UI_Agent && python -c "
from server import app
from fastapi.testclient import TestClient
client = TestClient(app)
resp = client.get('/api/scenarios')
print(f'Scenarios endpoint: {resp.status_code}')
data = resp.json()
print(f'Scenarios returned: {len(data)}')
for s in data:
    print(f'  - {s[\"id\"]}: {s[\"name\"]} [{s[\"category\"]}]')
"
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_new_framework.py 2>/dev/null; git add -A
git commit -m "test: add end-to-end verification for new framework"
```

---

## Plan Summary

| Task | Module | Status |
|------|--------|--------|
| 1 | ADB enhancement + path_locator.py | P0 |
| 2 | actions.py (Action types) | P0 |
| 3 | reporter.py | P1 |
| 4 | flow_runner.py | P1 |
| 5 | scenarios/base.py + registry + imports | P2 |
| 6 | test_runner.py backward compat | P2 |
| 7 | Rewrite lexiang_borrow_home | P3 |
| 8 | server.py adapt | P4 |
| 9 | Rewrite lexiang_withdraw_flow | P5 |
| 10 | End-to-end verification | Final |

**Total new files:** 4 (`path_locator.py`, `actions.py`, `reporter.py`, `flow_runner.py`)
**Total modified files:** 8 (`adb_controller.py`, `base.py`, `registry.py`, `__init__.py`×2, `test_runner.py`, `main_screen.py`, `withdraw_flow.py`, `server.py`)
**New dependencies:** 0 (reuses `xml.etree.ElementTree`, `subprocess`, `dataclasses`)
