"""
Garmin Connect 操作モジュール
- ログイン・アクティビティ取得
- 安静時心拍取得
- 過去N日の期間フィルタ
"""
from garminconnect import Garmin
import time
import datetime

from config import (
    GARMIN_EMAIL,
    GARMIN_PASSWORD,
    TARGET_ACTIVITY_TYPES,
    BATCH_SIZE,
    DEFAULT_RESTING_HR,
    LOOKBACK_DAYS,
)


def create_client() -> Garmin:
    """Garmin Connect にログインしてクライアントを返す"""
    print("🔐 Garmin Connect にログイン中...")
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    print("✅ ログイン成功！")
    return client


def get_lookback_date() -> str:
    """LOOKBACK_DAYS 日前の日付文字列 (YYYY-MM-DD) を返す"""
    dt = datetime.datetime.now() - datetime.timedelta(days=LOOKBACK_DAYS)
    return dt.strftime("%Y-%m-%d")


def fetch_recent_activities(client: Garmin) -> list[dict]:
    """
    過去 LOOKBACK_DAYS 日分のランニングアクティビティを取得。
    Garmin APIは日付範囲指定がないため、バッチ取得して日付フィルタする。
    """
    cutoff_date = get_lookback_date()
    print(f"\n📥 過去{LOOKBACK_DAYS}日分のアクティビティを取得中 (>= {cutoff_date})...")

    all_activities = []
    start = 0

    while True:
        batch = client.get_activities(start, BATCH_SIZE)
        if not batch:
            break

        # 日付とタイプでフィルタ
        for a in batch:
            activity_date = a.get("startTimeLocal", "")[:10]
            type_key = a.get("activityType", {}).get("typeKey", "")

            # cutoff_date より古いアクティビティが出てきたら終了
            if activity_date < cutoff_date:
                print(f"  → {cutoff_date} 以前のデータに到達。取得終了。")
                return all_activities

            if type_key in TARGET_ACTIVITY_TYPES:
                all_activities.append(a)

        print(f"  取得: {start}〜{start + len(batch)} "
              f"(ランニング累計: {len(all_activities)}件)")

        if len(batch) < BATCH_SIZE:
            break
        start += BATCH_SIZE
        time.sleep(1)

    print(f"📊 過去{LOOKBACK_DAYS}日のランニング: {len(all_activities)}件")
    return all_activities


def get_resting_hr(client: Garmin, date_str: str) -> int:
    """Garmin Connectからその日の安静時心拍を取得"""
    try:
        rhr_data = client.get_resting_heart_rate(date_str)
        if isinstance(rhr_data, dict):
            rhr = rhr_data.get("restingHeartRate")
            if rhr is None:
                stats = rhr_data.get("restingHeartRateValues", {})
                rhr = stats.get("restingHeartRate")
            if rhr and rhr > 0:
                return int(rhr)
        time.sleep(0.3)
    except Exception:
        pass
    return DEFAULT_RESTING_HR


def get_activity_laps(client: Garmin, activity_id) -> list[dict]:
    """アクティビティのラップデータを取得"""
    try:
        splits = client.get_activity_splits(activity_id)
        time.sleep(0.5)
        return splits.get("lapDTOs", [])
    except Exception as e:
        print(f"  ⚠️ ラップデータ取得失敗: {e}")
        return []
