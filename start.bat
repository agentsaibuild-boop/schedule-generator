@echo off
chcp 65001 >nul
title ВиК График Генератор
color 0B

echo.
echo  Стартирам ВиК График Генератор...
echo.

cd /d "%~dp0"

:: Провери за venv
if not exist "venv\Scripts\activate.bat" (
    echo  ✗ Приложението не е инсталирано!
    echo    Стартирайте install.bat първо.
    echo.
    pause
    exit /b 1
)

:: Провери за .env
if not exist ".env" (
    echo  ✗ Липсва конфигурационен файл (.env)!
    echo    Стартирайте install.bat първо.
    echo.
    pause
    exit /b 1
)

:: Активирай venv
call venv\Scripts\activate.bat

:: Провери за обновления на пакетите (тихо, бързо)
pip install -r requirements.txt --quiet --upgrade 2>nul

:: Стартирай Streamlit
echo  ✓ Приложението стартира...
echo  ✓ Браузърът ще се отвори автоматично.
echo.
echo  ┌──────────────────────────────────────┐
echo  │  Адрес: http://localhost:8501        │
echo  │  За спиране: Ctrl+C или затворете    │
echo  │  този прозорец                       │
echo  └──────────────────────────────────────┘
echo.

streamlit run app.py --server.headless=false --server.port=8501 --browser.gatherUsageStats=false --theme.primaryColor="#5B9BD5" --theme.backgroundColor="#FFFFFF" --theme.secondaryBackgroundColor="#F0F2F6" --theme.textColor="#262730"