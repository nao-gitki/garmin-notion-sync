"""
LLMコーチングモジュール
- OpenRouter経由でLLMを呼び出し、トレーニングフィードバックを生成
- coaching_memory.md を読み書きして長期的な一貫性を維持
- Notionのアクティビティページにフィードバックを追記
"""
import requests
import datetime
import os

from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    COACH_MODEL,
    COACHING_MEMORY_PATH,
    VDOT,
    MAX_HR,
)
from notion_client import notion_append_blocks


# ===================================================================
# コーチングメモリー（MDファイル）の読み書き
# ===================================================================

def load_coaching_memory() -> str:
    """coaching_memory.md を読み込む。なければ初期テンプレートを作成"""
    if os.path.exists(COACHING_MEMORY_PATH):
        with open(COACHING_MEMORY_PATH, "r", encoding="utf-8") as f:
            return f.read()

    # 初期テンプレート
    initial = """# 🏃 Running Coach Memory

## アスリートプロフィール
- 目標: サブ2:30（フルマラソン）
- 現在のPB: 2:44:31（2024年）
- VDOT: 55
- 最大心拍数: 205

## コーチング方針
- ダニエルズのランニング・フォーミュラをベースにした科学的アプローチ
- 怪我予防を最優先（月間走行距離の急激な増加を避ける）
- 週単位のメソサイクルでの負荷管理
- ポイント練習（I/T/R）と回復走（E/Recovery）のバランス

## トレーニング履歴サマリー
（ここに週ごとのサマリーが蓄積されていきます）

## 直近のフィードバック履歴
"""
    with open(COACHING_MEMORY_PATH, "w", encoding="utf-8") as f:
        f.write(initial)
    return initial


def append_to_coaching_memory(date_str: str, feedback: str) -> None:
    """coaching_memory.md にフィードバックを追記"""
    memory = load_coaching_memory()

    entry = f"\n### {date_str}\n{feedback}\n"
    memory += entry

    # メモリーが肥大化しすぎないよう、直近のフィードバック履歴を管理
    # （ここでは単純追記。将来的にはサマリー圧縮も検討）
    lines = memory.split("\n")
    if len(lines) > 500:
        # 500行超えたら古いフィードバックを圧縮するフラグを立てる
        print("  ⚠️ coaching_memory.md が500行超え。将来的にサマリー圧縮を検討してください。")

    with open(COACHING_MEMORY_PATH, "w", encoding="utf-8") as f:
        f.write(memory)


# ===================================================================
# OpenRouter API 呼び出し
# ===================================================================

SYSTEM_PROMPT = f"""あなたは経験豊富なマラソンコーチです。
ダニエルズのランニング・フォーミュラに基づき、科学的かつ実践的なアドバイスを提供します。

# アスリート情報
- 目標: サブ2:30（フルマラソン）
- 現在のPB: 2:44:31
- VDOT: {VDOT}
- 最大心拍数: {MAX_HR}

# フィードバックの方針
1. 今日のトレーニングの評価（良い点・改善点）
2. ペースゾーンとTRIMPの妥当性チェック
3. 心拍数データからの疲労・回復度の推察
4. 直近の練習の流れを踏まえた文脈的アドバイス（メモリー参照）
5. 次の練習への具体的な提案

# 出力ルール
- 簡潔に（200〜400字程度）
- 過度な褒めは不要。率直に。
- 怪我リスクがあれば必ず警告
- 日本語で回答
"""


def call_openrouter(messages: list[dict]) -> str | None:
    """OpenRouter API を呼び出してレスポンスを取得"""
    if not OPENROUTER_API_KEY:
        print("  ⚠️ OPENROUTER_API_KEY 未設定。コーチング機能をスキップ。")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/garmin-notion-sync",
    }

    payload = {
        "model": COACH_MODEL,
        "messages": messages,
        "max_tokens": 1000,
        "temperature": 0.7,
    }

    try:
        resp = requests.post(
            OPENROUTER_BASE_URL,
            headers=headers,
            json=payload,
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"  ❌ OpenRouter エラー: {resp.status_code} - {resp.text[:200]}")
            return None

        data = resp.json()
        return data["choices"][0]["message"]["content"]

    except Exception as e:
        print(f"  ❌ OpenRouter 呼び出し失敗: {e}")
        return None


# ===================================================================
# フィードバック生成
# ===================================================================

def build_activity_summary(activity: dict, trimp: float, intensity: str,
                            pace_zone: str, laps: list) -> str:
    """LLMに渡すアクティビティサマリーテキストを構築"""
    distance_km = round(activity.get("distance", 0) / 1000, 2)
    duration_s = activity.get("duration", 0)
    h = int(duration_s // 3600)
    m = int((duration_s % 3600) // 60)
    s = int(duration_s % 60)

    avg_hr = activity.get("averageHR", "-")
    max_hr = activity.get("maxHR", "-")
    cadence = activity.get("averageRunningCadenceInStepsPerMinute", "-")
    ae = activity.get("aerobicTrainingEffect", "-")
    ane = activity.get("anaerobicTrainingEffect", "-")

    summary = f"""## 今日のトレーニングデータ
- 日付: {activity.get("startTimeLocal", "")[:10]}
- 種類: {activity.get("activityType", {}).get("typeKey", "")}
- 距離: {distance_km} km
- タイム: {h}:{m:02d}:{s:02d}
- TRIMP: {trimp} ({intensity})
- ペースゾーン: {pace_zone}
- 平均心拍: {avg_hr} / 最大心拍: {max_hr}
- ピッチ: {cadence}
- 有酸素TE: {ae} / 無酸素TE: {ane}
"""

    # ラップサマリー（最大10ラップ）
    valid_laps = [(i, lap) for i, lap in enumerate(laps) if lap.get("distance", 0) > 0]
    if valid_laps:
        summary += "\n### ラップデータ\n"
        for i, lap in valid_laps[:10]:
            dist = round(lap.get("distance", 0) / 1000, 2)
            dur = lap.get("duration", 0)
            pace_s = dur / dist if dist > 0 else 0
            pace_m = int(pace_s // 60)
            pace_sec = int(pace_s % 60)
            hr = lap.get("averageHR", "-")
            summary += f"- Lap {lap.get('lapIndex', i+1)}: {dist}km {pace_m}:{pace_sec:02d}/km HR:{hr}\n"

        if len(valid_laps) > 10:
            summary += f"- ... 他 {len(valid_laps) - 10} ラップ\n"

    return summary


def generate_coaching_feedback(
    activity: dict,
    trimp: float,
    intensity: str,
    pace_zone: str,
    laps: list,
) -> str | None:
    """
    1つのアクティビティに対してLLMコーチングフィードバックを生成。
    coaching_memory.md の内容をコンテキストとして渡す。
    """
    # メモリー読み込み
    memory = load_coaching_memory()

    # アクティビティサマリー構築
    activity_summary = build_activity_summary(
        activity, trimp, intensity, pace_zone, laps
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"# コーチングメモリー（これまでの練習記録と方針）\n"
                f"{memory}\n\n"
                f"---\n\n"
                f"{activity_summary}\n\n"
                f"上記データに基づいてフィードバックをお願いします。"
            ),
        },
    ]

    feedback = call_openrouter(messages)

    if feedback:
        # メモリーに追記
        date_str = activity.get("startTimeLocal", "")[:10]
        append_to_coaching_memory(date_str, feedback)
        print(f"  🧠 コーチングメモリー更新済み")

    return feedback


# ===================================================================
# Notionページへのフィードバック追記
# ===================================================================

def append_feedback_to_notion(page_id: str, feedback: str) -> None:
    """Notionのアクティビティページにコーチングフィードバックを追記"""
    blocks = [
        {
            "object": "block",
            "type": "divider",
            "divider": {},
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {"type": "text", "text": {"content": "🧠 Coach Feedback"}}
                ]
            },
        },
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [
                    {"type": "text", "text": {"content": feedback[:2000]}}
                ],
                "icon": {"emoji": "💡"},
                "color": "blue_background",
            },
        },
    ]

    result = notion_append_blocks(page_id, blocks)
    if result:
        print(f"  📝 Notionにフィードバック追記完了")
    else:
        print(f"  ⚠️ Notionへのフィードバック追記失敗")
