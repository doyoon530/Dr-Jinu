@echo off
setlocal

ruff check --fix app.py
if errorlevel 1 exit /b %errorlevel%

ruff format app.py
if errorlevel 1 exit /b %errorlevel%

prettier --write "templates/**/*.html" "static/**/*.css" "static/**/*.js"
if errorlevel 1 exit /b %errorlevel%

echo Formatting completed.
