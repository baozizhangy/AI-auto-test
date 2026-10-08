"""AI 视觉验证层。

将 基线截图 (golden) 与 实际截图 发送给多模态模型做语义级比对，
返回结构化的验证结果，而非简单的像素 diff。
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import anthropic


def _encode_image(path: Path) -> dict:
    """将图片编码为 Anthropic API 的 base64 media block。"""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    ext = path.suffix.lower()
    media_type = "image/jpeg" if ext in {".jpg", ".jpeg"} else "image/png"
    return {"type": "base64", "media_type": media_type, "data": data}


class AIVerifier:
    """使用 Claude Vision 做 GUI 截图语义验证。"""

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-6") -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    SYSTEM_PROMPT = """\
你是一个 GUI 自动化测试的视觉验证专家。你会收到两张手机截图：
- 图 A（baseline）：测试用例的预期画面（基线）
- 图 B（actual）：测试实际运行时截到的画面

请仔细对比两张图，从以下维度判断它们是否一致：
1. **页面导航** — 是否在同一个页面 / 同一个 Screen
2. **关键 UI 元素** — 按钮、输入框、图标、列表项是否相同
3. **文字内容** — 标题、提示文字、按钮文字是否一致
4. **布局结构** — 元素位置、排列方式是否合理匹配
5. **状态差异** — 加载中、空状态、错误状态、勾选状态等

注意：
- 两张图可能是不同分辨率，不要因为尺寸不同而判失败
- 允许颜色/主题的细微差异
- 允许列表数据项的具体内容不同（如列表里显示的标题不同），但结构应对得上
- 如果是同一个页面但内容的合理变化（如时间戳不同），应视为通过

返回**纯 JSON**（不要 markdown 代码块），格式如下：
{"pass": true/false, "confidence": 0.0~1.0, "summary": "一句话结论", "differences": ["差异1", "差异2"]}
"""

    def verify(
        self,
        baseline_path: str | Path,
        actual_path: str | Path,
        custom_prompt: str | None = None,
    ) -> dict[str, Any]:
        """对比 baseline 和 actual，返回验证结果 dict。"""
        baseline_img = _encode_image(Path(baseline_path))
        actual_img = _encode_image(Path(actual_path))

        user_text = "对比图 A（baseline）和图 B（actual），判断是否一致。"
        if custom_prompt:
            user_text = f"{custom_prompt}\n\n{user_text}"

        message = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=self.SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "图 A — baseline（预期画面）："},
                        {"type": "image", "source": baseline_img},
                        {"type": "text", "text": "图 B — actual（实际截图）："},
                        {"type": "image", "source": actual_img},
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        )

        raw = message.content[0].text
        return self._parse_response(raw)

    def _parse_response(self, text: str) -> dict[str, Any]:
        """从模型回复中提取 JSON。"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 尝试从 markdown 代码块中提取
        match = re.search(r"\{[\s\S]*\"pass\"[\s\S]*\}", text)
        if match:
            return json.loads(match.group())
        # 都失败则返回原始文本
        return {"pass": False, "confidence": 0, "summary": text, "differences": []}
