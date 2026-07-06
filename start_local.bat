@echo off
setlocal EnableExtensions
chcp 65001 >nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1" %*
exit /b %ERRORLEVEL%
