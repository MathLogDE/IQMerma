"""
core/graficos.py — Gráficos SVG para los reportes imprimibles

Los reportes son HTML autocontenido que se imprime con Ctrl+P: no hay
JavaScript ni pedidos a la red. Por eso los gráficos se generan como **SVG
inline**, escrito a mano acá:

  - Sin dependencias nuevas (Plotly necesitaría kaleido, ~50 MB de Chromium,
    y devolvería un PNG que se pixela al imprimir).
  - Vectorial: nítido a cualquier zoom y en el PDF.
  - Hereda las variables CSS de base.html, así que respeta el color de acento
    del cliente.

Todas las funciones devuelven un string con el `<svg>` listo para inyectar en
la plantilla (con `| safe`). El texto de las etiquetas se escapa siempre.

Uso:
    from core.graficos import (
        svg_lineas, svg_barras_lineas, svg_barras_divergentes, svg_ranking_horizontal,
    )
"""

from html import escape

# Serie más reciente en el color de acento del cliente; las viejas, apagadas.
COLORES_SERIE = ["#9aa5b8", "#8fa9cf", "#5b87bd", "var(--azul)", "var(--acento)"]

# Paleta categórica (una entidad = un color fijo, sin gradiente hacia el
# acento) — para desagrupar por sucursal/tipo dentro de un mismo gráfico.
# Mismos valores que ui.comun.PALETA, para que un mismo dato se vea con el
# mismo color en pantalla y en el imprimible.
PALETA_CATEGORICA = ["#ff6b6b", "#ffa94d", "#748ffc", "#64ffda", "#f783ac", "#a9e34b", "#ffd43b"]

_GRIS = "#5b6472"
_LINEA = "#d9dee6"


def colores(n: int) -> list[str]:
    """n colores de la paleta, dejando el de acento para el último (el más nuevo)."""
    if n <= 0:
        return []
    if n > len(COLORES_SERIE):
        # Repetir el primer color (el más apagado) para las series viejas
        # de más, y conservar la paleta completa al final: el acento siempre
        # tiene que quedar en el último puesto, no en cualquier lado.
        return [COLORES_SERIE[0]] * (n - len(COLORES_SERIE)) + COLORES_SERIE
    return COLORES_SERIE[len(COLORES_SERIE) - n:]


def _abreviar(v: float) -> str:
    """Números cortos para los ejes: 1.2M, 340k, 25, 0.5."""
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:,.1f}B".replace(".0B", "B")
    if a >= 1e6:
        return f"{v / 1e6:,.1f}M".replace(".0M", "M")
    if a >= 1e3:
        return f"{v / 1e3:,.0f}k"
    # Escalas chicas (ratios): sin decimales las marcas se repetirían (0,0,1,1)
    if a < 10 and v != int(v):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,.0f}"


def _escala(minimo: float, maximo: float, pasos: int = 4) -> tuple[float, float, list[float]]:
    """
    Rango redondeado a números 'lindos' y sus marcas. Siempre incluye el cero
    para que las alturas se lean como proporciones y no exageren diferencias.
    """
    lo, hi = min(0.0, minimo), max(0.0, maximo)
    if lo == hi:
        return 0.0, 1.0, [0.0, 1.0]
    bruto = (hi - lo) / pasos
    mag = 10 ** len(str(int(abs(bruto)))) / 10 if abs(bruto) >= 1 else 0.1
    paso = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= bruto), bruto)
    lo = paso * (int(lo / paso) - (1 if lo % paso else 0)) if lo else 0.0
    hi = paso * (int(hi / paso) + (1 if hi % paso else 0))
    marcas, x = [], lo
    while x <= hi + paso / 1000:
        marcas.append(round(x, 6))
        x += paso
    return lo, hi, marcas


def _leyenda(series: list[dict], x: float, y: float,
            ancho_max: float | None = None, alto_fila: float = 14) -> tuple[str, int]:
    """
    Fila(s) de leyenda: muestra el trazo/color y el nombre de cada serie. Si
    se pasa `ancho_max`, hace wrap a varias filas cuando el total no entra en
    el ancho disponible (necesario para desagrupados con muchas entidades,
    ej. sucursal × año, donde una sola fila se saldría del gráfico).

    Returns: (svg parcial, cantidad de filas usadas — para que el llamador
    pueda agrandar el alto del SVG si hizo falta más de una).
    """
    partes, cursor, fila = [], x, 0
    for s in series:
        etq = escape(str(s["nombre"]))
        ancho_item = 30 + len(etq) * 5.2
        if ancho_max is not None and cursor > x and cursor + ancho_item > x + ancho_max:
            fila += 1
            cursor = x
        yy = y + fila * alto_fila
        color = s["color"]
        if s.get("tipo") == "linea":
            guion = ' stroke-dasharray="4 3"' if s.get("dash") else ""
            marca = (f'<line x1="{cursor}" y1="{yy - 4}" x2="{cursor + 16}" y2="{yy - 4}" '
                     f'stroke="{color}" stroke-width="2.5"{guion}/>')
        else:
            marca = (f'<rect x="{cursor}" y="{yy - 9}" width="11" height="11" rx="2" '
                     f'fill="{color}"/>')
        partes.append(marca)
        partes.append(f'<text x="{cursor + 21}" y="{yy}" font-size="9" '
                      f'fill="{_GRIS}">{etq}</text>')
        cursor += ancho_item
    return "".join(partes), fila + 1


def _marco(ancho: int, alto: int, izq: float, der: float, arriba: float,
           abajo: float, marcas: list[float], lo: float, hi: float,
           categorias: list[str], titulo_y: str = "",
           eje_der: tuple[list[float], float, float, str] | None = None) -> str:
    """Grilla horizontal, eje Y (uno o dos) y etiquetas del eje X."""
    alto_util = alto - arriba - abajo
    ancho_util = ancho - izq - der
    out = []

    def y_de(v, a=lo, b=hi):
        return arriba + alto_util * (1 - (v - a) / (b - a or 1))

    for m in marcas:
        y = y_de(m)
        out.append(f'<line x1="{izq}" y1="{y:.1f}" x2="{ancho - der}" y2="{y:.1f}" '
                   f'stroke="{_LINEA}" stroke-width="{1 if m else 1.4}"/>')
        out.append(f'<text x="{izq - 6}" y="{y + 3:.1f}" font-size="8.5" '
                   f'fill="{_GRIS}" text-anchor="end">{_abreviar(m)}</text>')

    if eje_der:
        marcas_d, lo_d, hi_d, _ = eje_der
        for m in marcas_d:
            y = y_de(m, lo_d, hi_d)
            out.append(f'<text x="{ancho - der + 6}" y="{y + 3:.1f}" font-size="8.5" '
                       f'fill="{_GRIS}">{_abreviar(m)}</text>')

    paso = ancho_util / max(len(categorias), 1)
    for i, c in enumerate(categorias):
        x = izq + paso * (i + 0.5)
        out.append(f'<text x="{x:.1f}" y="{alto - abajo + 14}" font-size="9" '
                   f'fill="{_GRIS}" text-anchor="middle">{escape(str(c))}</text>')

    if titulo_y:
        out.append(f'<text x="{izq}" y="{arriba - 8}" font-size="8.5" '
                   f'fill="{_GRIS}">{escape(titulo_y)}</text>')
    return "".join(out)


def svg_lineas(categorias: list[str], series: list[dict], titulo_y: str = "",
               ancho: int = 690, alto: int = 240) -> str:
    """
    Gráfico de líneas con una serie por año.

    Args:
        categorias: etiquetas del eje X (los meses).
        series:     [{"nombre": "2025", "valores": [...], "color": opcional,
                     "dash": opcional — línea punteada, para desagrupados
                     tipo "sucursal · año" donde el año viejo va punteado}]
    """
    series = [s for s in series if s.get("valores")]
    if not categorias or not series:
        return ""
    paleta = colores(len(series))
    izq, der, arriba, abajo = 46, 12, 26, 34

    todos = [v for s in series for v in s["valores"] if v is not None]
    lo, hi, marcas = _escala(min(todos, default=0), max(todos, default=0))
    alto_util, ancho_util = alto - arriba - abajo, ancho - izq - der
    paso = ancho_util / max(len(categorias), 1)

    def punto(i, v):
        x = izq + paso * (i + 0.5)
        y = arriba + alto_util * (1 - (v - lo) / (hi - lo or 1))
        return x, y

    cuerpo = [_marco(ancho, alto, izq, der, arriba, abajo, marcas, lo, hi,
                     categorias, titulo_y)]
    leyenda = []
    for s, color in zip(series, paleta):
        color = s.get("color", color)
        pts = [punto(i, v) for i, v in enumerate(s["valores"]) if v is not None]
        if not pts:
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                     for i, (x, y) in enumerate(pts))
        guion = ' stroke-dasharray="4 3"' if s.get("dash") else ""
        cuerpo.append(f'<path d="{d}" fill="none" stroke="{color}" '
                      f'stroke-width="2.2" stroke-linejoin="round"{guion}/>')
        for x, y in pts:
            cuerpo.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.8" fill="{color}"/>')
        leyenda.append({"nombre": s["nombre"], "color": color, "tipo": "linea",
                        "dash": bool(s.get("dash"))})

    leyenda_svg, filas = _leyenda(leyenda, izq, alto - 4, ancho_max=ancho_util)
    alto_total = alto + (filas - 1) * 14
    cuerpo.append(leyenda_svg)
    return (f'<svg viewBox="0 0 {ancho} {alto_total}" width="100%" '
            f'style="max-width:{ancho}px" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="Segoe UI, Arial, sans-serif">{"".join(cuerpo)}</svg>')


def svg_barras_lineas(categorias: list[str], barras: list[dict],
                      lineas: list[dict], titulo_izq: str = "",
                      titulo_der: str = "", ancho: int = 690,
                      alto: int = 260) -> str:
    """
    Combinado: barras agrupadas (eje izquierdo) + líneas (eje derecho propio).
    Es el gráfico de ventas contra reposición.
    """
    barras = [b for b in barras if b.get("valores")]
    lineas = [l for l in lineas if l.get("valores")]
    if not categorias or not (barras or lineas):
        return ""
    izq, der, arriba, abajo = 46, 46, 26, 40

    v_bar = [v for b in barras for v in b["valores"] if v is not None] or [0]
    v_lin = [v for l in lineas for v in l["valores"] if v is not None] or [0]
    lo_b, hi_b, marcas_b = _escala(min(v_bar), max(v_bar))
    lo_l, hi_l, marcas_l = _escala(min(v_lin), max(v_lin))

    alto_util, ancho_util = alto - arriba - abajo, ancho - izq - der
    paso = ancho_util / max(len(categorias), 1)
    pal_b = colores(len(barras))
    pal_l = colores(len(lineas))

    cuerpo = [_marco(ancho, alto, izq, der, arriba, abajo, marcas_b, lo_b, hi_b,
                     categorias, titulo_izq,
                     eje_der=(marcas_l, lo_l, hi_l, titulo_der))]
    if titulo_der:
        cuerpo.append(f'<text x="{ancho - der}" y="{arriba - 8}" font-size="8.5" '
                      f'fill="{_GRIS}" text-anchor="end">{escape(titulo_der)}</text>')

    # barras agrupadas: 70% del ancho de cada categoría repartido entre series
    n = max(len(barras), 1)
    w = paso * 0.7 / n
    leyenda = []
    for j, (b, color) in enumerate(zip(barras, pal_b)):
        color = b.get("color", color)
        for i, v in enumerate(b["valores"]):
            if v is None:
                continue
            x = izq + paso * i + paso * 0.15 + w * j
            y = arriba + alto_util * (1 - (v - lo_b) / (hi_b - lo_b or 1))
            h = arriba + alto_util * (1 - (0 - lo_b) / (hi_b - lo_b or 1)) - y
            cuerpo.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                          f'height="{max(h, 0.5):.1f}" fill="{color}" rx="1.5"/>')
        leyenda.append({"nombre": b["nombre"], "color": color, "tipo": "barra"})

    for j, (l, color) in enumerate(zip(lineas, pal_l)):
        color = l.get("color", color)
        pts = []
        for i, v in enumerate(l["valores"]):
            if v is None:
                continue
            x = izq + paso * (i + 0.5)
            y = arriba + alto_util * (1 - (v - lo_l) / (hi_l - lo_l or 1))
            pts.append((x, y))
        if not pts:
            continue
        guion = ' stroke-dasharray="5 3"' if j < len(lineas) - 1 else ""
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                     for i, (x, y) in enumerate(pts))
        cuerpo.append(f'<path d="{d}" fill="none" stroke="{color}" '
                      f'stroke-width="2.4" stroke-linejoin="round"{guion}/>')
        for x, y in pts:
            cuerpo.append(f'<rect x="{x - 2.6:.1f}" y="{y - 2.6:.1f}" width="5.2" '
                          f'height="5.2" fill="{color}" transform="rotate(45 {x:.1f} {y:.1f})"/>')
        leyenda.append({"nombre": l["nombre"], "color": color, "tipo": "linea",
                        "dash": bool(guion)})

    leyenda_svg, filas = _leyenda(leyenda, izq, alto - 4, ancho_max=ancho_util)
    alto_total = alto + (filas - 1) * 14
    cuerpo.append(leyenda_svg)
    return (f'<svg viewBox="0 0 {ancho} {alto_total}" width="100%" '
            f'style="max-width:{ancho}px" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="Segoe UI, Arial, sans-serif">{"".join(cuerpo)}</svg>')


def svg_barras_divergentes(items: list[dict], ancho: int = 690,
                           alto_fila: int = 17) -> str:
    """
    Barras horizontales que salen de un cero central: verde a la derecha
    (creció), rojo a la izquierda (cayó). Es el ranking de variación.

    Args:
        items: [{"etiqueta": str, "valor": float, "texto": str opcional}]
    """
    items = [i for i in items if i.get("valor") is not None]
    if not items:
        return ""
    izq, der, arriba, abajo = 150, 56, 8, 20
    alto = arriba + abajo + alto_fila * len(items)
    ancho_util = ancho - izq - der
    tope = max((abs(i["valor"]) for i in items), default=0) or 1
    centro = izq + ancho_util / 2
    # La etiqueta del valor va por fuera de la barra: hay que reservarle lugar,
    # si no la barra más larga la empuja encima del nombre de la sucursal.
    media = max(ancho_util / 2 - 46, 10)

    cuerpo = [f'<line x1="{centro}" y1="{arriba}" x2="{centro}" '
              f'y2="{alto - abajo}" stroke="{_LINEA}" stroke-width="1.4"/>']
    for k, i in enumerate(items):
        v = float(i["valor"])
        y = arriba + alto_fila * k
        largo = abs(v) / tope * media
        x = centro if v >= 0 else centro - largo
        color = "var(--verde)" if v >= 0 else "var(--acento)"
        cuerpo.append(f'<rect x="{x:.1f}" y="{y + 3.5:.1f}" width="{largo:.1f}" '
                      f'height="{alto_fila - 7}" fill="{color}" rx="1.5"/>')
        cuerpo.append(f'<text x="{izq - 6}" y="{y + alto_fila / 2 + 3:.1f}" '
                      f'font-size="8.5" fill="{_GRIS}" text-anchor="end">'
                      f'{escape(str(i["etiqueta"])[:34])}</text>')
        texto = i.get("texto") or _abreviar(v)
        tx = centro + largo + 5 if v >= 0 else centro - largo - 5
        anchor = "start" if v >= 0 else "end"
        cuerpo.append(f'<text x="{tx:.1f}" y="{y + alto_fila / 2 + 3:.1f}" '
                      f'font-size="8.5" fill="{_GRIS}" text-anchor="{anchor}">'
                      f'{escape(texto)}</text>')

    cuerpo.append(f'<text x="{centro}" y="{alto - 6}" font-size="8" '
                  f'fill="{_GRIS}" text-anchor="middle">0</text>')
    return (f'<svg viewBox="0 0 {ancho} {alto}" width="100%" '
            f'style="max-width:{ancho}px" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="Segoe UI, Arial, sans-serif">{"".join(cuerpo)}</svg>')


def svg_ranking_horizontal(items: list[dict], ancho: int = 690,
                           alto_fila: int = 17) -> str:
    """
    Barras horizontales desde cero (no divergentes): para rankings de una
    sola magnitud positiva (ej. días hasta cruzar una barrera de stock). A
    diferencia de `svg_barras_divergentes` (pensada para variaciones ±,
    centrada en cero), acá todas las barras salen del mismo margen
    izquierdo — usar la divergente para esto desperdiciaría la mitad del
    ancho hacia un lado que nunca se usa.

    Args:
        items: [{"etiqueta": str, "valor": float, "texto": opcional}], en el
               orden en que se quieren mostrar (de arriba hacia abajo — el
               llamador decide el orden, p.ej. más urgente primero).
    """
    items = [i for i in items if i.get("valor") is not None]
    if not items:
        return ""
    izq, der, arriba, abajo = 150, 56, 8, 12
    alto = arriba + abajo + alto_fila * len(items)
    ancho_util = ancho - izq - der
    tope = max((float(i["valor"]) for i in items), default=0) or 1

    cuerpo = []
    for k, i in enumerate(items):
        v = float(i["valor"])
        y = arriba + alto_fila * k
        largo = max(v / tope * ancho_util, 1.5)
        cuerpo.append(f'<rect x="{izq}" y="{y + 3.5:.1f}" width="{largo:.1f}" '
                      f'height="{alto_fila - 7}" fill="var(--acento)" rx="1.5"/>')
        cuerpo.append(f'<text x="{izq - 6}" y="{y + alto_fila / 2 + 3:.1f}" '
                      f'font-size="8.5" fill="{_GRIS}" text-anchor="end">'
                      f'{escape(str(i["etiqueta"])[:34])}</text>')
        texto = i.get("texto") or _abreviar(v)
        cuerpo.append(f'<text x="{izq + largo + 5:.1f}" y="{y + alto_fila / 2 + 3:.1f}" '
                      f'font-size="8.5" fill="{_GRIS}" text-anchor="start">'
                      f'{escape(texto)}</text>')

    return (f'<svg viewBox="0 0 {ancho} {alto}" width="100%" '
            f'style="max-width:{ancho}px" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="Segoe UI, Arial, sans-serif">{"".join(cuerpo)}</svg>')
