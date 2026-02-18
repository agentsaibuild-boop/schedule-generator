@echo off
echo Starting VIK Schedule Generator...
cd /d "%~dp0"
streamlit run app.py
pause
