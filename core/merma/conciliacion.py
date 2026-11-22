"""
core/merma/conciliacion.py — Conciliación de diferencias entre depósitos

Para uno o varios códigos, arma la lista de "eventos" que movieron unidades
en la red sin compensarse solos, con una columna por código para ver los
códigos en forma transversal:

  - remitos internos (RI, MD...) emparejados por número, cuyas puntas no
    suman cero entre depósitos (sin destino, sin origen, con diferencia,
    en tránsito, anulado). En los remitos el ERP pone la contraparte en
    `usuario` (destino en la salida, origen en la entrada): se muestra como
    "→ MENDOZA" / "← SALTA";
  - ajustes y otros comprobantes no venta, agrupados por fecha + depósito +
    usuario + tipo + motivo (suelto, conjunto, cruzado +/−).

Solo lectura: propone hipótesis ("en julio se ajustaron +6 en MZA 016"),
no prueba nada ni corrige el ERP.
"""
from datetime import timedelta
from itertools import combinations

import pandas as pd

from core.comun import conectar

# Ingresos de proveedor y devoluciones a proveedor no tienen contraparte en la red.
TIPOS_EXCLUIDOS = ("RE", "RDC")


def movimientos_codigos(proyecto: str, codigos: list[str],
                        fecha_desde, fecha_hasta) -> pd.DataFrame:
    """
    Todos los movimientos de `codigos` en el período, en todos los depósitos.
    Los remitos (RI, MD...) con alguna punta en el período se traen completos (aunque
    la otra punta caiga fuera), para no inventar descalces en los bordes.
    """
    params = {"codigos": list(codigos),
              "desde": str(pd.to_datetime(fecha_desde).date()),
              "hasta": str(pd.to_datetime(fecha_hasta).date())}
    conn = conectar(proyecto)
    try:
        return conn.execute("""
            SELECT m.fecha, m.codigodepo, d.abreviacion, m.tipomov, m.tipo,
                   m.tipo_aj, m.usuario, m.numero, m.codigo,
                   CAST(m.ingreso AS DOUBLE) AS ingreso,
                   CAST(m.egreso AS DOUBLE) AS egreso,
                   CAST(m.diferencia AS DOUBLE) AS diferencia
            FROM movimientos m
            LEFT JOIN depositos d ON m.codigodepo = d.codigodepo
            WHERE m.codigo IN (SELECT unnest($codigos))
              AND (m.fecha BETWEEN $desde AND $hasta
                   OR (m.tipomov = 'REM' AND EXISTS (
                        SELECT 1 FROM movimientos x
                        WHERE x.tipomov = 'REM' AND x.numero = m.numero AND x.tipo = m.tipo
                          AND x.codigo IN (SELECT unnest($codigos))
                          AND x.fecha BETWEEN $desde AND $hasta)))
            ORDER BY m.fecha, m.numero, m.codigodepo, m.codigo
        """, params).df()
    finally:
        conn.close()


def stock_por_deposito(proyecto: str, codigos: list[str], fecha) -> pd.DataFrame:
    """Stock ERP del último snapshot <= fecha, por depósito × código (long)."""
    conn = conectar(proyecto)
    try:
        return conn.execute("""
            SELECT codigodepo, codigo, fecha_snapshot, CAST(stock AS DOUBLE) AS stock
            FROM stock_sucursal
            WHERE codigo IN (SELECT unnest($codigos)) AND fecha_snapshot <= $fecha
            QUALIFY row_number() OVER (PARTITION BY codigo, codigodepo
                                       ORDER BY fecha_snapshot DESC) = 1
            ORDER BY codigodepo, codigo
        """, {"codigos": list(codigos),
              "fecha": str(pd.to_datetime(fecha).date())}).df()
    finally:
        conn.close()


def _depo(g: pd.DataFrame) -> str:
    pares = g[["codigodepo", "abreviacion"]].drop_duplicates().sort_values("codigodepo")
    return ", ".join(f"{c} {a}" if pd.notna(a) else str(c)
                     for c, a in pares.itertuples(index=False))


def eventos(mov: pd.DataFrame, fecha_corte, dias_transito: int = 7,
            dias_anulacion: int = 7) -> pd.DataFrame:
    """
    Eventos que no se compensan solos, una fila por evento y una columna por
    código con su efecto neto en la red (+ aparecieron, − desaparecieron).

    Args:
        mov:            salida de `movimientos_codigos`.
        fecha_corte:    último dato cargado; los remitos sin destino más nuevos
                        que `dias_transito` se marcan "en tránsito".
        dias_anulacion: ventana para emparejar un remito sin destino con el
                        ajuste CS que lo anula (mismo depósito, mismas
                        cantidades con signo contrario).

    Returns:
        DataFrame: fecha, depositos, tipo, tipo_aj, usuario, comprobantes,
        patron, <una columna por código>. attrs["codigos"] = lista de códigos.
    """
    codigos = sorted(mov["codigo"].unique())
    m = mov[(mov["diferencia"] != 0) & (mov["tipomov"] != "VTA")
            & ~mov["tipo"].isin(TIPOS_EXCLUIDOS)]
    corte_transito = pd.to_datetime(fecha_corte) - timedelta(days=dias_transito)
    filas = []

    es_rem = m["tipomov"] == "REM"
    for (numero, tipo), g in m[es_rem].groupby(["numero", "tipo"]):
        neto = g.groupby("codigo")["diferencia"].sum().round(4)
        if (neto == 0).all():
            continue
        un_depo = g["codigodepo"].nunique() == 1
        if un_depo and (neto <= 0).all():
            patron = ("remito en tránsito" if pd.to_datetime(g["fecha"].min()) > corte_transito
                      else "remito sin destino")
        elif un_depo and (neto >= 0).all():
            patron = "remito sin origen"
        else:
            patron = "remito con diferencia"
        contraparte = sorted({f"{'→' if d < 0 else '←'} {u}"
                              for u, d in zip(g["usuario"], g["diferencia"]) if pd.notna(u)})
        filas.append({"fecha": g["fecha"].min(), "depositos": _depo(g), "tipo": tipo,
                      "tipo_aj": None, "usuario": ", ".join(contraparte),
                      "comprobantes": str(numero), "patron": patron,
                      "_depo": g["codigodepo"].iloc[0] if un_depo else None, **neto.to_dict()})

    otros = m[~es_rem]
    claves = ["fecha", "codigodepo", "tipo", "tipo_aj", "usuario"]
    for (fecha, depo, tipo, tipo_aj, usuario), g in otros.groupby(claves, dropna=False):
        neto = g.groupby("codigo")["diferencia"].sum().round(4)
        neto = neto[neto != 0]
        if neto.empty:
            continue
        if (neto > 0).any() and (neto < 0).any():
            patron = "ajuste cruzado (+/−)"
        elif len(neto) > 1:
            patron = "ajuste conjunto"
        else:
            patron = "ajuste suelto"
        filas.append({"fecha": fecha, "depositos": _depo(g), "tipo": tipo,
                      "tipo_aj": tipo_aj if pd.notna(tipo_aj) else None,
                      "usuario": usuario if pd.notna(usuario) else None,
                      "comprobantes": ", ".join(sorted(g["numero"].astype(str).unique())),
                      "patron": patron, "_depo": depo, **neto.to_dict()})

    cols = ["fecha", "depositos", "tipo", "tipo_aj", "usuario", "comprobantes", "patron"]
    if not filas:
        ev = pd.DataFrame(columns=cols + codigos)
        ev.attrs["codigos"] = codigos
        return ev
    ev = pd.DataFrame(filas)
    for c in codigos:
        ev[c] = ev[c].fillna(0.0) if c in ev else 0.0
    ev["fecha"] = pd.to_datetime(ev["fecha"])
    ev = ev.sort_values(["fecha", "comprobantes"]).reset_index(drop=True)

    # Emparejar anulaciones: remito sin destino ↔ CS posterior en el mismo
    # depósito con exactamente las cantidades opuestas (sobre los códigos elegidos).
    # shortcut: compara solo los códigos seleccionados; si la anulación incluye otros
    # códigos igual matchea, y si el CS anula dos remitos juntos no matchea ninguno.
    def anula(j, r):
        return (ev.at[j, "tipo"] == "CS" and ev.at[j, "_depo"] == r["_depo"]
                and r["fecha"] <= ev.at[j, "fecha"] <= r["fecha"] + timedelta(days=dias_anulacion)
                and all(round(ev.at[j, c] + r[c], 4) == 0 for c in codigos))

    colgados = ev.index[ev["patron"].isin(["remito sin destino", "remito en tránsito"])]
    usados = {}  # índice del CS -> comprobante del remito que anula
    for i in colgados:
        r = ev.loc[i]
        j = next((j for j in ev.index if j not in usados and anula(j, r)), None)
        if j is not None:
            usados[j] = r["comprobantes"]
            ev.at[i, "patron"] = "remito anulado"
            ev.at[j, "patron"] = f"anulación de remito {r['comprobantes']}"
    # Si una anulación calza igual con otro remito colgado, no sabemos cuál anuló.
    for i in colgados:
        if ev.at[i, "patron"] == "remito anulado":
            continue
        otros = [n for j, n in usados.items() if anula(j, ev.loc[i])]
        if otros:
            ev.at[i, "patron"] += f" (o {', '.join(otros)}: anulación ambigua)"

    ev = ev[cols + codigos]
    ev.attrs["codigos"] = codigos
    return ev


def combinaciones(ev: pd.DataFrame, codigo: str, cantidad: float,
                  max_eventos: int = 3, tope: int = 40, max_resultados: int = 50) -> pd.DataFrame:
    """
    Combinaciones de hasta `max_eventos` eventos cuyo efecto sobre `codigo`
    suma exactamente `cantidad` (con signo). Solo mira eventos del mismo signo
    y de magnitud <= |cantidad|; los anulados no cuentan.

    Returns:
        DataFrame: n_eventos, desde, hasta, dias, detalle — ordenado por menos
        eventos y menor dispersión en fechas.
    """
    if cantidad == 0 or codigo not in ev:
        return pd.DataFrame(columns=["n_eventos", "desde", "hasta", "dias", "detalle"])
    vivos = ev[~ev["patron"].str.startswith(("remito anulado", "anulación"))]
    cand = vivos[(vivos[codigo] * cantidad > 0) & (vivos[codigo].abs() <= abs(cantidad))]
    # shortcut: tope de candidatos (C(40,3) ≈ 10k combinaciones); subir si quedan afuera.
    cand = cand.head(tope)

    res = []
    for k in range(1, max_eventos + 1):
        for idx in combinations(cand.index, k):
            if round(cand.loc[list(idx), codigo].sum() - cantidad, 4) != 0:
                continue
            sub = cand.loc[list(idx)]
            detalle = " + ".join(
                f"{r.fecha:%d/%m/%y} {r.depositos} {r.tipo} {r.tipo_aj or ''} "
                f"{r.usuario or ''} ({r.valor:+g})".replace("  ", " ")
                for r in sub.assign(valor=sub[codigo]).itertuples())
            res.append({"n_eventos": k, "desde": sub["fecha"].min(), "hasta": sub["fecha"].max(),
                        "dias": (sub["fecha"].max() - sub["fecha"].min()).days,
                        "detalle": detalle})
    out = pd.DataFrame(res, columns=["n_eventos", "desde", "hasta", "dias", "detalle"])
    return out.sort_values(["n_eventos", "dias"]).head(max_resultados).reset_index(drop=True)
