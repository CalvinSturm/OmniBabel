@echo off
setlocal

if not exist ".\venv\Scripts\activate.bat" (
    echo [Startup] Project virtual environment not found at .\venv
    echo [Startup] Create it with:
    echo   py -3.12 -m venv venv
    echo   .\venv\Scripts\activate
    echo   python -m pip install -r requirements.txt
    goto :end
)

call .\venv\Scripts\activate.bat
python main.py

:end
pause    
