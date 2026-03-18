@echo off
setlocal

python -m ruff check --fix app.py ncai_app
if errorlevel 1 exit /b %errorlevel%

python -m ruff format app.py ncai_app
if errorlevel 1 exit /b %errorlevel%

"%ProgramFiles%\nodejs\node.exe" "%~dp0node_modules\prettier\bin\prettier.cjs" --write "templates/**/*.html" "static/**/*.css" "static/**/*.js"
if errorlevel 1 exit /b %errorlevel%

echo Formatting completed.
