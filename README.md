# 春日部つむぎAI — Phase 1

Gemini / ChatGPT の Web 版とVOICEVOXを連携させた音声対話アプリケーションです。使用するLLMは `config.yaml` の `llm_backend` で切り替えます。

## 必要環境

- Windows 11
- Python 3.11
- Microsoft Edge
- VOICEVOX

## セットアップ

### 1. VOICEVOXを起動する

VOICEVOXを起動し、http://127.0.0.1:50021 にアクセスできることを確認してください。

### 2. セットアップを実行する

PowerShellでプロジェクトフォルダに移動し、以下を実行します。

```powershell
.\setup.bat
```

`setup.bat` が、Edgeの起動に必要なPlaywrightファイルの取得（`python -m playwright install msedge`）まで自動で行います。取得に失敗したときだけ、手動で次のコマンドを実行してください。

```powershell
python -m playwright install msedge
```

## 初回ログイン

初回は使用するLLM（Gemini の場合はGoogleアカウント、ChatGPT の場合はChatGPT）に手動でログインする必要があります。`run.bat`を実行するとEdgeが起動します。ログイン画面が表示されたら、Edgeの画面で直接ログインしてください。ログイン後、コンソールに表示される案内に従って操作します。2回目以降は不要です（ログイン状態は使用中のEdgeプロファイルに保持されます）。`use_real_edge_profile: true` の場合は、起動前にEdgeを完全に閉じてください。

## 起動

```powershell
.\run.bat
```

終了する場合は、`exit`と入力するか、Ctrl+Cを押します。

## 設定

`config.yaml`で設定を変更できます。

- `voicevox_url`: VOICEVOXのURL
- `speaker`: VOICEVOXのスピーカーID（春日部つむぎ = 8）
- `tts_split_by_sentence`: `true` の場合は文単位で合成・再生する。既定は `false`（全文を一括合成してから再生。文ごとに間が空かない）
- `llm_backend`: 使用するLLM（`gemini` または `chatgpt`）
- `browser_profile_dir`: 専用Edgeのプロファイルディレクトリ（プロジェクト直下の `./browser_profile`）
- `use_real_edge_profile`: `true` の場合は普段使いのEdgeプロファイルを使う（起動前にEdgeを完全に閉じること。アプリ終了時に普段のEdgeは閉じない）。既定は `false`（専用プロファイル）
- `cdp_port`: EdgeへのCDP接続ポート（既定値 9222）
- `chatgpt_url`: ChatGPTのURL
- `gemini_url`: GeminiのURL
- `system_prompt`: 春日部つむぎとしての応答指示

## LLMの変更

使用するLLMは `config.yaml` の `llm_backend` で切り替えます（`gemini` / `chatgpt`）。

新しいLLMを追加する場合は、`app/llm_base.py`の`LLMBackend`を継承して新しいクラスを作成し、`main.py`の`create_llm()`に追加してください。

## トラブルシューティング

- VOICEVOXに接続できない
  - VOICEVOXが起動しているか確認してください。
  - http://127.0.0.1:50021 にアクセスできるか確認してください。
  - `config.yaml`の`voicevox_url`を確認してください。
- ChatGPTにログインできない
  - 起動したEdgeの画面でChatGPTにログインしてください。
  - ブラウザのプロファイルが正しいか`config.yaml`で確認してください。
- 音声が出ない
  - スピーカーの音量を確認してください。
  - VOICEVOXのスピーカーが春日部つむぎ（speaker=8）になっているか確認してください。

## バージョン

- v0.4.0 — 音声の再生方式を変更。全文を一括で合成してから再生するようにし、文ごとの間が空く問題を解消。あわせてコンソールに [待機中] / [処理中] の状態表示を追加。文単位の合成は `tts_split_by_sentence: true` で従来通り使える
- v0.3.1 — `use_real_edge_profile` の既定値を `false`（専用プロファイル）に変更。実プロファイル方式は、Edge が裏で起動していると CDP 接続できない制約があるため、Gemini では専用プロファイルを標準とする。実プロファイル方式自体はオプションとして残してある
- v0.3.0 — Gemini バックエンド（`app/llm_gemini.py`）を追加し、`config.yaml` の `llm_backend` で ChatGPT と切り替え可能にした。あわせて普段使いの Edge プロファイルを使う `use_real_edge_profile` オプションを追加（ChatGPT は Cloudflare の検証が突破できないため一旦休止し、Gemini を試行）
- v0.2.0 — Edge の起動方式を CDP 接続に変更。Edge を通常プロセスとして起動し、Playwright が後から接続する方式にしたことで、Cloudflare の人間確認がループする問題への対策を強化
- v0.1.2 — Cloudflare の人間確認がループする対策として、Edge 起動時に自動操作フラグ（`--enable-automation` / `navigator.webdriver`）を抑えるよう修正
- v0.1.1 — setup.bat で `playwright install msedge` を自動実行するように修正
- v0.1.0 — Phase 1: テキスト入力 → ChatGPT Web (Playwright) → VOICEVOX 読み上げ
