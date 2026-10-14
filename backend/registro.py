"""Guarda en SQLite cada pregunta que se le hace al agente y qué hizo con ella.

Para qué sirve, en orden de importancia:

1. **Auditar un número viejo.** Alguien pregunta "¿de dónde salió este 9.738,71
   de la semana pasada?" y la respuesta está: el SQL exacto, los filtros, el
   código que cruzó los resultados.
2. **Medir.** Cuánto se gastó, cuántas veces hubo que reparar el SQL, qué
   preguntas terminaron pidiendo aclaración.
3. **Alimentar el set de evaluación** con preguntas reales en vez de inventadas.

Tres decisiones que valen la pena explicar:

**Archivo aparte.** La base de reclamos (`datos_basePublica/reclamos.db`) se abre
en solo lectura y esa restricción no se toca. El historial necesita escribir, así
que vive en otro archivo: `datos_app/historial.db` (configurable con
`HISTORIAL_RUTA`). Nunca en la base de reclamos.

**Una conexión por operación.** FastAPI atiende cada pedido en un hilo distinto y
una conexión de SQLite no se comparte entre hilos. Abrir una es barato (es un
archivo), y el modo WAL deja leer mientras otro escribe.

**Nunca rompe la respuesta.** Si el archivo no se puede abrir o el INSERT falla,
se registra el problema y la persona igual recibe su respuesta. El historial es
valioso, pero no más que contestar la pregunta.
"""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

RUTA = Path(
    os.environ.get("HISTORIAL_RUTA") or Path(__file__).parent / "datos_app" / "historial.db"
).resolve()

# Hora local en ISO 8601 ("2026-10-13T18:40:00"): ordena bien como texto y el
# frontend la lee con `new Date(...)` sin conversiones.
AHORA = "(strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))"

# Un turno por pregunta, y una fila por cada consulta que se ejecutó.
# JSON como texto en vez de más tablas: los filtros y las novedades se leen
# enteros o no se leen, y no vale la pena normalizar algo que nadie va a cruzar.
TABLAS = (
    f"""
    CREATE TABLE IF NOT EXISTS agente_turnos (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        momento                TEXT    NOT NULL DEFAULT {AHORA},
        thread_id              TEXT    NOT NULL,
        pregunta               TEXT    NOT NULL,
        titulo                 TEXT,
        respuesta              TEXT,
        acta                   TEXT,
        intencion              TEXT    NOT NULL DEFAULT 'datos',
        modelo                 TEXT,
        rama                   TEXT,
        periodo                TEXT,
        metrica                TEXT,
        metrica_aditiva        INTEGER,
        pide_participacion     INTEGER NOT NULL DEFAULT 0,
        dimension_parte        TEXT,
        universo_denominador   TEXT,
        interpretacion         TEXT,
        composicion            TEXT,
        novedades              TEXT,
        revision               TEXT,
        revision_result        TEXT,
        analisis               TEXT,
        resultado_json         TEXT,
        mostrar_tabla          INTEGER NOT NULL DEFAULT 1,
        reintentos             INTEGER NOT NULL DEFAULT 0,
        reparaciones           INTEGER NOT NULL DEFAULT 0,
        rondas_sondeo          INTEGER NOT NULL DEFAULT 0,
        necesito_aclaracion    INTEGER NOT NULL DEFAULT 0,
        error                  TEXT,
        filas_resultado        INTEGER NOT NULL DEFAULT 0,
        tokens_entrada         INTEGER NOT NULL DEFAULT 0,
        tokens_salida          INTEGER NOT NULL DEFAULT 0,
        tokens_cache           INTEGER NOT NULL DEFAULT 0,
        tokens_cache_escritura INTEGER NOT NULL DEFAULT 0,
        costo_entrada          REAL    NOT NULL DEFAULT 0,
        costo_salida           REAL    NOT NULL DEFAULT 0,
        costo_total            REAL    NOT NULL DEFAULT 0,
        segundos               REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_turnos_thread ON agente_turnos (thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_turnos_momento ON agente_turnos (momento)",
    """
    CREATE TABLE IF NOT EXISTS agente_consultas (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        turno_id      INTEGER NOT NULL REFERENCES agente_turnos (id) ON DELETE CASCADE,
        tipo          TEXT    NOT NULL DEFAULT 'extraccion',
        nombre        TEXT    NOT NULL,
        proposito     TEXT,
        sql_ejecutado TEXT,
        medidas       TEXT,
        dimensiones   TEXT,
        filtros       TEXT,
        inyectados    TEXT,
        filas         INTEGER NOT NULL DEFAULT 0,
        error         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_consultas_turno ON agente_consultas (turno_id)",
)


def _conectar() -> sqlite3.Connection:
    RUTA.parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(RUTA, timeout=10)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    return conexion


def crear_tablas() -> str:
    """Crea el archivo y las tablas si no existen. Devuelve dónde quedaron."""
    with closing(_conectar()) as conexion, conexion:
        # WAL queda grabado en el archivo: basta con pedirlo una vez.
        conexion.execute("PRAGMA journal_mode = WAL")
        for ddl in TABLAS:
            conexion.execute(ddl)
    return f"{RUTA} (agente_turnos, agente_consultas)"


def _json(valor) -> str:
    return json.dumps(valor, ensure_ascii=False, default=str)


def guardar_turno(
    thread_id: str,
    pregunta: str,
    analisis: dict,
    resultado: dict | None,
    uso: dict,
    segundos: float | None = None,
    acta: str = "",
    mostrar_tabla: bool = True,
    titulo: str = "",
) -> int | None:
    """Guarda el turno y sus consultas. Devuelve el id, o None si no se pudo.

    Nunca lanza: un problema para guardar el historial no puede tumbar una
    respuesta que ya está calculada y pagada.
    """
    try:
        with closing(_conectar()) as conexion, conexion:
            turno = conexion.execute(
                """
                INSERT INTO agente_turnos (
                    thread_id, pregunta, respuesta, modelo, rama, periodo, metrica,
                    metrica_aditiva, interpretacion, composicion, novedades, revision,
                    revision_result, reintentos, reparaciones, necesito_aclaracion,
                    error, filas_resultado, tokens_entrada, tokens_salida, tokens_cache,
                    costo_total, segundos, pide_participacion, dimension_parte,
                    universo_denominador, rondas_sondeo, analisis, resultado_json,
                    mostrar_tabla, intencion, acta, titulo,
                    costo_entrada, costo_salida, tokens_cache_escritura
                ) VALUES (
                    :thread_id, :pregunta, :respuesta, :modelo, :rama, :periodo, :metrica,
                    :metrica_aditiva, :interpretacion, :composicion, :novedades, :revision,
                    :revision_result, :reintentos, :reparaciones, :necesito_aclaracion,
                    :error, :filas_resultado, :tokens_entrada, :tokens_salida, :tokens_cache,
                    :costo_total, :segundos, :pide_participacion, :dimension_parte,
                    :universo_denominador, :rondas_sondeo, :analisis, :resultado_json,
                    :mostrar_tabla, :intencion, :acta, :titulo,
                    :costo_entrada, :costo_salida, :tokens_cache_escritura
                )
                """,
                {
                    "thread_id": thread_id,
                    "pregunta": pregunta,
                    "respuesta": analisis.get("respuesta", ""),
                    "modelo": uso.get("modelo", ""),
                    "rama": analisis.get("rama", ""),
                    "periodo": (analisis.get("periodo") or "")[:255],
                    "metrica": (analisis.get("metrica") or "")[:255],
                    "metrica_aditiva": int(bool(analisis.get("metrica_es_aditiva", True))),
                    "interpretacion": analisis.get("interpretacion", ""),
                    "composicion": analisis.get("composicion", ""),
                    "novedades": _json(analisis.get("novedades", [])),
                    "revision": _json(analisis.get("revision", {})),
                    "revision_result": _json(analisis.get("revision_resultado", {})),
                    "reintentos": int(analisis.get("reintentos", 0)),
                    "reparaciones": int(analisis.get("reparaciones", 0)),
                    "necesito_aclaracion": int(not analisis.get("consultas")),
                    "error": analisis.get("error", ""),
                    "filas_resultado": int((resultado or {}).get("total_filas", 0)),
                    "tokens_entrada": int(uso.get("tokens_entrada", 0)),
                    "tokens_salida": int(uso.get("tokens_salida", 0)),
                    "tokens_cache": int(uso.get("tokens_cache_lectura", 0)),
                    "costo_total": float(uso.get("costo_total", 0.0)),
                    "segundos": segundos,
                    "pide_participacion": int(bool(analisis.get("pide_participacion", False))),
                    "dimension_parte": (analisis.get("dimension_de_la_parte") or "")[:64],
                    "universo_denominador": analisis.get("universo_denominador", ""),
                    "rondas_sondeo": len(analisis.get("sondeos", [])),
                    # El analisis entero como JSON: es lo que permite volver a
                    # pintar el panel de razonamiento de una conversacion vieja
                    # sin recalcular nada ni volver a llamar al modelo.
                    "analisis": _json(analisis),
                    "resultado_json": _json(resultado) if resultado else None,
                    "mostrar_tabla": int(bool(mostrar_tabla)),
                    "intencion": (analisis.get("intencion") or "datos")[:16],
                    "acta": acta,
                    "titulo": (titulo or pregunta)[:120],
                    "costo_entrada": float(uso.get("costo_entrada", 0.0)),
                    "costo_salida": float(uso.get("costo_salida", 0.0)),
                    "tokens_cache_escritura": int(uso.get("tokens_cache_escritura", 0)),
                },
            )
            turno_id = turno.lastrowid

            # Los sondeos van a la misma tabla con `tipo = 'sondeo'`: son consultas
            # que se ejecutaron para responder esta pregunta, y auditar de dónde
            # salió un número incluye saber qué se miró antes de decidir.
            paso_previo = [({**s, "tipo": "sondeo"}) for s in analisis.get("sondeos", [])]
            for consulta in paso_previo + list(analisis.get("consultas", [])):
                conexion.execute(
                    """
                    INSERT INTO agente_consultas (
                        turno_id, tipo, nombre, proposito, sql_ejecutado, medidas,
                        dimensiones, filtros, inyectados, filas, error
                    ) VALUES (
                        :turno_id, :tipo, :nombre, :proposito, :sql, :medidas,
                        :dimensiones, :filtros, :inyectados, :filas, :error
                    )
                    """,
                    {
                        "turno_id": turno_id,
                        "tipo": consulta.get("tipo", "extraccion"),
                        "nombre": consulta.get("nombre", "")[:64],
                        "proposito": consulta.get("proposito", ""),
                        "sql": consulta.get("sql", ""),
                        "medidas": _json(consulta.get("medidas", [])),
                        "dimensiones": _json(consulta.get("dimensiones", [])),
                        "filtros": _json(consulta.get("filtros", [])),
                        "inyectados": _json(consulta.get("inyectados", [])),
                        "filas": int(
                            consulta.get("filas")
                            or (consulta.get("resultado") or {}).get("total_filas", 0)
                        ),
                        "error": consulta.get("error") or "",
                    },
                )
        return turno_id
    except Exception as error:
        print(f"No se pudo guardar el turno en el historial: {type(error).__name__}: {error}")
        return None


def _leer_json(valor):
    """Las columnas JSON se guardan como texto; se devuelven ya parseadas."""
    if valor is None or isinstance(valor, (dict, list)):
        return valor
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        return None


def uso_acumulado(thread_id: str | None = None) -> dict:
    """El gasto sumado desde el historial. Por conversación, o de todo si no se pasa una.

    El contador vivía solo en RAM, así que se ponía en cero en cada reinicio del
    backend y una conversación abierta desde el historial mostraba $0,00 aunque
    hubiera costado un dólar. El dato real siempre estuvo acá: cada turno guarda
    sus tokens y su costo. Esto solo lo suma.

    Nunca lanza: quedarse sin medidor es molesto, quedarse sin respuesta es peor.
    """
    filtro = "WHERE thread_id = :thread_id" if thread_id else ""
    try:
        with closing(_conectar()) as conexion:
            fila = conexion.execute(
                f"""
                SELECT COUNT(*)                                 AS turnos,
                       COALESCE(SUM(tokens_entrada), 0)         AS tokens_entrada,
                       COALESCE(SUM(tokens_salida), 0)          AS tokens_salida,
                       COALESCE(SUM(tokens_cache), 0)           AS cache_lectura,
                       COALESCE(SUM(tokens_cache_escritura), 0) AS cache_escritura,
                       COALESCE(SUM(costo_entrada), 0)          AS costo_entrada,
                       COALESCE(SUM(costo_salida), 0)           AS costo_salida,
                       COALESCE(SUM(costo_total), 0)            AS costo_total,
                       GROUP_CONCAT(DISTINCT modelo)            AS modelos
                FROM agente_turnos {filtro}
                """,
                {"thread_id": thread_id},
            ).fetchone()
    except Exception as error:
        print(f"No se pudo leer el gasto acumulado: {type(error).__name__}: {error}")
        return {}

    return {
        "consultas": int(fila["turnos"]),
        "respuestas": int(fila["turnos"]),
        "tokens_entrada": int(fila["tokens_entrada"]),
        "tokens_salida": int(fila["tokens_salida"]),
        "tokens_cache_lectura": int(fila["cache_lectura"]),
        "tokens_cache_escritura": int(fila["cache_escritura"]),
        "costo_entrada": float(fila["costo_entrada"]),
        "costo_salida": float(fila["costo_salida"]),
        "costo_total": float(fila["costo_total"]),
        "precio_conocido": True,
        "modelos": [m for m in (fila["modelos"] or "").split(",") if m],
    }


def conversaciones(limite: int = 50) -> list[dict]:
    """La lista para la barra lateral: un renglón por conversación.

    El título es la primera pregunta del hilo. Se calcula al leer y no se guarda
    aparte: la primera pregunta ya está, y una copia que se puede derivar es una
    copia que se puede desincronizar.
    """
    with closing(_conectar()) as conexion:
        filas = conexion.execute(
            """
            SELECT t.thread_id,
                   (SELECT p.pregunta FROM agente_turnos p
                    WHERE p.thread_id = t.thread_id ORDER BY p.id LIMIT 1) AS titulo,
                   COUNT(*)           AS turnos,
                   MAX(t.momento)     AS ultimo,
                   SUM(t.costo_total) AS costo
            FROM agente_turnos t
            GROUP BY t.thread_id
            ORDER BY MAX(t.id) DESC
            LIMIT :limite
            """,
            {"limite": int(limite)},
        ).fetchall()
        return [dict(fila) for fila in filas]


def mensajes_de(thread_id: str) -> list[dict]:
    """Los turnos de una conversación, listos para volver a pintarlos.

    Devuelve el análisis y el resultado completos, así el panel de razonamiento
    de una conversación de la semana pasada se abre igual que el del minuto
    pasado, sin volver a llamar al modelo ni a la base de reclamos.
    """
    with closing(_conectar()) as conexion:
        filas = conexion.execute(
            """
            SELECT id, momento, pregunta, respuesta, analisis, resultado_json,
                   mostrar_tabla, intencion
            FROM agente_turnos WHERE thread_id = :thread_id ORDER BY id
            """,
            {"thread_id": thread_id},
        ).fetchall()
        return [
            {
                "id": fila["id"],
                "momento": fila["momento"],
                "pregunta": fila["pregunta"],
                "respuesta": fila["respuesta"],
                "analisis": _leer_json(fila["analisis"]),
                "resultado": _leer_json(fila["resultado_json"]),
                "mostrar_tabla": bool(fila["mostrar_tabla"]),
                "intencion": fila["intencion"],
            }
            for fila in filas
        ]


def actas_de(thread_id: str, limite: int = 8) -> list[tuple[str, str]]:
    """(pregunta, acta) de los últimos turnos, para devolverle la memoria al agente.

    El checkpointer de LangGraph vive en RAM y se pierde al reiniciar el backend.
    Sin esto, después de un reinicio un "¿y en 2008?" no tiene con qué comparar
    y el agente contesta cualquier cosa con total seguridad.

    Se limita a los últimos turnos porque cada acta ocupa tokens en cada llamada,
    y una conversación de treinta preguntas haría el prompt impagable.
    """
    with closing(_conectar()) as conexion:
        filas = conexion.execute(
            """
            SELECT pregunta, acta FROM agente_turnos
            WHERE thread_id = :thread_id AND acta IS NOT NULL AND acta <> ''
            ORDER BY id DESC LIMIT :limite
            """,
            {"thread_id": thread_id, "limite": int(limite)},
        ).fetchall()
        return [(fila["pregunta"], fila["acta"]) for fila in reversed(filas)]


def ultimos(limite: int = 50, thread_id: str | None = None) -> list[dict]:
    """Los turnos más recientes. Para revisar el historial sin abrir la base."""
    filtro = "WHERE thread_id = :thread_id" if thread_id else ""
    with closing(_conectar()) as conexion:
        filas = conexion.execute(
            f"""
            SELECT id, momento, thread_id, pregunta, metrica, periodo, filas_resultado,
                   reintentos, reparaciones, costo_total, error
            FROM agente_turnos {filtro}
            ORDER BY id DESC LIMIT :limite
            """,
            {"limite": int(limite), "thread_id": thread_id},
        ).fetchall()
        return [dict(fila) for fila in filas]


def borrar_conversacion(thread_id: str) -> int:
    """Borra todos los turnos de una conversación. Devuelve cuántos se borraron.

    Las consultas de cada turno se van solas: `agente_consultas` tiene
    `ON DELETE CASCADE` y `_conectar` enciende las claves foráneas. Lanza si el
    archivo no se puede escribir: a diferencia de guardar un turno, acá la
    persona pidió algo explícito y tiene que enterarse si no se hizo.
    """
    with closing(_conectar()) as conexion, conexion:
        borrados = conexion.execute(
            "DELETE FROM agente_turnos WHERE thread_id = :thread_id", {"thread_id": thread_id}
        ).rowcount
    return borrados


def turno(turno_id: int) -> dict | None:
    """Un turno con sus consultas, para auditar de dónde salió un número."""
    with closing(_conectar()) as conexion:
        cabecera = conexion.execute(
            "SELECT * FROM agente_turnos WHERE id = :id", {"id": int(turno_id)}
        ).fetchone()
        if not cabecera:
            return None
        consultas = conexion.execute(
            "SELECT * FROM agente_consultas WHERE turno_id = :id ORDER BY id",
            {"id": int(turno_id)},
        ).fetchall()
        return {**dict(cabecera), "consultas": [dict(c) for c in consultas]}
