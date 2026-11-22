@echo off
rem MermaIQ — lanzador de la app
rem Usa "python -m streamlit" (no streamlit.exe, que Device Guard bloquea
rem por ser un ejecutable sin firma en AppData).
cd /d "%~dp0"
rem Puerto 8505: 8501 argy, 8502 instancia cliente, 8503 web.servidor, 8504 workflow.
python -m streamlit run ui/app.py --server.port 8505
