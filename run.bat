@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 仮想環境がありません。先に setup.bat を実行してください。
    pause
    exit /b 1
)
if not exist "config.yaml" (
    echo [ERROR] config.yaml が見つかりません。
    pause
    exit /b 1
)
echo 春日部つむぎAI を起動しています...
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 (
    echo.
    echo [ERROR] アプリケーションが異常終了しました。
    pause
    exit /b 1
)
echo.
echo 春日部つむぎAI を終了しました。
pause
endlocal
