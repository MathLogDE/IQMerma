"""ui/paginas/merma_conciliacion.py — Conciliación de diferencias entre depósitos"""
from datetime import timedelta

import streamlit as st

from ui.comun import *  # noqa: F401,F403

MAX_CODIGOS = 20
ANULADOS = ("remito anulado", "anulación")


def _codigos(texto: str) -> list[str]:
    # Texto, no número: los códigos pueden traer ceros a la izquierda.
    return list(dict.fromkeys(c.strip() for c in
                              texto.replace(";", ",").replace("\n", ",").replace(" ", ",").split(",")
                              if c.strip()))


def render(proyecto):
    st.title("Conciliación")
    st.caption(
        "¿A dónde fueron (o de dónde vinieron) unas unidades? Para uno o varios códigos, "
        "lista los remitos que no cerraron entre depósitos y los ajustes de toda la red, "
        "con una columna por código para verlos en forma transversal."
    )

    fmin, fmax = rango_disponible(proyecto)
    if fmax is None:
        st.info("No hay movimientos cargados todavía.")
        return

    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    texto = c1.text_input("Códigos", key="conc_cod",
                          placeholder="1006053, 1006054",
                          help="Separados por coma, espacio o punto y coma.")
    desde = c2.date_input("Desde", value=max(fmin, fmax - timedelta(days=180)),
                          min_value=fmin, max_value=fmax, key="conc_desde")
    hasta = c3.date_input("Hasta", value=fmax, min_value=fmin, max_value=fmax, key="conc_hasta")
    transito = c4.number_input("Días en tránsito", 0, 60, 7, key="conc_trans",
                               help="Remitos sin entrada más nuevos que esto se marcan "
                                    "«en tránsito» en vez de «sin destino».")

    codigos = _codigos(texto)
    if len(codigos) > MAX_CODIGOS:
        st.warning(f"Máximo {MAX_CODIGOS} códigos.")
        return
    if st.button("Buscar", disabled=not codigos or desde > hasta):
        with st.spinner("Buscando movimientos..."):
            try:
                mov = movimientos_codigos(proyecto, codigos, desde, hasta)
                st.session_state["conc"] = {
                    "codigos": codigos, "hasta": hasta, "mov": mov,
                    "ev": eventos_conciliacion(mov, fmax, dias_transito=transito),
                    "stock": stock_por_deposito(proyecto, codigos, hasta),
                }
            except Exception as e:
                st.error(f"Error: {e}")

    res = st.session_state.get("conc")
    if not res:
        return
    mov, ev, codigos = res["mov"], res["ev"], res["codigos"]

    cat = catalogo_cacheado(proyecto).set_index("codigo")["descripcion"]
    for c in codigos:
        st.markdown(f"- **{c}** — {cat.get(c, 'sin descripción en el catálogo')}"
                    + ("" if (mov["codigo"] == c).any() else " · *sin movimientos en el período*"))
    st.caption("⚠️ Son hipótesis, no pruebas: si al ERP le faltan movimientos de un depósito, "
               "se ven igual que un remito sin destino.")

    # ---- 1. Eventos ----------------------------------------------------------
    st.markdown("---")
    st.subheader("Eventos")
    st.caption("Una fila por remito descalzado o por ajuste (fecha + depósito + usuario + motivo). "
               "Cada código muestra su efecto neto en la red: **+** aparecieron, **−** desaparecieron. "
               "En los remitos, la columna usuario es la contraparte (→ destino, ← origen).")
    if ev.empty:
        st.info("No hay remitos descalzados ni ajustes para estos códigos en el período.")
    else:
        base = ev["patron"].str.replace(r" \(.*\)$", "", regex=True)
        patrones = sorted(base.unique())
        sel = st.multiselect("Patrones", patrones, key="conc_pat",
                             default=[p for p in patrones if not p.startswith(ANULADOS)])
        depos = sorted({d for ds in ev["depositos"] for d in ds.split(", ")})
        dsel = st.multiselect("Depósitos", depos, key="conc_dep", placeholder="Todos")
        vista = ev[base.isin(sel)]
        if dsel:
            vista = vista[vista["depositos"].apply(lambda ds: any(d in ds.split(", ") for d in dsel))]
        tabla = vista.assign(fecha=vista["fecha"].dt.date)
        st.dataframe(tabla, width="stretch", hide_index=True,
                     column_config={c: st.column_config.NumberColumn(format="%+g") for c in codigos})
        botones_descarga(tabla, "conciliacion_eventos", "conc_ev")

        # ---- 2. Combinaciones ------------------------------------------------
        st.markdown("---")
        st.subheader("¿Qué eventos suman la cantidad?")
        st.caption("Si te faltan 6 en un depósito, buscá **+6** (aparecieron en otro lado) "
                   "o **−6** (salieron y no llegaron). Combina hasta 3 eventos, sin anulados.")
        k1, k2 = st.columns([2, 1])
        cod = k1.selectbox("Código", codigos, key="conc_comb_cod",
                           format_func=lambda c: f"{c} — {cat.get(c, '')}")
        cant = k2.number_input("Cantidad (con signo)", value=0, step=1, key="conc_comb_cant")
        if cant:
            comb = combinaciones(ev, cod, cant)
            if comb.empty:
                st.info(f"Ningún evento ni combinación de hasta 3 suma {cant:+d}.")
            else:
                st.dataframe(comb.assign(desde=comb["desde"].dt.date, hasta=comb["hasta"].dt.date),
                             width="stretch", hide_index=True)

    # ---- 3. Red ----------------------------------------------------------------
    st.markdown("---")
    st.subheader("Toda la red")
    r1, r2 = st.columns(2)
    with r1:
        st.markdown(f"**Stock ERP al {res['hasta']:%d/%m/%Y}** (último snapshot)")
        stk = res["stock"]
        if stk.empty:
            st.caption("Sin snapshots de stock para estos códigos.")
        else:
            st.dataframe(stk.pivot(index="codigodepo", columns="codigo", values="stock").fillna(0),
                         width="stretch")
    with r2:
        st.markdown("**Neto del período por depósito y tipo**")
        if mov.empty:
            st.caption("Sin movimientos.")
        else:
            st.dataframe(mov.pivot_table(index=["codigodepo", "tipo"], columns="codigo",
                                         values="diferencia", aggfunc="sum", fill_value=0),
                         width="stretch")

    # ---- 4. Kardex -------------------------------------------------------------
    st.markdown("---")
    with st.expander(f"Kardex: todos los movimientos ({len(mov):,})"):
        st.dataframe(mov.head(5000), width="stretch", hide_index=True)
        botones_descarga(mov, "conciliacion_movimientos", "conc_mov")
