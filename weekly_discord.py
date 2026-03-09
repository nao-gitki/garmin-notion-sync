"""
週次 Discord レポートモジュール
毎週日曜 21:00 JST に実行。
Notion DB から月～日のアクティビティを取得し、AI サマリーを Discord に投稿する。
"""
import datetime
import time
import requests

from config import (
    NOTION_HEADERS, NOTION_RATE_LIMIT_WAIT, ACTIVITIES_DB_ID,
    DISCORD_WEBHOOK_URL, VDOT, MAX_HR, COACHING_MEMORY_PATH,
)
from notion_client import format_duration
from coach import load_coaching_memory_for_prompt, call_openrouter
from gcs_storage import download_memory


# =====================================================================
# 日付範囲
# =====================================================================

def get_week_range(target_date=None):
    """今週の月曜～日曜の日付範囲を返す"""
    if target_date is None:
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
        target_date = now.date()
    monday = target_date - datetime.timedelta(days=target_date.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


# =====================================================================
# Notion からアクティビティ取得
# =====================================================================

def fetch_week_activities(start_date, end_date):
    """指定期間のアクティビティを Notion DB から取得"""
    activities = []
    has_more = True
    start_cursor = None
    payload_base = {
        "filter": {"and": [
            {"property": "Date", "date": {"on_or_after": start_date}},
            {"property": "Date", "date": {"on_or_before": end_date}},
        ]},
        "sorts": [{"property": "Date", "direction": "ascending"}],
        "page_size": 100,
    }
    while has_more:
        payload = dict(payload_base)
        if start_cursor:
            payload["start_cursor"] = start_cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{ACTIVITIES_DB_ID}/query",
            headers=NOTION_HEADERS, json=payload,
        )
        time.sleep(NOTION_RATE_LIMIT_WAIT)
        if resp.status_code != 200:
            print(f"  Notion API error: {resp.status_code}")
            break
        data = resp.json()
        for page in data.get("results", []):
            activities.append(
                _extract_activity_from_page(page.get("properties", {}))
            )
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
    return activities


# =====================================================================
# Notion プロパティヘルパー
# =====================================================================

def _get_prop_text(props, key):
    prop = props.get(key, {})
    items = prop.get("title") or prop.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in items)


def _get_prop_number(props, key):
    return props.get(key, {}).get("number")


def _get_prop_select(props, key):
    sel = props.get(key, {}).get("select")
    return sel.get("name", "") if sel else ""


def _get_prop_date(props, key):
    date_obj = props.get(key, {}).get("date")
    return date_obj.get("start", "") if date_obj else ""


def _extract_activity_from_page(props):
    return {
        "name": _get_prop_text(props, "Activity Name"),
        "date": _get_prop_date(props, "Date"),
        "activity_type": _get_prop_select(props, "Activity Type"),
        "distance_km": _get_prop_number(props, "Distance km") or 0,
        "duration_sec": _get_prop_number(props, "Duration sec") or 0,
        "trimp": _get_prop_number(props, "TRIMP") or 0,
        "intensity": _get_prop_select(props, "Intensity"),
        "pace_zone": _get_prop_select(props, "Pace Zone"),
        "avg_hr": _get_prop_number(props, "Avg HR"),
        "avg_pace_str": _get_prop_text(props, "Avg Pace"),
    }


# =====================================================================
# 統計計算
# =====================================================================

def calc_weekly_stats(activities):
    """週間の統計を計算"""
    if not activities:
        return {
            "total_distance": 0, "total_trimp": 0, "run_days": 0,
            "activity_count": 0, "intensity_dist": {}, "avg_hr_list": [],
            "fatigue_score": 0, "recovery_score": 0,
            "high_intensity_days": 0, "easy_days": 0,
        }

    total_distance = sum(a["distance_km"] for a in activities)
    total_trimp = sum(a["trimp"] for a in activities)
    dates = set(a["date"] for a in activities)

    intensity_dist = {}
    avg_hr_list = []
    high_intensity_days = 0
    easy_days = 0

    for a in activities:
        if a["intensity"]:
            intensity_dist[a["intensity"]] = (
                intensity_dist.get(a["intensity"], 0) + 1
            )
        if a["avg_hr"]:
            avg_hr_list.append(a["avg_hr"])
        if a["intensity"] in ("Hard", "Race"):
            high_intensity_days += 1
        if a["intensity"] in ("Recovery", "Easy"):
            easy_days += 1

    fatigue_score = min(100, int(total_trimp / 5 + high_intensity_days * 10))
    recovery_score = min(
        100, int(easy_days * 20 + max(0, (7 - len(dates)) * 10))
    )

    return {
        "total_distance": round(total_distance, 1),
        "total_trimp": round(total_trimp, 1),
        "run_days": len(dates),
        "activity_count": len(activities),
        "intensity_dist": intensity_dist,
        "avg_hr_list": avg_hr_list,
        "fatigue_score": fatigue_score,
        "recovery_score": recovery_score,
        "high_intensity_days": high_intensity_days,
        "easy_days": easy_days,
    }


# =====================================================================
# テキストサマリー構築（AI プロンプト用）
# =====================================================================

def build_weekly_summary_text(activities, stats, start_date, end_date):
    """AI に渡すためのテキストサマリーを構築"""
    lines = [
        f"## Weekly Running Report ({start_date} - {end_date})",
        "",
        f"- Run days: {stats['run_days']} / Activities: {stats['activity_count']}",
        f"- Total distance: {stats['total_distance']} km",
        f"- Total TRIMP: {stats['total_trimp']}",
        f"- Fatigue score: {stats['fatigue_score']}/100",
        f"- Recovery score: {stats['recovery_score']}/100",
    ]

    if stats["intensity_dist"]:
        dist_str = ", ".join(
            f"{k}: {v}" for k, v in sorted(stats["intensity_dist"].items())
        )
        lines.append(f"- Intensity distribution: {dist_str}")

    if stats["avg_hr_list"]:
        avg_hr_mean = round(
            sum(stats["avg_hr_list"]) / len(stats["avg_hr_list"])
        )
        lines.append(f"- Average HR (overall): {avg_hr_mean} bpm")

    lines.append("")
    lines.append("### Activities")

    for a in activities:
        dur = format_duration(a["duration_sec"])
        dist = round(a["distance_km"], 2)
        parts = [
            f"{a['date']}",
            a["name"],
            f"{dist}km / {dur}",
            f"TRIMP {a['trimp']} ({a['intensity']})",
        ]
        if a["avg_hr"]:
            parts.append(f"HR {a['avg_hr']}bpm")
        if a["avg_pace_str"]:
            parts.append(f"Pace {a['avg_pace_str']}")
        if a["pace_zone"]:
            parts.append(a["pace_zone"])
        lines.append("- " + " | ".join(parts))

    return "\n".join(lines)


# =====================================================================
# AI サマリー生成
# =====================================================================

def generate_ai_summary(summary_text):
    """OpenRouter を使って AI による週間分析を生成"""
    coaching_memory = load_coaching_memory_for_prompt()

    system_prompt = (
        "あなたはランニングコーチです。以下の選手プロフィールを踏まえ、"
        "週間レポートを分析してください。\n"
        f"- VDOT: {VDOT}\n"
        f"- 最大心拍: {MAX_HR}\n\n"
        f"{coaching_memory}\n\n"
        "Discord に投稿するため、以下のフォーマットで日本語出力してください"
        "（Markdown 対応）:\n"
        "1. **今週のサマリー**: 2-3文で概要\n"
        "2. **良かった点**: 箇条書き1-2個\n"
        "3. **改善ポイント**: 箇条書き1-2個\n"
        "4. **来週へのアドバイス**: 1-2文\n\n"
        "簡潔に。絵文字は適度にOK。全体で300文字以内を目安に。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": summary_text},
    ]

    return call_openrouter(messages)


# =====================================================================
# Discord 投稿
# =====================================================================

def send_discord_message(content):
    """Discord Webhook にメッセージを送信"""
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL が未設定です")
        return False

    # Discord のメッセージ上限は 2000 文字
    if len(content) > 2000:
        content = content[:1997] + "..."

    payload = {"content": content}
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)

    if resp.status_code in (200, 204):
        print("Discord に投稿しました")
        return True
    else:
        print(f"Discord 投稿失敗: {resp.status_code} {resp.text}")
        return False


# =====================================================================
# メイン
# =====================================================================

def main():
    print("週次 Discord レポート生成開始")
    print("=" * 50)

    # GCS から coaching_memory.md をダウンロード
    print("GCS から coaching_memory.md をダウンロード中...")
    download_memory(COACHING_MEMORY_PATH)

    # 今週の日付範囲を取得
    start_date, end_date = get_week_range()
    print(f"対象期間: {start_date} - {end_date}")

    # Notion からアクティビティ取得
    print("Notion からアクティビティを取得中...")
    activities = fetch_week_activities(start_date, end_date)
    print(f"  取得件数: {len(activities)}件")

    if not activities:
        msg = (
            f"**週間レポート ({start_date} - {end_date})**\n\n"
            "今週のランニング記録はありませんでした。来週は走りましょう！"
        )
        send_discord_message(msg)
        return

    # 統計計算
    stats = calc_weekly_stats(activities)
    print(f"  総距離: {stats['total_distance']}km | 日数: {stats['run_days']}日")

    # テキストサマリー構築
    summary_text = build_weekly_summary_text(
        activities, stats, start_date, end_date
    )

    # AI サマリー生成
    print("AI サマリー生成中...")
    ai_summary = generate_ai_summary(summary_text)

    # Discord メッセージ構築
    header = f"**週間ランニングレポート ({start_date} - {end_date})**"
    stats_line = (
        f"{stats['total_distance']}km | "
        f"{stats['run_days']}日 | "
        f"TRIMP {stats['total_trimp']} | "
        f"疲労 {stats['fatigue_score']}/100 | "
        f"回復 {stats['recovery_score']}/100"
    )

    if ai_summary:
        discord_msg = f"{header}\n{stats_line}\n\n{ai_summary}"
    else:
        discord_msg = (
            f"{header}\n{stats_line}\n\n"
            "AI サマリーの生成に失敗しました。"
        )

    # Discord に投稿
    print("Discord に投稿中...")
    send_discord_message(discord_msg)

    print("週次レポート完了")


if __name__ == "__main__":
    main()
