@echo off
title Sistema de objetos perdidos
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No se encontro el entorno virtual .venv.
    echo Primero debes crear el entorno con: python -m venv .venv
    pause
    exit /b 1
)

start "Servidor Flask - Objetos perdidos" cmd /k ".venv\Scripts\python.exe app.py"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5000/auth/login"
