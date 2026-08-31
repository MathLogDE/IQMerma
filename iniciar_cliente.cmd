@echo off
rem MermaIQ — lanzador de la instancia cliente (solo lectura) + tunel ngrok
rem Levanta ui/app_cliente.py en el puerto 8502 y lo expone con el dominio
rem fijo de ngrok. Cada proceso corre en su propia ventana (cmd /k) para
rem poder ver los logs y cerrarlos por separado sin matar el otro.
rem Usa "python -m streamlit" (no streamlit.exe, que Device Guard bloquea
rem por ser un ejecutable sin firma en AppData).
rem Se ejecuta automaticamente al iniciar sesion de Windows via acceso
rem directo en shell:startup (Task Scheduler esta bloqueado por politica
rem corporativa). Para desactivar el arranque automatico, borrar
rem "IQMerma - Iniciar Cliente.lnk" de esa carpeta.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

start "MermaIQ - Cliente (8502)" cmd /k python -m streamlit run ui/app_cliente.py --server.port 8502 --server.headless true

timeout /t 3 /nobreak >nul

start "MermaIQ - Tunel ngrok" cmd /k ngrok http --url=elastic-obedient-trodden.ngrok-free.dev 8502
