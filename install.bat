@echo off
chcp 65001 >nul
title ВиК График Генератор — Инсталация
color 0A

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║     ВиК График Генератор — Инсталация           ║
echo  ║     РАИ Комерс                                  ║
echo  ╚══════════════════════════════════════════════════╝
echo.

:: ======================================
:: СТЪПКА 1: Провери за Python
:: ======================================
echo [1/5] Проверявам за Python...

python --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo    ✓ Python е наличен
    python --version
    goto :PYTHON_OK
)

python3 --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo    ✓ Python3 е наличен
    set PYTHON_CMD=python3
    goto :PYTHON_OK
)

:: Python не е намерен — инсталирай
echo    ✗ Python не е намерен. Инсталирам...
echo.

:: Провери дали имаме вграден Python installer
if exist "%~dp0tools\python-installer.exe" (
    echo    Намерен е вграден инсталатор.
    "%~dp0tools\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=0
    goto :CHECK_PYTHON_AFTER_INSTALL
)

:: Свали Python от интернет
echo    Свалям Python 3.12 от python.org...
echo    (Това може да отнеме 1-2 минути)
echo.

:: Използвай PowerShell за сваляне
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%TEMP%\python_installer.exe'}"

if not exist "%TEMP%\python_installer.exe" (
    echo.
    echo    ✗ ГРЕШКА: Не мога да сваля Python.
    echo      Моля инсталирайте Python 3.12 ръчно от:
    echo      https://www.python.org/downloads/
    echo      ВАЖНО: Сложете отметка "Add Python to PATH"!
    echo.
    pause
    exit /b 1
)

echo    Инсталирам Python 3.12...
"%TEMP%\python_installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=0

:CHECK_PYTHON_AFTER_INSTALL
:: Обнови PATH за текущата сесия
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"

python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo    ✗ ГРЕШКА: Python инсталацията не успя.
    echo      Моля инсталирайте Python 3.12 ръчно от:
    echo      https://www.python.org/downloads/
    echo      ВАЖНО: Сложете отметка "Add Python to PATH"!
    echo.
    pause
    exit /b 1
)

echo    ✓ Python инсталиран успешно
python --version

:PYTHON_OK

:: ======================================
:: СТЪПКА 2: Създай виртуална среда
:: ======================================
echo.
echo [2/5] Създавам виртуална среда...

cd /d "%~dp0"

if exist "venv" (
    echo    ✓ Виртуалната среда вече съществува
) else (
    python -m venv venv
    if %ERRORLEVEL% NEQ 0 (
        echo    ✗ ГРЕШКА: Не мога да създам виртуална среда.
        pause
        exit /b 1
    )
    echo    ✓ Виртуална среда създадена
)

:: ======================================
:: СТЪПКА 3: Инсталирай пакети
:: ======================================
echo.
echo [3/5] Инсталирам необходимите пакети...
echo    (Това може да отнеме 2-5 минути при първата инсталация)
echo.

call venv\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo    ✗ ГРЕШКА: Инсталацията на пакетите не успя.
    echo      Проверете интернет връзката и опитайте отново.
    pause
    exit /b 1
)

echo    ✓ Всички пакети инсталирани

:: ======================================
:: СТЪПКА 4: Създай .env с фирмени ключове
:: ======================================
echo.
echo [4/5] Настройвам конфигурацията...

if exist ".env" (
    echo    ✓ Конфигурацията вече съществува (.env)
) else (
    if exist ".env.company" (
        copy ".env.company" ".env" >nul
        echo    ✓ Фирмена конфигурация приложена
    ) else (
        echo    ✗ ВНИМАНИЕ: Не е намерен .env.company файл.
        echo      Копирайте .env.example в .env и попълнете ключовете.
        copy ".env.example" ".env" >nul
    )
)

:: ======================================
:: СТЪПКА 5: Създай shortcut на десктопа
:: ======================================
echo.
echo [5/5] Създавам пряк път на десктопа...

:: Използвай PowerShell за създаване на shortcut
powershell -Command "& {$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\ВиК Графици.lnk'); $s.TargetPath = '%~dp0start.bat'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = '%SystemRoot%\System32\SHELL32.dll,21'; $s.Description = 'ВиК График Генератор — РАИ Комерс'; $s.Save()}"

if %ERRORLEVEL% EQU 0 (
    echo    ✓ Пряк път "ВиК Графици" създаден на десктопа
) else (
    echo    ! Не успях да създам пряк път. Използвайте start.bat директно.
)

:: ======================================
:: ГОТОВО
:: ======================================
echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║     ✓ Инсталацията е завършена успешно!          ║
echo  ║                                                  ║
echo  ║     Стартирайте приложението:                    ║
echo  ║     • Двоен клик на "ВиК Графици" на десктопа   ║
echo  ║     • Или двоен клик на start.bat                ║
echo  ╚══════════════════════════════════════════════════╝
echo.

pause