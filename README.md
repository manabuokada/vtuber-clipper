# vtuber_clipper — 新人・個人勢VTuber ショート切り抜き制作・運用パイプライン

切り抜き許諾を明記している新人・個人勢VTuberの発掘から、許諾エビデンス保全、見どころ抽出、縦型ショート動画の自動編集、YouTube Data API v3 での非公開アップロードまでを一気通貫で行う CLI ツール群です。

## 運用ポリシー・安全設計

1. **切り抜き許諾の明記確認＆エビデンス保全**
   - 動画概要欄・公式ガイドライン等で「切り抜き歓迎／許可」を明記している配信者のみを対象とします。
   - 承認前に [capture_evidence.py](scripts/discovery/capture_evidence.py) で概要欄全文・取得日時・SHA-256ハッシュを `work/evidence/` へローカル保全し、必ず人間の確認を経て [manage_channels.py](scripts/discovery/manage_channels.py) で承認します。
2. **クレジット明記＆禁止枠・時限PRの除外**
   - 元チャンネルURL・元配信URL・指定切り抜きタグを概要欄テンプレートから自動挿入します。
   - メンバーシップ限定・歌枠（楽曲権利あり）・切り抜き禁止指定枠は対象外とします。
   - 終了済みイベント告知などの日時依存 PR シーンは切り抜きません（[analyze_highlights.py](scripts/production/analyze_highlights.py) は最新配信アーカイブを優先し、過去配信内の告知ワードを自動警告・スコア減点して、雑談・リアクション等のタイムレスな名場面を優先します）。
3. **誤爆防止チャンネルロック**
   - [upload_youtube.py](scripts/publish/upload_youtube.py) は初回 `--auth-and-lock` で取得した投稿専用チャンネルIDを `config/upload_config.json` に保存し、アップロード時に認証先チャンネルIDが一致しない場合は即時停止します。
   - アップロードはデフォルトで `private`（非公開）とし、YouTube Studio での確認後に公開します。

---

## ディレクトリ構成

```text
vtuber-clipper/
├── README.md
├── mise.toml                        # Python 3.14 / uv バージョン固定
├── requirements.txt                 # Python 依存パッケージ
├── docs/
│   └── STATUS.md                    # 開発進捗・マイルストーン
├── examples/                        # 設定テンプレート（Public）
│   ├── channels.example.yaml
│   └── shorts_spec.example.yaml
├── scripts/
│   ├── discovery/                   # 1. 発掘・競合調査・許諾管理
│   │   ├── discover_candidates.py
│   │   ├── check_clip_saturation.py
│   │   ├── capture_evidence.py
│   │   └── manage_channels.py
│   ├── production/                  # 2. 見どころ抽出・素材生成・動画編集
│   │   ├── analyze_highlights.py
│   │   ├── generate_assets.py
│   │   ├── build_shorts.py
│   │   └── render_clips.py
│   ├── publish/                     # 3. YouTubeアップロード・Webhook通知
│   │   └── upload_youtube.py
│   └── maintenance/                 # 4. ローカル動画・中間ファイルの整理
│       └── cleanup_work.py
├── config/                          # (.gitignore) 認証・チャンネル運用設定
│   ├── channels.yaml
│   ├── candidates.yaml
│   ├── client_secret.json
│   ├── token.json
│   └── upload_config.json
└── work/                            # (.gitignore) 生成素材・動画・エビデンス
    ├── assets/
    ├── clips/
    └── evidence/
```

---

## パイプライン全体像

```text
1. 候補発掘          scripts/discovery/discover_candidates.py     → config/candidates.yaml
2. 競合度チェック    scripts/discovery/check_clip_saturation.py   → 第三者切り抜き10件未満を優先
3. 許諾エビデンス    scripts/discovery/capture_evidence.py        → work/evidence/<channel_id>/<date>/
4. 人間による承認    scripts/discovery/manage_channels.py approve → config/channels.yaml
5. 見どころ抽出      scripts/production/analyze_highlights.py     → ライブチャット急増＋日本語字幕分析
6. 素材生成          scripts/production/generate_assets.py        → work/assets/ (透過PNG・BGM・SE)
7. 動画生成          scripts/production/build_shorts.py           → work/clips/<name>/edited_shorts.mp4
8. 非公開アップロード scripts/publish/upload_youtube.py           → YouTube Data API v3 (private)
```

---

## スクリプト一覧

| ファイル | 役割 |
|---|---|
| `scripts/discovery/discover_candidates.py` | 切り抜き許可・切り抜きタグを明記する新人・個人勢VTuber候補を自動探索し `config/candidates.yaml` へ追記 |
| `scripts/discovery/check_clip_saturation.py` | 候補ごとの既存第三者切り抜き本数を調査し、競合度（`blue_ocean_ok` / `saturated_exclude`）を記録 |
| `scripts/discovery/capture_evidence.py` | 許諾記載の概要欄全文・外部ガイドライン・取得日時・SHA-256ハッシュを `work/evidence/` へ保全 |
| `scripts/discovery/manage_channels.py` | 候補（`config/candidates.yaml`）と承認済みチャンネル（`config/channels.yaml`）の一覧・承認・却下 CLI |
| `scripts/production/analyze_highlights.py` | 最新配信アーカイブのライブチャット急増＋日本語字幕から見どころを抽出（過去配信の時限・告知PRシーンは自動警告＆スコア減点） |
| `scripts/production/generate_assets.py` | ショート動画用の透過PNGフレーム・集中線・赤枠・BGM・効果音SEを `work/assets/` へ生成 |
| `scripts/production/build_shorts.py` | YAML 設定（`--spec`）駆動の 1080×1920 縦動画生成（冒頭フック・ジェットカット・レイアウト別クロップ・ズーム＆シェイク・字幕・ダッキングBGM） |
| `scripts/production/render_clips.py` | 横動画（1080p H.264 / -14 LUFS）＋サムネイル並列レンダリング用スクリプト |
| `scripts/publish/upload_youtube.py` | YouTube Data API v3 アップローダー（OAuth認証・誤爆防止チャンネルロック・Webhook完了通知・`--dry-run`・`--cleanup` 対応） |
| `scripts/maintenance/cleanup_work.py` | `work/clips/` 配下の動画・中間ファイル・確認用画像を安全に整理する CLI（デフォルト dry-run、`work/evidence/` やメタデータは厳格保護） |

---

## セットアップ手順

### 1. 事前ツール
- [mise](https://mise.jdx.dev/)（[mise.toml](mise.toml) で Python 3.14・`uv` を固定）
- `ffmpeg`（OS パッケージ：`libx264`・`libass`・日本語フォント連携のため）
- `yt-dlp`（YouTube 仕様変更への追従のため最新版を推奨）

### 2. Python 仮想環境と依存パッケージ
```bash
mise trust
mise install
uv pip install -r requirements.txt
```
※ `mise` を使わない場合は `python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt` でも構築できます。

### 3. チャンネル設定ファイルの準備
[examples/channels.example.yaml](examples/channels.example.yaml) を参考に `config/channels.yaml` を用意するか、`discover_candidates.py` → `capture_evidence.py` → `manage_channels.py approve` のフローで生成します。

### 4. 共通素材の生成とショート動画の生成
```bash
./.venv/bin/python scripts/production/generate_assets.py
./.venv/bin/python scripts/production/build_shorts.py --spec examples/shorts_spec.example.yaml
```
※ `work/assets/custom_bgm.mp3` を配置するとフリーBGMへの差し替えが可能です。チャンネルごとの画面解像度・アバター位置・字幕は [examples/shorts_spec.example.yaml](examples/shorts_spec.example.yaml) をコピーして設定します。

### 5. YouTube Data API v3 認証とチャンネルロック
1. Google Cloud Console で **YouTube Data API v3** を有効化し、OAuth 同意画面のテストユーザーに**投稿専用 Google アカウント**を追加する。
2. デスクトップアプリ用の OAuth クライアント ID を作成し、JSON を `config/client_secret.json` として配置（`chmod 600 config/client_secret.json`）。
3. 投稿専用アカウントで認証し、投稿先チャンネルをロックする：
```bash
./.venv/bin/python scripts/publish/upload_youtube.py --auth-and-lock
```

### 6. アップロード実行例
```bash
# 設定・メタデータ確認のみ
./.venv/bin/python scripts/publish/upload_youtube.py \
  --video work/clips/<name>/edited_shorts.mp4 \
  --meta work/clips/<name>/metadata.json \
  --dry-run

# 非公開アップロード
./.venv/bin/python scripts/publish/upload_youtube.py \
  --video work/clips/<name>/edited_shorts.mp4 \
  --meta work/clips/<name>/metadata.json \
  --privacy private
```

### 7. アップロード完了通知（任意・Webhook）
`config/upload_config.json`（`.gitignore` 対象）に `"notify_webhook_url"` を追記するか、環境変数 `NOTIFY_WEBHOOK_URL`（または `--notify-url`）を指定すると、アップロード完了時に **Discord / Slack / ntfy.sh** を自動判別して YouTube Studio の編集リンクを通知します。

```json
{
  "allowed_channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
  "allowed_channel_title": "Your Channel",
  "notify_webhook_url": "https://ntfy.sh/<random-topic>"
}
```
```bash
# 通知だけの疎通テスト
./.venv/bin/python scripts/publish/upload_youtube.py --test-notify
```

### 8. ローカル動画・中間ファイルのクリーンアップ
- [build_shorts.py](scripts/production/build_shorts.py) は中間ファイル（`part_*.mp4` / `joined_xfade.mp4`）を OS 一時ディレクトリへ生成し、終了時に自動削除します（デバッグ時に残したい場合は `--keep-intermediates` を指定）。
- [upload_youtube.py](scripts/publish/upload_youtube.py) に `--cleanup` を付けると、アップロード成功時のみ対象の動画ファイル（`--video`）を自動削除します（`--dry-run` や失敗時は削除しません）。
- 残ったローカル動画・確認用画像は [cleanup_work.py](scripts/maintenance/cleanup_work.py) で一括整理できます（`work/evidence/`・`config/`・`*.yaml`・`metadata.json` 等は保護対象）：

```bash
# 削除候補と解放見込み容量の確認（デフォルト dry-run）
./.venv/bin/python scripts/maintenance/cleanup_work.py

# 中間ファイルと確認用画像（verify_*.jpg / layout_probe.jpg）のみ削除
./.venv/bin/python scripts/maintenance/cleanup_work.py --intermediates-only --apply

# 7日以上前の動画・確認用画像を削除
./.venv/bin/python scripts/maintenance/cleanup_work.py --older-than 7 --apply

# work/clips/ の対象動画をすべて削除
./.venv/bin/python scripts/maintenance/cleanup_work.py --apply
```

---

## Git 管理ルール（コミット対象外）

秘密情報・環境固有設定・第三者配信者の運用データ・大容量生成物は `.gitignore` で除外しています：
- ローカル運用設定・認証情報：`config/`（`channels.yaml`, `candidates.yaml`, `client_secret*.json`, `token.json`, `upload_config.json`）
- ローカル生成物・素材・エビデンス：`work/`（`assets/`, `clips/`, `evidence/`）
- 仮想環境・キャッシュ：`.venv/`, `__pycache__/`, `*.pyc`

## 関連ドキュメント
- [docs/STATUS.md](docs/STATUS.md) — パイプライン開発進捗と今後のマイルストーン
