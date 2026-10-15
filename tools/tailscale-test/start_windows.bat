@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul && (py -3 ap_test.py %*) || (python ap_test.py %*)
pause
