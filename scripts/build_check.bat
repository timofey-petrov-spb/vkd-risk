@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
set "QT_DIR=C:\Qt\6.11.0\msvc2022_64"
set "PATH=C:\Qt\Tools\CMake_64\bin;C:\Qt\Tools\Ninja;%QT_DIR%\bin;%PATH%"
cmake --version
echo === MONO ===
cmake --preset mono            || exit /b 1
cmake --build --preset mono    || exit /b 2
echo === DLL ===
cmake --preset dll             || exit /b 3
cmake --build --preset dll     || exit /b 4
echo === OK ===
