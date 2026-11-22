# Graph Report - IQMerma  (2026-11-22)

## Corpus Check
- 83 files · ~301,403 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 4 file(s) not represented in the graph (top: (none) 2, .bat 1, .cmd 1)

## Summary
- 1388 nodes · 3967 edges · 62 communities (50 shown, 12 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 187 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `3e6b3c9e`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- reporte.py
- render
- IQMerma — Documento de referencia
- pandas
- 6. Analizar la merma
- streamlit
- snapshot_usado
- ui/comun.py
- movimientos.py
- generar_reporte_diferencias
- Actor
- a_excel_pedido_general
- series.py
- paginas/no_conformidades.py
- CLAUDE.md
- demanda.py
- conectar
- semanal.py
- obtener
- pandas
- nc_mail.py
- precio_unitario_fila
- stock.py
- paginas/calidad_datos.py
- numpy
- render
- _limpiar_serie
- comprobantes.py
- forecast_pooled
- crear_borrador
- numero_impreso
- graficos.py
- conectar
- get_connection
- clasificar_demanda_sku
- inventario_interanual.py
- pedido_general.py
- politicas.py
- auth.py
- setup_db.py
- Guía de uso — MermaIQ
- IQMerma — Estrategia de compliance y expansión
- render
- comercial_comprobantes.py
- core/no_conformidades.py
- 4. Lean Six Sigma
- 5. Qué está atado al cliente piloto y cómo generalizarlo
- 5. Cargar datos
- parsear_docx
- MermaIQ
- core/comun.py
- 5. Decisiones de cálculo
- 2. Decisiones de arquitectura (ADR)
- _rachas_quiebre

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
- `5.6 Inventario, políticas y salud` --references--> `avance_conteo()`  [INFERRED]
  IQMERMA.md → core/merma/avance.py
- `6.3 Congelamiento e integridad del documento` --references--> `_html_verificado()`  [INFERRED]
  IQMERMA.md → core/no_conformidades.py
- `6.5 Anexos con datos de IQMerma` --references--> `serie_stock()`  [INFERRED]
  IQMERMA.md → core/inventario/series.py
- `Movimientos con varias pestañas (un Excel por año)` --references--> `ingestar_libro_movimientos()`  [INFERRED]
  GUIA_USO.md → ingesta/movimientos.py
- `9. Estado y pendientes` --references--> `estimar_demanda_stock_sku()`  [INFERRED]
  IQMERMA.md → core/forecasting/demanda.py

## Import Cycles
- None detected.

## Communities (62 total, 12 thin omitted)

### Community 0 - "reporte.py"
Cohesion: 0.06
Nodes (33): svg_ranking_horizontal(), cargar_branding(), _cargar_logo_iqmerma(), _dias(), _dir_proyecto(), _env(), _fecha_o_guion(), generar_acta_cierre() (+25 more)

### Community 1 - "render"
Cohesion: 0.11
Nodes (37): listar_fechas_valorizacion(), boton_reporte(), botones_descarga(), catalogo_cacheado(), color_grilla(), controles_periodo(), etiqueta_seleccion_sucursales(), etiqueta_stock() (+29 more)

### Community 2 - "IQMerma — Documento de referencia"
Cohesion: 0.06
Nodes (33): 10. Glosario, 11. Mapa de documentos, 12. Changelog del documento, 1. Qué es IQMerma, 2. Arquitectura, 3. Funcionalidades actuales, 4. Qué NO hace (límites explícitos), 6.1 Ciclo de vida (+25 more)

### Community 3 - "pandas"
Cohesion: 0.11
Nodes (17): validar_modo_valorizacion(), calcular_merma(), calcular_merma_dinamica(), _componentes_sucursal(), _ensamblar(), _ensamblar_sucursal(), _extremos(), fechas_corte_comprobante() (+9 more)

### Community 4 - "6. Analizar la merma"
Cohesion: 0.14
Nodes (14): 6. Analizar la merma, Análisis → pestaña Comparativa por sucursal, Análisis → pestaña Pareto por rubro, Ingesta → ⚙ Depósitos, Página Análisis, Página Control de merma, Página Forecast, Página Min / Opt / Max (+6 more)

### Community 5 - "streamlit"
Cohesion: 0.08
Nodes (3): controles_periodo_variable(), render(), _estado()

### Community 6 - "snapshot_usado"
Cohesion: 0.14
Nodes (7): snapshot_usado(), agregar_contraste_transferido(), diferencias_camion(), resumen_transferido(), total_transferido(), detalle_transformaciones(), pares_transformacion()

### Community 7 - "ui/comun.py"
Cohesion: 0.09
Nodes (19): etiqueta_periodo(), a_excel(), a_excel_forecast_semanal_sku(), a_excel_matriz_merma(), avance_conteo_cacheada(), avance_matriz_cacheada(), clasificar_demanda_sku_cacheada(), comparativa_cacheada() (+11 more)

### Community 8 - "movimientos.py"
Cohesion: 0.13
Nodes (11): _detectar_periodo(), _get_connection(), ingestar_libro_movimientos(), ingestar_movimientos(), _insertar_movimientos(), _leer_excel(), _normalizar(), _periodo_de_hoja() (+3 more)

### Community 9 - "generar_reporte_diferencias"
Cohesion: 0.33
Nodes (3): evolucion_diferencias(), resumen_diferencias(), generar_reporte_diferencias()

### Community 10 - "Actor"
Cohesion: 0.09
Nodes (42): agregar_nuevos(), _agenda(), _despachar(), enviar_cierre(), enviar_nc(), _validar_envio(), Actor, actualizar_borrador() (+34 more)

### Community 11 - "a_excel_pedido_general"
Cohesion: 0.43
Nodes (5): a_excel_pedido_general(), borde_grupo(), col_idx(), combinar(), rango()

### Community 12 - "series.py"
Cohesion: 0.05
Nodes (31): _cadencia_remitos(), _dias_con_stock(), _dias_desde_quiebre_real(), pedido_por_dias(), perfil_reposicion(), resumen_reposicion(), _stock_disponible(), _tiempo_hasta_quiebre() (+23 more)

### Community 13 - "paginas/no_conformidades.py"
Cohesion: 0.07
Nodes (35): rango_default(), rubros(), _conn(), grupos(), guardar_todos(), _limpio(), listar(), _migrar() (+27 more)

### Community 15 - "demanda.py"
Cohesion: 0.11
Nodes (13): _agregar_forecast(), _agregar_reales(), _ajustar_con_modelo(), _candidatos_aplicables(), _elegir_modelo(), forecast_ventas(), _indices_estacionales(), _modelo_estacional_robusto() (+5 more)

### Community 16 - "conectar"
Cohesion: 0.12
Nodes (8): ancla(), conectar(), html_para_reimprimir(), listar_observaciones(), _migrar(), verificar_cadena(), TestInmutabilidad, TestMigracion

### Community 17 - "semanal.py"
Cohesion: 0.10
Nodes (12): mapa_estructura(), _ajustar_prophet(), calendario_eventos_ar(), forecast_ventas_semanal(), _serie_semanal(), forecast_semanal_sku(), _semana_equivalente_anio_anterior(), _serie_semanal_sku() (+4 more)

### Community 18 - "obtener"
Cohesion: 0.08
Nodes (14): acciones_con_implementacion(), aceptar_descargo(), envios(), eventos(), obtener(), Base, TestCiclo, TestCierreConSucursal (+6 more)

### Community 19 - "pandas"
Cohesion: 0.10
Nodes (9): combinaciones(), _depo(), eventos(), movimientos_codigos(), stock_por_deposito(), _m(), TestEventos, _codigos() (+1 more)

### Community 20 - "nc_mail.py"
Cohesion: 0.06
Nodes (26): componer(), componer_cierre(), config_mail(), construir_mensaje(), contexto(), contexto_cierre(), enviar_smtp(), explicar_error() (+18 more)

### Community 21 - "precio_unitario_fila"
Cohesion: 0.17
Nodes (6): precio_unitario_fila(), precios_snapshot(), avance_conteo(), ajustes_por(), evolucion_mensual(), movimientos_outliers()

### Community 22 - "stock.py"
Cohesion: 0.17
Nodes (10): _despivotear(), _detectar_columnas(), _get_connection(), ingestar_stock(), _insertar_stock(), _leer_excel(), _obtener_mapa_abreviaciones(), _parsear_numerico() (+2 more)

### Community 24 - "numpy"
Cohesion: 0.23
Nodes (6): _baseline_tendencia_historico(), _curva_mensual(), _evaluar_intermitente(), evaluar_modelos(), _wmape(), elegir_modelo_intermitente()

### Community 25 - "render"
Cohesion: 0.14
Nodes (7): abreviaciones_sucursal(), grupo_metrica_matriz(), grupos_matriz_merma(), matriz_merma_ancha(), texto_ventanas(), ventanas_tupla(), render()

### Community 26 - "_limpiar_serie"
Cohesion: 0.20
Nodes (6): _elegir_donantes(), _limpiar_serie(), _mape(), _modelo_knn_prestado(), _theil_sen(), _vector_estacional_objetivo()

### Community 27 - "comprobantes.py"
Cohesion: 0.17
Nodes (10): afinidad_skus(), _agregar_metrica(), _comprobantes(), evolucion_comprobantes(), evolucion_comprobantes_multi(), evolucion_comprobantes_sucursales(), perfil_sku_en_comprobante(), _recortar_periodo() (+2 more)

### Community 28 - "forecast_pooled"
Cohesion: 0.31
Nodes (3): _agregar_features(), forecast_pooled(), _panel_mensual()

### Community 29 - "crear_borrador"
Cohesion: 0.13
Nodes (10): agregar_anexo(), crear_borrador(), disponible(), html_a_pdf(), navegador(), _anexo(), _datos(), TestAdjuntosBackupObs (+2 more)

### Community 30 - "numero_impreso"
Cohesion: 0.25
Nodes (4): calcular_plazos(), listar(), numero_impreso(), vencimientos()

### Community 31 - "graficos.py"
Cohesion: 0.09
Nodes (12): _abreviar(), colores(), _escala(), _leyenda(), _marco(), svg_barras_divergentes(), svg_barras_lineas(), svg_lineas() (+4 more)

### Community 32 - "conectar"
Cohesion: 0.08
Nodes (16): detectar_dias_anomalos(), catalogo_articulos(), conectar(), estimar_demanda_stock_sku(), anios_disponibles(), cobertura_meses(), comparar_anios(), _consultar() (+8 more)

### Community 33 - "get_connection"
Cohesion: 0.29
Nodes (4): listar_snapshots(), obtener_snapshot(), get_connection(), categorias_merma()

### Community 34 - "clasificar_demanda_sku"
Cohesion: 0.17
Nodes (5): forecast_intermitente(), _nivel_croston(), _nivel_tsb(), clasificar_demanda_sku(), ventas_mensuales_por_sku()

### Community 37 - "pedido_general.py"
Cohesion: 0.20
Nodes (6): pedido_general_por_dias(), _pedido_visible(), perfil_pedido_general(), _stock_disponible_multi(), tabla_pedido_general(), _ventas_por_codigo_depo()

### Community 38 - "politicas.py"
Cohesion: 0.12
Nodes (7): adjuntar_rubros(), sugerir_transferencias(), calcular_politicas(), comparar_clase_abc(), resumen_politicas(), analizar_stock(), resumen_salud()

### Community 39 - "auth.py"
Cohesion: 0.08
Nodes (27): cambiar_password(), _cargar(), _config_nueva(), crear_usuario(), eliminar_usuario(), _guardar(), listar_usuarios(), set_acceso_pagina() (+19 more)

### Community 41 - "setup_db.py"
Cohesion: 0.16
Nodes (7): 5.3 Catálogo `tipos_categoria` (editable sin código), get_db_path(), _migrar(), _sembrar_tipos_categoria(), setup(), _tablas(), TestStockParcial

### Community 43 - "Guía de uso — MermaIQ"
Cohesion: 0.17
Nodes (12): 1. Requisitos e instalación, 2. Crear un proyecto, 3. Correr la app, 4. La interfaz de un vistazo, 7. Cómo se calcula la merma, 8. Mantenimiento, 9. Problemas comunes, Editar el catálogo de tipos (+4 more)

### Community 45 - "IQMerma — Estrategia de compliance y expansión"
Cohesion: 0.18
Nodes (11): 1. Visión, 3. Brecha ISO 9001:2015 por cláusula, 6.1 Hipótesis de valor, 6.2 Criterio de validación, 6.3 Guía de preguntas para las entrevistas, 6. Minería (a validar), 7. Roadmap, 8. Riesgos y preguntas abiertas (+3 more)

### Community 46 - "render"
Cohesion: 0.25
Nodes (5): _agrupar_periodo(), _demanda_por_metodo(), _descripcion_modelo(), _etiqueta_periodo(), _mes_largo()

### Community 50 - "comercial_comprobantes.py"
Cohesion: 0.39
Nodes (4): _agregar_ponderado(), _agregar_por_sucursal(), _agregar_por_tipo(), _color()

### Community 52 - "core/no_conformidades.py"
Cohesion: 0.06
Nodes (39): guardar_config_mail(), _acciones_impl(), acta_verificada(), actor_admin(), adjuntos(), anexos(), _anexos_de(), backup() (+31 more)

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
Cohesion: 0.15
Nodes (11): articulos(), buscar(), comparar_con_indice(), _es_toc(), _estilos(), _parrafos(), parsear_docx(), _starts() (+3 more)

### Community 59 - "MermaIQ"
Cohesion: 0.33
Nodes (6): Estructura, Fórmula central, Guía de uso, MermaIQ, Setup inicial, Stack

### Community 61 - "core/comun.py"
Cohesion: 0.09
Nodes (9): analizar_margenes(), evolucion_costos(), categorias_merma(), filtrar_catalogo(), listar_categorias(), movimientos_detalle(), snapshots_parciales(), tipos_movimiento() (+1 more)

### Community 62 - "5. Decisiones de cálculo"
Cohesion: 0.17
Nodes (9): _parsear_fechas(), 5.2 Valorización: siempre desde el snapshot de stock, 5.4 Particularidades de los datos del ERP (no romper), 5.5 Reconstrucción de stock diaria, 5.6 Inventario, políticas y salud, 5.7 Forecasting, 5.8 Calidad de datos, 5.9 Plazos de una NC (+1 more)

### Community 64 - "2. Decisiones de arquitectura (ADR)"
Cohesion: 0.50
Nodes (4): 2. Decisiones de arquitectura (ADR), ADR-1 · IQMerma es de solo lectura sobre el inventario, ADR-2 · El conteo móvil es una app separada, offline-first, con contrato de datos, ADR-3 · No construimos un WMS

## Knowledge Gaps
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ingestar_libro_movimientos()` connect `movimientos.py` to `semanal.py`, `auth.py`, `5. Cargar datos`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `Actor` (e.g. with `activar_version()` and `cargar_version()`) actually correct?**
  _`Actor` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Should `reporte.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06057945566286216 - nodes in this community are weakly interconnected._
- **Why does `5. Cargar datos` connect `5. Cargar datos` to `Guía de uso — MermaIQ`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Should `render` be split into smaller, more focused modules?**
  _Cohesion score 0.10628674807779286 - nodes in this community are weakly interconnected._
- **Why does `Movimientos con varias pestañas (un Excel por año)` connect `5. Cargar datos` to `movimientos.py`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Should `IQMerma — Documento de referencia` be split into smaller, more focused modules?**
  _Cohesion score 0.06060606060606061 - nodes in this community are weakly interconnected._