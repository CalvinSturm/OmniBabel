@echo off
setlocal

set "VENV_PYTHON=.\venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [Startup] Project virtual environment not found at .\venv
    echo [Startup] Create it with:
    echo   py -3.10 -m venv venv
    echo   .\venv\Scripts\python.exe -m pip install -r requirements.txt
    goto :end
)

echo [Startup] Using interpreter: %VENV_PYTHON%
"%VENV_PYTHON%" main.py

:end
pause
