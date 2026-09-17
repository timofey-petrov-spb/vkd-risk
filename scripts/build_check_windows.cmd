@echo off
setlocal
rem Discover MSVC instead of relying on another developer's installation path.
if not defined VS_DIR (
    for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS_DIR=%%i"
)
if not defined QT_DIR set "QT_DIR=C:\Qt\6.11.0\msvc2022_64"
if not defined CMAKE_BIN set "CMAKE_BIN=C:\Qt\Tools\CMake_64\bin"
if not defined NINJA_BIN set "NINJA_BIN=C:\Qt\Tools\Ninja"
if not exist "%VS_DIR%\VC\Auxiliary\Build\vcvars64.bat" (
    echo ERROR: MSVC not found. Set VS_DIR to the Visual Studio installation.
    exit /b 1
)
if not exist "%QT_DIR%\lib\cmake\Qt6\Qt6Config.cmake" (
    echo ERROR: Qt not found. Set QT_DIR to the Windows x64 MSVC Qt prefix.
    exit /b 1
)
call "%VS_DIR%\VC\Auxiliary\Build\vcvars64.bat" || exit /b 1
set "PATH=%CMAKE_BIN%;%NINJA_BIN%;%QT_DIR%\bin;%PATH%"
pushd "%~dp0.." || exit /b 1
cmake --version || goto :failed
ninja --version || goto :failed
qmake -query QT_VERSION || goto :failed
cmake --preset mono || goto :failed
cmake --build --preset mono || goto :failed
ctest --preset mono || goto :failed
cmake --preset dll || goto :failed
cmake --build --preset dll || goto :failed
ctest --preset dll || goto :failed
echo Both Windows builds OK. No tests found means compilation only.
popd
exit /b 0
:failed
popd
exit /b 1
