@echo off
setlocal

ruff check app.py
if errorlevel 1 exit /b %errorlevel%

python -m py_compile app.py
if errorlevel 1 exit /b %errorlevel%

prettier --check "templates/**/*.html" "static/**/*.css" "static/**/*.js"
if errorlevel 1 exit /b %errorlevel%

echo Lint checks passed.
