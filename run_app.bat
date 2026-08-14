@echo off
chcp 65001 >nul
title StockVision AI Launcher
echo ============================================
echo   StockVision AI - One-click Launcher
echo ============================================
echo.

cd /d "C:\Users\88697\Documents\GitHub\homework\MakeMoney App"

set "PYTHON=C:\Users\88697\miniconda3\python.exe"
set "APP=C:\Users\88697\Documents\GitHub\homework\MakeMoney App\app.py"
set "URL=http://localhost:8501"
set "MAX_WAIT=60"

:: 若服務已在執行，直接開啟瀏覽器
netstat -ano | findstr /r /c:":8501 .*LISTENING" > nul 2>&1
if not errorlevel 1 (
    echo [OK] StockVision AI 已在執行，直接開啟瀏覽器...
    start "" "%URL%"
    exit /b 0
)

:: 背景啟動 Streamlit 服務（獨立最小化視窗，關閉該視窗即停止服務）
echo [1/2] 背景啟動 StockVision AI 服務...
start "StockVision AI Server" /min "%PYTHON%" -m streamlit run "%APP%"

:: 輪詢健康檢查，等待服務就緒後再開啟瀏覽器
set /a waited=0
:wait_loop
ping -n 3 127.0.0.1 > nul
set /a waited+=2
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://localhost:8501/_stcore/health' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { exit 1 }" > nul 2>&1
if not errorlevel 1 goto ready
if %waited% geq %MAX_WAIT% (
    echo [WARN] 服務在 %MAX_WAIT% 秒內未就緒，仍開啟瀏覽器。
    echo        若頁面無法載入，請檢查 StockVision AI Server 視窗中的錯誤訊息。
    goto ready
)
echo        ...等待服務啟動中 (%waited%s)...
goto wait_loop

:ready
echo [2/2] 服務已就緒，開啟瀏覽器...
start "" "%URL%"

echo.
echo 完成。服務視窗可最小化；關閉「StockVision AI Server」視窗即停止服務。
exit /b 0
