"""
Notion API 操作モジュール
- ページ作成・ブロック追加
- 既存アクティビティID照会
- TRIMP計算・分類・ラップテーブル構築
"""
import requests
import time
import math

from config import (
    NOTION_HEADERS,
    NOTION_RATE_LIMIT_WAIT,
    ACTIVITIES_DB_ID,
    ACTIVITY_TYPE_MAP,
    MAX_HR,
    PACE_ZONES,
    TRIMP_THRESHOLDS,
)


# ===================================================================
# 計算関数
# ===================================================================

def calc_trimp(avg_hr: float | None, duration_sec: float, resting_hr: int) -> float:
    """Banister式 TRIMP"""
    if avg_hr is None or avg_hr <= resting_hr:
        return 0.0
    duration_min = duration_sec / 60
    delta_hr = (avg_hr - resting_hr) / (MAX_HR - resting_hr)
    delta_hr = max(0, min(delta_hr, 1.0))
    trimp = duration_min * delta_hr * 0.64 * math.exp(1.92 * delta_hr)
    return round(trimp, 1)


def classify_intensity(trimp: float) -> str:
    for intensity, threshold in TRIMP_THRESHOLDS.items():
        if trimp <= threshold:
            return intensity
    return "Race"


def classify_pace_zone(avg_pace_sec_per_km: float | None) -> str:
    if avg_pace_sec_per_km is None or avg_pace_sec_per_km <= 0:
        return "Recovery"
    for zone_name, upper_limit in PACE_ZONES.items():
        if avg_pace_sec_per_km <= upper_limit:
            return zone_name
    return "Recovery"


# ===================================================================
# フォーマット関数
# ===================================================================

def format_duration(seconds: float) -> str:
    if seconds is None or seconds == 0:
        return "0:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}"


def format_lap_duration(seconds: float) -> str:
    if seconds is None or seconds == 0:
        return "0:00.0"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}:{s:04.1f}"


def format_pace(seconds_per_km: float) -> str:
    if seconds_per_km is None or seconds_per_km <= 0:
        return "0:00"
    m = int(seconds_per_km // 60)
    s = int(seconds_per_km % 60)
    return f"{m}:{s:02d}"


def calc_pace_str(duration_s: float, distance_km: float) -> str:
    if distance_km is None or distance_km <= 0:
        return "0:00"
    return format_pace(duration_s / distance_km)


def safe_round(value, digits=1):
    if value is None:
        return None
    return round(value, digits)


def val_or_dash(value, digits=0) -> str:
    if value is None:
        return "-"
    if digits == 0:
        return str(int(round(value)))
    return str(round(value, digits))


# ===================================================================
# Notion API 操作
# ===================================================================

def _notion_post(url: str, payload: dict) -> dict | None:
    """Notion APIへのPOST (レート制限対応)"""
    resp = requests.post(url, headers=NOTION_HEADERS, json=payload)
    time.sleep(NOTION_RATE_LIMIT_WAIT)

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        print(f"  ⏳ レート制限。{retry_after}秒待機...")
        time.sleep(retry_after)
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload)
        time.sleep(NOTION_RATE_LIMIT_WAIT)

    if resp.status_code != 200:
        print(f"  ❌ Notion API エラー: {resp.status_code} - {resp.text[:200]}")
        return None
    return resp.json()


def _notion_patch(url: str, payload: dict) -> dict | None:
    """Notion APIへのPATCH (レート制限対応)"""
    resp = requests.patch(url, headers=NOTION_HEADERS, json=payload)
    time.sleep(NOTION_RATE_LIMIT_WAIT)

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        print(f"  ⏳ レート制限。{retry_after}秒待機...")
        time.sleep(retry_after)
        resp = requests.patch(url, headers=NOTION_HEADERS, json=payload)
        time.sleep(NOTION_RATE_LIMIT_WAIT)

    if resp.status_code != 200:
        print(f"  ⚠️ Notion PATCH エラー: {resp.status_code} - {resp.text[:200]}")
        return None
    return resp.json()


def notion_create_page(database_id: str, properties: dict) -> dict | None:
    """Notion DBにページを作成"""
    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    return _notion_post("https://api.notion.com/v1/pages", payload)


def notion_append_blocks(page_id: str, blocks: list) -> dict | None:
    """ページにブロック（テーブルなど）を追加"""
    payload = {"children": blocks}
    return _notion_patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children", payload
    )


def query_existing_activity_ids(database_id: str) -> set[str]:
    """既にNotionに登録済みのActivity IDセットを取得"""
    existing_ids = set()
    has_more = True
    start_cursor = None

    print("📋 既存データの確認中...")

    while has_more:
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        resp = requests.post(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers=NOTION_HEADERS,
            json=payload,
        )
        time.sleep(NOTION_RATE_LIMIT_WAIT)

        if resp.status_code != 200:
            print(f"  ⚠️ クエリエラー: {resp.status_code}")
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            aid_prop = props.get("Activity ID", {})
            rich_texts = aid_prop.get("rich_text", [])
            if rich_texts:
                existing_ids.add(rich_texts[0].get("plain_text", ""))

        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    print(f"  → 既存アクティビティ: {len(existing_ids)}件")
    return existing_ids


# ===================================================================
# ラップテーブル構築
# ===================================================================

def _build_text_cell(text: str) -> list:
    return [{"type": "text", "text": {"content": str(text)}}]


def build_lap_table_blocks(laps: list) -> list:
    """ラップデータからNotionテーブルブロックを構築"""
    valid_laps = [(i, lap) for i, lap in enumerate(laps) if lap.get("distance", 0) > 0]
    if not valid_laps:
        return []

    heading_block = {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": "🏃 Lap Summary"}}]
        },
    }

    headers = [
        "Lap", "距離(km)", "タイム", "ペース(/km)",
        "平均心拍", "最大心拍", "ピッチ", "ストライド(cm)",
    ]
    header_row = {
        "type": "table_row",
        "table_row": {"cells": [_build_text_cell(h) for h in headers]},
    }

    data_rows = []
    for i, lap in valid_laps:
        distance_km = lap.get("distance", 0) / 1000
        duration_s = lap.get("duration", 0)
        lap_index = lap.get("lapIndex", i + 1)
        row = {
            "type": "table_row",
            "table_row": {
                "cells": [
                    _build_text_cell(str(lap_index)),
                    _build_text_cell(str(safe_round(distance_km, 2))),
                    _build_text_cell(format_lap_duration(duration_s)),
                    _build_text_cell(calc_pace_str(duration_s, distance_km)),
                    _build_text_cell(val_or_dash(lap.get("averageHR"))),
                    _build_text_cell(val_or_dash(lap.get("maxHR"))),
                    _build_text_cell(val_or_dash(lap.get("averageRunCadence"))),
                    _build_text_cell(val_or_dash(lap.get("strideLength"), 1)),
                ]
            },
        }
        data_rows.append(row)

    table_block = {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(headers),
            "has_column_header": True,
            "has_row_header": False,
            "children": [header_row] + data_rows,
        },
    }

    blocks = [heading_block, table_block]

    # Running Dynamics（データがある場合のみ）
    has_dynamics = any(
        lap.get("averagePower") is not None
        or lap.get("averageGroundContactTime") is not None
        or lap.get("averageVerticalOscillation") is not None
        for _, lap in valid_laps
    )

    if has_dynamics:
        dyn_heading = {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [
                    {"type": "text", "text": {"content": "⚡ Running Dynamics"}}
                ]
            },
        }
        dyn_headers = [
            "Lap", "パワー(W)", "接地時間(ms)", "上下動(cm)",
            "上昇(m)", "下降(m)", "気温(℃)",
        ]
        dyn_header_row = {
            "type": "table_row",
            "table_row": {"cells": [_build_text_cell(h) for h in dyn_headers]},
        }
        dyn_rows = []
        for i, lap in valid_laps:
            lap_index = lap.get("lapIndex", i + 1)
            row = {
                "type": "table_row",
                "table_row": {
                    "cells": [
                        _build_text_cell(str(lap_index)),
                        _build_text_cell(val_or_dash(lap.get("averagePower"))),
                        _build_text_cell(
                            val_or_dash(lap.get("averageGroundContactTime"))
                        ),
                        _build_text_cell(
                            val_or_dash(lap.get("averageVerticalOscillation"), 1)
                        ),
                        _build_text_cell(val_or_dash(lap.get("elevationGain"), 1)),
                        _build_text_cell(val_or_dash(lap.get("elevationLoss"), 1)),
                        _build_text_cell(
                            val_or_dash(lap.get("averageTemperature"), 1)
                        ),
                    ]
                },
            }
            dyn_rows.append(row)

        dyn_table = {
            "object": "block",
            "type": "table",
            "table": {
                "table_width": len(dyn_headers),
                "has_column_header": True,
                "has_row_header": False,
                "children": [dyn_header_row] + dyn_rows,
            },
        }
        blocks.extend([dyn_heading, dyn_table])

    return blocks


# ===================================================================
# プロパティ構築
# ===================================================================

def build_activity_properties(
    activity: dict,
    avg_pace_str: str,
    avg_pace_sec: float,
    duration_str: str,
    trimp: float,
    intensity: str,
    pace_zone: str,
    resting_hr: int,
) -> dict:
    activity_type = ACTIVITY_TYPE_MAP.get(
        activity.get("activityType", {}).get("typeKey", ""), "Other"
    )

    props = {
        "Activity Name": {
            "title": [
                {"text": {"content": activity.get("activityName", "Untitled")}}
            ]
        },
        "Activity ID": {
            "rich_text": [
                {"text": {"content": str(activity.get("activityId", ""))}}
            ]
        },
        "Date": {"date": {"start": activity.get("startTimeLocal", "")[:10]}},
        "Activity Type": {"select": {"name": activity_type}},
        "Distance km": {
            "number": safe_round(activity.get("distance", 0) / 1000, 2)
        },
        "Duration": {"rich_text": [{"text": {"content": duration_str}}]},
        "Duration sec": {
            "number": safe_round(activity.get("duration", 0), 1)
        },
        "Avg Pace": {"rich_text": [{"text": {"content": avg_pace_str}}]},
        "TRIMP": {"number": trimp},
        "Intensity": {"select": {"name": intensity}},
        "Pace Zone": {"select": {"name": pace_zone}},
        "Resting HR": {"number": resting_hr},
    }

    optional_fields = {
        "Avg HR": ("averageHR", 0),
        "Max HR": ("maxHR", 0),
        "Avg Cadence": ("averageRunningCadenceInStepsPerMinute", 1),
        "Calories": ("calories", 0),
        "Elevation Gain": ("elevationGain", 1),
        "Avg Power": ("avgPower", 1),
        "Training Effect Aerobic": ("aerobicTrainingEffect", 1),
        "Training Effect Anaerobic": ("anaerobicTrainingEffect", 1),
        "VO2 Max": ("vO2MaxValue", 1),
        "Avg Temperature": ("averageTemperature", 1),
    }

    for notion_key, (garmin_key, digits) in optional_fields.items():
        val = activity.get(garmin_key)
        if val is not None:
            props[notion_key] = {"number": safe_round(val, digits)}

    return props
