"""
Garmin Connect → Notion 日次同期 メインスクリプト

1. 過去5日分のランニングアクティビティを取得
2. 未登録分をNotionに転記（ラップテーブル付き）
3. OpenRouter経由でLLMコーチングフィードバックを生成
4. coaching_memory.md を更新（Git commit/pushはGitHub Actionsが実行）
5. コーチング未記入の既存ページにも後から追記（バックフィル）
"""
from config import ACTIVITIES_DB_ID, DEFAULT_RESTING_HR
from garmin_client import (
    create_client,
    fetch_recent_activities,
    get_resting_hr,
    get_activity_laps,
)
from notion_client import (
    query_existing_activity_ids,
    check_activity_exists,
    find_pages_without_coaching,
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
    append_feedback_to_notion,
)


def main():
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

    # ----- 4. 古い順にソートして処理 -----
    new_activities.sort(key=lambda a: a.get("startTimeLocal", ""))

    resting_hr_cache: dict[str, int] = {}
    success_count = 0
    error_count = 0
    coaching_count = 0

    for idx, activity in enumerate(new_activities):
        activity_id = activity.get("activityId")
        activity_name = activity.get("activityName", "Untitled")
        date_str = activity.get("startTimeLocal", "")[:10]

        print(f"\n[{idx + 1}/{len(new_activities)}] {date_str} - {activity_name} "
              f"(ID: {activity_id})")

        try:
            # --- 作成直前の重複チェック（二重登録防止） ---
            if check_activity_exists(ACTIVITIES_DB_ID, str(activity_id)):
                print(f"  ⏭️ 既にNotionに存在。スキップ。")
                continue

            # --- 安静時心拍 ---
            if date_str not in resting_hr_cache:
                rhr = get_resting_hr(client, date_str)
                resting_hr_cache[date_str] = rhr
                label = "実測" if rhr != DEFAULT_RESTING_HR else "デフォルト"
                print(f"  💓 安静時心拍: {rhr} bpm（{label}）")
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

            print(f"  📈 TRIMP: {trimp} | Intensity: {intensity} | "
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
            laps = get_activity_laps(client, activity_id)
            if laps:
                table_blocks = build_lap_table_blocks(laps)
                if table_blocks:
                    notion_append_blocks(page_id, table_blocks)

            # --- LLMコーチングフィードバック ---
            print(f"  🤖 コーチングフィードバック生成中...")
            feedback = generate_coaching_feedback(
                activity, trimp, intensity, pace_zone, laps
            )
            if feedback:
                append_feedback_to_notion(page_id, feedback)
                coaching_count += 1

            print(f"  ✅ 完了")
            success_count += 1

        except Exception as e:
            print(f"  ❌ エラー: {e}")
            error_count += 1
            continue

    # ----- 5. コーチング未記入ページのバックフィル -----
    backfill_count = backfill_coaching(client)

    # ----- 結果サマリー -----
    print("\n" + "=" * 50)
    print(f"📊 処理結果:")
    print(f"  ✅ 新規同期: {success_count}件")
    print(f"  ❌ エラー: {error_count}件")
    print(f"  🧠 コーチング生成（新規）: {coaching_count}件")
    print(f"  🧠 コーチング追記（既存）: {backfill_count}件")
    print(f"  💓 安静時心拍 取得日数: {len(resting_hr_cache)}")
    print("=" * 50)


def backfill_coaching(client) -> int:
    """コーチング未記入の既存ページにフィードバックを後から追記"""
    pages = find_pages_without_coaching(ACTIVITIES_DB_ID)

    if not pages:
        print("✅ コーチング未記入のページはありません。")
        return 0

    print(f"\n🔄 コーチング未記入 {len(pages)}件にフィードバックを追記中...")
    backfill_count = 0

    for page_info in pages:
        page_id = page_info["page_id"]
        activity_id = page_info["activity_id"]
        name = page_info["name"]
        date = page_info.get("date", "")

        print(f"\n  📝 {date} - {name} (ID: {activity_id})")

        try:
            if not activity_id:
                print(f"    ⏭️ Activity IDなし。スキップ。")
                continue

            # Garminからアクティビティの詳細を取得
            activity = _fetch_activity_detail(client, int(activity_id))
            if not activity:
                print(f"    ⚠️ Garminからデータ取得できず。スキップ。")
                continue

            # 安静時心拍
            rhr = get_resting_hr(client, date)

            # 計算
            duration_s = activity.get("duration", 0)
            distance_km = activity.get("distance", 0) / 1000
            avg_hr = activity.get("averageHR")
            avg_pace_sec = duration_s / distance_km if distance_km > 0 else 0

            trimp = calc_trimp(avg_hr, duration_s, rhr)
            intensity = classify_intensity(trimp)
            pace_zone = classify_pace_zone(avg_pace_sec)

            laps = get_activity_laps(client, int(activity_id))

            print(f"    🤖 コーチングフィードバック生成中...")
            feedback = generate_coaching_feedback(
                activity, trimp, intensity, pace_zone, laps
            )
            if feedback:
                append_feedback_to_notion(page_id, feedback)
                backfill_count += 1
                print(f"    ✅ 追記完了")
            else:
                print(f"    ⚠️ フィードバック生成失敗")

        except Exception as e:
            print(f"    ❌ エラー: {e}")
            continue

    return backfill_count


def _fetch_activity_detail(client, activity_id: int) -> dict | None:
    """Garmin Connectから特定のアクティビティ詳細を取得"""
    try:
        # get_activitiesでバッチ取得し、該当IDを探す
        activities = client.get_activities(0, 50)
        for a in activities:
            if a.get("activityId") == activity_id:
                return a

        # 見つからない場合、もう少し遡る
        activities = client.get_activities(50, 50)
        for a in activities:
            if a.get("activityId") == activity_id:
                return a

    except Exception as e:
        print(f"    ⚠️ アクティビティ詳細取得エラー: {e}")

    return None


if __name__ == "__main__":
    main()
