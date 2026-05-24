@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 导入角色 ZIP

set "ROLES_DIR=C:\Users\Administrator\AstrBot\source\data\plugins\astrbot_plugin_roleplay\data\roles"
set "IMPORT_DIR=C:\Users\Administrator\AstrBot\待导入角色"

echo.
echo ╔══════════════════════════════════╗
echo ║    📥 Roleplay 批量导入角色     ║
echo ╚══════════════════════════════════╝
echo.

if not exist "%IMPORT_DIR%" (
    echo [错误] 未找到待导入文件夹:
    echo %IMPORT_DIR%
    pause
    exit /b 1
)

if not exist "%IMPORT_DIR%\*.zip" (
    echo [提示] %IMPORT_DIR%\ 中没有 .zip 文件
    echo.
    echo 请把要导入的角色 ZIP 放到这个文件夹，再运行本脚本。
    echo.
    pause
    exit /b 0
)

echo [1] 扫描待导入文件夹: %IMPORT_DIR%
echo.

set COUNT=0
set FAILED=0

for %%f in ("%IMPORT_DIR%\*.zip") do (
    set "ZIP_NAME=%%~nxf"
    set "ZIP_PATH=%%~f"

    echo ─────────────────────────────────
    echo 📦 正在安装: !ZIP_NAME!

    REM 尝试用 tar 解压（Win10+ 内置）
    set "TEMP_DIR=%TEMP%\role_import_!RANDOM!"
    mkdir "!TEMP_DIR!" 2>nul

    powershell -NoProfile -Command ^
        "$zip='!ZIP_PATH!'; $dest='!TEMP_DIR!';" ^
        "Expand-Archive -Path $zip -DestinationPath $dest -Force;" ^
        "Write-Output 'SUCCESS'" >nul 2>&1

    if !errorlevel! neq 0 (
        echo    ❌ 解压失败!ZIP_PATH!
        set /a FAILED+=1
        if exist "!TEMP_DIR!" rmdir /s /q "!TEMP_DIR!"
        goto :next_zip
    )

    REM 找到解压后的根目录
    set "ROLE_NAME="
    for /d %%d in ("!TEMP_DIR!\*") do (
        set "ROLE_NAME=%%~nxd"
        set "SRC_DIR=%%~fd"
        goto :found_dir
    )
    REM 如果没找到子目录，可能是ZIP本身就在根目录
    if exist "!TEMP_DIR!\config.yaml" (
        set "ROLE_NAME=!ZIP_NAME:.zip=!"
        set "SRC_DIR=!TEMP_DIR!"
        goto :found_dir
    )
    echo    ❌ ZIP中未找到 config.yaml
    set /a FAILED+=1
    if exist "!TEMP_DIR!" rmdir /s /q "!TEMP_DIR!"
    goto :next_zip

    :found_dir
    REM 确认 config.yaml 存在
    if not exist "!SRC_DIR!\config.yaml" (
        echo    ❌ 解压后未找到 config.yaml
        set /a FAILED+=1
        if exist "!TEMP_DIR!" rmdir /s /q "!TEMP_DIR!"
        goto :next_zip
    )

    REM 提取角色名（优先用 config.yaml 中的 name）
    for /f "usebackq tokens=2 delims=: " %%n in (`powershell -NoProfile -Command "(Get-Content '!SRC_DIR!\config.yaml' -Encoding UTF8 | Select-String '^name:' | Select-Object -First 1) -replace 'name:\s*',''"`) do set "CFG_NAME=%%n"
    if "!CFG_NAME!"=="" set "CFG_NAME=!ROLE_NAME!"

    REM 安装到角色目录
    set "DEST_DIR=%ROLES_DIR%\!CFG_NAME!"
    if exist "!DEST_DIR!" (
        echo    ⚠ 角色 [!CFG_NAME!] 已存在，正在覆盖...
        rmdir /s /q "!DEST_DIR!" 2>nul
    )

    xcopy "!SRC_DIR!\*" "!DEST_DIR!\" /E /I /Q /Y
    echo    ✅ 角色 [!CFG_NAME!] 安装成功!

    REM 清理临时文件
    if exist "!TEMP_DIR!" rmdir /s /q "!TEMP_DIR!"

    set /a COUNT+=1

    :next_zip
    echo.
)

echo ══════════════════════════════════
echo 导入完成: !COUNT! 个成功 / !FAILED! 个失败
echo.
echo 📂 角色安装目录:
echo %ROLES_DIR%
echo.
echo 💡 请重启 AstrBot 使新角色生效！
echo.
pause
