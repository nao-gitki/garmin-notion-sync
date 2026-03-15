import os
content = '''\
"""
週次 Discord レポートモジュール - 毎週日曜 21:00 JST に実行
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


def get_week_range(target_date=None):
    if target_date is None:
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
        target_date = now.date()
    monday = target_date - datetime.timedelta(days=target_date.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def fetch_week_activities(start_date, end_date):
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
            break
        data = resp.json()
        for page in data.get("results", []):
            activities.append(_extract_activity_from_page(page.get("properties", {})))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
    return activities


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
    date = props.get(key, {}).get("date")
    return date.get("start", "") if date else ""

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


def calc_weekly_stats(activities):
    if not activities:
        return {"total_distance": 0, "total_trimp": 0, "run_days": 0,
                "activity_count": 0, "intensity_dist": {}, "avg_hr_list": [],
                "fatigue_score": 0, "recovery_score": 0,
                "high_intensity_days": 0, "easy_days": 0}
    total_distance = sum(a["distance_km"] for a in activities)
    total_trimp = sum(a["trimp"] for a in activities)
    dates = set(a["date"] for a in activities)
    intensity_dist, avg_hr_list = {}, []
    high_intensity_days = easy_days = 0
    for a in activities:
        if a["intensity"]:
            intensity_dist[a["intensity"]] = intensity_dist.get(a["intensity"], 0) + 1
        if a["avg_hr"]:
            avg_hr_list.append(a["avg_hr"])
        if a["intensity"] in ("Hard", "Race"):
            high_intensity_days += 1
        if a["intensity"] in ("Recovery", "Easy"):
            easy_days += 1
    fatigue_score = min(100, int(total_trimp / 5 + high_intensity_days * 10))
    recovery_score = min(100, int(easy_days * 20 + max(0, (7 - len(dates)) * 10)))
    return {
        "total_distance": round(total_distance, 1), "total_trimp": round(total_trimp, 1),
        "run_days": len(dates), "activity_count": len(activities),
        "intensity_dist": intensity_dist, "avg_hr_list": avg_hr_list,
        "fatigue_score": fatigue_score, "recovery_score": recovery_score,
        "high_intensity_days": high_intensity_days, "easy_days": easy_days,
    }
'''
with open("/tmp/garmin-gcp/weekly_discord.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Part 1 written OK")
