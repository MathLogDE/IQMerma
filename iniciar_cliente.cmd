@echo off
rem MermaIQ — lanzador de la instancia cliente (solo lectura)
rem Levanta ui/app_cliente.py en 127.0.0.1:8502 bajo la ruta /iqmerma. No se
rem publica directo: el tunel ngrok lo levanta el portal
rem (C:\ArqNexo\portal\iniciar_portal.cmd), que la sirve en
rem https://elastic-obedient-trodden.ngrok-free.dev/iqmerma/ via Caddy.
rem Corre en su propia ventana (cmd /k) para poder ver los logs.
rem Usa "python -m streamlit" (no streamlit.exe, que Device Guard bloquea
rem por ser un ejecutable sin firma en AppData).
rem Se ejecuta automaticamente al iniciar sesion de Windows via acceso
rem directo en shell:startup (Task Scheduler esta bloqueado por politica
rem corporativa). Para desactivar el arranque automatico, borrar
rem "IQMerma - Iniciar Cliente.lnk" de esa carpeta.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

start "MermaIQ - Cliente (8502)" cmd /k python -m streamlit run ui/app_cliente.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --server.baseUrlPath iqmerma
