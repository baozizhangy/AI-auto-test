# UI Agent — AI 驱动的自动化测试框架

基于视觉大模型的 GUI 自动化测试工具，支持 **Web 浏览器** (Playwright) 和 **Android 设备** (ADB) 两种驱动模式。通过 Claude Vision API 对截图进行语义级比对验证，附带 Web 后台管理界面。

## 架构概览

```
┌─────────────────────────────────────────────────┐
│                 Web Dashboard                    │
│            (FastAPI + Vanilla JS)                │
├──────────┬──────────────────────────────────────┤
│  Scenarios  │          Agent Core               │
│  ┌───────┐  │  ┌──────────┐  ┌──────────────┐  │
│  │  APP   │  │  │ADB Ctl   │  │ AI Verifier  │  │
│  │scenario│  │  │(adb CLI) │  │(Claude Vision)│  │
│  ├───────┤  │  ├──────────┤  │              │  │
│  │  WEB  │  │  │Web Ctl   │  │ 截图语义对比  │  │
│  │scenario│  │  │(Playwright)│  │              │  │
│  └───────┘  │  └──────────┘  └──────────────┘  │
│  Registry   │         TestRunner                 │
│  (场景注册) │    (action → 截图 → AI验证)        │
└──────────────┴──────────────────────────────────┘
```

**核心流程：** 声明测试步骤 → 控制器执行操作 → 截图 → AI 语义验证 → 生成报告

## 项目结构

```
UI_Agent/
├── agent/                         # 核心引擎
│   ├── adb_controller.py          # Android ADB 控制（截图、点击、滑动、按键）
│   ├── web_controller.py          # Playwright 浏览器控制（导航、点击、填表）
│   ├── ai_verifier.py             # Claude Vision API 截图语义比对
│   ├── test_runner.py             # 测试编排器（步骤执行 + 断言 + 报告）
│   └── config.py                  # 全局配置（环境变量加载）
├── scenarios/                     # 测试场景（按驱动类型分目录）
│   ├── base.py                    # StepDef / Scenario 数据类
│   ├── registry.py                # 场景注册中心
│   ├── app/                       # APP 场景（ADB）
│   │   └── main_screen.py
│   └── web/                       # WEB 场景（Playwright）
│       └── demo.py
├── server.py                      # FastAPI 后端（REST + WebSocket）
├── static/
│   └── index.html                 # 单页管理后台
├── tests/
│   └── example_test.py            # 命令行示例（不依赖 Web 后台）
├── baselines/                     # 基线截图（预期画面）
├── screenshots/                   # 运行时截图（自动生成）
├── reports/                       # JSON 测试报告（自动生成）
└── requirements.txt
```

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
cd UI_Agent

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器（仅 WEB 测试需要）
playwright install chromium
```

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`，必填项：

```env
# 必填 — Claude API 密钥（仅 visual 断言场景需要）
ANTHROPIC_API_KEY=sk-ant-your-key-here

# 可选 — 模型选择
ANTHROPIC_MODEL=claude-sonnet-4-6

# WEB 测试默认配置
BROWSER_TYPE=chromium
BROWSER_HEADLESS=true
DEFAULT_BASE_URL=https://www.example.com

# APP 测试默认设备
ANDROID_SERIAL=emulator-5554
```

> **注意：** 纯 DOM 断言（`assertion_type="dom"`）的 Web 场景不需要 API Key 即可运行。

### 3. 启动

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 **http://localhost:8000**

### 4. 运行测试

- 在 Web 后台选择 APP 或 WEB 标签
- 下拉选择场景 → 点击"运行测试"
- 右侧面板实时显示每步执行状态
- 底部展示结果汇总和截图画廊

## 测试场景编写

### Web 场景示例

```python
# scenarios/web/demo.py
from scenarios.base import Scenario, StepDef
from scenarios.registry import register

def _open_home(web):
    web.navigate("/")

def _check_title(web):
    title = web.title()
    assert "Example" in title, f"标题不符合预期: {title}"

demo_web = Scenario(
    id="demo_web",
    name="示例网站首页检查",
    description="打开 example.com，验证标题",
    category="web",
    driver_type="web",                    # 使用 Playwright
    config={"base_url": "https://www.example.com"},
    steps=[
        StepDef(name="打开首页", action=_open_home, assertion_type="dom"),
        StepDef(name="验证标题", action=_check_title, assertion_type="dom"),
    ],
)
register(demo_web)
```

### APP 场景示例

```python
# scenarios/app/main_screen.py
from agent.adb_controller import ADBController
from scenarios.base import Scenario, StepDef
from scenarios.registry import register

def _go_home(adb: ADBController):
    adb.press_home()

def _start_settings(adb: ADBController):
    adb.start_app("com.android.settings")

app_main_screen = Scenario(
    id="app_main_screen",
    name="设置应用主页检查",
    category="android",
    driver_type="app",                    # 使用 ADB
    steps=[
        StepDef(name="回到主页", action=_go_home),
        StepDef(name="启动设置", action=_start_settings,
                baseline="settings_main.png"),  # AI 视觉对比基线截图
    ],
)
register(app_main_screen)
```

### 断言类型

| assertion_type | 说明 | 适用驱动 | 需要 API Key |
|---|---|---|---|
| `"visual"` | AI 截图语义比对（与基线对比） | APP / WEB | 是 |
| `"dom"` | 代码级断言（`assert` 语句） | WEB | 否 |
| `"text"` | 文字内容检查 | WEB | 否 |

### WebController 常用 API

| 方法 | 说明 |
|---|---|
| `web.navigate(url)` | 导航到 URL（相对路径拼接到 base_url） |
| `web.click(selector)` | 点击元素 |
| `web.fill(selector, text)` | 填写输入框 |
| `web.get_text(selector)` | 获取元素文字 |
| `web.is_visible(selector)` | 检查元素是否可见 |
| `web.wait_for_selector(s)` | 等待元素出现 |
| `web.title()` | 获取页面标题 |
| `web.screenshot(path)` | 截取当前页面 |

### ADBController 常用 API

| 方法 | 说明 |
|---|---|
| `adb.tap(x, y)` | 点击坐标 |
| `adb.swipe(x1, y1, x2, y2)` | 滑动 |
| `adb.input_text(text)` | 输入文字 |
| `adb.press_back()` / `adb.press_home()` | 按键 |
| `adb.start_app(package)` | 启动应用 |
| `adb.screenshot(path)` | 截取设备画面 |

## API 接口

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/scenarios` | 列出所有场景 |
| GET | `/api/scenarios/{id}` | 场景详情 |
| GET | `/api/config` | 当前配置 |
| POST | `/api/run` | 启动测试 `{"scenario_id":"...","model":"..."}` |
| GET | `/api/runs` | 历史运行列表 |
| GET | `/api/runs/{run_id}` | 某次运行完整报告 |
| WS | `/ws/run/{run_id}` | 实时步骤推送 |

## 命令行运行（不启动 Web 后台）

```python
# tests/example_test.py
from agent import TestRunner, ADBController

runner = TestRunner(
    test_name="my_test",
    controller=ADBController("emulator-5554"),
)
result = runner.step("打开设置", lambda adb: adb.start_app("com.android.settings"))
print(f"passed={result.passed}, confidence={result.confidence}")
```

## 关键设计

- **语义比对而非像素比对**：AI 理解截图内容，容忍分辨率差异、主题变化、合理内容更新
- **Duck Typing Controller**：`TestRunner` 不依赖具体控制器类型，只需要 `screenshot(path)` 和 `wait(seconds)` 两个方法
- **单线程执行**：同一时间只允许一个测试运行，避免设备/浏览器冲突
- **WebSocket 实时推送**：每步执行完成后立即推送结果到前端
