"""
LLMコーチングモジュール
- OpenRouter経由でLLMを呼び出し、トレーニングフィードバックを生成
- coaching_memory.md を読み書きして長期的な一貫性を維持
- Notionのアクティビティページにフィードバックを追記
"""
import requests
import datetime
import re
import os

from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    COACH_MODEL,
    COACHING_MEMORY_PATH,
    VDOT,
    MAX_HR,
)
from notion_client import notion_append_blocks, format_pace


# ===================================================================
# coaching_memory.md の初期テンプレート（新構造）
# ===================================================================

_INITIAL_MEMORY = """# 🏃 Running Coach Memory

## アスリートプロフィール（固定・自動更新しない）
- 目標: ハーフマラソン 1時間15分以内 / フルマラソン サブ2:45
- ハーフPB: 1:16:41 / フルPB: 2:44:31（2024年）
- VDOT: 55 / 最大心拍数: 205 / 安静時心拍: 57 bpm

## コーチング方針（固定・自動更新しない）
- サラザール流：厳格な規律と選手個々への配慮の両立
- 怪我予防最優先、オーバートレーニング厳禁
- 心拍ゾーン管理による科学的トレーニング

## 週次サマリー（自動更新）
<!-- WEEKLY_SUMMARY_START -->
<!-- WEEKLY_SUMMARY_END -->

## 直近7日の対話ログ（自動更新・7日経過分は週次サマリーへ圧縮）
<!-- DAILY_LOG_START -->
<!-- DAILY_LOG_END -->
"""


# ===================================================================
# コーチングメモリー（MDファイル）の読み書き
# ===================================================================

def load_coaching_memory() -> str:
    """coaching_memory.md を読み込む。なければ初期テンプレートを作成"""
    if os.path.exists(COACHING_MEMORY_PATH):
        with open(COACHING_MEMORY_PATH, "r", encoding="utf-8") as f:
            return f.read()

    with open(COACHING_MEMORY_PATH, "w", encoding="utf-8") as f:
        f.write(_INITIAL_MEMORY)
    return _INITIAL_MEMORY


def _extract_between(text: str, start_marker: str, end_marker: str) -> str:
    """マーカー間のテキストを抽出"""
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1:
        return ""
    return text[start + len(start_marker):end]


def _parse_daily_log_entries(log_content: str) -> list[tuple[str, str]]:
    """DAILY_LOG コンテンツを (date_str, entry_text) のリストに変換（新しい順）"""
    entries = []
    current_date = None
    current_lines: list[str] = []

    for line in log_content.split("\n"):
        stripped = line.strip()
        # "### YYYY-MM-DD" 形式の行を日付エントリの区切りとして検出
        if stripped.startswith("### ") and len(stripped) >= 14:
            potential_date = stripped[4:].strip()
            if (
                len(potential_date) == 10
                and potential_date[4] == "-"
                and potential_date[7] == "-"
            ):
                if current_date is not None:
                    entries.append((current_date, "\n".join(current_lines).strip()))
                current_date = potential_date
                current_lines = [line]
            else:
                if current_date is not None:
                    current_lines.append(line)
        else:
            if current_date is not None:
                current_lines.append(line)

    if current_date is not None and current_lines:
        entries.append((current_date, "\n".join(current_lines).strip()))

    return entries


def _create_week_entry_from_daily(
    week_key: str,
    week_label: str,
    day_entries: list[tuple[str, str]],
) -> str:
    """日次エントリのリストから週次サマリーエントリを生成"""
    total_dist = 0.0
    total_trimp = 0.0
    practices: list[str] = []
    coach_comments: list[str] = []

    for _date_str, entry_text in day_entries:
        for line in entry_text.split("\n"):
            # "**1日合計**: 総距離13.6km / 総TRIMP123.6" パターン
            dist_trimp = re.search(r'総距離([\d.]+)km.*?総TRIMP([\d.]+)', line)
            if dist_trimp:
                total_dist += float(dist_trimp.group(1))
                total_trimp += float(dist_trimp.group(2))
            # "**練習構成（N件）**: ..." パターン
            elif "練習構成" in line and "**:" in line:
                parts = line.split("**: ", 1)
                if len(parts) > 1:
                    practices.append(parts[1].strip())
            # "**コーチ所感**: ..." パターン
            elif line.startswith("**コーチ所感**:"):
                comment = line.replace("**コーチ所感**:", "").strip()
                if comment:
                    coach_comments.append(comment[:50])

    practices_str = "、".join(practices[:3]) if practices else "-"
    coach_str = " / ".join(coach_comments[:2]) if coach_comments else "-"

    return (
        f"### Week of {week_key}（{week_label}）\n"
        f"- 総距離: {total_dist:.1f}km / 総TRIMP: {total_trimp:.1f} / 走行日数: {len(day_entries)}日\n"
        f"- 主な練習: {practices_str}\n"
        f"- コーチ所感: {coach_str}"
    )


def _add_to_weekly_summary(
    memory: str, rollover_entries: list[tuple[str, str]]
) -> str:
    """ロールオーバーエントリを週次サマリーセクションに追加"""
    weekly_start_marker = "<!-- WEEKLY_SUMMARY_START -->"
    weekly_end_marker = "<!-- WEEKLY_SUMMARY_END -->"

    start_idx = memory.find(weekly_start_marker)
    end_idx = memory.find(weekly_end_marker)
    if start_idx == -1 or end_idx == -1:
        return memory

    before = memory[:start_idx + len(weekly_start_marker)]
    weekly_content = memory[start_idx + len(weekly_start_marker):end_idx]
    after = memory[end_idx:]

    # エントリを週ごとにグループ化
    week_groups: dict[str, list[tuple[str, str]]] = {}
    week_labels: dict[str, str] = {}
    for date_str, entry_text in rollover_entries:
        date_obj = datetime.date.fromisoformat(date_str)
        week_start = date_obj - datetime.timedelta(days=date_obj.weekday())
        week_end = week_start + datetime.timedelta(days=6)
        week_key = week_start.strftime("%Y-%m-%d")
        week_labels[week_key] = (
            f"{week_start.strftime('%m/%d')}〜{week_end.strftime('%m/%d')}"
        )
        if week_key not in week_groups:
            week_groups[week_key] = []
        week_groups[week_key].append((date_str, entry_text))

    # 各週のエントリを処理
    for week_key, entries in sorted(week_groups.items()):
        week_header = f"### Week of {week_key}"
        if week_header in weekly_content:
            # 既存週エントリに追記行を挿入
            extra_lines = []
            for date_str, entry_text in entries:
                for line in entry_text.split("\n"):
                    if "総距離" in line or "練習構成" in line or "コーチ所感" in line:
                        extra_lines.append(f"  ({date_str}) {line.strip()}")
            if extra_lines:
                idx = weekly_content.find(week_header)
                end_of_entry = weekly_content.find("\n### ", idx + 1)
                if end_of_entry == -1:
                    end_of_entry = len(weekly_content)
                weekly_content = (
                    weekly_content[:end_of_entry]
                    + "\n" + "\n".join(extra_lines)
                    + weekly_content[end_of_entry:]
                )
        else:
            # 新しい週エントリを先頭に挿入
            new_entry = _create_week_entry_from_daily(
                week_key, week_labels[week_key], entries
            )
            weekly_content = "\n" + new_entry + "\n" + weekly_content

    return before + weekly_content + after


def load_coaching_memory_for_prompt() -> str:
    """
    LLMプロンプト用にメモリを優先度順で組み立てる（最大4000字）。
    1. アスリートプロフィール＋コーチング方針（全文・固定）
    2. 週次サマリー（直近2週分）
    3. 直近7日の対話ログ（全文）
    4. 合計4000字超えの場合は週次サマリーの古い方を削る
    """
    memory = load_coaching_memory()

    # 旧フォーマット（マーカーなし）の場合は末尾を返す
    if "<!-- DAILY_LOG_START -->" not in memory:
        return memory[-4000:]

    # 1. アスリートプロフィール＋コーチング方針（WEEKLY_SUMMARY_START より前）
    weekly_start_idx = memory.find("<!-- WEEKLY_SUMMARY_START -->")
    fixed = memory[:weekly_start_idx].strip() if weekly_start_idx != -1 else ""

    # 2. 週次サマリー（直近2週分）
    weekly_all = _extract_between(
        memory, "<!-- WEEKLY_SUMMARY_START -->", "<!-- WEEKLY_SUMMARY_END -->"
    ).strip()
    weekly_entries: list[str] = []
    current_lines: list[str] = []
    for line in weekly_all.split("\n"):
        if line.startswith("### Week of") and current_lines:
            entry = "\n".join(current_lines).strip()
            if entry:
                weekly_entries.append(entry)
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        entry = "\n".join(current_lines).strip()
        if entry:
            weekly_entries.append(entry)
    weekly_recent = "\n\n".join(weekly_entries[-2:]) if weekly_entries else ""

    # 3. 直近7日の対話ログ
    daily_log = _extract_between(
        memory, "<!-- DAILY_LOG_START -->", "<!-- DAILY_LOG_END -->"
    ).strip()

    # 組み立て
    parts = []
    if fixed:
        parts.append(fixed)
    if weekly_recent:
        parts.append(f"## 週次サマリー（直近2週）\n{weekly_recent}")
    if daily_log:
        parts.append(f"## 直近7日の対話ログ\n{daily_log}")

    result = "\n\n".join(parts)

    # 4000字超えたら週次サマリーを1週だけに削る
    if len(result) > 4000 and weekly_entries:
        parts2 = []
        if fixed:
            parts2.append(fixed)
        weekly_one = weekly_entries[-1]
        parts2.append(f"## 週次サマリー（直近1週）\n{weekly_one}")
        if daily_log:
            parts2.append(f"## 直近7日の対話ログ\n{daily_log}")
        result = "\n\n".join(parts2)

    return result[:4000] if len(result) > 4000 else result


def append_to_coaching_memory(
    date_str: str,
    feedback: str,
    activity_line: str = "",
) -> None:
    """
    coaching_memory.md の DAILY_LOG に当日エントリを追加・更新する。
    - 当日エントリが既にあれば「コーチ所感」を上書き
    - なければ新規エントリを先頭に追加（新しい日付が上）
    - 7日超えたら最古エントリを週次サマリーへ圧縮
    """
    memory = load_coaching_memory()

    # 旧フォーマット（マーカーなし）の場合は後方互換で末尾追記
    if "<!-- DAILY_LOG_START -->" not in memory:
        entry = f"\n### {date_str}\n{feedback}\n"
        memory += entry
        with open(COACHING_MEMORY_PATH, "w", encoding="utf-8") as f:
            f.write(memory)
        return

    # フィードバックを150字以内に要約
    feedback_summary = (feedback[:150] + "…") if len(feedback) > 150 else feedback

    log_start_marker = "<!-- DAILY_LOG_START -->"
    log_end_marker = "<!-- DAILY_LOG_END -->"

    start_idx = memory.find(log_start_marker)
    end_idx = memory.find(log_end_marker)
    if start_idx == -1 or end_idx == -1:
        return

    before_log = memory[:start_idx + len(log_start_marker)]
    log_content = memory[start_idx + len(log_start_marker):end_idx]
    after_log = memory[end_idx:]

    # 既存エントリをパース
    entries = _parse_daily_log_entries(log_content)

    # 当日エントリが既にあれば「コーチ所感」を上書き
    updated = False
    for i, (entry_date, entry_text) in enumerate(entries):
        if entry_date == date_str:
            lines = entry_text.split("\n")
            new_lines = []
            found_coach = False
            for line in lines:
                if line.startswith("**コーチ所感**:"):
                    new_lines.append(f"**コーチ所感**: {feedback_summary}")
                    found_coach = True
                else:
                    new_lines.append(line)
            if not found_coach:
                new_lines.append(f"**コーチ所感**: {feedback_summary}")
            entries[i] = (entry_date, "\n".join(new_lines))
            updated = True
            break

    if not updated:
        # 新規エントリを先頭に追加（新しい日付が上に来る）
        entry_lines = [f"### {date_str}"]
        if activity_line:
            for line in activity_line.split("\n"):
                if line.strip():
                    entry_lines.append(line)
        entry_lines.append(f"**コーチ所感**: {feedback_summary}")
        entry_lines.append(f"**あなたのコメント**: ")
        entries.insert(0, (date_str, "\n".join(entry_lines)))

    # 7日超えたら最古エントリ（末尾）を週次サマリーへ圧縮
    rollover_entries: list[tuple[str, str]] = []
    while len(entries) > 7:
        rollover_entries.append(entries.pop())

    # DAILY_LOG を再構築
    new_log_content = ""
    if entries:
        new_log_content = "\n" + "\n\n".join(e[1] for e in entries) + "\n"
    memory = before_log + new_log_content + after_log

    # 週次サマリーへのロールオーバー
    if rollover_entries:
        memory = _add_to_weekly_summary(memory, rollover_entries)

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
    # メモリーを優先度順に組み立て（修正④）
    memory_for_prompt = load_coaching_memory_for_prompt()

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
                f"{memory_for_prompt}\n\n"
                f"---\n\n"
                f"{activity_summary}\n\n"
                f"上記データに基づいてフィードバックをお願いします。"
            ),
        },
    ]

    feedback = call_openrouter(messages)

    if feedback:
        date_str = activity.get("startTimeLocal", "")[:10]
        dist = round(activity.get("distance", 0) / 1000, 1)
        dur_s = activity.get("duration", 0)
        pace_str = format_pace(dur_s / dist if dist > 0 else 0)
        avg_hr = activity.get("averageHR", "-")
        type_key = activity.get("activityType", {}).get("typeKey", "running")
        activity_line = (
            f"**練習構成（1件）**: {type_key} {dist}km（{pace_str}/km, HR:{avg_hr}）\n"
            f"**1日合計**: 総距離{dist}km / 総TRIMP{trimp}"
        )
        append_to_coaching_memory(date_str, feedback, activity_line)
        print(f"  🧠 コーチングメモリー更新済み")

    return feedback


def generate_coaching_feedback_for_day(
    day_activities: list[dict],
    day_laps: dict[str, list],
    day_trimps: dict[str, float],
    day_intensities: dict[str, str],
    day_pace_zones: dict[str, str],
) -> str | None:
    """
    同日複数アクティビティを「1日の流れ」としてまとめてフィードバック生成。
    coaching_memory.md の内容をコンテキストとして渡す。
    """
    n = len(day_activities)
    date_str = day_activities[0].get("startTimeLocal", "")[:10]

    # メモリーを優先度順に組み立て
    memory_for_prompt = load_coaching_memory_for_prompt()

    # 最高TRIMPのアクティビティを本練習として判定
    max_trimp_idx = 0
    max_trimp_val = -1.0
    for i, act in enumerate(day_activities):
        act_id = str(act.get("activityId"))
        t = day_trimps.get(act_id, 0.0)
        if t > max_trimp_val:
            max_trimp_val = t
            max_trimp_idx = i

    # 役割説明の構築
    role_lines = []
    for i, act in enumerate(day_activities):
        act_id = str(act.get("activityId"))
        dist = round(act.get("distance", 0) / 1000, 1)
        dur_s = act.get("duration", 0)
        avg_hr = act.get("averageHR", "-")
        pace_str = format_pace(dur_s / dist if dist > 0 else 0)

        if i < max_trimp_idx:
            role = "アップ / Recovery"
        elif i == max_trimp_idx:
            role = "本練習（最も負荷が高い）"
        else:
            role = "クールダウン"

        role_lines.append(
            f"{i + 1}件目（{dist}km, {pace_str}/km, HR:{avg_hr}）→ {role}"
        )

    # 各アクティビティのサマリーを連結
    summaries = []
    for act in day_activities:
        act_id = str(act.get("activityId"))
        laps = day_laps.get(act_id, [])
        trimp = day_trimps.get(act_id, 0.0)
        intensity = day_intensities.get(act_id, "")
        pace_zone = day_pace_zones.get(act_id, "")
        summaries.append(build_activity_summary(act, trimp, intensity, pace_zone, laps))

    total_trimp = sum(
        day_trimps.get(str(a.get("activityId")), 0.0) for a in day_activities
    )

    # フロー文字列（練習構成の記述）
    flow_parts = []
    for act in day_activities:
        dist = round(act.get("distance", 0) / 1000, 1)
        type_key = act.get("activityType", {}).get("typeKey", "running")
        flow_parts.append(f"{type_key} {dist}km")
    flow_str = " → ".join(flow_parts)

    user_message = (
        f"# コーチングメモリー（これまでの練習記録と方針）\n"
        f"{memory_for_prompt}\n\n"
        f"---\n\n"
        f"今日のトレーニングは{n}件で構成されています。\n"
        f"up（アップ）→ 本練習 → down（クールダウン）のような1日の流れとして総合評価してください。\n\n"
        f"【件数と推定役割】\n"
        + "\n".join(role_lines) + "\n\n"
        + "【各アクティビティの詳細】\n"
        + "\n---\n".join(summaries) + "\n\n"
        + f"1日全体のTRIMP合計: {total_trimp:.1f}\n\n"
        + "上記データに基づいて1日全体のフィードバックをお願いします。"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    feedback = call_openrouter(messages)

    if feedback:
        total_dist = sum(a.get("distance", 0) / 1000 for a in day_activities)
        activity_line = (
            f"**練習構成（{n}件）**: {flow_str}\n"
            f"**1日合計**: 総距離{total_dist:.1f}km / 総TRIMP{total_trimp:.1f}"
        )
        append_to_coaching_memory(date_str, feedback, activity_line)
        print(f"  🧠 コーチングメモリー更新済み（{n}件分）")

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
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [
                    {"type": "text", "text": {"content": "💬 Your Response"}}
                ]
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "（ここにコメントを入力してください）"}}
                ]
            },
        },
    ]

    result = notion_append_blocks(page_id, blocks)
    if result:
        print(f"  📝 Notionにフィードバック追記完了")
    else:
        print(f"  ⚠️ Notionへのフィードバック追記失敗")
