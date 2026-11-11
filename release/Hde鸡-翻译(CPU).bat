@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
set "cpath=%~dp0"
set "cpath=%cpath:~0,-1%"

if "%~1"=="" (
    echo 请将音视频文件夹拖到此窗口，然后按回车:
    set "src_dir="
    set /p "src_dir="
    if not defined src_dir (
        echo 未提供输入文件夹。
        pause
        exit /b 1
    )
) else (
    set "src_dir=%~f1"
)

setlocal DisableDelayedExpansion
if not exist "%src_dir%\" (
    echo 错误: 指定的路径不是有效文件夹。
    pause
    exit /b 1
)
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo 工作目录: !src_dir!
echo ========================================
echo.
echo [1/2] 转录与翻译由 main.exe 统一执行（参数见 config.json → transcription）
echo.
echo ========================================
echo   HDE G 翻译 (CPU)
echo ========================================
echo.
echo.
echo   mode: CPU（请在 config.json 设 transcription.device="cpu"）
echo.


echo [2/2] 正在启动转录+翻译流水线...
> "%cpath%\input_path.txt" (
    if "%~1"=="" (
        echo !src_dir!
    ) else (
        for %%a in (%*) do echo %%~fa
    )
)

:: 读取 config.json 中的 main_exe，为空则默认同目录下的 main.exe
set "main_exe="
set "cfg_tmp=%temp%\hdeg_mainexe.txt"
if exist "%cpath%\config.json" (
    python -c "import json;c=json.load(open(r'%cpath:\=/%/config.json','r',encoding='utf-8'));v=c.get('transcription',{}).get('main_exe','');f=open(r'%cfg_tmp:\=/%','w',encoding='utf-8');f.write(v);f.close()" 2>nul
    if exist "%cfg_tmp%" (
        set /p "main_exe=" < "%cfg_tmp%"
        del "%cfg_tmp%" 2>nul
    )
)
if "!main_exe!"=="" set "main_exe=%cpath%\main.exe"
if "!main_exe:~0,2!"==".\" set "main_exe=%cpath%\!main_exe:~2!"
if "!main_exe:~0,2!"=="./" set "main_exe=%cpath%/!main_exe:~2!"
echo [引擎] main.exe: !main_exe!
"!main_exe!"

if errorlevel 1 (
    echo [错误] 翻译失败。
    del "%cpath%\input_path.txt" 2>nul
    pause
    exit /b 1
)

del "%cpath%\input_path.txt" 2>nul

echo.
echo ========================================
echo 全部完成！
echo.
echo 文件说明:
echo   .lrc     = 当前使用的中文歌词（播放器读取）
echo   .ja.lrc  = 日文原版留档
echo ========================================
pause