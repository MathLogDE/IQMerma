"""ui/paginas/ingesta.py — Página Ingesta"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Ingesta de datos")

    tab1, tab2, tab3, tab4 = st.tabs(
        ["📋 Períodos cargados", "📁 Cargar archivo", "⚙ Depósitos", "🎨 Cliente"]
    )

    with tab1:
        df_periodos = listar_periodos_ingesta(proyecto)
        if df_periodos.empty:
            st.info("No hay datos cargados aún.")
        else:
            tablas = ["Todos"] + sorted(df_periodos["tabla"].unique().tolist())
            filtro = st.selectbox("Filtrar por tabla", tablas)
            if filtro != "Todos":
                df_periodos = df_periodos[df_periodos["tabla"] == filtro]
            st.dataframe(df_periodos, width="stretch", hide_index=True)

    with tab2:
        st.markdown("#### Cargar archivo manualmente")
        st.caption(
            "El inventario físico entra dentro de **movimientos** "
            "(TIPOMOV='INV'), en el mismo export del ERP — no se carga como "
            "archivo aparte."
        )

        tipo_archivo = st.selectbox(
            "Tipo de archivo",
            ["movimientos", "stock", "depositos", "estructura", "articulos"]
        )

        archivo = st.file_uploader(
            "Seleccioná el archivo Excel",
            type=["xlsx"],
            key=f"uploader_{tipo_archivo}"
        )

        if tipo_archivo == "stock":
            fecha_snapshot = st.date_input("Fecha del snapshot de stock")

        multi_hoja = False
        if tipo_archivo == "movimientos":
            multi_hoja = st.checkbox(
                "Archivo con varias pestañas (una por mes)", value=False,
                help="Carga cada pestaña como su propio período.",
            )

        forzar = st.checkbox("Reemplazar si ya existe (forzar)", value=False)

        if archivo and st.button("Ingestar"):
            tmp_dir = ROOT / "exports"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / archivo.name
            tmp_path.write_bytes(archivo.read())

            try:
                with st.spinner("Procesando..."):
                    if tipo_archivo == "movimientos":
                        if multi_hoja:
                            from ingesta.movimientos import ingestar_libro_movimientos
                            r = ingestar_libro_movimientos(tmp_path, proyecto, forzar=forzar)
                            st.success(
                                f"✓ {r['registros_total']} movimientos — "
                                f"{r['cargadas']}/{r['hojas']} pestañas cargadas"
                            )
                            if r["errores"]:
                                st.warning(
                                    "Pestañas con error: "
                                    + ", ".join(f"{e['hoja']} ({e['error']})" for e in r["errores"])
                                )
                        else:
                            from ingesta.movimientos import ingestar_movimientos
                            r = ingestar_movimientos(tmp_path, proyecto, forzar=forzar)
                            st.success(f"✓ {r['registros']} movimientos cargados — período {r['periodo']}")

                    elif tipo_archivo == "stock":
                        from ingesta.stock import ingestar_stock
                        r = ingestar_stock(
                            tmp_path, proyecto,
                            fecha_snapshot=str(fecha_snapshot),
                            forzar=forzar
                        )
                        st.success(
                            f"✓ {r['registros']:,} registros cargados — "
                            f"{len(r['sucursales_mapeadas'])} sucursales — "
                            f"snapshot {r['fecha_snapshot']}"
                        )
                        if r['columnas_no_mapeadas']:
                            st.warning(
                                f"Columnas sin mapeo en depósitos (ignoradas): "
                                f"{r['columnas_no_mapeadas']}"
                            )

                    elif tipo_archivo == "depositos":
                        from ingesta.referencias import cargar_depositos
                        r = cargar_depositos(tmp_path, proyecto)
                        st.success(f"✓ Depósitos: {r['insertados']} nuevos, {r['actualizados']} actualizados")

                    elif tipo_archivo == "estructura":
                        from ingesta.referencias import cargar_estructura
                        r = cargar_estructura(tmp_path, proyecto)
                        st.success(f"✓ Estructura: {r['insertados']} nuevos, {r['actualizados']} actualizados")

                    elif tipo_archivo == "articulos":
                        from ingesta.referencias import cargar_articulos
                        r = cargar_articulos(tmp_path, proyecto)
                        st.success(f"✓ Artículos: {r['insertados']} nuevos, {r['actualizados']} actualizados")

            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                tmp_path.unlink(missing_ok=True)

    with tab3:
        st.markdown("#### Depósitos y centros de logística")
        st.caption(
            "Marcá `es_logistica` para los centros de distribución (CDC, CR2…). "
            "Los CD se excluyen de las comparativas de merma y del análisis de "
            "salud de stock. El flag se preserva al recargar el archivo de depósitos."
        )
        conn = get_connection(proyecto)
        df_deps = conn.execute("""
            SELECT codigodepo, nombre, abreviacion, es_logistica
            FROM depositos ORDER BY codigodepo
        """).df()
        conn.close()

        if df_deps.empty:
            st.info("No hay depósitos cargados.")
        else:
            df_edit = st.data_editor(
                df_deps,
                column_config={
                    "codigodepo":   st.column_config.TextColumn("Código", disabled=True),
                    "nombre":       st.column_config.TextColumn("Nombre", disabled=True),
                    "abreviacion":  st.column_config.TextColumn("Abrev.", disabled=True),
                    "es_logistica": st.column_config.CheckboxColumn("Logística (CD)"),
                },
                hide_index=True, width="stretch", key="editor_deps",
            )
            if st.button("Guardar cambios", key="save_deps"):
                cambios = df_edit[
                    df_edit["es_logistica"].fillna(False)
                    != df_deps["es_logistica"].fillna(False)
                ]
                if cambios.empty:
                    st.info("No hay cambios para guardar.")
                else:
                    conn = get_connection(proyecto)
                    for _, r in cambios.iterrows():
                        conn.execute(
                            "UPDATE depositos SET es_logistica = ? WHERE codigodepo = ?",
                            [bool(r["es_logistica"]), r["codigodepo"]],
                        )
                    conn.close()
                    st.success(f"✓ {len(cambios)} depósitos actualizados")
                    st.rerun()

    with tab4:
        st.markdown("#### Marca del cliente en los reportes imprimibles")
        st.caption(
            "El logo y los datos aparecen en el encabezado de todos los "
            "reportes; el color de acento tiñe títulos, KPIs y barras. "
            "Se guarda junto a la base del proyecto (fuera de git)."
        )
        br = cargar_branding(proyecto)

        b1, b2 = st.columns([2, 1])
        with b1:
            br_nombre = st.text_input("Nombre del cliente", value=br["nombre"],
                                      key="br_nombre")
            br_datos = st.text_area(
                "Datos (una línea por dato: razón social, CUIT, contacto…)",
                value="\n".join(br["datos"]), height=100, key="br_datos",
            )
            br_pie = st.text_input(
                "Pie de página", value=br["pie"],
                placeholder="MermaIQ — análisis de inventario", key="br_pie",
            )
        with b2:
            br_color = st.color_picker("Color de acento",
                                       value=br["color"] or "#c0392b", key="br_color")
            logo_file = st.file_uploader("Logo (PNG/JPG)", type=["png", "jpg", "jpeg"],
                                         key="br_logo")
            if br["logo_uri"] and not logo_file:
                st.image(br["logo_uri"], caption="Logo actual", width=160)

        if st.button("Guardar marca", key="br_save"):
            logo_bytes, logo_ext = None, ".png"
            if logo_file is not None:
                logo_bytes = logo_file.read()
                logo_ext = Path(logo_file.name).suffix or ".png"
            guardar_branding(
                proyecto, nombre=br_nombre, datos=br_datos,
                color=br_color, pie=br_pie,
                logo_bytes=logo_bytes, logo_ext=logo_ext,
            )
            st.success("✓ Marca guardada — se aplica a todos los reportes")
            st.rerun()
