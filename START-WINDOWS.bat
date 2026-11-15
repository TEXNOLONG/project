@echo off
setlocal
chcp 65001 >nul
title OTO - запуск
cd /d "%~dp0"

echo ============================================
echo   ОТО - диспетчеризация аэропорта
echo ============================================
echo.

where py >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python не найден.
        echo Установите Python 3.10 или новее: https://www.python.org/downloads/windows/
        echo При установке включите пункт "Add python.exe to PATH".
        start "" "https://www.python.org/downloads/windows/"
        pause
        exit /b 1
    )
    set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Создаю виртуальное окружение...
    %PY% -m venv .venv
    if errorlevel 1 goto :error
) else (
    echo [1/3] Виртуальное окружение уже есть.
)

if not exist ".venv\deps-ok" (
    echo [2/3] Устанавливаю зависимости. Первый раз нужен интернет...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :error
    echo ok>".venv\deps-ok"
) else (
    echo [2/3] Зависимости уже установлены.
)

echo.
echo Выберите аэропорт:
echo   1 - Шереметьево (SVO)
echo   2 - Домодедово (DME)
set "choice="
set /p "choice=Введите 1 или 2 и нажмите Enter: "
if "%choice%"=="2" (
    set "AIRPORT=data\airport_domodedovo.json"
    set "APNAME=Домодедово"
) else (
    set "AIRPORT=data\airport_sheremetyevo.json"
    set "APNAME=Шереметьево"
)

echo.
echo [3/3] Запускаю %APNAME%...
start "OTO server" cmd /k ".venv\Scripts\python.exe run.py --airport %AIRPORT%"
echo Ожидаю запуск сервера...
timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:8000"
echo.
echo Готово. Браузер должен открыться автоматически.
echo Если нет - откройте вручную: http://127.0.0.1:8000
echo 3D-вид: http://127.0.0.1:8000/?view=3d
echo.
echo Вход в демо-режиме:
echo   Диспетчер: http://127.0.0.1:8000/
echo     dispatcher@oto.ru / Dispatcher2026!
echo   Инженер:  http://127.0.0.1:8000/engineer
echo     tech1@oto.ru / Engineer2026!
echo.
echo Новый инженер: /login -^> "Регистрация инженера".
echo После отправки заявки диспетчер подтверждает её во вкладке "Техники".
echo.
echo Чтобы остановить сервер, закройте окно "OTO server".
pause
exit /b 0

:error
echo.
echo Не удалось установить зависимости.
echo Проверьте интернет и права доступа, затем запустите файл снова.
pause
exit /b 1
