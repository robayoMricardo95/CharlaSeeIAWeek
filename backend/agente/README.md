# El agente

El grafo de LangGraph que va de la pregunta en lenguaje natural a la respuesta.
Sigue el protocolo que está en las instrucciones de `../main.py` y se apoya en el
diccionario de la tabla (`../datos_basePublica/diccionario_sabana.yaml`).

```
comprender ─┬─(dudas sobre los datos)─> sondear ─> comprender
            │
            └─> revisar ─┬─(error y queda intento)─> comprender
                         └─> construir ─> extraer ─> componer ─┬─(falla)─> reparar ─> componer
                                                               v
                                        revisar_resultado ─> leer ─> explicar ─> redactar
```

| Nodo | Modelo | Qué hace |
|---|---|---|
| `comprender` | sí | Recorre el protocolo y **decide**: período, métrica, premisa, qué extracciones hacen falta y cómo se combinan. **No escribe SQL.** |
| `sondear` | no | Corre consultas chicas de reconocimiento (qué valores tiene una columna) y vuelve a `comprender` con el dato. |
| `revisar` | sí | Primero los chequeos mecánicos de `reglas.py`; si pasan, un revisor busca lo que falta. Con un error, se vuelve a `comprender` una vez. |
| `construir` | **no** | `reglas.construir_sql` arma el SQL de SQLite desde las decisiones. |
| `extraer` | no | Corre las consultas contra `reclamos.db`, en solo lectura, vía `consulta.py` → `datos_basePublica/ejecucion.py`. |
| `componer` | no | Ejecuta el Python del modelo en el contenedor aislado (`aislamiento.py`). Si hay una sola consulta y no hace falta cruce, se saltea. |
| `reparar` | sí | Le muestra el error y las columnas reales de cada extracción, y le pide el código corregido. Una sola vez. |
| `revisar_resultado` | sí | ¿Lo que volvió responde lo que se preguntó? |
| `leer` | sí | El modelo elige qué comparar en la tabla; el sandbox hace la cuenta. |
| `explicar` | sí | La cuenta en lenguaje claro; cada cifra citada se verifica contra lo que devolvió la base. |
| `redactar` | sí | La respuesta en español. Si la pregunta traía una premisa falsa, empieza diciéndolo. |

## La frontera: SQL extrae, Python compone

- **SQL** filtra, agrupa y agrega sobre la tabla `sabana`. Una consulta puede traer
  varias medidas. Lo arma código, así que no puede salir una columna inexistente
  ni un valor sin escapar.
- **Python** hace todo lo demás: cruzar dos extracciones, dividir para una razón,
  comparar 2008 contra 2009, recalcular una métrica no aditiva. Corre en un
  contenedor sin red, sin disco y sin credenciales.

Una lista de plantillas siempre tiene un caso N+1; descomponer en extracciones
simples más una cuenta, no.

## Sin reglas de negocio fijas

Lo que significa cada columna, qué métrica usar por defecto y qué trampas tiene el
dato están en el diccionario, y el modelo lo aplica con criterio. En código solo
vive lo que se comprueba **contando o comparando números**, sin criterio
(`reglas.py`):

| Chequeo | Qué evita |
|---|---|
| `verificar_plan_no_vacio` | Un plan sin consultas y sin pregunta de aclaración: el usuario recibiría "no pude completar el análisis" sin saber por qué |
| `verificar_periodo` | Pedir un año o un mes que no está en la tabla (solo hay 2008 y 2009): la consulta devolvería vacío y el modelo inventaría el motivo |
| `verificar_participacion` | Un porcentaje sin denominador: todas las consultas filtran la parte |
| `verificar_medidas_repetidas` | Dos medidas idénticas con alias de año (`pagado_2008`, `pagado_2009`): una medida no lleva filtro propio y las dos dan lo mismo |
| `verificar_premisa` | Una pregunta que supone un alza o una caída ("¿dónde se concentró el alza?") sin la consulta del total que la confirme |
| `preparar_sondeo` | Sondeos grandes, o `MAX(anio)` junto a `MAX(mes)`, que da una combinación que puede no existir |

**La premisa**, en detalle: si la pregunta da por hecho que algo subió o bajó, el
código compara el total por año (`contrastar_premisa`) y el redactor está obligado
a empezar diciendo si la premisa se cumple. Si el modelo no trajo la consulta del
total ni después de que el revisor la pidiera, el código la arma y la ejecuta él
mismo. Medido contra la API: sin esto, a "¿en qué se concentró el alza
hospitalaria de 2009?" el agente explicaba un alza en un total que había bajado
4,9 %.

## Archivos

| Archivo | Contenido |
|---|---|
| `decisiones.py` | `Decisiones`, `ConsultaBase`, `Medida`, `Filtro`, `Novedad`, `Derivacion`, `Revision` |
| `reglas.py` | Los chequeos mecánicos, el contraste de la premisa y `construir_sql` |
| `esquema_sabana.py` | Copia congelada de las 25 columnas y sus tipos; permite probar sin base |
| `estado.py` | El `TypedDict` que viaja por el grafo |
| `nodos.py` | Los nodos |
| `grafo.py` | El `StateGraph` y el checkpointer |

## Pruebas

Ninguna gasta API. Las del sandbox necesitan Docker abierto:

```
backend\.venv\Scripts\python.exe backend\pruebas_reglas.py       # chequeos y armado del SQL, más las cifras de control contra la base
backend\.venv\Scripts\python.exe backend\pruebas_grafo.py        # cableado, con modelo falso
backend\.venv\Scripts\python.exe backend\pruebas_aislamiento.py  # el contenedor
```

Cada prueba nace de un modo de fallar observado, no de un caso imaginado.
