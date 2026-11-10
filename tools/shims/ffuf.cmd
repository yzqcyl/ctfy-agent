@echo off
if defined CTFY_PYTHON ("%CTFY_PYTHON%" "%~dp0ffuf_shim.py" %*) else (python "%~dp0ffuf_shim.py" %*)
