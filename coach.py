"""
LLM coaching module
- Calls LLM via OpenRouter (OpenAI SDK) with reasoning to generate training feedback
- Reads/writes coaching_memory.md for long-term consistency
- Appends feedback to Notion activity pages
"""
from openai import OpenAI
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
# Coaching memory (MD file) read/write
# ===================================================================

def load_coaching_memory() -> str:
    """coaching_memory.md を読み込む。なければ初期テンプレートを作成"""
    if os.path.exists(COACHING_MEMORY_PATH):
        with open(COACHING_MEMORY_PATH, "r", encoding="utf-8") as f:
            return f.read()

    # 初期テンプレート
    initial = """# 🏃 Running Coach Memory

## アスリートプロフィール
- 目標: ハーフマラソン 1時間15分以内 / フルマラソン サブ2:45
- ハーフPB: 1:16:41
- フルPB: 2:44:31（2024年）
- VDOT: 55
- 最大心拍数: 205
- 安静時心拍: 57 bpm

## コーチング方針 (サラザール流)
- 「厳格な規律」と「選手個々への配慮」を両立
- 心拍ゾーンに基づく科学的トレーニング管理
- 怪我予防を最優先（オーバートレーニング厳禁）
- 「toughness（精神的強靭さ）」の醸成
- 休息と回復こそ「真の強さ」

## 直近のトレーニングログ
(自動更新)
"""
    with open(COACHING_MEMORY_PATH, "w", encoding="utf-8") as f:
        f.write(initial)
    return initial


def append_to_coaching_memory(date_str: str, feedback: str) -> None:
    """coaching_memory.md にフィードバックを追記"""
    memory = load_coaching_memory()
    entry = f"\n### {date_str}\n{feedback}\n"
    memory += entry

    lines = memory.split("\n")
    if len(lines) > 500:
        print("  ⚠️ coaching_memory.md が500行超え。将来的にサマリー圧縮を検討してください。")

    with open(COACHING_MEMORY_PATH, "w", encoding="utf-8") as f:
        f.write(memory)


# ===================================================================
# OpenRouter API call via OpenAI SDK (with reasoning)
# ===================================================================

SYSTEM_PROMPT = f"""あなたは「世界最高のランニングコーチ」、アルベルト・サラザールのようにストイックかつ厳格な指導ができる最強のコーチです。
選手のトレーニングデータを分析し、ランニングのトレーニングやメンタル面、パフォーマンス向上につながる率直なフィードバックを提供します。

# アスリート情報
- 目標: ハーフマラソン 1時間15分以内（現PB: 1:16:41）
- フルマラソンPB: 2:44:31
- VDOT: {VDOT}
- 最大心拍数: {MAX_HR}
- フルマラソン時の平均心拍: 最大心拍の約92%

# サラザールの哲学に基づく指導原則
1. 「厳格な規律」と「選手個々への配慮」を両立させる
2. 「toughness（精神的強靭さ）」を重視しつつ、適切な休息も「真の強さ」と捉える
3. コントロールできないことに動揺しない「メンタル・ゲーム」の重要性を説く
4. 自身のオーバートレーニング経験から、休息と回復を極めて重要視する
5. 多様性と特異性を両立させた練習メニューを提案する

# 心拍ゾーン別トレーニング基準
- ゾーン2（134〜154 bpm）: 週の70〜80%。基礎持久力と脂肪燃焼。20〜25kmロング走を4:30/km程度で。
- ゾーン3（154〜173 bpm）: 週1回。閾値走（8kmを4:00/km）で乳酸処理能力向上。
- ゾーン4（173〜182 bpm）: 週1回。高強度インターバル（1km×5を3:40/km、レスト2分）でVO2max向上。

# ペース設定基準（フルマラソンレースペース 3:47/km 基準）
- スローロングラン: レースペース+60〜90秒（4:47〜5:17/km）
- 閾値走: レースペース-10〜15秒（3:32〜3:37/km）
- インターバル走: レースペース-30〜40秒（3:07〜3:17/km）

# 練習強度配分
- 低強度: 週の60〜70%（ゆっくりペース、基礎持久力と脂肪燃焼）
- 中強度: 週の20〜30%（閾値走やペース走で心肺機能向上）
- 高強度: 週の10〜20%（インターバルや坂道ダッシュでスピード強化）

# フィードバックの方針
1. 今日のトレーニングの率直な評価（良い点・改善点）。過度な褒めは不要。
2. ペースゾーンとTRIMPの妥当性チェック
3. 心拍数データからの疲労・回復度の推察
4. 直近の練習の流れを踏まえた文脈的アドバイス（メモリー参照）
5. 次の練習への具体的な提案（サラザール流の多様なメニュー提案を含む）
6. 怪我リスクがあれば必ず警告。妥協は許さない。
7. メンタル面への助言（精神的リカバリー、身体との対話の重要性）

# 出力ルール
- 簡潔に（300〜500字程度）
- 日本語で回答
- 建設的だが率直に。厳しい指摘も成長のために躊躇しない。
- 「ソフトサーフェス・トレーニング」「ヒルスプリント」「プライオメトリクス」等、サラザール流の具体的メニューも適宜提案
"""


def call_openrouter(messages: list[dict]) -> str | None:
    """OpenRouter API を OpenAI SDK 経由で呼び出し（reasoning有効）"""
    if not OPENROUTER_API_KEY:
        print("  ⚠️ OPENROUTER_API_KEY 未設定。コーチング機能をスキップ。")
        return None

    try:
        client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
        )

        response = client.chat.completions.create(
            model=COACH_MODEL,
            messages=messages,
            extra_body={"reasoning": {"enabled": True}},
        )

        return response.choices[0].message.content

    except Exception as e:
        print(f"  ❌ OpenRouter 呼び出し失敗: {e}")
        return None


# ===================================================================
# Feedback generation
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
    max_hr_act = activity.get("maxHR", "-")
    cadence = activity.get("averageRunningCadenceInStepsPerMinute", "-")
    aero = activity.get("aerobicTrainingEffect", "-")
    anaero = activity.get("anaerobicTrainingEffect", "-")

    name = activity.get("activityName", "Run")
    date = activity.get("startTimeLocal", "")[:10]

    avg_pace_s = duration_s / distance_km if distance_km > 0 else 0
    pace_m = int(avg_pace_s // 60)
    pace_sec = int(avg_pace_s % 60)

    summary = f"""## {date} - {name}
- 距離: {distance_km} km
- タイム: {h}:{m:02d}:{s:02d}
- 平均ペース: {pace_m}:{pace_sec:02d}/km
- 平均HR: {avg_hr} bpm | 最大HR: {max_hr_act} bpm
- ケイデンス: {cadence} spm
- TRIMP: {trimp:.1f} | 強度: {intensity} | ペースゾーン: {pace_zone}
- 有酸素TE: {aero} | 無酸素TE: {anaero}
"""

    # ラップデータ
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

    # メッセージ組み立て
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"""以下はコーチングメモリー（直近のトレーニング履歴と方針）です：

{memory[-3000:]}

---

以下の今日のトレーニングデータを分析し、サラザール流の率直なフィードバックを提供してください：

{activity_summary}
""",
        },
    ]

    print(f"  🤖 コーチングフィードバック生成中（{COACH_MODEL} + reasoning）...")
    feedback = call_openrouter(messages)

    if feedback:
        # メモリーに追記
        date_str = activity.get("startTimeLocal", "")[:10]
        append_to_coaching_memory(date_str, feedback)
        print(f"  🧠 コーチングメモリー更新済み")

    return feedback


# ===================================================================
# Notion page feedback append
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
                    {"type": "text", "text": {"content": "🧠 Coach Feedback (Salazar Style)"}}
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
                "icon": {"emoji": "🏃"},
                "color": "red_background",
            },
        },
    ]

    notion_append_blocks(page_id, blocks)
