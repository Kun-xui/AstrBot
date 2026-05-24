@echo off
chcp 65001 >nul
echo ========================================
echo  GPT-SoVITS 一键启动脚本
echo  用于角色扮演插件 TTS 语音合成
echo ========================================
echo.

set SOVITS_DIR=C:\GPT-SoVITS

if not exist "%SOVITS_DIR%" (
    echo [错误] GPT-SoVITS 目录未找到: %SOVITS_DIR%
    echo 请修改此脚本中的 SOVITS_DIR 变量指向你的 GPT-SoVITS 安装目录
    pause
    exit /b 1
)

echo [信息] 正在启动 GPT-SoVITS API 服务...
echo [信息] 目录: %SOVITS_DIR%

cd /d "%SOVITS_DIR%"

echo [信息] 启动 api_v2.py (端口 9880)...
start "GPT-SoVITS-API" python api_v2.py

echo [信息] 等待服务启动... (约30秒)
timeout /t 10 /nobreak >nul

echo [信息] 检查服务状态...
curl -s http://127.0.0.1:9880/ >nul 2>&1
if %errorlevel% equ 0 (
    echo [成功] GPT-SoVITS API 已启动: http://127.0.0.1:9880
) else (
    echo [提示] 服务可能仍在启动中，请稍候再检查
)

echo.
echo ========================================
echo  启动完成，请将此窗口最小化
echo  角色扮演插件将自动连接到此服务
echo ========================================
pause
