@echo off
rem MermaIQ — lanzador de la app
rem Usa "python -m streamlit" (no streamlit.exe, que Device Guard bloquea
rem por ser un ejecutable sin firma en AppData).
cd /d "%~dp0"
python -m streamlit run ui/app.py
