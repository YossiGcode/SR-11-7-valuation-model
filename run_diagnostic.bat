@echo off
cd C:\Users\yossi\SR-validation-engine
echo Running validation with diagnostic output...
.venv\Scripts\python.exe main.py
echo.
echo ======================================
echo Checking Excel output...
.venv\Scripts\python.exe test_diagnostic.py
