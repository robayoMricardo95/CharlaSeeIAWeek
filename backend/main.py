import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

import carga_conocimiento
import consulta
import registro
import trazas
from agente.esquema_sabana import verificar_contra_la_base
from agente.grafo import construir_grafo
from agente.nodos import Nodos, filtros_en_lenguaje_de_negocio
from uso_tokens import Contador, sumar_usos

# Ruta explícita: load_dotenv() sin argumentos busca ".env" en el directorio
# desde donde se arrancó el proceso, no al lado de este archivo.
load_dotenv(Path(__file__).parent / ".env")

# El nombre del modelo en una sola constante: lo usa el modelo y también la
# tabla de precios de uso_tokens.py. Si cambia acá, el costo se recalcula solo.
MODELO = "gpt-4.1"

# Azure u OpenAI directo, según haya credenciales de Azure en el .env.
#
# Se conectan con la misma clase porque Azure expone `/openai/v1`, una superficie
# compatible con la API de OpenAI: alcanza con cambiar `base_url` y `api_key`. La
# alternativa (`AzureChatOpenAI`) obliga a manejar `api_version` y nombres de
# deployment por separado, y no aporta nada acá.
#
# El nombre del deployment en Azure es "gpt-4.1", igual que el modelo, así que la
# tabla de precios de `uso_tokens.py` sigue encontrándolo y el medidor de costo no
# se entera del cambio. Si algún día el deployment se llama distinto, hay que
# separar "nombre del deployment" de "nombre del modelo para el precio".
AZURE_BASE = (os.getenv("AZURE_OPENAI_BASE_URL") or "").strip().rstrip("/")
# El portal de Azure muestra la URL completa de una ruta (`.../openai/v1/responses`
# o `.../openai/v1/chat/completions`), y es la que uno suele copiar. ChatOpenAI
# necesita la base, porque la ruta la agrega él: con la URL completa responde 404.
for ruta in ("/responses", "/chat/completions"):
    if AZURE_BASE.endswith(ruta):
        AZURE_BASE = AZURE_BASE[: -len(ruta)]
AZURE_KEY = (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()

# `max_retries` alto a propósito. El prefijo cacheado son miles de tokens y la
# cuenta tiene un tope de tokens por minuto, así que una pregunta que sondea o que
# se rehace hace tres o cuatro llamadas y puede chocar contra el 429. Reintentar
# con espera es más barato que perder el turno.
if AZURE_BASE and AZURE_KEY:
    modelo = ChatOpenAI(model=MODELO, base_url=AZURE_BASE, api_key=AZURE_KEY, max_retries=6)
    PROVEEDOR = f"Azure · {AZURE_BASE}"
else:
    modelo = ChatOpenAI(model=MODELO, max_retries=6)
    PROVEEDOR = "OpenAI directo"

print(f"Modelo: {MODELO} ({PROVEEDOR})")
contador = Contador()

# Seguimiento: LangSmith, archivo local o nada, según TRAZAS en el .env.
# Se resuelve una sola vez, acá; ningún nodo sabe cuál está activo.
CALLBACKS, MODO_TRAZAS = trazas.configurar()
print(f"Trazas: {MODO_TRAZAS}")

# Historial: cada pregunta y lo que el agente hizo con ella queda en SQLite
# (`datos_app/historial.db`, ver `registro.py`). Si falla la creación de tablas,
# el backend arranca igual y se avisa: sin historial se puede trabajar, sin
# agente no.
try:
    print(f"Historial en: {registro.crear_tablas()}")
except Exception as error:
    print(f"AVISO: no se pudo preparar el historial ({error}). Se sigue sin guardar.")

# El prefijo cacheable: rol + diccionario de la sábana. Se arma una sola vez al
# arrancar y es byte por byte idéntico en cada pregunta, que es la condición para
# que el proveedor lo cobre a precio de caché. Todo lo que cambia (la fecha, el
# historial, la pregunta) va después, en otros mensajes.
INSTRUCCIONES = """Eres un analista de datos de seguros de salud. Trabajas sobre la
tabla `sabana`: una fila por reclamo médico. Es una base PÚBLICA y SINTÉTICA:
reclamos de Medicare (Estados Unidos) de la muestra 1 del DE-SynPUF del CMS,
años 2008 y 2009. Sirve para analizar y construir herramientas, no para sacar
conclusiones sobre personas ni sobre la población real.

IDIOMA: escribes en español de Ecuador. Trata al usuario de **usted**. Nada de
voseo ("vos", "tenés", "podés", "fijate") ni de modismos rioplatenses ("acá",
"plata", "recién"). Usa "aquí", "ahora", "usted puede", "tiene". Vocabulario
neutro y profesional.

Primero clasificas la pregunta en el campo `intencion`:

- **`datos`** — se pide información de la tabla `sabana`. Es el caso normal y
  para eso sigues todo el protocolo de abajo.
- **`conversacion`** — un saludo, un agradecimiento, una pregunta sobre qué sabes
  hacer, o algo que la tabla `sabana` no puede responder. Aquí NO declaras
  consultas ni sondeos: dejas `consultas` y `sondeos` vacíos y el sistema
  responde cordialmente explicando en qué puede ayudar.

Cuando la intención es `datos`, sigues este protocolo. El resultado de cada paso
no se escribe como texto: se completa en los campos de la respuesta
estructurada, que es el checklist auditable. Cuando algo de un paso valga la
pena auditar, lo reportas en `novedades` con ese paso.

1. **temporalidad** — Resuelve el período a valores concretos de `anio` y `mes`.
   La tabla solo tiene 2008 y 2009 (la lista exacta va al final). "El último
   mes" o "el último año" es el último de esa lista, nunca el del calendario. Si
   la pregunta no pide período, usa los dos años y dilo en `periodo`. Si pide un
   año que no existe, pon `necesita_aclaracion` y dile qué años hay.
2. **diccionario** — Traduce cada término de la pregunta a una columna y a un
   valor EXACTO del diccionario de abajo (los valores de texto están en español
   y con mayúscula inicial: 'Hospitalario', 'Femenino', 'California'). Si un
   término no tiene columna, no la inventes: dilo.
3. **trampas** — Revisa las trampas del diccionario que apliquen. Las que más
   cambian el número: qué "costo" se pide (por defecto `monto_pagado`, y se
   declara), no filtrar los montos en cero, no promediar hospitalario y
   ambulatorio juntos, no calcular gasto per cápita, y verificar la premisa de
   la pregunta. Si la pregunta da por hecho un alza o una caída ("¿en qué se
   concentró el alza?"), completa `premisa_direccion` y `premisa`, y agrega una
   consulta con el total agrupado SOLO por `anio`: el sistema compara los dos
   años y, si la premisa es falsa, la respuesta empieza diciéndolo. Esa consulta
   es SOLO para verificar: la `composicion` responde lo que se pidió (por ejemplo
   los grupos con su variación, con sus nombres) y NO mete los totales adentro.
4. **interpretacion** — Reformula la pregunta: qué métrica, sobre qué universo,
   con qué dimensiones y qué comparación.
5. **sondeo** — Si algo depende de un dato que solo la base tiene (qué valores
   existen en una columna, si existe un prestador o un código), pide un sondeo
   en vez de suponerlo, y pídelo antes de pedir una aclaración.
6. **participacion** — Si se pide un porcentaje, declara la parte y el todo.
7. **metricas** — Usa las métricas canónicas del diccionario.
8. **composicion** — Arma las extracciones y, si hace falta, el código que las
   combina.

**No escribes SQL.** El trabajo se reparte en dos:

1. **Extracción** — declaras una o varias `consultas` base: qué medidas, sobre
   qué columnas, agrupadas por qué, con qué filtros. El sistema arma el SQL (de
   SQLite) y lo muestra en el panel de razonamiento. Cada consulta es una
   extracción simple sobre la tabla: filtrar, agrupar, agregar. Puede pedir
   varias medidas en la misma consulta.

2. **Composición** — todo lo demás lo escribes como código Python en el campo
   `composicion`: cruzar dos extracciones, dividir para una razón, comparar dos
   períodos, recalcular una métrica no aditiva al grano total. Tienes `pd`, `np` y
   el diccionario `consultas` con los nombres que elegiste. Deja el resultado
   final en una variable `resultado`. Sin imports, sin archivos, sin red.
   `resultado` tiene que ser una tabla con las etiquetas como COLUMNAS: si usas
   `pivot`, `set_index` o `groupby`, termina con `.reset_index()`. El índice se
   descarta al devolver el resultado, y con él se pierden los nombres (de los
   grupos, de los estados) que la respuesta necesita.

Las consultas de ejemplo del diccionario están escritas en SQL: definen QUÉ tiene
que dar cada pregunta, no cómo pedirla. Tradúcelas a extracciones simples más
composición. Por ejemplo, una comparativa 2008 contra 2009 con `CASE WHEN` es
UNA extracción agrupada por `anio` (y por la dimensión que se compare), y la
variación se calcula en Python. El costo promedio por reclamo es `SUM` y `COUNT`
en la misma extracción, dividido en Python.

Una medida NO lleva filtro propio: los filtros son de la consulta entera. Dos
medidas `SUM(monto_pagado)` con alias `pagado_2008` y `pagado_2009` dan
exactamente el mismo número, porque ninguna de las dos filtra el año. Para
separar años o categorías, agrupa por esa columna o haz una consulta por lado.

Si una sola consulta ya responde la pregunta, deja `composicion` vacío.

Reglas:
- Apóyate SIEMPRE en el diccionario. Si un término está ahí mapeado a un valor
  exacto, usa ese valor.
- Nunca inventes cifras: el número lo pone la base de datos.
- No hay filtros automáticos: lo que se ejecuta es exactamente lo que declaras.
  Si una métrica necesita un filtro para tener sentido (por ejemplo la estancia
  promedio solo en `ambito = 'Hospitalario'`), lo pones tú.
- Los códigos de diagnóstico, procedimiento y DRG no tienen descripción en la
  base. Para hablar de enfermedades usa `grupo_enfermedad`.
- Si la pregunta pide un porcentaje o una participación, di explícitamente cuál
  es la parte y cuál es el todo. El universo que la pregunta nombra ("el costo
  hospitalario") es el DENOMINADOR y va filtrado en las dos consultas; la
  categoría de la que se pide el porcentaje ("aparato circulatorio") es la PARTE
  y solo va filtrada en el numerador. Invertirlos da un número creíble y
  equivocado.
- Tienes el historial de la conversación. Si la pregunta es un seguimiento
  ("¿y en 2008?", "ahora por ámbito"), parte de las decisiones anteriores y
  cambia solo lo que corresponda.
- Si la pregunta no se puede resolver sin adivinar algo que cambia el número,
  pon `necesita_aclaracion` en true y escribe qué hay que preguntar.
- En `novedades` reporta solo lo que vale la pena auditar. No hagas una entrada
  por paso: si un paso no tuvo nada digno de mención, no lo incluyas."""

# Qué contesta el agente cuando la pregunta no es de datos. Va aparte del prefijo
# porque es corto y se usa en una llamada distinta, sin el diccionario entero.
CAPACIDADES = """Puedo responder preguntas sobre reclamos médicos de una base pública y
sintética de Medicare (Estados Unidos), años 2008 y 2009. En concreto:

- Costo (monto pagado por la aseguradora, o costo completo de la atención) por
  año, mes, ámbito (hospitalario o ambulatorio), sexo, raza, banda de edad,
  estado, grupo de enfermedad, prestador o médico tratante.
- Número de reclamos y de afiliados distintos.
- Costo promedio por reclamo y estancia hospitalaria promedio.
- Participaciones y porcentajes sobre un total, y comparaciones entre 2008 y
  2009.

No puedo calcular gasto per cápita (la base solo tiene a los afiliados que
tuvieron algún reclamo), ni describir códigos de diagnóstico o procedimiento
individuales. Los datos son sintéticos: sirven para analizar, no para concluir
nada sobre la población real.

Cada respuesta viene con el detalle de cómo se calculó: las consultas que se
ejecutaron, los filtros que se aplicaron y los valores que entraron a la fórmula."""

CONOCIMIENTO = carga_conocimiento.cargar()
# El esquema y el rango de fechas se leen de la base al arrancar: es verdad
# medida, no lo que dice el diccionario.
ESQUEMA = consulta.esquema_real()
COBERTURA = consulta.cobertura_temporal()
PERMITIDAS = consulta.columnas_reales()

# Si la tabla cambió respecto de la copia congelada en `agente/esquema_sabana.py`,
# conviene enterarse al arrancar y no cuando una consulta falle.
for aviso in verificar_contra_la_base(PERMITIDAS):
    print(f"AVISO DE ESQUEMA: {aviso}")

PREFIJO = f"{INSTRUCCIONES}\n\n# Diccionario de la tabla\n\n{CONOCIMIENTO}"

# El esquema real y la cobertura temporal van al final del prompt y NO adentro
# del prefijo cacheado. Puestos antes de miles de tokens de diccionario, el modelo
# tiende a ignorarlos: usa columnas que no existen o resuelve "el último mes"
# contra el calendario. Al final sí los respeta, y son pocos tokens: sacarlos del
# prefijo casi no toca la caché y los pone donde sí se leen.
CIERRE = f"{COBERTURA}\n\n{ESQUEMA}"

nodos = Nodos(
    modelo, MODELO, PREFIJO, CIERRE, PERMITIDAS, CAPACIDADES,
    # El rango como números, para comprobar el período sin depender del prompt.
    rango=consulta.rango_temporal(),
)
app_grafo = construir_grafo(nodos)

class RespuestaJSON(JSONResponse):
    """JSON que declara su codificación.

    Starlette manda `application/json` a secas: solo le agrega `charset` a los
    tipos `text/*`. Los bytes que salen ya son UTF-8 correctos —la ú viaja como
    `\\xc3\\xba`— pero sin la declaración cada cliente
    decide por su cuenta cómo leerlos, y el que decide latín-1 muestra "últimoo"
    en vez de "último". Decirlo cuesta una línea.
    """

    media_type = "application/json; charset=utf-8"


app = FastAPI(default_response_class=RespuestaJSON)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Cuántas filas de cada consulta base viajan al panel de razonamiento. Es para
# poder auditar de dónde salió el número, no para analizar: con las primeras
# alcanza y no se infla la respuesta.
FILAS_POR_CONSULTA_EN_EL_PANEL = 50


def _recortar(resultado: dict | None) -> dict | None:
    """El resultado de una consulta base, con pocas filas, para el panel."""
    if not resultado:
        return None
    return {
        **resultado,
        "filas": resultado["filas"][:FILAS_POR_CONSULTA_EN_EL_PANEL],
        "truncado": resultado["total_filas"] > FILAS_POR_CONSULTA_EN_EL_PANEL,
    }


def _memoria_del_hilo(thread_id: str, configuracion: dict) -> list:
    """Los mensajes previos de la conversación, si el grafo no los tiene ya.

    Se consulta primero al checkpointer, que es la fuente rápida y completa. Solo
    si está vacío —backend recién arrancado, o conversación abierta desde el
    historial— se reconstruye desde el historial con las actas guardadas.

    Nunca rompe: quedarse sin memoria degrada la respuesta de un seguimiento,
    pero no impide contestar.
    """
    try:
        guardado = app_grafo.get_state(configuracion)
        if (guardado.values or {}).get("mensajes"):
            return []  # el checkpointer ya tiene el hilo; `add_messages` lo continúa
    except Exception as error:
        print(f"AVISO: no se pudo leer el estado del hilo ({error}).")

    try:
        previos = []
        for pregunta, acta in registro.actas_de(thread_id):
            previos.append(HumanMessage(content=pregunta))
            previos.append(AIMessage(content=acta))
        if previos:
            print(f"Memoria rehidratada desde el historial: {len(previos) // 2} turno(s).")
        return previos
    except Exception as error:
        print(f"AVISO: no se pudo rehidratar el historial ({error}).")
        return []


class PedidoChat(BaseModel):
    mensaje: str
    thread_id: str


@app.post("/api/chat")
def chat(pedido: PedidoChat):
    # El `thread_id` le dice al checkpointer qué historial cargar. Lo que es de
    # este turno se blanquea acá y no dentro de un nodo: el checkpointer guarda
    # todo el estado entre turnos, y si no se resetea, el turno nuevo arranca
    # creyendo que viene de un rechazo del turno anterior.
    arranque = time.perf_counter()
    configuracion = {
        "configurable": {"thread_id": pedido.thread_id},
        "callbacks": CALLBACKS,
        # Se etiqueta la corrida para poder filtrarla después en LangSmith.
        "metadata": {"thread_id": pedido.thread_id, "modelo": MODELO},
    }
    # El checkpointer vive en RAM y se pierde al reiniciar el backend. Si el hilo
    # no tiene memoria pero sí tiene historial guardado, se lo devolvemos antes de
    # arrancar: sin esto, después de un reinicio un "¿y en 2008?" no tiene con
    # qué comparar y el agente contesta cualquier cosa con total seguridad.
    mensajes = _memoria_del_hilo(pedido.thread_id, configuracion)
    mensajes.append(HumanMessage(content=pedido.mensaje))

    estado = app_grafo.invoke(
        {
            "mensajes": mensajes,
            "reintentos": 0,
            "reparaciones": 0,
            # Los sondeos son del turno: lo que se averiguó para la pregunta
            # anterior no vale como respuesta para esta.
            "sondeos": {},
            "rondas_sondeo": 0,
            "derivacion": {},
            "perfil": {},
            "lectura": {},
            "premisa": {},
            "revision": {},
            "uso_comprension": None,
            "uso_revision": None,
            "uso_reparacion": None,
            "uso_revision_resultado": None,
            "uso_lectura": None,
            "uso_explicacion": None,
            "uso_redaccion": None,
        },
        configuracion,
    )

    uso = sumar_usos(
        estado.get("uso_comprension"),
        estado.get("uso_revision"),
        estado.get("uso_reparacion"),
        estado.get("uso_revision_resultado"),
        estado.get("uso_lectura"),
        estado.get("uso_explicacion"),
        estado.get("uso_redaccion"),
    )
    acumulado = contador.registrar(pedido.thread_id, uso)

    decisiones = estado["decisiones"]
    inyectados = estado.get("inyectados") or {}
    extracciones = estado.get("extracciones") or {}
    sqls = estado.get("sqls") or {}

    analisis = {
        "interpretacion": decisiones.get("interpretacion", ""),
        "periodo": decisiones.get("periodo", ""),
        "rama": decisiones.get("rama", ""),
        "metrica": decisiones.get("metrica_pedida", ""),
        "metrica_es_aditiva": decisiones.get("metrica_es_aditiva", True),
        # Parte y todo: vacío en la enorme mayoría de las preguntas, y lo único
        # que importa en las que piden un porcentaje.
        "pide_participacion": decisiones.get("pide_participacion", False),
        "dimension_de_la_parte": decisiones.get("dimension_de_la_parte", ""),
        "universo_denominador": decisiones.get("universo_denominador", ""),
        # Lo que se le preguntó a la base ANTES de armar el plan, con su respuesta.
        "sondeos": [
            {
                "nombre": nombre,
                "proposito": hecho.get("proposito", ""),
                "sql": hecho.get("sql", ""),
                "inyectados": hecho.get("inyectados", []),
                "error": hecho.get("error"),
                "resultado": _recortar(hecho.get("resultado")),
            }
            for nombre, hecho in (estado.get("sondeos") or {}).items()
        ],
        # Una entrada por consulta base: qué mide, cómo se agrupa, qué filtros puso
        # el modelo, cuáles inyectó el código, el SQL final y lo que devolvió.
        "consultas": [
            {
                "nombre": c["nombre"],
                "proposito": c.get("proposito", ""),
                "medidas": c.get("medidas", []),
                "dimensiones": c.get("dimensiones", []),
                "filtros": c.get("filtros", []),
                "inyectados": inyectados.get(c["nombre"], []),
                "sql": sqls.get(c["nombre"], ""),
                "filas": (extracciones.get(c["nombre"]) or {}).get("total_filas", 0),
                "error": (extracciones.get(c["nombre"]) or {}).get("error"),
                # El resultado de ESTA consulta, para verlo junto a su SQL.
                "resultado": _recortar(extracciones.get(c["nombre"])),
            }
            for c in decisiones.get("consultas", [])
        ],
        # Cómo se calculó, para mostrar junto a la respuesta.
        "derivacion": estado.get("derivacion") or {},
        # La lectura calculada por codigo, para poder auditarla aparte.
        "perfil": estado.get("perfil") or {},
        # Lo que el modelo decidio comparar, ya calculado en el sandbox.
        "lectura": estado.get("lectura") or {},
        # Lo que la pregunta daba por hecho contra el total real, si suponía algo.
        "premisa": estado.get("premisa") or {},
        "filtros_aplicados": filtros_en_lenguaje_de_negocio(decisiones),
        "intencion": decisiones.get("intencion", "datos"),
        "composicion": estado.get("composicion", ""),
        "impreso": estado.get("impreso", ""),
        "reparaciones": estado.get("reparaciones", 0),
        "novedades": decisiones.get("novedades", []),
        "revision": estado.get("revision") or {},
        "revision_resultado": estado.get("revision_resultado") or {},
        "reintentos": estado.get("reintentos", 0),
        "error": estado.get("error_extraccion") or estado.get("error_composicion") or "",
        "respuesta": estado["respuesta"],
    }

    # El historial se guarda después de tener la respuesta y nunca la bloquea:
    # `guardar_turno` se traga sus propios errores y devuelve None.
    turno_id = registro.guardar_turno(
        thread_id=pedido.thread_id,
        pregunta=pedido.mensaje,
        analisis=analisis,
        resultado=estado.get("resultado"),
        uso=uso,
        segundos=round(time.perf_counter() - arranque, 2),
        # El acta es el ultimo mensaje que dejo `redactar`: la respuesta en texto
        # plano mas el plan. Es exactamente lo que se le devuelve al agente cuando
        # la conversacion se reabre despues de un reinicio.
        acta=estado["mensajes"][-1].content if estado.get("mensajes") else "",
        mostrar_tabla=estado.get("mostrar_tabla", True),
    )

    return {
        "texto": estado["respuesta"],
        "analisis": analisis,
        "turno_id": turno_id,
        "resultado": estado.get("resultado"),
        # Con pocas filas el número va adentro del texto y la tabla sobra.
        "mostrar_tabla": estado.get("mostrar_tabla", True),
        "uso": uso,
        "acumulado": _uso_de(pedido.thread_id),
        "total_sesion": _uso_de(None),
        "turnos_en_memoria": len(estado["mensajes"]),
    }


@app.get("/api/conversaciones")
def listar_conversaciones(limite: int = 50):
    """Las conversaciones guardadas, para la barra lateral.

    El frontend arrancaba con seis conversaciones de mentira en el codigo y todo
    lo real se perdia al recargar la pagina. Ahora la lista sale del historial.
    """
    return {"conversaciones": registro.conversaciones(limite=limite)}


@app.get("/api/conversaciones/{thread_id}")
def abrir_conversacion(thread_id: str):
    """Los turnos de una conversacion, listos para volver a pintarlos.

    Trae el analisis y el resultado completos: el panel de razonamiento de una
    conversacion vieja se abre igual que el de recien, sin volver a llamar al
    modelo ni a la base de reclamos.
    """
    return {
        "thread_id": thread_id,
        "turnos": registro.mensajes_de(thread_id),
        "uso": _uso_de(thread_id),
    }


@app.delete("/api/conversaciones/{thread_id}")
def borrar_conversacion(thread_id: str):
    """Borra una conversación: su historial guardado y la memoria del agente.

    Las dos cosas, porque cada una sola deja la conversación a medias. Si solo se
    borra el historial, el checkpointer sigue recordando el hilo y un mensaje
    nuevo con ese id continuaría la charla vieja. Si solo se borra la memoria, la
    conversación vuelve a aparecer en la barra lateral al recargar.
    """
    borrados = registro.borrar_conversacion(thread_id)
    app_grafo.checkpointer.delete_thread(thread_id)
    contador.olvidar(thread_id)
    return {"thread_id": thread_id, "borrados": borrados, "total_sesion": _uso_de(None)}


@app.get("/api/historial")
def historial(limite: int = 50, thread_id: str | None = None):
    """Las últimas preguntas. Para revisar qué se preguntó sin abrir la base."""
    return {"turnos": registro.ultimos(limite=limite, thread_id=thread_id)}


@app.get("/api/historial/{turno_id}")
def historial_turno(turno_id: int):
    """Un turno con sus consultas: de dónde salió exactamente un número."""
    return registro.turno(turno_id) or {"error": "No existe ese turno."}


# El gasto se lee del historial (SQLite), no del contador en RAM.
#
# El contador sigue existiendo y se usa para la respuesta del turno, porque ahi
# ya esta calculado. Pero como acumulado mentia: se ponia en cero en cada
# reinicio del backend, y una conversacion abierta desde el historial mostraba
# $0,00 aunque hubiera costado un dolar. El dato real siempre estuvo en la tabla.
def _uso_de(thread_id: str | None):
    guardado = registro.uso_acumulado(thread_id)
    if guardado:
        return guardado
    # Si la base no responde, al menos lo de esta sesion.
    return contador.de_conversacion(thread_id) if thread_id else contador.total()


@app.get("/api/uso/{thread_id}")
def uso_de_conversacion(thread_id: str):
    return {"acumulado": _uso_de(thread_id), "total_sesion": _uso_de(None)}


@app.get("/api/uso")
def uso_total():
    return {"total_sesion": _uso_de(None)}
