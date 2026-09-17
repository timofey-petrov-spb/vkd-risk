@echo off
setlocal
if not defined VS_DIR (
    for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS_DIR=%%i"
)
if not defined QT_DIR set "QT_DIR=C:\Qt\6.11.0\msvc2022_64"
if not defined CMAKE_BIN set "CMAKE_BIN=C:\Qt\Tools\CMake_64\bin"
if not defined NINJA_BIN set "NINJA_BIN=C:\Qt\Tools\Ninja"
call "%VS_DIR%\VC\Auxiliary\Build\vcvars64.bat" || exit /b 1
set "PATH=%CMAKE_BIN%;%NINJA_BIN%;%QT_DIR%\bin;%PATH%"
ver
echo HOST=%PROCESSOR_ARCHITECTURE% TARGET=%VSCMD_ARG_TGT_ARCH%
echo VCToolsVersion=%VCToolsVersion%
echo WindowsSDKVersion=%WindowsSDKVersion%
"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -property installationVersion
cl 2>&1
cmake --version
ninja --version
qmake -query QT_VERSION
qmake -query QT_INSTALL_PREFIX
git --version
exit /b 0
