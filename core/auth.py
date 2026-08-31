"""
core/auth.py — Gestión de usuarios de acceso web (auth/usuarios.yaml)

CRUD simple sobre el YAML que lee ui/app_cliente.py, pensado para usarse
desde la pestaña "Usuarios web" de Ingesta (instancia admin, nunca expuesta
por el túnel).

Las contraseñas nuevas se guardan en texto plano: streamlit_authenticator
las hashea sola (auto_hash=True) la primera vez que ese usuario se loguea en
ui/app_cliente.py, y reescribe el archivo con el hash — este módulo nunca
hashea nada él mismo, para no duplicar esa lógica ni pisar sin querer un
hash ya generado.
"""

import secrets
from pathlib import Path

import yaml

AUTH_DIR = Path(__file__).parent.parent / "auth"
AUTH_CONFIG = AUTH_DIR / "usuarios.yaml"

_COOKIE_DEFAULT = {"name": "mermaiq_cliente", "expiry_days": 30}

# Catálogo de módulos -> páginas de ui/app_cliente.py. Única fuente de verdad:
# tanto la navegación del cliente como el panel de permisos de Ingesta usan
# este dict, para que nunca queden desincronizados.
PAGINAS_DISPONIBLES = {
    "General":       ["Inicio"],
    "Merma":         ["Análisis", "Control de merma", "Avance de inventario",
                      "Transformaciones"],
    "Inventario":    ["Salud de stock", "Serie de stock", "Cruce por sucursal",
                      "Evolución interanual", "Perfil de reposición",
                      "Pedido desde sucursal", "Pedido general de distribución",
                      "Min / Opt / Max"],
    "Distribución":  ["Transferencias", "Diferencias de camión"],
    "Comercial":     ["Márgenes", "Comprobantes y canasta"],
    "Forecasting":   ["Forecast"],
}

TODAS_LAS_PAGINAS = [p for paginas in PAGINAS_DISPONIBLES.values() for p in paginas]


def _config_nueva() -> dict:
    return {
        "credentials": {"usernames": {}},
        "cookie": {**_COOKIE_DEFAULT, "key": secrets.token_hex(32)},
    }


def _cargar() -> dict:
    if not AUTH_CONFIG.exists():
        return _config_nueva()
    config = yaml.safe_load(AUTH_CONFIG.read_text(encoding="utf-8")) or {}
    config.setdefault("credentials", {}).setdefault("usernames", {})
    config.setdefault("cookie", {**_COOKIE_DEFAULT, "key": secrets.token_hex(32)})
    return config


def _guardar(config: dict) -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_CONFIG.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def listar_usuarios() -> dict:
    """dict username -> {name, email, proyectos, ...}."""
    return _cargar()["credentials"]["usernames"]


def crear_usuario(username: str, name: str, email: str, password: str,
                  proyectos: list[str], paginas: list[str] | None = None) -> None:
    """`paginas=None` deja al usuario con acceso a todas (comportamiento por
    defecto, igual al de antes de que existiera este permiso); pasar una
    lista explícita restringe a esas nomás."""
    config = _cargar()
    usuarios = config["credentials"]["usernames"]
    if username in usuarios:
        raise ValueError(f"El usuario '{username}' ya existe.")
    usuarios[username] = {
        "name": name, "email": email, "password": password,
        "proyectos": sorted(set(proyectos)),
        "paginas": sorted(set(paginas if paginas is not None else TODAS_LAS_PAGINAS)),
    }
    _guardar(config)


def cambiar_password(username: str, password: str) -> None:
    config = _cargar()
    usuarios = config["credentials"]["usernames"]
    if username not in usuarios:
        raise ValueError(f"El usuario '{username}' no existe.")
    usuarios[username]["password"] = password
    _guardar(config)


def set_acceso_proyecto(username: str, proyecto: str, tiene_acceso: bool) -> None:
    """Agrega o quita `proyecto` de la lista de proyectos del usuario."""
    config = _cargar()
    usuarios = config["credentials"]["usernames"]
    if username not in usuarios:
        raise ValueError(f"El usuario '{username}' no existe.")
    actuales = set(usuarios[username].get("proyectos") or [])
    if tiene_acceso:
        actuales.add(proyecto)
    else:
        actuales.discard(proyecto)
    usuarios[username]["proyectos"] = sorted(actuales)
    _guardar(config)


def set_acceso_pagina(username: str, pagina: str, tiene_acceso: bool) -> None:
    """Agrega o quita `pagina` de las páginas visibles del usuario. Usuarios
    sin `paginas` guardado todavía (creados antes de este permiso) parten
    de "todas" — el toggle recién los restringe a partir de ese punto."""
    config = _cargar()
    usuarios = config["credentials"]["usernames"]
    if username not in usuarios:
        raise ValueError(f"El usuario '{username}' no existe.")
    actuales = set(usuarios[username].get("paginas")
                   if usuarios[username].get("paginas") is not None
                   else TODAS_LAS_PAGINAS)
    if tiene_acceso:
        actuales.add(pagina)
    else:
        actuales.discard(pagina)
    usuarios[username]["paginas"] = sorted(actuales)
    _guardar(config)


def set_acceso_paginas(username: str, paginas: list[str], tiene_acceso: bool) -> None:
    """Igual que set_acceso_pagina pero para varias de una vez (ej. togglear
    un módulo entero) en un único read/write del yaml."""
    config = _cargar()
    usuarios = config["credentials"]["usernames"]
    if username not in usuarios:
        raise ValueError(f"El usuario '{username}' no existe.")
    actuales = set(usuarios[username].get("paginas")
                   if usuarios[username].get("paginas") is not None
                   else TODAS_LAS_PAGINAS)
    if tiene_acceso:
        actuales.update(paginas)
    else:
        actuales.difference_update(paginas)
    usuarios[username]["paginas"] = sorted(actuales)
    _guardar(config)


def eliminar_usuario(username: str) -> None:
    config = _cargar()
    config["credentials"]["usernames"].pop(username, None)
    _guardar(config)
