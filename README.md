# 春日部つむぎAI — Phase 1

ChatGPT WebとVOICEVOXを連携させた音声対話アプリケーションです。

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

初回はChatGPTに手動でログインする必要があります。`run.bat`を実行するとEdgeが起動します。ログイン画面が表示されたら、Edgeの画面で直接ログインしてください。ログイン後、コンソールに表示される案内に従って操作します。2回目以降は不要です（ログイン状態は `browser_profile/` に保持されます）。

## 起動

```powershell
.\run.bat
```

終了する場合は、`exit`と入力するか、Ctrl+Cを押します。

## 設定

`config.yaml`で設定を変更できます。

- `voicevox_url`: VOICEVOXのURL
- `speaker`: VOICEVOXのスピーカーID（春日部つむぎ = 8）
- `browser_profile_dir`: 専用Edgeのプロファイルディレクトリ（プロジェクト直下の `./browser_profile`）
- `cdp_port`: EdgeへのCDP接続ポート（既定値 9222）
- `chatgpt_url`: ChatGPTのURL
- `system_prompt`: 春日部つむぎとしての応答指示

## LLMの変更

LLMの差し替えは、`app/llm_base.py`の`LLMBackend`を継承して新しいクラスを作成し、`main.py`の`create_llm()`を変更してください。

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

- v0.2.0 — Edge の起動方式を CDP 接続に変更。Edge を通常プロセスとして起動し、Playwright が後から接続する方式にしたことで、Cloudflare の人間確認がループする問題への対策を強化
- v0.1.2 — Cloudflare の人間確認がループする対策として、Edge 起動時に自動操作フラグ（`--enable-automation` / `navigator.webdriver`）を抑えるよう修正
- v0.1.1 — setup.bat で `playwright install msedge` を自動実行するように修正
- v0.1.0 — Phase 1: テキスト入力 → ChatGPT Web (Playwright) → VOICEVOX 読み上げ
