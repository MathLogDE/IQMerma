"""
core/pdf.py — HTML autocontenido -> PDF con un navegador Chromium en modo headless

Sin dependencias nuevas: usa el Edge que trae Windows (o Chrome) con
--print-to-pdf, el mismo motor con el que se imprime a mano con Ctrl+P, así el
PDF sale igual que la vista del navegador (respeta @page y los saltos de página).
Se corre con un perfil temporal propio para no chocar con un navegador abierto.

El navegador se puede forzar con la variable de entorno MERMAIQ_NAVEGADOR.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_CANDIDATOS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def navegador() -> Path | None:
    env = os.environ.get("MERMAIQ_NAVEGADOR")
    if env and Path(env).exists():
        return Path(env)
    for c in _CANDIDATOS:
        if Path(c).exists():
            return Path(c)
    for nombre in ("msedge", "chrome", "chromium", "google-chrome"):
        w = shutil.which(nombre)
        if w:
            return Path(w)
    return None


def disponible() -> bool:
    return navegador() is not None


def html_a_pdf(html: str, timeout: int = 90) -> bytes:
    exe = navegador()
    if exe is None:
        raise RuntimeError("No se encontró Edge ni Chrome para generar el PDF "
                           "(se puede indicar con MERMAIQ_NAVEGADOR).")
    with tempfile.TemporaryDirectory(prefix="mermaiq_pdf_") as tmp:
        tmp = Path(tmp)
        entrada, salida = tmp / "doc.html", tmp / "doc.pdf"
        entrada.write_text(html, encoding="utf-8")
        cmd = [str(exe), "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               f"--user-data-dir={tmp / 'perfil'}", "--no-pdf-header-footer",
               f"--print-to-pdf={salida}", entrada.as_uri()]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if not salida.exists() or salida.stat().st_size == 0:
            raise RuntimeError("El navegador no generó el PDF: "
                               + proc.stderr.decode(errors="replace")[-400:])
        return salida.read_bytes()
