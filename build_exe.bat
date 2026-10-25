@echo off
chcp 65001 >nul
cd /d %~dp0
echo [打包] 安装 PyInstaller...
pip install pyinstaller -q
echo [打包] 开始构建（约 1-3 分钟）...
python -m PyInstaller --noconfirm --onefile --windowed --name 微信群机器人面板 --workpath build --distpath dist gui_entry.py
if %errorlevel%==0 (
  echo [打包] 完成：dist\微信群机器人面板.exe
) else (
  echo [打包] 失败，请把上方报错发给开发者
)
pause
