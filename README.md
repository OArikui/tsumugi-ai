# 春日部つむぎAI

Gemini / ChatGPT WebとVOICEVOXを連携させた音声対話アプリケーションです。使用するLLMは config.yaml の llm_backend で切り替えます。

v0.6.0ではLive2Dアバター表示とVOICEVOXの再生音量に応じた口パク機能を追加しました。

## 必要環境

- Windows 11
- Python 3.11（64bit推奨）
- Microsoft Edge
- VOICEVOX
- インターネット接続

Live2Dを使用する場合は、追加で以下が必要です。

- live2d-py
- GLFW
- PyOpenGL
- Live2D Cubism Core DLL
- 利用許諾を確認したCubism 3以降のモデル

## セットアップ

### 1. VOICEVOXを起動する

VOICEVOXを起動し、次のAPIにアクセスできることを確認してください。

http://127.0.0.1:50021

### 2. Python環境をセットアップする

PowerShellでプロジェクトフォルダに移動し、実行します。

~~~powershell
.\setup.bat
~~~

PlaywrightのEdge用ファイル取得に失敗した場合は、仮想環境を有効にして次を実行してください。

~~~powershell
.\.venv\Scripts\Activate.ps1
python -m playwright install msedge
~~~

## 初回ログイン

初回は使用するLLMに手動でログインする必要があります。

- Gemini: Googleアカウント
- ChatGPT: ChatGPTアカウント

起動したEdgeの画面からログインしてください。ログイン状態はEdgeプロファイルに保存されます。

use_real_edge_profile が true の場合は、アプリ起動前にEdgeを完全に終了してください。

## 起動

~~~powershell
.\run.bat
~~~

終了する場合は exit と入力するか、Ctrl+Cを押してください。

---

# Phase 2: Live2D

## 機能

- Live2Dモデルを独立ウィンドウに表示
- VOICEVOXのRMS音量に応じた口パク
- 自動まばたき
- sin波による呼吸表現
- モデルにIdleモーションが定義されていれば起動時に再生
- Live2Dが利用できない場合はアバターなしで音声対話を継続

描画は専用スレッドで実行し、音声再生側からは口の開き具合のみをスレッドセーフに更新します。

## 1. live2d-pyのインストール

requirements.txtには次のパッケージを追加しています。

- live2d-py
- glfw
- PyOpenGL

通常のセットアップを再実行してください。

~~~powershell
.\setup.bat
~~~

公式リポジトリ:
https://github.com/EasyLive2D/live2d-py

PyPI:
https://pypi.org/project/live2d-py/

## 2. Cubism Core DLLの準備

重要: live2d-pyの配布物には、ライセンス上の理由からLive2D Cubism Coreは同梱されていません。Live2D公式サイトからCubism SDK for Nativeを取得し、利用条件に従ってCore DLLを用意してください。

公式ダウンロード:
https://www.live2d.com/en/sdk/download/native/

Windows 64bitの場合は、SDK内の次のDLLを使用します。

~~~text
Core/
└── dll/
    └── windows/
        └── x86_64/
            └── Live2DCubismCore.dll
~~~

取得したDLLは、次のいずれかに配置してください。

1. プロジェクト直下の Live2DCubismCore.dll
2. プロジェクト直下の Core/dll/windows/x86_64/Live2DCubismCore.dll
3. LIVE2D_CORE_DLL 環境変数で指定した絶対パス

PowerShellで環境変数を設定する例:

~~~powershell
$env:LIVE2D_CORE_DLL = "C:\Live2D\Core\dll\windows\x86_64\Live2DCubismCore.dll"
~~~

PythonとDLLのアーキテクチャを一致させてください。DLLが見つからない、または読み込めない場合は警告を表示し、Live2Dなしで起動します。

Cubism Core DLLはライセンス上の理由からリポジトリに含めないでください。

## 3. Live2Dモデルの配置

モデルファイルはリポジトリに含めていません。利用者が利用許諾を確認したモデルを用意してください。

既定のモデル配置先は次のとおりです。

~~~text
tsumugi-ai/
├── assets/
│   └── live2d/
│       └── tsumugi/
│           ├── model.model3.json
│           ├── model.moc3
│           ├── textures/
│           ├── motions/
│           ├── expressions/
│           └── physics/
├── app/
├── main.py
└── config.yaml
~~~

これは配置例です。実際にはmodel.model3.json内の相対パス指定に従って、MOC3、テクスチャ、モーション、物理演算などの関連ファイルを配置してください。

モデルデータはリポジトリに追加しないでください。各利用者が自身で用意します。

## 4. config.yamlの設定

Live2Dは既定で無効です。次の設定を変更すると有効になります。

~~~yaml
live2d_enabled: true
live2d_model_path: "./assets/live2d/tsumugi/model.model3.json"
live2d_width: 480
live2d_height: 640
live2d_mouth_scale: 3.0
~~~

| 設定 | 説明 | 既定値 |
|---|---|---|
| live2d_enabled | Live2Dを使用するか | false |
| live2d_model_path | CubismモデルJSONのパス | 上記配置例 |
| live2d_width | ウィンドウ幅 | 480 |
| live2d_height | ウィンドウ高さ | 640 |
| live2d_mouth_scale | 口パクの感度 | 3.0 |

設定キーがconfig.yamlに存在しない場合は、main.py側の既定値を使用します。

## 5. 口パク

VOICEVOXの再生コールバックから音声チャンクごとのRMSを受け取ります。

RMSに live2d_mouth_scale を掛け、0.0〜1.0に制限した値をLive2Dに渡します。

Live2D側では描画スレッドが次のパラメータを更新します。

~~~text
ParamMouthOpenY
~~~

音声再生が終了したときは、口の開き具合を0.0に戻します。Live2Dが無効、または起動に失敗した場合はRMSコールバックから口パク処理を行いません。

## 6. 待機中の演出

### まばたき

次のパラメータを数秒間隔で短時間閉じるように更新します。

~~~text
ParamEyeLOpen
ParamEyeROpen
~~~

### 呼吸

sin波を使い、次のパラメータをゆっくり変化させます。

~~~text
ParamBreath
~~~

各パラメータがモデルに存在する場合だけ更新します。

### Idleモーション

モデルJSONの FileReferences.Motions.Idle を確認し、Idleグループが定義されていれば最初のモーションを起動時に再生します。Idleがないモデルでは省略します。

## 7. トラブルシューティング

### Live2Dが表示されない

- live2d-py、glfw、PyOpenGLがインストールされているか確認してください。
- Live2DCubismCore.dllが配置されているか確認してください。
- PythonとDLLのアーキテクチャが一致しているか確認してください。
- モデルJSONと参照ファイルが存在するか確認してください。
- OpenGLを利用できる環境か確認してください。

Live2Dの初期化に失敗しても、VOICEVOXとLLMによる音声対話は継続します。

### VOICEVOXに接続できない

- VOICEVOXが起動しているか確認してください。
- http://127.0.0.1:50021 にアクセスできるか確認してください。
- config.yamlの voicevox_url を確認してください。

### LLMにログインできない

- 起動したEdgeの画面で手動ログインしてください。
- config.yamlのbrowser_profile_dirを確認してください。
- use_real_edge_profile が true の場合は、起動前に通常のEdgeを完全に終了してください。

### 音声が出ない

- Windowsの出力デバイスと音量を確認してください。
- VOICEVOXの話者IDが春日部つむぎ（speaker=8）になっているか確認してください。

---

# LLMの変更

config.yamlの llm_backend を gemini または chatgpt に変更します。

新しいLLMを追加する場合は、app/llm_base.pyのLLMBackendを継承し、main.pyのcreate_llm()に追加してください。

---

# バージョン履歴

- v0.6.0 — Phase 2。Live2DViewerを追加。別スレッドでのモデル描画、VOICEVOX RMS連携による口パク、自動まばたき、呼吸表現、Idleモーション再生に対応。Live2Dが利用できない場合はアバターなしで継続するフォールバックを追加。
- v0.5.1 — 処理が完了したときにバーを全部塗りつぶした状態で1行残すよう変更。例外時は完了表示を出さずに行を消す。
- v0.5.0 — 状態表示をバー表示に変更。処理中は流れるバー、再生中は再生進捗バーと音量メーターをコンソールの1行に表示。
- v0.4.0 — 音声を全文一括で合成してから再生する方式に変更。文単位の合成は tts_split_by_sentence: true で利用可能。
- v0.3.1 — use_real_edge_profile の既定値を false に変更し、専用Edgeプロファイルを標準にした。
- v0.3.0 — Geminiバックエンドを追加し、config.yamlのllm_backendでChatGPTと切り替え可能にした。
- v0.2.0 — Edgeの起動方式をCDP接続に変更。
- v0.1.2 — Cloudflareの人間確認がループする問題への対策としてEdge起動時の自動操作フラグを調整。
- v0.1.1 — setup.batでPlaywrightのEdge用ファイルを自動取得するように修正。
- v0.1.0 — Phase 1。テキスト入力 → ChatGPT Web → VOICEVOX読み上げ。
