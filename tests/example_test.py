"""示例测试用例：演示 GUI Agent 的完整测试流程。

⚠ 运行前需要：
1. ADB 连接一台 Android 设备或模拟器
2. 设置 ANTHROPIC_API_KEY 环境变量或 .env 文件
3. 准备基线截图放在 baselines/<test_name>/ 下
"""

from agent import TestRunner


def test_app_main_screen():
    """测试：启动 App 后，主界面 UI 符合预期。"""
    runner = TestRunner(test_name="app_main_screen")

    def go_home(adb):
        adb.press_home()

    def start_app(adb):
        adb.start_app("com.android.settings")

    results = runner.run(
        [
            # (步骤名, 操作函数, 基线截图文件名)
            ("01_back_to_home", go_home, None),
            ("02_start_settings", start_app, "settings_main.png"),
        ]
    )

    # 打印报告
    report = results
    print(f"\n测试: {report.test_name}")
    print(f"通过: {report.passed} | 耗时: {report.total_duration_ms:.0f}ms\n")
    for r in report.steps:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.step_name} | confidence={r.confidence:.2f} | {r.summary}")
        if r.differences:
            for d in r.differences:
                print(f"     → {d}")

    assert len(report.steps) == 2


if __name__ == "__main__":
    test_app_main_screen()
