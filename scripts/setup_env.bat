@echo off
rem ---------------------------------------------------------------------------
rem Настройка окружения сборки. Запускать в обычной cmd, затем оттуда cmake.
rem Пути ниже проверены на машине Тимофея 17.09.2026.
rem На второй машине поправить три строки: VS_DIR, QT_DIR, OCCT_DIR.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion

set "VS_DIR=C:\Program Files\Microsoft Visual Studio\18\Community"
set "QT_DIR=C:\Qt\6.11.0\msvc2022_64"
set "OCCT_DIR=C:\OpenCASCADE\occt-7.9.2"
set "CMAKE_BIN=C:\Qt\Tools\CMake_64\bin"
set "NINJA_BIN=C:\Qt\Tools\Ninja"

if not exist "%VS_DIR%\VC\Auxiliary\Build\vcvars64.bat" (
    echo [ОШИБКА] Не найден vcvars64.bat в "%VS_DIR%".
    echo          Поправьте VS_DIR в этом файле.
    exit /b 1
)
if not exist "%QT_DIR%\lib\cmake\Qt6" (
    echo [ОШИБКА] Не найден Qt6 в "%QT_DIR%".
    echo          Поправьте QT_DIR в этом файле.
    exit /b 1
)

call "%VS_DIR%\VC\Auxiliary\Build\vcvars64.bat" >nul
set "PATH=%CMAKE_BIN%;%NINJA_BIN%;%QT_DIR%\bin;%PATH%"

echo.
echo   MSVC   : готов
for /f "tokens=*" %%v in ('cmake --version 2^>^&1 ^| findstr /r "^cmake"') do echo   %%v
for /f "tokens=*" %%v in ('ninja --version 2^>^&1') do echo   ninja  : %%v
echo   QT_DIR : %QT_DIR%
echo   OCCT   : %OCCT_DIR%
echo.
echo   Дальше:
echo     cmake --preset mono ^&^& cmake --build --preset mono
echo     cmake --preset dll  ^&^& cmake --build --preset dll
echo.

endlocal & set "PATH=%CMAKE_BIN%;%NINJA_BIN%;%QT_DIR%\bin;%PATH%" & set "QT_DIR=%QT_DIR%" & set "OCCT_DIR=%OCCT_DIR%"
cmd /k
