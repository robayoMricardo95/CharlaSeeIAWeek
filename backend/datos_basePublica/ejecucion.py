"""Acceso de solo lectura a la base pública de reclamos (SQLite).

Toda consulta del agente pasa por acá. Ningún nodo del grafo debería abrir
su propia conexión ni ejecutar SQL por su cuenta: centralizarlo en un solo
punto es lo que hace posible auditar, limitar y cortar lo que se ejecuta.

La base es un archivo (`reclamos.db`) que no va al repositorio: se regenera con
`descargar.py` → `preparar.py` → `sabana.py`. Datos sintéticos del CMS
(DE-SynPUF, muestra 1), de dominio público.

Tres capas de protección, de la más fuerte a la más débil:

1. `mode=ro` en la conexión: SQLite rechaza cualquier escritura.
2. `PRAGMA query_only`: segunda llave por si alguien cambia la URI.
3. Las validaciones de texto de abajo: rechazan antes de llegar a la base.
"""

import os
import re
import sqlite3
import time
from pathlib import Path

import pandas as pd

RUTA = Path(os.environ.get("SQLITE_RUTA") or Path(__file__).parent / "reclamos.db").resolve()

# Una consulta que tarda más que esto se corta. Sobre 656 mil filas, lo normal
# es menos de un segundo; un producto cartesiano accidental no termina nunca.
SEGUNDOS_MAXIMOS = 30

# ATTACH abriría otro archivo (incluso uno escribible); PRAGMA puede cambiar el
# modo de la conexión. Van en la lista aunque la sentencia empiece con SELECT.
PROHIBIDAS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|UPSERT"
    r"|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|ANALYZE)\b",
    re.I,
)


def _sin_comentarios(sql: str) -> str:
    """Quita comentarios para que no se usen como disfraz.

    Sin esto, `SELECT 1 /* */ ; DROP TABLE sabana` esquiva las validaciones.
    """
    sin_bloque = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sin_bloque)


def _conectar() -> sqlite3.Connection:
    if not RUTA.exists():
        raise FileNotFoundError(
            f"No existe la base {RUTA}. Regenerarla con los scripts de "
            f"backend/datos_basePublica/ (descargar.py → preparar.py → sabana.py)."
        )
    conexion = sqlite3.connect(f"{RUTA.as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conexion.execute("PRAGMA query_only = ON")

    # Corta la consulta si se pasa de tiempo: devolver distinto de cero aborta.
    limite = time.monotonic() + SEGUNDOS_MAXIMOS
    conexion.set_progress_handler(lambda: int(time.monotonic() > limite), 100_000)
    return conexion


def consultar(sql: str, limite: int = 5000) -> pd.DataFrame:
    """Ejecuta una consulta de lectura y devuelve el resultado como DataFrame.

    Rechaza cualquier cosa que no sea SELECT o WITH, y agrega un LIMIT si la
    consulta no trae uno: un SELECT sin límite sobre la sábana llena la memoria
    del proceso.
    """
    limpio = _sin_comentarios(sql).strip().rstrip(";")

    if not re.match(r"^\s*(SELECT|WITH)\b", limpio, re.I):
        raise ValueError("Solo se permiten consultas de lectura (SELECT o WITH)")

    if PROHIBIDAS.search(limpio):
        raise ValueError("La consulta contiene una sentencia no permitida")

    if ";" in limpio:
        raise ValueError("No se permite más de una sentencia por consulta")

    if not re.search(r"\bLIMIT\b", limpio, re.I):
        limpio += f" LIMIT {limite}"

    conexion = _conectar()
    try:
        return pd.read_sql_query(limpio, conexion)
    finally:
        conexion.close()
