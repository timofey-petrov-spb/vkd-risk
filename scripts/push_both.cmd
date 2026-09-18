@echo off
rem Отправить в оба репозитория: рабочий GitHub и сдаточный GitVerse.
git push origin main || exit /b 1
git push gitverse main || exit /b 2
echo.
echo Отправлено в GitHub и GitVerse.
