@echo off
chcp 65001 >nul 2>&1
title BuddySecretary - 超级秘书

echo ========================================
echo    BuddySecretary - 超级秘书
echo ========================================
echo.

cd /d "%~dp0"

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

REM 显示菜单
:menu
cls
echo ========================================
echo    超级秘书 - 主菜单
echo ========================================
echo.
echo  [1] 查看系统状态
echo  [2] 全量扫描所有群
echo  [3] 增量扫描(获取最新消息)
echo  [4] AI处理消息
echo  [5] 生成每日报告
echo  [6] 扫描群文件
echo  [7] 搜索消息
echo  [8] 交互模式
echo.
echo  [0] 退出
echo ========================================
echo.

set /p choice=请选择操作: 

if "%choice%"=="1" goto status
if "%choice%"=="2" goto fullscan
if "%choice%"=="3" goto scan
if "%choice%"=="4" goto process
if "%choice%"=="5" goto report
if "%choice%"=="6" goto files
if "%choice%"=="7" goto search
if "%choice%"=="8" goto interactive
if "%choice%"=="0" exit /b

goto menu

:status
echo.
python main.py status
echo.
pause
goto menu

:fullscan
echo.
echo 开始全量扫描...
echo.
python main.py fullscan
echo.
pause
goto menu

:scan
echo.
python main.py scan
echo.
pause
goto menu

:process
echo.
echo 开始AI处理...
echo.
python main.py process
echo.
pause
goto menu

:report
echo.
python main.py report
echo.
pause
goto menu

:files
echo.
python main.py files
echo.
pause
goto menu

:search
echo.
set /p keyword=请输入搜索关键词: 
python main.py search -k "%keyword%"
echo.
pause
goto menu

:interactive
cls
python main.py interactive
goto menu
