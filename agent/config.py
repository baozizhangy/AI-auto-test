"""配置管理：从环境变量 / .env 文件加载配置。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


class Config:
    """全局配置，优先读环境变量，其次使用默认值。"""

    # Anthropic
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # ADB (APP)
    ADB_PATH = os.getenv("ADB_PATH", "adb")
    ANDROID_SERIAL = os.getenv("ANDROID_SERIAL", "emulator-5554")

    # 测试APP
    APP_PACKAGE = os.getenv("APP_PACKAGE", "")
    APP_LAUNCH_ACTIVITY = os.getenv("APP_LAUNCH_ACTIVITY", "")
    APP_NAME = os.getenv("APP_NAME", "")

    # Playwright (WEB)
    BROWSER_TYPE = os.getenv("BROWSER_TYPE", "chromium")  # chromium / firefox / webkit
    BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
    DEFAULT_BASE_URL = os.getenv("DEFAULT_BASE_URL", "https://www.example.com")

    # 路径
    BASELINE_DIR = Path(os.getenv("BASELINE_DIR", str(BASE_DIR / "baselines")))
    SCREENSHOT_DIR = Path(os.getenv("SCREENSHOT_DIR", str(BASE_DIR / "screenshots")))
    REPORT_DIR = Path(os.getenv("REPORT_DIR", str(BASE_DIR / "reports")))

    # MySQL（测试过程中查询/清理数据）
    MYSQL_HOST = os.getenv("MYSQL_HOST", "47.103.56.190")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "50103"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "K41MOcbbSe#U")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "")
    MYSQL_CHARSET = os.getenv("MYSQL_CHARSET", "utf8mb4")

    @classmethod
    def validate(cls) -> None:
        """校验必要配置是否存在。"""
        if not cls.ANTHROPIC_API_KEY:
            raise ValueError("请设置 ANTHROPIC_API_KEY 环境变量，或在 .env 文件中填写密钥")
