@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 导出角色 ZIP

set "ROLES_DIR=C:\Users\Administrator\AstrBot\source\data\plugins\astrbot_plugin_roleplay\data\roles"
set "OUT_DIR=%USERPROFILE%\Desktop"

echo.
echo ╔══════════════════════════════════╗
echo ║    📤 Roleplay 导出角色 ZIP     ║
echo ╚══════════════════════════════════╝
echo.

if not exist "%ROLES_DIR%" (
    echo [错误] 未找到角色目录
    pause
    exit /b 1
)

echo 📂 已安装的角色:
echo ─────────────────────────────────
set IDX=0
for /d %%d in ("%ROLES_DIR%\*") do (
    set "NAME=%%~nxd"
    if exist "%%d\config.yaml" (
        set /a IDX+=1
        echo   !IDX!. !NAME!
    )
)
if !IDX! equ 0 (
    echo    (无已安装角色)
    pause
    exit /b 0
)
echo ─────────────────────────────────
echo.

:ask_name
set "ROLE_NAME="
set /p ROLE_NAME="请输入要导出的角色名（或输入序号）: "

REM 如果是序号，转换为角色名
set "TEST_NUM=%ROLE_NAME%"
set "IS_NUM=1"
for /f "delims=0123456789" %%i in ("%TEST_NUM%") do set "IS_NUM="
if defined IS_NUM (
    set /a NUM=%ROLE_NAME% 2>nul
    set IDX=0
    for /d %%d in ("%ROLES_DIR%\*") do (
        if exist "%%d\config.yaml" (
            set /a IDX+=1
            if !IDX! equ !NUM! set "ROLE_NAME=%%~nxd"
        )
    )
)

if "%ROLE_NAME%"=="" (
    echo [错误] 角色名不能为空
    goto :ask_name
)

set "SRC_DIR=%ROLES_DIR%\%ROLE_NAME%"
if not exist "%SRC_DIR%" (
    echo [错误] 角色 "%ROLE_NAME%" 不存在
    goto :ask_name
)
if not exist "%SRC_DIR%\config.yaml" (
    echo [错误] 角色 "%ROLE_NAME%" 缺少 config.yaml
    goto :ask_name
)

set "ZIP_FILE=%OUT_DIR%\%ROLE_NAME%.zip"
if exist "%ZIP_FILE%" del "%ZIP_FILE%" 2>nul

echo.
echo 📦 正在打包: %ROLE_NAME% ...

powershell -NoProfile -Command ^
    "$src='%SRC_DIR%'; $zip='%ZIP_FILE%';" ^
    "if (Test-Path $zip) { Remove-Item $zip -Force };" ^
    "Compress-Archive -Path $src\* -DestinationPath $zip -Force;" ^
    "if ($?) { Write-Output 'SUCCESS' } else { Write-Output 'FAIL' }"

if exist "%ZIP_FILE%" (
    for %%f in ("%ZIP_FILE%") do set "SIZE=%%~zf"
    set /a SIZEMB=!SIZE! / 1048576

    echo.
    echo ══════════════════════════════════
    echo ✅ 导出成功!
    echo.
    echo 📦 文件: %ROLE_NAME%.zip
    echo 📂 位置: %OUT_DIR%\
    echo 📏 大小: !SIZEMB! MB
    echo 🔒 注意: 此 ZIP 包含原始配置，分享前建议使用
    echo    WebUI 的「隐私清洗」功能过滤敏感信息。
    echo ══════════════════════════════════
    echo.
    echo 💡 分享给他人: 直接发送 %ROLE_NAME%.zip 即可
    echo 💡 导入: 接收方放到「待导入角色」文件夹 →
    echo    双击「导入角色.bat」→ 重启Bot
) else (
    echo ❌ 打包失败!
)

echo.
pause
