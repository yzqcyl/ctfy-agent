@echo off
if defined CTFY_PYTHON ("%CTFY_PYTHON%" "%~dp0shim.py" xxd %*) else (python "%~dp0shim.py" xxd %*)
