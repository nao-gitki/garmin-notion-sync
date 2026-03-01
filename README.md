# Garmin Connect → Notion Daily Sync

Garminのランニングデータを毎日自動でNotionに転記し、LLMコーチからのフィードバックを蓄積するシステム。

## 機能

- **日次自動同期**: GitHub Actionsで毎日AM 6:00 JST に実行
- **過去5日リカバリー**: 旅行や接続不良時も5日以内なら自動復旧
- **TRIMP計算**: Banister式 + ダニエルズ式ペースゾーン分類
- **ラップテーブル**: Notionページ本文にラップ詳細を埋め込み
- **AIコーチング**: OpenRouter経由でLLMがトレーニングを分析・フィードバック
- **コーチングメモリー**: `coaching_memory.md` にフィードバック履歴を蓄積し、一貫した長期的コーチングを実現

## セットアップ

### 1. リポジトリのSecrets設定

GitHub Settings → Secrets and variables → Actions に以下を登録:

| Secret名 | 説明 |
|---|---|
| `GARMIN_EMAIL` | Garmin Connectのメールアドレス |
| `GARMIN_PASSWORD` | Garmin Connectのパスワード |
| `NOTION_API_KEY` | Notion Integration APIキー |
| `OPENROUTER_API_KEY` | OpenRouter APIキー |

### 2. Notion側の準備

1. [Notion Integrations](https://www.notion.so/my-integrations) でIntegrationを作成
2. Activities DBの「接続」からIntegrationを追加
3. `config.py` の `ACTIVITIES_DB_ID` を自分のDB IDに更新

### 3. OpenRouter

1. https://openrouter.ai でアカウント作成
2. APIキーを取得してSecretsに登録

## ファイル構成

```
├── main.py                 # エントリーポイント
├── config.py               # 設定値・個人パラメータ
├── garmin_client.py        # Garmin Connect操作
├── notion_client.py        # Notion API操作
├── coach.py                # LLMコーチング（OpenRouter）
├── coaching_memory.md      # コーチングメモリー（自動更新）
├── requirements.txt
└── .github/workflows/
    └── daily_sync.yml      # GitHub Actions定義
```

## 手動実行

```bash
export GARMIN_EMAIL="your@email.com"
export GARMIN_PASSWORD="yourpassword"
export NOTION_API_KEY="ntn_xxx"
export OPENROUTER_API_KEY="sk-or-xxx"
python main.py
```
