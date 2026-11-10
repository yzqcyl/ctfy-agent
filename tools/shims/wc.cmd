@echo off
if defined CTFY_PYTHON ("%CTFY_PYTHON%" "%~dp0shim.py" wc %*) else (python "%~dp0shim.py" wc %*)
