@echo off
chcp 65001 >nul
echo.
echo ============================================
echo   Installing git pre-commit hook...
echo ============================================
echo.

if not exist ".git\hooks" (
    echo ERROR: .git\hooks directory not found.
    echo Make sure you run this from the schedule-generator directory.
    pause
    exit /b 1
)

copy /Y "hooks\pre-commit" ".git\hooks\pre-commit" >nul
echo OK: Pre-commit hook installed.
echo.
echo The hook will run unit tests + E2E tests before each commit.
echo To skip the hook (not recommended): git commit --no-verify
echo.
pause
