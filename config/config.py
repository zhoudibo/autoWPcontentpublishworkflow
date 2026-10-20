#!/usr/bin/env python3
"""
config.py - 统一配置加载器（从 .env 或环境变量读取）

用法：
    from config import WP_SITE, WP_AUTH, MINIMAX_CONFIG, QUALITY_CONFIG
    
所有敏感信息（用户名/密码/API Key）均从环境变量读取，
绝不硬编码在代码中。
"""

import os
import sys
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"


def _load_dotenv():
    """加载 .env 文件到 os.environ（不覆盖已存在的环境变量）"""
    env_file = CONFIG_DIR / ".env"
    if not env_file.exists():
        return False
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 保留 os.environ 中已设置的值（优先级更高）
            if key and key not in os.environ:
                os.environ[key] = value
    return True


_load_dotenv()


def _require(key, default=None, description=""):
    """读取必需的环境变量"""
    value = os.environ.get(key, default)
    if value is None or value == "":
        if default is None:
            print(f"[配置错误] 缺少环境变量: {key}")
            print(f"  说明: {description}")
            print(f"  请在 config/.env 中设置，或 export {key}=...")
            sys.exit(1)
    return value


# ============================================================
# WordPress 配置
# ============================================================
WP_SITE_DOMAIN = _require(
    "WP_SITE_DOMAIN",
    description="WordPress 站点域名（不含 https://）"
)
WP_USERNAME = _require(
    "WP_USERNAME",
    description="WordPress 管理员用户名"
)
WP_APP_PASSWORD = _require(
    "WP_APP_PASSWORD",
    description="WordPress Application Password"
)
WP_SCHEME = os.environ.get("WP_SCHEME", "https")
WP_BASE_URL = f"{WP_SCHEME}://{WP_SITE_DOMAIN}"
WP_API_BASE = f"{WP_BASE_URL}/wp-json/wp/v2"

# Basic Auth 头（运行时生成，不落盘）
import base64
WP_AUTH = base64.b64encode(
    f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()
).decode()


# ============================================================
# AI 内容生成配置
# ============================================================
MINIMAX_API_KEY = _require(
    "MINIMAX_API_KEY",
    description="MiniMax API Key"
)
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M2.7")
MINIMAX_API_BASE = os.environ.get(
    "MINIMAX_API_BASE", "https://api.minimaxi.com/v1"
)
MINIMAX_API_URL = f"{MINIMAX_API_BASE}/chat/completions"


# ============================================================
# 内容质量配置
# ============================================================
QUALITY_CONFIG = {
    "MIN_WORDS": int(os.environ.get("MIN_WORDS", "1500")),
    "TARGET_WORDS_MIN": int(os.environ.get("TARGET_WORDS_MIN", "1600")),
    "TARGET_WORDS_MAX": int(os.environ.get("TARGET_WORDS_MAX", "1800")),
    "MAX_TOKENS_CONTENT": int(os.environ.get("MAX_TOKENS_CONTENT", "6144")),
    "MAX_TOKENS_H2_REWRITE": int(os.environ.get("MAX_TOKENS_H2_REWRITE", "3000")),
}

# 内部链接配置
LINK_CONFIG = {
    "MIN_INTERNAL_LINKS": int(os.environ.get("MIN_INTERNAL_LINKS", "3")),
    "RECOMMENDED_INTERNAL_LINKS": int(
        os.environ.get("RECOMMENDED_INTERNAL_LINKS", "5")
    ),
}

# 站点信息
SITE_NAME = os.environ.get("SITE_NAME", "Your AI Tools Site")


# ============================================================
# 模板 H2 黑名单（触发重写）
# ============================================================
TEMPLATE_H2_BANNED = [
    "introduction",
    "conclusion",
    "summary",
    "overview",
    "key features",
    "pros and cons",
    "what is",
    "how to choose",
    "getting started",
    "the best",
    "top picks",
    "benefits",
    "drawbacks",
    "faq",
    "review",
    "verdict",
    "features",
]


if __name__ == "__main__":
    print("=" * 55)
    print("  AutoWPContentPublishWorkflow - 配置检查")
    print("=" * 55)
    print(f"  站点: {WP_BASE_URL}")
    print(f"  用户: {WP_USERNAME}")
    print(f"  密码: {'*' * len(WP_APP_PASSWORD)} (已加载)")
    print(f"  模型: {MINIMAX_MODEL}")
    print(f"  API:  {MINIMAX_API_BASE}")
    print(f"  词数: 最小 {QUALITY_CONFIG['MIN_WORDS']} / "
          f"目标 {QUALITY_CONFIG['TARGET_WORDS_MIN']}-{QUALITY_CONFIG['TARGET_WORDS_MAX']}")
    print(f"  内链: 最少 {LINK_CONFIG['MIN_INTERNAL_LINKS']} / "
          f"推荐 {LINK_CONFIG['RECOMMENDED_INTERNAL_LINKS']}")
    print("=" * 55)
    print("\n✅ 配置检查通过")
