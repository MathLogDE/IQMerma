@echo off
rem MermaIQ — lanzador de la instancia cliente (solo lectura)
rem Levanta ui/app_cliente.py en 127.0.0.1:8502 bajo la ruta /iqmerma. No se
rem publica directo: el tunel ngrok lo levanta el portal
rem (C:\ArqNexo\portal\iniciar_portal.cmd), que la sirve en
rem https://elastic-obedient-trodden.ngrok-free.dev/iqmerma/ via Caddy.
rem Corre en su propia ventana (cmd /k) para poder ver los logs.
rem Usa "python -m streamlit" (no streamlit.exe, que Device Guard bloquea
rem por ser un ejecutable sin firma en AppData).
rem Lo llama C:\ArqNexo\portal\iniciar_portal.cmd (el que arranca todo al
rem iniciar sesion y con el acceso directo "Iniciar servidores" del escritorio);
rem tambien se puede correr solo. Task Scheduler esta bloqueado por politica
rem corporativa, por eso el arranque va por shell:startup.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

start "MermaIQ - Cliente (8502)" cmd /k python -m streamlit run ui/app_cliente.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --server.baseUrlPath iqmerma
