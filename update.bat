@echo off
chcp 65001 >nul
title ВиК График Генератор — Обновяване
color 0E

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║     ВиК График Генератор — Обновяване           ║
echo  ╚══════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: Провери за Git
git --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  ✗ Git не е инсталиран.
    echo    Обновяването изисква Git.
    echo    Изтеглете нова версия ръчно от администратора.
    echo.
    pause
    exit /b 1
)

:: Запази локални промени
echo [1/4] Запазвам локални промени...
git stash >nul 2>&1
echo    ✓ Готово

:: Изтегли обновления
echo.
echo [2/4] Изтеглям обновления...
git pull origin main

if %ERRORLEVEL% NEQ 0 (
    echo    ✗ ГРЕШКА при изтегляне.
    echo      Проверете интернет връзката.
    git stash pop >nul 2>&1
    pause
    exit /b 1
)
echo    ✓ Готово

:: Обнови пакети
echo.
echo [3/4] Обновявам пакети...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet --upgrade
echo    ✓ Готово

:: Възстанови локални промени
echo.
echo [4/4] Възстановявам локални настройки...
git stash pop >nul 2>&1

:: Запази .env (не се презаписва от git pull, но за всеки случай)
if not exist ".env" (
    if exist ".env.company" (
        copy ".env.company" ".env" >nul
        echo    ✓ Конфигурацията е възстановена
    )
)

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║     ✓ Обновяването е завършено!                  ║
echo  ║     Стартирайте приложението с start.bat         ║
echo  ╚══════════════════════════════════════════════════╝
echo.

pause