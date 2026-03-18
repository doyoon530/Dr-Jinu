@echo off
setlocal

set PATH=%ProgramFiles%\nodejs;%PATH%
npm run format
if errorlevel 1 exit /b %errorlevel%

echo Formatting completed.
