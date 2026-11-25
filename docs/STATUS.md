# 開発進捗ステータス（2026-11-25）

## 現在の到達点

**候補発掘 → 競合度チェック → 許諾エビデンス保全 → 人間承認 → 見どころ抽出 → ショート自動編集 → 投稿専用チャンネルへの YouTube Data API v3 非公開アップロード** まで、全工程の一気通貫での動作確認が完了しています。

---

## 実装済みコンポーネント

- **候補探索・審査フェーズ（`scripts/discovery/`）**
  - [x] 新人・個人勢VTuber候補の自動探索（`discover_candidates.py`）
  - [x] 第三者切り抜き飽和度チェック（`check_clip_saturation.py`、デフォルト閾値：10件未満を優先）
  - [x] 許諾エビデンス保全＆SHA-256ハッシュ記録（`capture_evidence.py`）
  - [x] 承認／却下ワークフロー CLI（`manage_channels.py`）
- **分析・動画編集フェーズ（`scripts/production/`）**
  - [x] 最新配信優先のライブチャット急増＋日本語字幕による見どころ抽出＆過去告知PR自動警告フィルタ（`analyze_highlights.py`）
  - [x] ショート動画用グラフィック・BGM・SE 素材自動生成（`generate_assets.py`）
  - [x] YAML 設定駆動の縦型ショート動画自動生成（`build_shorts.py --spec`：レイアウト別クロップ・冒頭フック・ジェットカット・1.06倍速・ズーム＆シェイク・テロップ・ダッキングBGM、複数解像度・別チャンネルで検証済み）
  - [x] 横動画レンダリング＋サムネイル生成（`render_clips.py`）
- **YouTube 投稿・安全管理フェーズ（`scripts/publish/`）**
  - [x] YouTube Data API v3 アップローダー実装（`upload_youtube.py`、`--dry-run`・`--privacy`・Webhook 完了通知対応）
  - [x] 投稿専用 Google アカウント／チャンネル分離と誤爆防止チャンネルIDロック（`--auth-and-lock` → `config/upload_config.json`）
  - [x] 非公開（`private`）でのエンドツーエンド・テストアップロード検証完了

---

## 運用データの管理方針

- 配信者ごとの承認リスト（`config/channels.yaml`）、探索候補（`config/candidates.yaml`）、許諾エビデンス全文（`work/evidence/`）、認証情報（`config/client_secret.json`, `config/token.json`, `config/upload_config.json`）はすべて `.gitignore` によりローカル環境のみで管理します。
- スキーマ例は [examples/channels.example.yaml](../examples/channels.example.yaml) を参照してください。

---

## 次のマイルストーン（TODO）

- [ ] 定期スケジュール運用スクリプトの作成（週次の候補発掘・日次または隔日のショート生成＆非公開アップロード）
- [ ] チャンネルブランディング素材（アイコン・バナー・概要欄テンプレート）の整備
- [ ] テスト・CI（スクリプトの構文チェック・設定バリデーション）の追加
