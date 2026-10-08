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
    \s*>\s*                        # > direct child combinator
    |\s+                           # whitespace: descendant combinator
    |\|\s*                         # | alternative path separator
    |\#(?P<res_id>[\w./:]+)       # #resource_id (allow : for full package)
    |(?P<index>\[\-?\d+\])         # [N] or [-N]
    |\[(?P<attr>\w+)               # [attr
        (?:
            _(?P<op>contains|starts_with|matches)  # _op
            \s*=\s*"(?P<val1>[^"]*)"              # ="value" (quoted)
            |
            \s*=\s*"(?P<val2>[^"]*)"             # ="value" (quoted, exact)
            |
            \s*=\s*(?P<val3>[^"\]]+)             # =value (unquoted)
        )?
        \]                          # ]
    |(?P<class_name>\w+(?:\.\w+)*)  # ClassName
    |\*(?=\[)                      # * wildcard (only before [)
    """,
    re.VERBOSE,
)


class _Segment:
    """路径中的一个匹配段。"""
    __slots__ = ("child_combinator", "resource_id", "class_name", "attrs", "index", "is_wildcard")

    def __init__(self) -> None:
        self.child_combinator: str = " "        # " " descendant, ">" direct child, "|" alternative
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

    # built-in: short class name → fully qualified name
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

            matched = m.group()

            # > direct child combinator
            if matched.startswith(">") or matched.strip() == ">":
                cur = _Segment()
                cur.child_combinator = ">"
                segments.append(cur)
                pos = m.end()
                # skip trailing whitespace after >
                while pos < len(path) and path[pos] == " ":
                    pos += 1
                continue

            # | alternative
            if matched.startswith("|"):
                cur = _Segment()
                cur.child_combinator = "|"
                segments.append(cur)
                pos = m.end()
                continue

            # pure whitespace = descendant combinator
            if matched.strip() == "":
                whitespace_end = m.end()
                # peek ahead: if next token is > or |, skip this whitespace
                lookahead = _SELECTOR_RE.match(path, whitespace_end)
                if lookahead and lookahead.group().strip() in (">", "|"):
                    pos = whitespace_end
                    continue
                cur = _Segment()
                cur.child_combinator = " "
                segments.append(cur)
                pos = whitespace_end
                continue

            # actual matchers
            if m.group("res_id"):
                cur.resource_id = m.group("res_id")
            elif m.group("attr"):
                attr_name = m.group("attr")
                op = m.group("op") or "="
                val = m.group("val1") or m.group("val2") or m.group("val3") or ""
                cur.attrs[attr_name] = (op, val)
            elif m.group("index"):
                cur.index = int(m.group("index").strip("[]"))
            elif m.group("class_name"):
                cur.class_name = cls._resolve_class(m.group("class_name"))
            elif matched.startswith("*"):
                cur.is_wildcard = True

            pos = m.end()

        # filter out empty segments
        final: list[_Segment] = []
        for seg in segments:
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
        # resource_id: support partial match like "id/webview_fl" matching "com.app:id/webview_fl"
        if seg.resource_id is not None:
            eid = element.resource_id
            if eid != seg.resource_id:
                # partial match: the stored ID ends with :{path_id}
                if not (eid.endswith(":" + seg.resource_id)):
                    # also support "id/xxx" form
                    if not (seg.resource_id.startswith("id/") and eid.endswith(seg.resource_id)):
                        return False

        # class_name
        if seg.class_name is not None:
            if element.class_name != seg.class_name:
                # try matching by short name
                short = element.class_name.split(".")[-1] if "." in element.class_name else element.class_name
                seg_short = seg.class_name.split(".")[-1] if "." in seg.class_name else seg.class_name
                if short != seg_short:
                    return False

        # wildcard matches everything (but still checks attrs below)

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
        """在一个候选列表中按段条件匹配，返回匹配的元素列表。若指定 index，只返回该索引元素。"""
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
    def _get_descendants_of(cls, root: ET.Element, parent_bounds: tuple[int, int, int, int],
                            adb: "ADBController", direct_only: bool) -> list["UIElement"]:
        """获取 parent 元素的子/后代元素列表。

        Args:
            root: XML 根节点
            parent_bounds: 父元素的 bounds
            adb: ADBController（用于 _parse_bounds）
            direct_only: True=仅直接子元素, False=所有后代
        """
        from agent.adb_controller import UIElement as UE

        # find parent node by bounds
        target_str = f"[{parent_bounds[0]},{parent_bounds[1]}][{parent_bounds[2]},{parent_bounds[3]}]"
        parent_node = None
        for node in root.iter("node"):
            if node.get("bounds") == target_str:
                parent_node = node
                break

        if parent_node is None:
            return []

        elements: list["UIElement"] = []

        if direct_only:
            # only immediate children of parent_node
            children_iter = parent_node.findall("node")
        else:
            # all descendants (skip parent_node itself)
            children_iter = parent_node.iter("node")

        for child in children_iter:
            if child is parent_node:
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

    # ── Public API ──────────────────────────────────────────

    @classmethod
    def find_all(cls, adb: "ADBController", path: str) -> list["UIElement"]:
        """返回所有匹配元素（立即返回，不等待）。"""
        # handle alternative paths: split by first top-level |
        if cls._has_toplevel_alternative(path):
            parts = cls._split_alternatives(path)
            for part in parts:
                results = cls.find_all(adb, part)
                if results:
                    return results
            return []

        segments = cls._parse_path(path)
        xml_text = adb.dump_ui_xml()
        root = ET.fromstring(xml_text)

        from agent.adb_controller import UIElement as UE

        # initial candidates: all elements in the tree
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

        # match segment by segment
        for i, seg in enumerate(segments):
            if i == 0:
                # first segment: match against all elements
                candidates = cls._match_segment(seg, candidates, seg.index)
            else:
                # subsequent segments: match within descendants of previous matches
                new_candidates: list["UIElement"] = []
                for prev_elem in candidates:
                    direct_only = (seg.child_combinator == ">")
                    children = cls._get_descendants_of(
                        root, prev_elem.bounds, adb, direct_only,
                    )
                    matched = cls._match_segment(seg, children, seg.index)
                    new_candidates.extend(matched)
                candidates = new_candidates

            if not candidates and i < len(segments) - 1:
                break  # intermediate segment no match, early exit

        return candidates

    @classmethod
    def find(cls, adb: "ADBController", path: str, timeout: float = 10) -> "UIElement":
        """查找单个匹配元素，在 timeout 内以 1s 间隔轮询重试。

        Raises:
            ElementNotFoundError: timeout 后仍未找到
        """
        start = time.perf_counter()

        while True:
            results = cls.find_all(adb, path)
            if results:
                return results[0]

            elapsed = time.perf_counter() - start
            if elapsed >= timeout:
                raise ElementNotFoundError(
                    path,
                    f"路径 '{path}' 未匹配到任何元素（{timeout}s 超时）",
                )
            time.sleep(1)

    @classmethod
    def exists(cls, adb: "ADBController", path: str) -> bool:
        """检查元素是否存在（立即返回 True/False）。"""
        results = cls.find_all(adb, path)
        return len(results) > 0

    @classmethod
    def wait_until_gone(cls, adb: "ADBController", path: str, timeout: float = 15) -> bool:
        """等待元素从 UI 树中消失，返回是否成功消失。"""
        start = time.perf_counter()
        while True:
            if not cls.exists(adb, path):
                return True
            if time.perf_counter() - start >= timeout:
                return False
            time.sleep(1)

    # ── Alternative path helpers ────────────────────────────

    @classmethod
    def _has_toplevel_alternative(cls, path: str) -> bool:
        """检查路径是否包含顶层 | (不在 [...] 内)。"""
        depth = 0
        for i, ch in enumerate(path):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            elif ch == "|" and depth == 0:
                return True
        return False

    @classmethod
    def _split_alternatives(cls, path: str) -> list[str]:
        """在顶层 | 处拆分备选路径。"""
        parts = []
        depth = 0
        start = 0
        for i, ch in enumerate(path):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            elif ch == "|" and depth == 0:
                parts.append(path[start:i].strip())
                start = i + 1
        parts.append(path[start:].strip())
        return parts
