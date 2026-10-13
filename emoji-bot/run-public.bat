@echo off
chcp 65001 >nul
rem Бот + публичный адрес через localhost.run с автоматическим обновлением WEBAPP_URL (см. tools\run-public.ps1).
rem Первый запуск делай через run.bat: он создаёт окружение и файл настроек.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run-public.ps1"
pause
