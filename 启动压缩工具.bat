@echo off
setlocal
set "PYW=D:\miniconda3\pythonw.exe"
set "PY=D:\miniconda3\python.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0gui.py"
    goto :eof
)
if exist "%PY%" (
    "%PY%" "%~dp0gui.py"
    goto :eof
)
echo Python not found: D:\miniconda3
pause
