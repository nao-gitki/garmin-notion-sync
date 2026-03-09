"""
設定値・個人パラメータ
"""
import os

# ===================================================================
# 環境変数から取得
# ===================================================================
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ===================================================================
# Notion設定
# ===================================================================
ACTIVITIES_DB_ID = "71b5e43ff5034faf925436f2590e757f"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

NOTION_RATE_LIMIT_WAIT = 0.35

# ===================================================================
# Garmin設定
# ===================================================================
TARGET_ACTIVITY_TYPES = [
    "running",
    "trail_running",
    "treadmill_running",
    "track_running",
]

ACTIVITY_TYPE_MAP = {
    "running": "Running",
    "trail_running": "Trail Running",
    "treadmill_running": "Treadmill Running",
    "track_running": "Track Running",
}

BATCH_SIZE = 50

# ===================================================================
# 同期設定
# ===================================================================
LOOKBACK_DAYS = 5  # 過去何日分を確認するか

# ===================================================================
# 個人パラメータ（★ ここを自分の値に変更 ★）
# ===================================================================
MAX_HR = 205
DEFAULT_RESTING_HR = 57
VDOT = 55

# ===================================================================
# ダニエルズ式ペースゾーン（VDOT 55 基準、秒/km）
# ===================================================================
PACE_ZONES = {
    "R (Repetition)": 196,
    "I (Interval)": 212,
    "T (Threshold)": 226,
    "M (Marathon)": 255,
    "E (Easy)": 320,
    "Recovery": 9999,
}

TRIMP_THRESHOLDS = {
    "Recovery": 25,
    "Easy": 75,
    "Moderate": 150,
    "Hard": 300,
    "Race": 9999,
}

# ===================================================================
# OpenRouter / LLMコーチング設定
# ===================================================================
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
COACH_MODEL = os.environ.get("COACH_MODEL", "x-ai/grok-4.1-fast")

# coaching_memory.md のパス（リポジトリルートからの相対パス）
COACHING_MEMORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "coaching_memory.md"
)

# ===================================================================
# Google Cloud 設定
# ===================================================================
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "run-log-automation-2026-memory")

# ===================================================================
# Discord 設定
# ===================================================================
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
