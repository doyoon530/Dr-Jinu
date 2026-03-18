@echo off
setlocal

if "%HOST%"=="" set HOST=0.0.0.0
if "%PORT%"=="" set PORT=5000
if "%WAITRESS_THREADS%"=="" set WAITRESS_THREADS=8
if "%LLM_PROVIDER%"=="" set LLM_PROVIDER=local

echo Starting NCAI server on http://%HOST%:%PORT%  [provider=%LLM_PROVIDER%]
python app.py
