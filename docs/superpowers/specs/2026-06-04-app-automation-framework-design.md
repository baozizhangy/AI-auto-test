# APP 自动化测试框架 — 架构设计

**日期：** 2026-06-04
**状态：** 设计完成，待实现
**范围：** Android APP 自动化测试核心引擎重构（第一版）

---

## 1. 目标与原则

### 1.1 核心目标

从零开始构建一个**灵活、可复用**的 Android APP 自动化测试框架，实现：
1. 从连接 APP 到执行测试流程，准确执行并持续断言
2. 可复用的流程，而不是黑盒式堆积代码
3. 第一版统一通过元素路径（path）断言，不依赖视觉核对
4. 灵活可交付使用，遇到新问题加 Action 类型而非堆积代码

### 1.2 设计原则

- **分层隔离**：Core → Action → Flow 三层，每层独立可测
- **声明式优先**：场景定义用声明式对象，不写流程控制代码
- **Fail-Fast**：步骤形成依赖链，任一步骤失败终止流程
- **Action 级 Retry**：每个 Action 自带重试机制，处理 UI 渲染延迟等偶发问题
- **最小依赖**：不新增第三方库，复用现有 `uiautomator dump` 基础设施
- **向后兼容**：现有 `Scenario` / `StepDef` 保留包装层，不破坏已有代码

---

## 2. 架构总览

```
┌────────────────────────────────────────────────────┐
│  scenarios/        Flow 定义（纯声明，不写逻辑）      │
│                    setup → steps → teardown         │
├────────────────────────────────────────────────────┤
│  actions.py        Action 类型                      │
│                    定位 → 交互 → 断言                │
│                    Tap / Input / Wait / Scroll       │
├────────────────────────────────────────────────────┤
│  path_locator.py   路径语法解析 + XML 树匹配         │
│                    '#id > *[text="确认"]'           │
├────────────────────────────────────────────────────┤
│  flow_runner.py    执行引擎                         │
│                    Fail-Fast + Retry + 截图 + 报告   │
├────────────────────────────────────────────────────┤
│  adb_controller.py ADB 设备控制（保留增强）           │
├────────────────────────────────────────────────────┤
│  server.py         FastAPI + WebSocket（最小改动）    │
└────────────────────────────────────────────────────┘
```

### 2.1 三层引擎

| 层 | 模块 | 职责 |
|----|------|------|
| Core | `path_locator.py`, `adb_controller.py` | 元素定位、设备操控、截图 |
| Action | `actions.py` | 可复用的业务动作（定位→交互→断言） |
| Flow | `flow_runner.py`, `scenarios/` | 业务流程编排（setup→steps→teardown） |

### 2.2 依赖方向

```
scenarios  →  actions  →  path_locator  →  adb_controller
                │
                └──────────►  adb_controller

flow_runner  →  actions + path_locator + adb_controller + reporter
```

- `scenarios` 只依赖 `actions` 中的类型定义，不依赖 `flow_runner`
- `actions` 依赖 `path_locator` 和 `adb_controller`
- `path_locator` 只依赖 `adb_controller`（需要 dump XML）
- 不存在循环依赖

---

## 3. Core Layer 详细设计

### 3.1 Path Locator（path_locator.py）

#### 3.1.1 路径语法

采用类 CSS Selector 的简洁路径表达式：

| 语法 | 含义 | 示例 |
|------|------|------|
| `#resource_id` | resource-id 完全匹配 | `#com.app:id/webview_fl` |
| `ClassName` | 按 class 匹配（省略 `android.widget.` 前缀） | `EditText`, `WebView`, `Button` |
| `>` | 直接子元素 | `#container > *[text="确认"]` |
| 空格 | 后代元素（任意深度） | `#page EditText` |
| `[attr=value]` | 属性等于：text, class, content_desc, clickable, enabled, package | `*[text="借款申请"]` |
| `[attr_op=value]` | 属性匹配：`contains`, `starts_with`, `matches` | `*[text_contains="可取现"]` |
| `[N]` / `[-N]` | 正序/倒序索引（0-based） | `EditText[0]` |
| `\|` | 备选路径（任一命中即返回） | `*[text="我知道了"] \| *[text="确定"]` |
| `*` | 通配，匹配任意元素 | `*[clickable=true]` |

#### 3.1.2 API

```python
class PathLocator:
    """路径定位器 —— 将简洁路径语法编译为 uiautomator XML 树的匹配"""

    def find(self, adb: ADBController, path: str, timeout: float = 10) -> UIElement:
        """
        解析路径，在最新 dump 的 XML 树中逐段匹配。
        如果元素未找到，在 timeout 内以 1s 间隔轮询重试。
        超时则抛出 ElementNotFoundError。
        """

    def find_all(self, adb: ADBController, path: str) -> list[UIElement]:
        """返回所有匹配元素（立即返回，不等待）"""

    def exists(self, adb: ADBController, path: str) -> bool:
        """检查元素是否存在（立即返回 True/False）"""

    def wait_until_gone(self, adb: ADBController, path: str, timeout: float = 15) -> bool:
        """等待元素从 UI 树中消失，返回是否成功"""
```

#### 3.1.3 实现要点

- 解析在 `uiautomator dump` 的 XML 树之上，逐段匹配
- resource-id 优先作为第一段匹配条件（最高稳定性）
- 备选路径 `|` 从左到右尝试，第一个命中即返回
- 文本匹配使用 Unicode 规范化（`NFKC`），容忍隐藏字符

### 3.2 ADB Controller（adb_controller.py）— 保留增强

现有 `ADBController` 保留，新增少数方法：

```python
# 新增方法
def dump_ui_xml(self) -> str:
    """执行 uiautomator dump 并返回 XML 文本（从 _dump_ui 提升为公开方法）"""

def get_page_source(self) -> str:
    """别名，语义更清晰"""
```

现有 `find_element` / `find_elements` / `tap_element` / `assert_element_exists` 保留不变，作为 `PathLocator` 的底层支撑。

---

## 4. Action Layer 详细设计（actions.py）

### 4.1 设计理念

Action 是业务层面的一个完整操作，遵循 **定位 → 交互 → 验证** 的三步闭环：

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Locate     │ ──► │  Interact   │ ──► │   Assert    │
│  定位目标元素 │     │  点击/输入等  │     │  验证结果    │
└─────────────┘     └─────────────┘     └─────────────┘
```

### 4.2 数据结构

```python
@dataclass
class ActionResult:
    """Action 执行结果"""
    action_name: str
    success: bool
    message: str
    element: UIElement | None = None          # 操作的目标元素
    screenshot_before: str | None = None      # 操作前截图路径
    screenshot_after: str | None = None       # 操作后截图路径
    retries_used: int = 0                     # 实际重试次数
    duration_ms: float = 0                    # 总耗时（含重试）

@dataclass
class Action:
    """业务动作基类 —— 定位→交互→断言，三步合一"""

    name: str                                 # 动作名称（必填）
    path: str = ""                            # 定位路径
    wait_before: float = 1.0                  # 定位前等待
    wait_after: float = 1.5                   # 操作后等待
    retries: int = 3                          # 失败重试次数
    retry_delay: float = 2.0                  # 重试间隔（秒）

    def execute(self, adb: ADBController) -> ActionResult:
        """模板方法：定位 → 交互 → 断言 → 返回结果"""
```

### 4.3 预置 Action 类型

#### TapAction — 点击操作

```python
@dataclass
class TapAction(Action):
    """点击操作：定位元素 → 点击 → 验证后续状态"""
    assertions: list[str] = field(default_factory=list)  # 点击后应出现的元素路径列表

    # execute 逻辑：
    #   1. locator.find(path) → 获取元素
    #   2. 截图 before
    #   3. adb.tap(element.center) → 点击
    #   4. adb.wait(wait_after)
    #   5. 逐一执行 assertions 中的路径断言
    #   6. 截图 after
    #   7. 全部断言通过 → success=True
```

#### InputAction — 输入操作

```python
@dataclass
class InputAction(Action):
    """输入操作：定位输入框 → 清空 → 输入 → 失焦 → 验证值"""
    value: str = ""                           # 要输入的值
    assert_value: str | None = None           # 期望的输入结果（默认等于 value）

    # execute 逻辑：
    #   1. locator.find(path) → 获取输入框元素
    #   2. 截图 before
    #   3. adb.tap(input_element.center) → 聚焦
    #   4. 点击清除按钮（在输入框右侧查找小尺寸空文本元素）或全选删除
    #   5. adb.input_text(value)
    #   6. 点击标题区域失焦
    #   7. adb.wait(wait_after)
    #   8. 断言 element.text == assert_value
    #   9. 截图 after
```

#### WaitAction — 等待操作

```python
@dataclass
class WaitAction(Action):
    """纯等待操作：等待元素出现或消失"""
    until_exists: str | None = None           # 等待此路径对应的元素出现
    until_gone: str | None = None             # 等待此路径对应的元素消失
    timeout: float = 30                       # 最长等待时间（秒）

    # execute 逻辑：
    #   until_exists: 轮询 locator.exists(path) 直到 True 或超时
    #   until_gone: 轮询 locator.wait_until_gone(path) 直到消失或超时
```

#### ScrollAction — 滚动操作

```python
@dataclass
class ScrollAction(Action):
    """滚动操作：滚动查找目标元素"""
    direction: str = "down"                   # up / down / left / right
    max_scrolls: int = 10                     # 最大滚动次数

    # execute 逻辑：
    #   循环 max_scrolls 次：
    #     1. 检查 path 元素是否存在（可见）
    #     2. 存在 → 截图 after → 返回
    #     3. 不存在 → adb.swipe(方向滚动) → adb.wait(1s)
    #   循环结束仍未找到 → success=False
```

#### CustomAction — 自定义操作

```python
@dataclass
class CustomAction(Action):
    """自定义操作：用于无法被预置类型覆盖的特殊业务逻辑"""
    execute_fn: Callable[[ADBController], ActionResult] | None = None

    # execute 逻辑：
    #   直接调用 execute_fn(adb)，结果原样返回
    #   作为逃生舱，确保框架灵活性
```

### 4.4 Retry 机制

每个 Action 内置重试，在 `execute` 模板方法中统一处理：

```
执行 action._do_execute(adb)
  ├── 成功 → 返回 ActionResult(success=True)
  └── 失败 →
        ├── retries > 0 → 等待 retry_delay → 重新 dump UI → 重试
        └── retries = 0 → 返回 ActionResult(success=False)
```

重试发生在 **整个 Action 级别**（定位→交互→断言 全部重新执行），确保每次都从最新 UI 状态开始。

---

## 5. Flow Layer 详细设计

### 5.1 数据结构

```python
@dataclass
class Flow:
    """业务流程编排 —— Action 的有序依赖链"""

    id: str                                   # 唯一标识
    name: str                                 # 展示名称
    description: str = ""                     # 描述
    category: str = "app"                     # app / web
    driver_type: str = "app"                  # app / web

    setup: list[Action] = field(default_factory=list)      # 前置操作（失败→终止）
    steps: list[Action] = field(default_factory=list)       # 核心步骤（顺序依赖链）
    teardown: list[Action] = field(default_factory=list)    # 后置清理（始终执行）

    config: dict = field(default_factory=dict)              # 流程级配置
```

### 5.2 执行引擎（flow_runner.py）

```python
class FlowRunner:
    """流程执行器 —— 按 setup → steps → teardown 顺序执行"""

    def __init__(self, adb: ADBController, flow: Flow, screenshot_base: Path):

    def run(self) -> FlowReport:
        """
        执行策略：
         1. setup:     顺序执行，任一失败 → 立即终止（Fail-Fast）
         2. steps:     顺序执行，每个 Action 自带 retry，
                       任一 retry 耗尽 → 终止整个流程
         3. teardown:  始终执行（即使 setup/steps 失败），异常被吞掉
        """
```

#### 执行流程

```
FlowRunner.run()
  │
  ├─► 创建截图目录: screenshots/{flow_id}_{run_tag}/
  │
  ├─► [setup 阶段]
  │     for action in flow.setup:
  │       result = action.execute(adb)
  │       if not result.success: BAIL  ← Fail-Fast
  │
  ├─► [steps 阶段]
  │     for action in flow.steps:
  │       result = action.execute(adb)    ← 内部自动 retry
  │       if not result.success: BAIL    ← retry 耗尽则终止
  │
  ├─► [teardown 阶段]  ← 始终执行
  │     for action in flow.teardown:
  │       try: action.execute(adb)
  │       except: pass                    ← 异常全部吞掉
  │
  └─► 生成 FlowReport
```

### 5.3 报告格式（reporter.py）

```python
@dataclass
class ActionReport:
    """单个 Action 的执行报告"""
    index: int
    name: str
    phase: str                              # setup / step / teardown
    passed: bool
    error: str | None                       # 失败原因
    retries_used: int
    duration_ms: float
    screenshot_before: str | None           # 相对路径
    screenshot_after: str | None            # 相对路径

@dataclass
class FlowReport:
    """一次 Flow 执行的完整报告"""
    flow_id: str
    flow_name: str
    run_tag: str                            # 8位随机标识
    started_at: datetime
    finished_at: datetime
    passed: bool                            # 所有 setup + steps 都通过
    total_actions: int
    passed_actions: int
    actions: list[ActionReport]
    aborted_at_phase: str | None            # setup / step / None
```

JSON 报告示例：

```json
{
  "flow_id": "lexiang_withdraw_v2",
  "flow_name": "乐小融V2提现流程",
  "run_tag": "a3f8b2c1",
  "started_at": "2026-06-04T10:30:00Z",
  "finished_at": "2026-06-04T10:30:45Z",
  "passed": false,
  "total_actions": 8,
  "passed_actions": 5,
  "aborted_at_phase": "step",
  "actions": [
    {
      "index": 1,
      "name": "启动APP",
      "phase": "setup",
      "passed": true,
      "error": null,
      "retries_used": 0,
      "duration_ms": 8200,
      "screenshot_before": "a3f8b2c1/01_start_app_before.png",
      "screenshot_after": "a3f8b2c1/01_start_app_after.png"
    },
    {
      "index": 5,
      "name": "选择借款期限9期",
      "phase": "step",
      "passed": false,
      "error": "路径 '*[text=\"9期\"]' 未匹配到任何元素（重试3次后）",
      "retries_used": 3,
      "duration_ms": 12000,
      "screenshot_before": "a3f8b2c1/05_select_period_before.png",
      "screenshot_after": null
    }
  ]
}
```

### 5.4 截图命名规则

```
screenshots/{flow_id}_{run_tag}/
  ├── 01_{action_name}_before.png
  ├── 01_{action_name}_after.png
  ├── 02_{action_name}_before.png
  ├── 02_{action_name}_after.png
  ...
  └── report.json
```

`action_name` 做文件名校验（替换空格、非法字符为下划线），保持长度 ≤ 40 字符。

---

## 6. 目录结构

```
UI_Agent/
├── agent/
│   ├── adb_controller.py          # ADB 设备控制（保留）
│   ├── path_locator.py       [新]  # Path 语法解析 + XML 树匹配
│   ├── actions.py             [新]  # Action 基类 + Tap/Input/Wait/Scroll/Custom
│   ├── flow_runner.py         [新]  # Flow 执行引擎
│   ├── reporter.py            [新]  # 报告数据类 + JSON 序列化
│   ├── test_runner.py              # 保留，包装旧 Scenario 为临时 Flow
│   ├── ai_verifier.py              # 保留（未来 visual 断言用）
│   ├── web_controller.py           # 保留
│   └── config.py                   # 保留
│
├── scenarios/
│   ├── base.py                     # Flow / Action 数据类（移到这里）
│   ├── registry.py                 # 保留，注册对象改为 Flow
│   ├── app/
│   │   ├── lexiang_borrow_home.py  # 用新 Flow 重写
│   │   └── lexiang_withdraw.py     # 用新 Flow 重写
│   └── web/                        # 不动
│
├── server.py                       # 最小改动（FlowRunner 替代 TestRunner）
├── static/                         # 不动
├── tools/                          # 保留 run_test.py / show_report.py
├── requirements.txt                # 不新增依赖
```

---

## 7. 向后兼容策略

### 7.1 旧 Scenario 兼容

`Scenario` / `StepDef` 不删除，`TestRunner` 内部增加包装方法：

```python
# test_runner.py 新增
def _wrap_legacy_scenario(self, scenario: Scenario) -> Flow:
    """将旧 Scenario 包装为临时 Flow：
       - setup = []
       - steps = 每个 StepDef 包装为 CustomAction
       - teardown = []
    """
```

### 7.2 server.py 改动

```python
# 原代码
runner = TestRunner(test_name=scenario_id, controller=controller)
step_defs = scenario.steps
for sd in step_defs:
    result = runner.step(name=sd.name, action=sd.action, ...)

# 新代码（新增分支）
if hasattr(scenario, 'flow'):  # 新 Flow
    flow_runner = FlowRunner(controller, scenario.flow, screenshot_dir)
    report = flow_runner.run()
else:  # 旧 Scenario，走包装路径
    flow = _wrap_legacy_scenario(scenario)
    flow_runner = FlowRunner(controller, flow, screenshot_dir)
    report = flow_runner.run()
```

### 7.3 WebSocket 推送

消息格式不变（`step_starting` / `step_completed` / `run_completed`），前端无感切换。

---

## 8. 边界与非目标

### 8.1 第一版明确包含

- Android APP 自动化测试（ADB 驱动）
- Path 定位器（类 CSS 语法 + XML 树匹配）
- 四种 Action 类型：Tap / Input / Wait / Scroll
- Flow 三段式编排：setup → steps → teardown
- Fail-Fast + Action 级 Retry
- before/after 截图 + 结构化 JSON 报告
- Web Dashboard（只做展示，不做编辑）
- 旧 Scenario 向后兼容

### 8.2 第一版明确不包含

- 视觉核对系统（AI Verifier 保留代码但不用）
- Web 端测试（Playwright）的改造
- YAML/JSON DSL（直接用 Python 定义 Flow）
- 元素拾取器 / Record & Replay
- 批量/定时执行
- 并行测试
- 页面对象模型（Page Object）— 后续版本考虑

---

## 9. 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| `uiautomator dump` 在 WebView 中元素信息不完整 | Path 语法支持 resource-id + text 多种定位方式组合 |
| XML dump 在低端设备上慢（>2s） | 同一操作内缓存 dump 结果，仅在 retry 时重新 dump |
| 路径语法表达能力不足以覆盖所有场景 | 预留 `CustomAction` 逃生舱，极端情况退回函数式 |
| 重写现有场景工作量大 | 保留旧 Scenario 兼容层，新场景逐步迁移 |

---

## 10. 验收标准

1. **新 Flow 可运行**：用新 `Flow` + `Action` 定义"乐享借主页验证"，通过 `FlowRunner.run()` 执行通过
2. **旧场景不受影响**：通过 Web Dashboard 运行现有 `lexiang_borrow_home` 场景，结果与重构前一致
3. **Path 定位准确**：`PathLocator.find(adb, '*[text="确认借款"]')` 能正确定位到元素
4. **Retry 生效**：模拟元素延迟出现（如等待 3s 后才出现），Action 自动重试并成功
5. **报告完整**：执行完成后 `reports/` 目录生成 JSON 报告，含每步的 before/after 截图路径
6. **dashboard 正常**：Web Dashboard 能列出场景、触发执行、实时查看进度
