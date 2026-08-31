"""ui/paginas/ingesta.py — Página Ingesta"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403
from core.auth import (
    PAGINAS_DISPONIBLES, TODAS_LAS_PAGINAS, set_acceso_pagina, set_acceso_paginas,
)


def render(proyecto):
    st.title("Ingesta de datos")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["📋 Períodos cargados", "📁 Cargar archivo", "⚙ Depósitos", "🎨 Cliente",
         "👤 Usuarios web"]
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
        hoja_sel = 0
        hojas = None
        if tipo_archivo == "movimientos" and archivo is not None:
            try:
                hojas = pd.ExcelFile(archivo, engine="openpyxl").sheet_names
            except Exception:
                hojas = None
            archivo.seek(0)  # el listado de pestañas consume el stream

            if hojas and len(hojas) > 1:
                multi_hoja = st.checkbox(
                    "Cargar todas las pestañas (una por mes)", value=False,
                    help="Si no lo marcás, elegís abajo una sola pestaña para cargar.",
                )
                if not multi_hoja:
                    hoja_sel = st.selectbox(
                        "Pestaña a cargar", hojas, key="ingesta_hoja_sel",
                        help="Solo se carga esta pestaña; las demás quedan afuera.",
                    )
            elif hojas:
                st.caption(f"Pestaña: **{hojas[0]}**")

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
                            r = ingestar_movimientos(tmp_path, proyecto, forzar=forzar, sheet=hoja_sel)
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

    with tab5:
        st.markdown("#### Usuarios con acceso web")
        st.caption(
            "Cuentas para la instancia de solo lectura (ui/app_cliente.py), "
            "la que se expone por internet. Un usuario puede tener acceso a "
            f"más de un proyecto; acá se administra su acceso a **{proyecto}** "
            "en particular. Esta pestaña solo existe en tu sesión local — "
            "nunca se expone."
        )

        usuarios = listar_usuarios()
        con_acceso = {u: d for u, d in usuarios.items()
                     if proyecto in (d.get("proyectos") or [])}
        sin_acceso = {u: d for u, d in usuarios.items()
                     if proyecto not in (d.get("proyectos") or [])}

        st.markdown("###### Con acceso a este proyecto")
        if not con_acceso:
            st.info("Nadie tiene acceso a este proyecto todavía.")
        for u, d in sorted(con_acceso.items()):
            c1, c2, c3 = st.columns([2, 3, 1])
            c1.write(f"**{u}**")
            c2.caption(f"{d.get('name', '')} · {d.get('email', '')}")
            if c3.button("Quitar acceso", key=f"quitar_{u}"):
                set_acceso_proyecto(u, proyecto, False)
                st.rerun()

        if sin_acceso:
            with st.expander(f"Otros usuarios sin acceso a este proyecto ({len(sin_acceso)})"):
                for u, d in sorted(sin_acceso.items()):
                    c1, c2, c3 = st.columns([2, 3, 1])
                    c1.write(u)
                    otros = ", ".join(d.get("proyectos") or []) or "ninguno"
                    c2.caption(f"{d.get('name', '')} · acceso hoy a: {otros}")
                    if c3.button("Dar acceso", key=f"dar_{u}"):
                        set_acceso_proyecto(u, proyecto, True)
                        st.rerun()

        st.markdown("---")
        st.markdown("###### Qué módulos y páginas puede ver cada usuario")
        st.caption(
            "Independiente del proyecto: aplica a todos los proyectos que ese "
            "usuario vea. Un usuario sin ninguna página marcada entra pero no "
            "ve nada."
        )
        for u, d in sorted(usuarios.items()):
            paginas_guardadas = d.get("paginas")
            paginas_usuario = set(
                paginas_guardadas if paginas_guardadas is not None else TODAS_LAS_PAGINAS
            )
            with st.expander(f"{u} — {d.get('name', '')} "
                             f"({len(paginas_usuario)}/{len(TODAS_LAS_PAGINAS)} páginas)"):
                for modulo, paginas_modulo in PAGINAS_DISPONIBLES.items():
                    todo_marcado = all(pag in paginas_usuario for pag in paginas_modulo)
                    key_todo = f"pagtodo_{u}_{modulo}"
                    marcar_todo = st.checkbox(
                        f"**{modulo}**", value=todo_marcado, key=key_todo
                    )
                    if marcar_todo != todo_marcado:
                        set_acceso_paginas(u, paginas_modulo, marcar_todo)
                        # Los checkboxes individuales de este módulo quedaron con
                        # su valor viejo pegado en session_state (Streamlit no
                        # los actualiza solo porque cambió `value=`) — se
                        # descartan para que se recalculen desde el archivo
                        # recién escrito, si no el rerun los detecta como
                        # "tildados a mano" y deshace el cambio del módulo.
                        for pag in paginas_modulo:
                            st.session_state.pop(f"pag_{u}_{pag}", None)
                        st.rerun()
                    cols = st.columns(len(paginas_modulo))
                    for col, pag in zip(cols, paginas_modulo):
                        key_pag = f"pag_{u}_{pag}"
                        marcado = col.checkbox(
                            pag, value=pag in paginas_usuario, key=key_pag
                        )
                        if marcado != (pag in paginas_usuario):
                            set_acceso_pagina(u, pag, marcado)
                            # mismo motivo: el checkbox "todo el módulo" no debe
                            # quedar con un valor viejo pegado.
                            st.session_state.pop(key_todo, None)
                            st.rerun()

        st.markdown("---")
        st.markdown("###### Crear usuario nuevo")
        with st.form("form_nuevo_usuario", clear_on_submit=True):
            nu_user = st.text_input(
                "Usuario", key="nu_user",
                help="Minúsculas, sin espacios — es lo que va a tipear para entrar.")
            nu_nombre = st.text_input("Nombre y apellido", key="nu_nombre")
            nu_email = st.text_input("Email", key="nu_email")
            nu_pass = st.text_input("Contraseña", type="password", key="nu_pass")
            crear = st.form_submit_button(f"Crear con acceso a {proyecto}")

        if crear:
            username = nu_user.strip().lower().replace(" ", "_")
            if not username or not nu_pass:
                st.error("Usuario y contraseña son obligatorios.")
            else:
                try:
                    crear_usuario(username, nu_nombre.strip(), nu_email.strip(),
                                 nu_pass, [proyecto])
                    st.success(f"✓ Usuario '{username}' creado con acceso a {proyecto}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

        if usuarios:
            st.markdown("###### Cambiar contraseña")
            cp1, cp2, cp3 = st.columns([2, 2, 1])
            with cp1:
                cp_user = st.selectbox("Usuario", sorted(usuarios), key="cp_user")
            with cp2:
                cp_pass = st.text_input("Contraseña nueva", type="password", key="cp_pass")
            with cp3:
                st.write("")
                st.write("")
                if st.button("Actualizar", key="cp_btn"):
                    if not cp_pass:
                        st.error("Ingresá una contraseña.")
                    else:
                        cambiar_password(cp_user, cp_pass)
                        st.success(f"✓ Contraseña actualizada para {cp_user}")

            st.markdown("###### Eliminar usuario")
            de1, de2 = st.columns([2, 1])
            with de1:
                del_user = st.selectbox("Usuario a eliminar", sorted(usuarios), key="del_user")
            with de2:
                st.write("")
                st.write("")
                if st.button("Eliminar definitivamente", key="del_btn"):
                    eliminar_usuario(del_user)
                    st.success(f"✓ '{del_user}' eliminado")
                    st.rerun()
