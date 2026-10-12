@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Окружение и настройки лежат вне OneDrive: там тысячи файлов зависимостей только мешают,
rem а токен бота не должен синхронизироваться в облако.
set "VENV=%LOCALAPPDATA%\emoji-bot-venv"
set "PY=%VENV%\Scripts\python.exe"
set "DATA=%LOCALAPPDATA%\EzzProdBot"

if not exist "%PY%" (
  echo Первый запуск: создаю окружение...
  python -m venv "%VENV%" || goto :fail
)

"%PY%" -c "import fastapi, aiogram, aiosqlite, PIL, dotenv, multipart, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo Устанавливаю зависимости, это займёт несколько минут...
  "%PY%" -m pip install --quiet -r requirements.txt || goto :fail
)

if not exist "%DATA%\.env" if not exist ".env" (
  if not exist "%DATA%" mkdir "%DATA%"
  copy /y ".env.example" "%DATA%\.env" >nul
  echo.
  echo Создан файл настроек: %DATA%\.env
  echo Впиши BOT_TOKEN и WEBAPP_URL, сохрани и запусти run.bat снова. Подробности в README.md
  notepad "%DATA%\.env"
  goto :end
)

"%PY%" -m app
goto :end

:fail
echo.
echo Не получилось подготовить окружение. Нужен Python 3.11+ ^(python --version^).

:end
echo.
pause
