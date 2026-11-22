# Graph Report - iqmerma  (2026-11-22)

## Corpus Check
- 83 files · ~301,403 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 4 file(s) not represented in the graph (top: (none) 2, .bat 1, .cmd 1)

## Summary
- 1386 nodes · 3967 edges · 72 communities (59 shown, 13 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 187 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `6a78fa80`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- _render
- render
- IQMerma — Documento de referencia
- pandas
- 6. Analizar la merma
- streamlit
- core/comun.py
- ui/comun.py
- movimientos.py
- generar_reporte_diferencias
- Actor
- a_excel_pedido_general
- reposicion.py
- paginas/no_conformidades.py
- CLAUDE.md
- demanda.py
- conectar
- semanal.py
- cerrar_con_sucursal
- pandas
- nc_mail.py
- precio_unitario_fila
- stock.py
- render
- cargar_config
- .emitida
- nc_contactos.py
- comprobantes.py
- _config_mail
- crear_borrador
- reporte.py
- graficos.py
- interanual.py
- _despivotear
- clasificar_demanda_sku
- test_no_conformidades.py
- .test_login_solo_plain_y_mensajes
- pedido_general.py
- politicas.py
- auth.py
- generar_reporte_transformaciones
- setup_db.py
- datetime
- Guía de uso — MermaIQ
- enviar_cierre
- IQMerma — Estrategia de compliance y expansión
- render
- series.py
- referencias.py
- nc_anexos.py
- comercial_comprobantes.py
- json
- core/no_conformidades.py
- 4. Lean Six Sigma
- 5. Qué está atado al cliente piloto y cómo generalizarlo
- 5. Cargar datos
- parsear_docx
- conciliacion.py
- Migración v1 → v2 (análisis por selección)
- MermaIQ
- tipos_movimiento
- _parsear_fechas
- test_stock_parcial.py
- 2. Decisiones de arquitectura (ADR)
- render
- construir_mensaje

## God Nodes (most connected - your core abstractions)
1. `conectar()` - 89 edges
2. `conectar()` - 71 edges
3. `Actor` - 46 edges
4. `NCError` - 39 edges
5. `precios_snapshot()` - 37 edges
6. `obtener()` - 34 edges
7. `_tx()` - 33 edges
8. `_evento()` - 31 edges
9. `render()` - 31 edges
10. `_exigir()` - 29 edges

## Surprising Connections (you probably didn't know these)
- `6.5 Anexos con datos de IQMerma` --references--> `serie_stock()`  [INFERRED]
  IQMERMA.md → core/inventario/series.py
- `5.6 Inventario, políticas y salud` --references--> `avance_conteo()`  [INFERRED]
  IQMERMA.md → core/merma/avance.py
- `6.3 Congelamiento e integridad del documento` --references--> `_html_verificado()`  [INFERRED]
  IQMERMA.md → core/no_conformidades.py
- `Movimientos con varias pestañas (un Excel por año)` --references--> `ingestar_libro_movimientos()`  [INFERRED]
  GUIA_USO.md → ingesta/movimientos.py
- `Hecho` --references--> `cargar_depositos()`  [INFERRED]
  MIGRACION_V2.md → ingesta/referencias.py

## Import Cycles
- None detected.

## Communities (72 total, 13 thin omitted)

### Community 0 - "_render"
Cohesion: 0.09
Nodes (15): generar_reporte(), generar_reporte_avance_conteo(), generar_reporte_control(), generar_reporte_cruce(), generar_reporte_forecast(), generar_reporte_interanual(), _filas(), generar_reporte_politicas() (+7 more)

### Community 1 - "render"
Cohesion: 0.08
Nodes (32): resumen_salud(), boton_reporte(), botones_descarga(), catalogo_cacheado(), color_grilla(), etiqueta_seleccion_sucursales(), etiqueta_stock(), etiquetas_sucursal() (+24 more)

### Community 2 - "IQMerma — Documento de referencia"
Cohesion: 0.05
Nodes (40): 10. Glosario, 11. Mapa de documentos, 12. Changelog del documento, 1. Qué es IQMerma, 2. Arquitectura, 3. Funcionalidades actuales, 4. Qué NO hace (límites explícitos), 5.2 Valorización: siempre desde el snapshot de stock (+32 more)

### Community 3 - "pandas"
Cohesion: 0.11
Nodes (17): validar_modo_valorizacion(), calcular_merma(), calcular_merma_dinamica(), _componentes_sucursal(), _ensamblar(), _ensamblar_sucursal(), _extremos(), fechas_corte_comprobante() (+9 more)

### Community 4 - "6. Analizar la merma"
Cohesion: 0.14
Nodes (14): 6. Analizar la merma, Análisis → pestaña Comparativa por sucursal, Análisis → pestaña Pareto por rubro, Ingesta → ⚙ Depósitos, Página Análisis, Página Control de merma, Página Forecast, Página Min / Opt / Max (+6 more)

### Community 6 - "core/comun.py"
Cohesion: 0.09
Nodes (13): analizar_margenes(), adjuntar_rubros(), filtrar_catalogo(), movimientos_detalle(), precios_snapshot(), snapshot_usado(), agregar_contraste_transferido(), diferencias_camion() (+5 more)

### Community 7 - "ui/comun.py"
Cohesion: 0.07
Nodes (24): a_excel(), a_excel_forecast_semanal_sku(), a_excel_matriz_merma(), abreviaciones_sucursal(), avance_conteo_cacheada(), avance_matriz_cacheada(), clasificar_demanda_sku_cacheada(), comparativa_cacheada() (+16 more)

### Community 8 - "movimientos.py"
Cohesion: 0.13
Nodes (10): _detectar_periodo(), _get_connection(), ingestar_libro_movimientos(), ingestar_movimientos(), _insertar_movimientos(), _leer_excel(), _periodo_de_hoja(), _registrar_ingesta() (+2 more)

### Community 9 - "generar_reporte_diferencias"
Cohesion: 0.33
Nodes (3): evolucion_diferencias(), resumen_diferencias(), generar_reporte_diferencias()

### Community 10 - "Actor"
Cohesion: 0.10
Nodes (34): _acciones_impl(), Actor, actualizar_borrador(), agregar_adjunto(), _ahora(), anular(), cerrar_con_sucursal(), eliminar_borrador() (+26 more)

### Community 11 - "a_excel_pedido_general"
Cohesion: 0.43
Nodes (5): a_excel_pedido_general(), borde_grupo(), col_idx(), combinar(), rango()

### Community 12 - "reposicion.py"
Cohesion: 0.13
Nodes (11): _cadencia_remitos(), _dias_con_stock(), _dias_desde_quiebre_real(), pedido_por_dias(), perfil_reposicion(), resumen_reposicion(), _stock_disponible(), _tiempo_hasta_quiebre() (+3 more)

### Community 13 - "paginas/no_conformidades.py"
Cohesion: 0.13
Nodes (24): rango_default(), rubros(), anexos(), tipos_ordenados(), modo_solo_lectura(), _acciones(), _actor(), _adjunto() (+16 more)

### Community 15 - "demanda.py"
Cohesion: 0.09
Nodes (19): _agregar_forecast(), _agregar_reales(), _ajustar_con_modelo(), _candidatos_aplicables(), _elegir_donantes(), _elegir_modelo(), estimar_demanda_stock_sku(), forecast_ventas() (+11 more)

### Community 16 - "conectar"
Cohesion: 0.11
Nodes (13): cierre(), conectar(), html_verificado(), listar_observaciones(), _migrar(), verificacion(), verificar_cadena(), articulos() (+5 more)

### Community 17 - "semanal.py"
Cohesion: 0.09
Nodes (14): evolucion_costos(), mapa_estructura(), _modelo_holt_winters(), _modelo_sarima(), _ajustar_prophet(), calendario_eventos_ar(), forecast_ventas_semanal(), _serie_semanal() (+6 more)

### Community 18 - "cerrar_con_sucursal"
Cohesion: 0.12
Nodes (10): acciones_con_implementacion(), aceptar_descargo(), acta_verificada(), criterio_sugerido(), dias_verificacion(), obtener(), verificar_eficacia(), Base (+2 more)

### Community 19 - "pandas"
Cohesion: 0.16
Nodes (4): combinaciones(), eventos(), _m(), TestEventos

### Community 20 - "nc_mail.py"
Cohesion: 0.17
Nodes (11): contexto(), contexto_cierre(), enviar_smtp(), _fmt(), _login(), _nombre(), plazos_para(), previsualizar() (+3 more)

### Community 21 - "precio_unitario_fila"
Cohesion: 0.14
Nodes (8): precio_unitario_fila(), avance_conteo(), avance_matriz(), _validar_metodo(), _validar_nivel(), ajustes_por(), evolucion_mensual(), movimientos_outliers()

### Community 22 - "stock.py"
Cohesion: 0.16
Nodes (11): _detectar_columnas(), _get_connection(), ingestar_stock(), _insertar_stock(), _leer_excel(), listar_snapshots(), _obtener_mapa_abreviaciones(), obtener_snapshot() (+3 more)

### Community 23 - "render"
Cohesion: 0.11
Nodes (12): resumen_dias_anomalos(), listar_fechas_valorizacion(), get_connection(), categorias_merma(), controles_periodo(), controles_periodo_variable(), listar_periodos_ingesta(), merma_por_categoria() (+4 more)

### Community 24 - "cargar_config"
Cohesion: 0.21
Nodes (8): componer(), enviar_nc(), fechas_clave(), guardar_config_mail(), cargar_config(), guardar_config(), TestMailAutocompletado, _tab_config()

### Community 25 - ".emitida"
Cohesion: 0.10
Nodes (11): ancla(), html_para_reimprimir(), verificar_actas(), verificar_adjuntos(), verificar_html_emitidos(), 6.4 Registro append-only y cadena de hashes, TestAdjuntosBackupObs, TestInmutabilidad (+3 more)

### Community 26 - "nc_contactos.py"
Cohesion: 0.17
Nodes (12): agregar_nuevos(), _conn(), grupos(), guardar_todos(), _limpio(), listar(), _migrar(), _normalizar() (+4 more)

### Community 27 - "comprobantes.py"
Cohesion: 0.10
Nodes (15): detectar_dias_anomalos(), afinidad_skus(), _agregar_metrica(), _comprobantes(), evolucion_comprobantes(), evolucion_comprobantes_multi(), evolucion_comprobantes_sucursales(), perfil_sku_en_comprobante() (+7 more)

### Community 28 - "_config_mail"
Cohesion: 0.25
Nodes (7): guardar_password(), mail_valido(), password(), separar(), _validar_envio(), _config_mail(), _form_mail()

### Community 29 - "crear_borrador"
Cohesion: 0.25
Nodes (7): agregar_anexo(), crear_borrador(), verificar_anexos(), _anexo(), _datos(), TestAnexos, TestNumeracion

### Community 30 - "reporte.py"
Cohesion: 0.11
Nodes (13): numero_impreso(), cargar_branding(), _cargar_logo_iqmerma(), _dias(), _dir_proyecto(), _fecha_o_guion(), generar_acta_cierre(), generar_no_conformidad() (+5 more)

### Community 31 - "graficos.py"
Cohesion: 0.12
Nodes (10): _abreviar(), colores(), _escala(), _leyenda(), _marco(), svg_barras_divergentes(), svg_barras_lineas(), svg_lineas() (+2 more)

### Community 32 - "interanual.py"
Cohesion: 0.17
Nodes (7): cobertura_meses(), comparar_anios(), _consultar(), _expr(), serie_mensual_anios(), serie_mensual_sucursales(), ventas_vs_remitos()

### Community 34 - "clasificar_demanda_sku"
Cohesion: 0.09
Nodes (15): catalogo_articulos(), _baseline_tendencia_historico(), _curva_mensual(), _evaluar_intermitente(), evaluar_modelos(), _wmape(), elegir_modelo_intermitente(), forecast_intermitente() (+7 more)

### Community 35 - "test_no_conformidades.py"
Cohesion: 0.24
Nodes (3): disponible(), html_a_pdf(), navegador()

### Community 36 - ".test_login_solo_plain_y_mensajes"
Cohesion: 0.14
Nodes (4): _despachar(), explicar_error(), parece_password_de_aplicacion(), TestMailErrores

### Community 37 - "pedido_general.py"
Cohesion: 0.18
Nodes (7): pedido_general_por_dias(), _pedido_visible(), perfil_pedido_general(), _stock_disponible_multi(), tabla_pedido_general(), _ventas_por_codigo_depo(), serie_stock()

### Community 38 - "politicas.py"
Cohesion: 0.20
Nodes (4): sugerir_transferencias(), calcular_politicas(), comparar_clase_abc(), resumen_politicas()

### Community 39 - "auth.py"
Cohesion: 0.21
Nodes (12): cambiar_password(), _cargar(), _config_nueva(), crear_usuario(), eliminar_usuario(), _guardar(), listar_usuarios(), set_acceso_pagina() (+4 more)

### Community 40 - "generar_reporte_transformaciones"
Cohesion: 0.20
Nodes (8): svg_ranking_horizontal(), _env(), generar_reporte_comprobantes(), generar_reporte_transformaciones(), _top_sku(), _pesos(), _sin_cero_negativo(), _unidades()

### Community 41 - "setup_db.py"
Cohesion: 0.24
Nodes (5): 5.3 Catálogo `tipos_categoria` (editable sin código), _migrar(), _sembrar_tipos_categoria(), setup(), _tablas()

### Community 43 - "Guía de uso — MermaIQ"
Cohesion: 0.17
Nodes (12): 1. Requisitos e instalación, 2. Crear un proyecto, 3. Correr la app, 4. La interfaz de un vistazo, 7. Cómo se calcula la merma, 8. Mantenimiento, 9. Problemas comunes, Editar el catálogo de tipos (+4 more)

### Community 44 - "enviar_cierre"
Cohesion: 0.15
Nodes (6): componer_cierre(), config_mail(), enviar_cierre(), rellenar(), _Seguro, TestMailCierre

### Community 45 - "IQMerma — Estrategia de compliance y expansión"
Cohesion: 0.18
Nodes (11): 1. Visión, 3. Brecha ISO 9001:2015 por cláusula, 6.1 Hipótesis de valor, 6.2 Criterio de validación, 6.3 Guía de preguntas para las entrevistas, 6. Minería (a validar), 7. Roadmap, 8. Riesgos y preguntas abiertas (+3 more)

### Community 46 - "render"
Cohesion: 0.27
Nodes (6): _agrupar_periodo(), _demanda_por_metodo(), _descripcion_modelo(), _etiqueta_periodo(), _mes_largo(), render()

### Community 47 - "series.py"
Cohesion: 0.13
Nodes (9): _expr_dimension(), marcadores_sucursal(), quiebres_por_dia(), resumen_movimientos(), resumen_quiebres(), series_diarias(), stock_por_categoria(), stock_por_sucursal() (+1 more)

### Community 48 - "referencias.py"
Cohesion: 0.19
Nodes (8): cargar_articulos(), cargar_depositos(), cargar_estructura(), _get_connection(), _leer_excel(), marcar_logistica(), _normalizar_activo(), _print_resumen()

### Community 49 - "nc_anexos.py"
Cohesion: 0.21
Nodes (9): marcadores_movimiento(), serie_diaria(), _anexo_merma(), _anexo_serie(), _categorias_merma(), _corte(), descripciones(), _env() (+1 more)

### Community 50 - "comercial_comprobantes.py"
Cohesion: 0.24
Nodes (6): anios_disponibles(), _agregar_ponderado(), _agregar_por_sucursal(), _agregar_por_tipo(), _color(), render()

### Community 51 - "json"
Cohesion: 0.21
Nodes (7): adjuntos(), _anexos_de(), envios(), eventos(), respuestas(), TestMail, falla()

### Community 52 - "core/no_conformidades.py"
Cohesion: 0.09
Nodes (24): actor_admin(), backup(), calcular_plazos(), _canon_evento(), contenido_canonico(), _datos_acta(), _depo3(), dir_adjuntos() (+16 more)

### Community 53 - "4. Lean Six Sigma"
Cohesion: 0.29
Nodes (7): 4.1 IRA (Inventory Record Accuracy), 4.2 Control estadístico de procesos (SPC), 4.3 DPMO y nivel sigma del conteo, 4.4 Pareto de causas raíz (6M), 4.5 DMAIC desde la reincidencia, 4.6 Programa de conteo cíclico ABC, 4. Lean Six Sigma

### Community 54 - "5. Qué está atado al cliente piloto y cómo generalizarlo"
Cohesion: 0.29
Nodes (7): 5.1 Ingesta → adaptadores y esquema canónico, 5.2 Denominador de merma configurable, 5.3 Valorización, 5.4 Issues de valores fijos en el código, 5.5 Lo que ya es configurable (se reutiliza), 5.6 Despliegue, 5. Qué está atado al cliente piloto y cómo generalizarlo

### Community 55 - "5. Cargar datos"
Cohesion: 0.29
Nodes (7): 5. Cargar datos, Formato de cada archivo, Movimientos con varias pestañas (un Excel por año), Opción A — desde la interfaz, Opción B — script Python (más rápido para carga masiva), Orden de carga (importante), Recargar / actualizar un mes (carga incremental)

### Community 56 - "parsear_docx"
Cohesion: 0.24
Nodes (6): comparar_con_indice(), _es_toc(), _estilos(), _parrafos(), parsear_docx(), _starts()

### Community 57 - "conciliacion.py"
Cohesion: 0.28
Nodes (3): _depo(), movimientos_codigos(), stock_por_deposito()

### Community 58 - "Migración v1 → v2 (análisis por selección)"
Cohesion: 0.29
Nodes (7): Decisiones de negocio tomadas, Hecho, Migración v1 → v2 (análisis por selección), Notas operativas, Notas técnicas, Plan de remediación, Preguntas abiertas (bloquean parte del trabajo)

### Community 59 - "MermaIQ"
Cohesion: 0.33
Nodes (6): Estructura, Fórmula central, Guía de uso, MermaIQ, Setup inicial, Stack

### Community 62 - "_parsear_fechas"
Cohesion: 0.29
Nodes (3): _normalizar(), _parsear_fechas(), 5.4 Particularidades de los datos del ERP (no romper)

### Community 64 - "2. Decisiones de arquitectura (ADR)"
Cohesion: 0.50
Nodes (4): 2. Decisiones de arquitectura (ADR), ADR-1 · IQMerma es de solo lectura sobre el inventario, ADR-2 · El conteo móvil es una app separada, offline-first, con contrato de datos, ADR-3 · No construimos un WMS

### Community 65 - "render"
Cohesion: 0.24
Nodes (4): etiqueta_periodo(), _marcas(), _rachas_quiebre(), render()

## Knowledge Gaps
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `IQMerma — Documento de referencia` connect `IQMerma — Documento de referencia` to `IQMERMA.md`, `demanda.py`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `Actor` (e.g. with `activar_version()` and `cargar_version()`) actually correct?**
  _`Actor` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Should `_render` be split into smaller, more focused modules?**
  _Cohesion score 0.08907563025210084 - nodes in this community are weakly interconnected._
- **Why does `get_db_path()` connect `stock.py` to `core/comun.py`, `ui/comun.py`, `movimientos.py`, `setup_db.py`, `referencias.py`, `core/no_conformidades.py`, `render`, `comprobantes.py`, `reporte.py`, `test_stock_parcial.py`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Should `render` be split into smaller, more focused modules?**
  _Cohesion score 0.08158508158508158 - nodes in this community are weakly interconnected._
- **Why does `avance_conteo()` connect `precio_unitario_fila` to `IQMerma — Documento de referencia`, `pandas`, `core/comun.py`, `ui/comun.py`, `semanal.py`, `5. Qué está atado al cliente piloto y cómo generalizarlo`, `comprobantes.py`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Should `IQMerma — Documento de referencia` be split into smaller, more focused modules?**
  _Cohesion score 0.05 - nodes in this community are weakly interconnected._