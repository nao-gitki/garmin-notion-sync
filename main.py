"""
Garmin Connect → Notion 日次同期 メインスクリプト

1. GCS から coaching_memory.md をダウンロード
2. 過去5日分のランニングアクティビティを取得
3. 未登録分をNotionに転記（ラップテーブル付き）
4. OpenRouter経由でLLMコーチングフィードバックを生成
   - 同日複数件は「1日の流れ」としてまとめてフィードバック
5. coaching_memory.md を GCS にアップロード
"""
from collections import defaultdict

from config import ACTIVITIES_DB_ID, DEFAULT_RESTING_HR
from garmin_client import (
    create_client,
    fetch_recent_activities,
    get_resting_hr,
    get_activity_laps,
)
from notion_client import (
    query_existing_activity_ids,
    calc_trimp,
    classify_intensity,
    classify_pace_zone,
    format_duration,
    format_pace,
    build_activity_properties,
    build_lap_table_blocks,
    notion_create_page,
    notion_append_blocks,
)
from coach import (
    generate_coaching_feedback,
    generate_coaching_feedback_for_day,
    append_feedback_to_notion,
)
from gcs_storage import download_memory, upload_memory
from config import COACHING_MEMORY_PATH


def main():
    # ----- 0. GCS から coaching_memory.md をダウンロード -----
    print("☁️  GCS から coaching_memory.md をダウンロード中...")
    download_memory(COACHING_MEMORY_PATH)

    # ----- 1. Garminログイン -----
    client = create_client()

    # ----- 2. 既存データ確認（重複防止） -----
    existing_ids = query_existing_activity_ids(ACTIVITIES_DB_ID)

    # ----- 3. 過去5日分のアクティビティ取得 -----
    activities = fetch_recent_activities(client)

    new_activities = [
        a for a in activities
        if str(a.get("activityId", "")) not in existing_ids
    ]

    print(f"\n🆕 新規（未登録）: {len(new_activities)}件")

    if not new_activities:
        print("✅ すべてのアクティビティが登録済みです。")
        # GCS にアップロード（メモリー更新がなくても整合性確保）
        upload_memory(COACHING_MEMORY_PATH)
        return

    # ----- 4. 古い順にソートして日付グループ化 -----
    new_activities.sort(key=lambda a: a.get("startTimeLocal", ""))

    grouped: dict[str, list] = defaultdict(list)
    for a in new_activities:
        grouped[a.get("startTimeLocal", "")[:10]].append(a)

    resting_hr_cache: dict[str, int] = {}
    success_count = 0
    error_count = 0
    coaching_count = 0

    for date_str, day_acts in sorted(grouped.items()):
        n = len(day_acts)
        print(f"\n📅 {date_str} ({n}件)")

        # 各アクティビティのページ作成・ラップ取得
        day_page_ids: list[str] = []
        day_laps: dict[str, list] = {}
        day_trimps: dict[str, float] = {}
        day_intensities: dict[str, str] = {}
        day_pace_zones: dict[str, str] = {}
        day_processed: list[dict] = []

        for idx, activity in enumerate(day_acts):
            activity_id = activity.get("activityId")
            activity_name = activity.get("activityName", "Untitled")

            print(f"\n  [{idx + 1}/{n}] {activity_name} (ID: {activity_id})")

            try:
                # --- 安静時心拍 ---
                if date_str not in resting_hr_cache:
                    rhr = get_resting_hr(client, date_str)
                    resting_hr_cache[date_str] = rhr
                    label = "実測" if rhr != DEFAULT_RESTING_HR else "デフォルト"
                    print(f"    💓 安静時心拍: {rhr} bpm（{label}）")
                resting_hr = resting_hr_cache[date_str]

                # --- サマリー計算 ---
                duration_s = activity.get("duration", 0)
                distance_km = activity.get("distance", 0) / 1000
                avg_hr = activity.get("averageHR")
                duration_str = format_duration(duration_s)

                if distance_km > 0:
                    avg_pace_sec = duration_s / distance_km
                    avg_pace_str = format_pace(avg_pace_sec)
                else:
                    avg_pace_sec = 0
                    avg_pace_str = "0:00"

                # --- TRIMP & 分類 ---
                trimp = calc_trimp(avg_hr, duration_s, resting_hr)
                intensity = classify_intensity(trimp)
                pace_zone = classify_pace_zone(avg_pace_sec)

                print(f"    📈 TRIMP: {trimp} | Intensity: {intensity} | "
                      f"Pace Zone: {pace_zone}")

                # --- Notionページ作成 ---
                props = build_activity_properties(
                    activity, avg_pace_str, avg_pace_sec, duration_str,
                    trimp, intensity, pace_zone, resting_hr,
                )
                page = notion_create_page(ACTIVITIES_DB_ID, props)

                if not page:
                    error_count += 1
                    continue

                page_id = page["id"]

                # --- ラップデータ取得 & テーブル埋め込み ---
                laps = get_activity_laps(client, activity_id) or []
                if laps:
                    table_blocks = build_lap_table_blocks(laps)
                    if table_blocks:
                        notion_append_blocks(page_id, table_blocks)

                # データを記録
                act_id_str = str(activity_id)
                day_page_ids.append(page_id)
                day_laps[act_id_str] = laps
                day_trimps[act_id_str] = trimp
                day_intensities[act_id_str] = intensity
                day_pace_zones[act_id_str] = pace_zone
                day_processed.append(activity)

                print(f"    ✅ ページ作成完了")
                success_count += 1

            except Exception as e:
                print(f"    ❌ エラー: {e}")
                error_count += 1
                continue

        if not day_processed:
            continue

        # ----- コーチングフィードバック生成 -----
        print(f"\n  🤖 コーチングフィードバック生成中...")

        if len(day_processed) == 1:
            # 単独アクティビティ：従来通り
            activity = day_processed[0]
            act_id_str = str(activity.get("activityId"))
            laps = day_laps.get(act_id_str, [])
            feedback = generate_coaching_feedback(
                activity,
                day_trimps[act_id_str],
                day_intensities[act_id_str],
                day_pace_zones[act_id_str],
                laps,
            )
            if feedback and day_page_ids:
                append_feedback_to_notion(day_page_ids[0], feedback)
                coaching_count += 1
        else:
            # 複数アクティビティ：1日まとめてフィードバック
            feedback = generate_coaching_feedback_for_day(
                day_processed,
                day_laps,
                day_trimps,
                day_intensities,
                day_pace_zones,
            )
            if feedback and day_page_ids:
                # 本練習（最高TRIMP）のページに追記、なければ最後のページ
                target_page_id = day_page_ids[-1]
                max_trimp = -1.0
                for i, act in enumerate(day_processed):
                    t = day_trimps.get(str(act.get("activityId")), 0.0)
                    if t > max_trimp and i < len(day_page_ids):
                        max_trimp = t
                        target_page_id = day_page_ids[i]
                append_feedback_to_notion(target_page_id, feedback)
                coaching_count += 1

    # ----- 結果サマリー -----
    print("\n" + "=" * 50)
    print(f"📊 処理結果:")
    print(f"  ✅ 成功: {success_count}件")
    print(f"  ❌ エラー: {error_count}件")
    print(f"  🧠 コーチング生成: {coaching_count}日")
    print(f"  💓 安静時心拍 取得日数: {len(resting_hr_cache)}")
    print("=" * 50)

    # ----- 5. GCS に coaching_memory.md をアップロード -----
    upload_memory(COACHING_MEMORY_PATH)


if __name__ == "__main__":
    main()
