@echo off
rem Launch the CTF GUI (no console window).
cd /d "%~dp0"
start "" "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe" "%~dp0run_gui.pyw"
exit /b
