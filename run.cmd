@echo off
REM 启动 bilisum 本地 Web 界面
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [错误] 找不到虚拟环境，请先运行: uv venv --python 3.12 ^&^& uv pip install -e .
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m bilisum.web
pause
