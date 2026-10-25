@echo off
chcp 65001 >nul
cd /d %~dp0
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "微信机器人控制台" pythonw -m bot.gui
) else (
  start "微信机器人控制台" python -m bot.gui
)
