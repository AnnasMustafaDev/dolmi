@echo off
title Dolmi Setup
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
