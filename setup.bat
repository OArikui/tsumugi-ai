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
echo [INFO] Playwright用のEdgeブラウザファイルを取得しています...
".venv\Scripts\python.exe" -m playwright install msedge
if errorlevel 1 (
    echo [ERROR] Playwrightブラウザファイルの取得に失敗しました。
    echo [INFO] 手動で次のコマンドを実行してください: python -m playwright install msedge
    pause
    exit /b 1
)
echo.
echo [INFO] セットアップが完了しました。
echo.
pause
endlocal
