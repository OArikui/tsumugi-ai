# 春日部つむぎAI

Gemini / ChatGPT WebとVOICEVOXを連携させた音声対話アプリケーションです。使用するLLMは config.yaml の llm_backend で切り替えます。

v0.7.0ではLLMの感情タグに応じた表情、長時間待機時の自然な休憩動作、実行ごとのログファイル出力に対応しました。

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
- LLMの感情タグ（normal / happy / sad / shocked / shy）に応じた表情
- VOICEVOXのRMS音量に応じた口パク
- 自動まばたき
- sin波による呼吸表現
- 待機中の角度・体のゆらゆら揺れ
- 20秒以上口が動かなければ、長めの閉眼・視線移動・小さな首振りを行う自然な休憩状態
- モデルにIdleモーションが定義されていれば起動時に再生
- 実行ごとのUTF-8ログファイル（logs/）とコンソールへの同時出力
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

既定の公式モデル配置先は次のとおりです。

~~~text
tsumugi-ai/
├── assets/
│   └── live2d/
│       └── tsumugi/
│           ├── 春日部つむぎ公式live2Dモデル.model3.json
│           ├── *.moc3
│           ├── textures/
│           ├── motions/
│           └── expressions/
├── app/
├── main.py
└── config.yaml
~~~

これは配置例です。実際にはmodel3.json内の相対パス指定に従って、MOC3、テクスチャ、モーション、物理演算などの関連ファイルを配置してください。

## 4. config.yamlの設定

Live2Dは既定で有効です。モデルやCubism Core DLLが利用できない環境では警告を出し、アバターなしで音声対話を継続します。

~~~yaml
live2d_enabled: true
live2d_model_path: "./assets/live2d/tsumugi/春日部つむぎ公式live2Dモデル.model3.json"
live2d_width: 480
live2d_height: 640
live2d_mouth_scale: 3.0
live2d_mouth_param: ""
live2d_idle_sway: true
live2d_natural_idle: true
live2d_expressions:
  normal: null
  happy: "きらーん"
  sad: "うるうる"
  shocked: "がーん"
  shy: "照れ"
log_to_file: true
log_dir: "logs"
~~~

| 設定 | 説明 | 既定値 |
|---|---|---|
| live2d_enabled | Live2Dを使用するか | true |
| live2d_model_path | CubismモデルJSONのパス | 上記配置例 |
| live2d_width | ウィンドウ幅 | 480 |
| live2d_height | ウィンドウ高さ | 640 |
| live2d_mouth_scale | 口パクの感度 | 3.0 |
| live2d_mouth_param | 口開閉パラメータID。空文字なら自動判定 | 空文字 |
| live2d_idle_sway | 待機中の角度・体のゆらゆら揺れ | true |
| live2d_natural_idle | 20秒以上口が動かないときの自然な休憩状態 | true |
| live2d_expressions | 感情ごとの表情名。nullはnormal | 下記の例 |
| log_to_file | 実行ごとのログファイルを出力するか | true |
| log_dir | ログの保存先（相対パスはプロジェクトルート基準） | logs |

設定キーがconfig.yamlに存在しない場合は、main.py側の既定値を使用します。

## 5. 感情表現

LLMには返答の先頭へ次のいずれか1つを付けるよう system_prompt で指示します。

| タグ | 意味 | 既定の表情 |
|---|---|---|
| [emotion:normal] | 通常 | 表情をリセット |
| [emotion:happy] | うれしい | きらーん |
| [emotion:sad] | 悲しい | うるうる |
| [emotion:shocked] | 驚き | がーん |
| [emotion:shy] | 照れ | 照れ |

main.py は先頭のタグを解析して読み上げ本文から取り除きます。先頭以外に誤って出力された感情タグも読み上げないよう除去します。タグがない、または種類が不正な場合はnormalとして扱います。読み上げが終わるとnormalへ戻します。

表情名は live2d_expressions で変更できます。値は model3.json の FileReferences.Expressions に登録された Name と一致させてください。null は通常表情へのリセットです。

~~~yaml
live2d_expressions:
  normal: null
  happy: "きらーん"
  sad: "うるうる"
  shocked: "がーん"
  shy: "照れ"
~~~

モデルの表情APIが使えない場合は、Param_eye_uru / Param_eye_shitake / Param_tear / Param_cheek などのパラメータを大文字小文字を区別せず検索し、感情プリセットを適用します。どちらの方式も使えない場合は警告を出し、Live2Dと音声対話は継続します。

## 6. 自然な休憩状態

live2d_natural_idle: true の場合、最後に口パクの値が変化してから20秒以上経過すると休憩状態になります。休憩中は、ときどきゆっくり目を閉じて数秒保ち、視線を小さく動かし、周期的にわずかに横を向きます。口が再び動くと通常の待機状態へ戻ります。

~~~yaml
live2d_natural_idle: true
~~~

live2d_idle_sway は従来の角度・体のゆらゆら揺れを制御し、live2d_natural_idle は休憩時の閉眼・視線・首振りを制御します。両者は独立して切り替えられます。

## 7. ログファイル

log_to_file: true の場合、起動ごとに logs/tsumugi-YYYYMMDD-HHMMSS.log を作成します。文字コードはUTF-8で、コンソールと同じ書式のログを同時に記録します。保存先は log_dir で変更できます。ログファイルと logs/ フォルダはGit管理対象外です。

~~~yaml
log_to_file: true
log_dir: "logs"
~~~

## 8. パラメータIDの自動判定

モデルごとにCubismパラメータIDの表記が異なるため、起動時に model.GetParamIds() から実際のIDを取得し、候補名を大文字小文字を区別せず照合します。

口・眼・呼吸は次の候補に対応しています。

| 用途 | 標準形式 | 公式モデル形式 |
|---|---|---|
| 口の開閉 | ParamMouthOpenY | PARAM_MOUTH_OPEN_Y |
| 左眼の開閉 | ParamEyeLOpen | PARAM_EYE_L_OPEN |
| 右眼の開閉 | ParamEyeROpen | PARAM_EYE_R_OPEN |
| 呼吸 | ParamBreath | PARAM_BREATH |

解決した実際のパラメータIDは起動時にINFOログへ出力されます。

口のパラメータを明示したい場合は、live2d_mouth_param にモデルのIDを指定してください。例:

~~~yaml
live2d_mouth_param: "PARAM_MOUTH_OPEN_Y"
~~~

空文字の場合は自動判定します。指定したIDが見つからなければ標準候補による自動判定へフォールバックします。

口のIDが解決できない場合は警告を出して口パクだけを省略します。眼や呼吸のパラメータがない場合も、その演出だけを省略して表示を継続します。

## 9. 待機中のゆらゆら揺れ

live2d_idle_sway: true の場合、描画フレームごとに角度・体のパラメータへ小さなsin波を加算します。

| パラメータ | 振幅 | 周期 |
|---|---:|---:|
| 角度X | ±3.0 | 約6.3秒 |
| 角度Y | ±2.0 | 約7.9秒 |
| 角度Z | ±1.5 | 約9.1秒 |
| 体の回転X | ±1.5 | 約6.3秒 |
| 体の回転Z | ±1.0 | 約10.3秒 |

存在するパラメータだけに適用します。AddParamByIdで加算するため、Idleモーションなどの基礎動作に小さな揺れを重ねます。

無効にする場合:

~~~yaml
live2d_idle_sway: false
~~~

## 10. 口パク

VOICEVOXの再生コールバックから音声チャンクごとのRMSを受け取ります。

RMSに live2d_mouth_scale を掛け、0.0〜1.0に制限した値をLive2Dに渡します。

音声再生終了後は、口の開き具合を0.0に戻します。

## 11. 待機中の演出

### まばたき

ParamEyeLOpen / PARAM_EYE_L_OPEN と ParamEyeROpen / PARAM_EYE_R_OPEN を数秒間隔で短時間閉じるように更新します。

### 呼吸

ParamBreath / PARAM_BREATH をsin波でゆっくり変化させます。

### Idleモーション

モデルJSONの FileReferences.Motions.Idle を確認し、Idleグループが定義されていれば最初のモーションを起動時に再生します。

## 12. トラブルシューティング

### Live2Dが表示されない

- live2d-py、glfw、PyOpenGLがインストールされているか確認してください。
- Live2DCubismCore.dllが配置されているか確認してください。
- PythonとDLLのアーキテクチャが一致しているか確認してください。
- モデルJSONと参照ファイルが存在するか確認してください。
- OpenGLを利用できる環境か確認してください。

Live2Dの初期化に失敗しても、VOICEVOXとLLMによる音声対話は継続します。

### 口パクやまばたきが動かない

- 起動ログで実際に解決されたパラメータIDを確認してください。
- 公式モデルでは PARAM_MOUTH_OPEN_Y、PARAM_EYE_L_OPEN、PARAM_EYE_R_OPEN などの大文字形式が使われる場合があります。
- 口のIDは live2d_mouth_param で明示指定できます。

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

- v0.7.0 — LLMの先頭感情タグを解析して読み上げ文から除去し、Live2D表情APIまたは表情パラメータプリセットで感情表現を追加。20秒以上の無発話時に閉眼・視線移動・小さな首振りを行う自然な休憩状態、Live2D既定有効化、実行ごとのUTF-8ログファイル出力に対応。
- v0.6.1 — 公式Live2Dモデルの PARAM_ 形式パラメータIDに対応。口・眼・呼吸パラメータを大文字小文字を区別せず自動解決し、live2d_mouth_param による口パラメータの明示指定を追加。待機中の角度・体のゆらゆら揺れと live2d_idle_sway 設定を追加。
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
