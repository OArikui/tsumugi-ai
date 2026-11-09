@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ========================================
echo 春日部つむぎAI - Setup
echo ========================================
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] 仮想環境を作成しています...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo [ERROR] 仮想環境の作成に失敗しました。Python 3.11がインストールされているか確認してください。
        pause
        exit /b 1
    )
)
echo [INFO] 依存パッケージをインストールしています...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] パッケージのインストールに失敗しました。
    pause
    exit /b 1
)
echo.
echo [INFO] セットアップが完了しました。
echo.
echo [INFO] 初回のみ、以下のコマンドでEdgeの起動に必要なPlaywrightファイルを取得してください。
echo python -m playwright install msedge
REM 初回のみ実行してください。初回は Microsoft Edge のブラウザ起動に必要なファイルを取得します。
REM python -m playwright install msedge
echo.
pause
endlocal
