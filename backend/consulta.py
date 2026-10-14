"""Ejecuta el SQL que propone el agente y deja el resultado listo para JSON.

Envuelve a `datos_basePublica.ejecucion.consultar` — que es la única puerta a la
base SQLite y la que tiene las validaciones de solo lectura — y le agrega lo que
necesita el grafo:

- convertir el DataFrame a algo serializable (Decimal, NaN y fechas no viajan en JSON),
- separar lo que ve el modelo (pocas filas, para no gastar tokens) de lo que ve
  la persona en el frontend (más filas),
- capturar el error como dato en vez de dejarlo explotar, para que el nodo de
  reparación pueda leerlo e intentar corregir la consulta.
"""

from decimal import Decimal
from typing import TypedDict

import pandas as pd

from datos_basePublica.ejecucion import consultar

# La única tabla que consulta el agente: una fila por reclamo.
TABLA = "sabana"

# Cuántas filas se le muestran a cada uno. El modelo paga tokens por cada fila
# que lee, pero el límite no puede ser tan bajo que le esconda datos.
#
# Con un límite bajo, un desglose por estado y mes (cientos de filas ordenadas
# por costo) llega recortado: el redactor ve las más altas, no encuentra algunos
# meses de un estado chico, y escribe que "no hay datos" para esos meses. Los
# datos están: los cortamos nosotros. Un corte silencioso se lee como una
# ausencia real.
FILAS_PARA_EL_MODELO = 200
FILAS_PARA_EL_FRONTEND = 500

# A partir de cuántas filas conviene mostrar la tabla en vez de enumerar en el
# texto. Con tres o menos, el número dentro de la frase se lee mejor.
FILAS_PARA_MOSTRAR_TABLA = 3


class Resultado(TypedDict):
    sql: str
    columnas: list[str]
    filas: list[list]
    total_filas: int
    truncado: bool
    error: str | None


def _a_json(valor):
    """Decimal, NaN y timestamps no son serializables tal cual."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (pd.Timestamp,)):
        return valor.isoformat()
    if hasattr(valor, "item"):  # tipos de numpy
        return valor.item()
    return valor


def vacio(sql: str = "") -> Resultado:
    return {
        "sql": sql,
        "columnas": [],
        "filas": [],
        "total_filas": 0,
        "truncado": False,
        "error": None,
    }


def desde_tabla(tabla: pd.DataFrame, sql: str = "") -> Resultado:
    """DataFrame -> la forma que entiende el frontend.

    La usan tanto `ejecutar` (con lo que devuelve la base) como el nodo que compone
    en Python (con el DataFrame que sale del sandbox).
    """
    base = vacio(sql)
    recorte = tabla.head(FILAS_PARA_EL_FRONTEND)
    base["columnas"] = [str(columna) for columna in tabla.columns]
    base["filas"] = [[_a_json(valor) for valor in fila] for fila in recorte.itertuples(index=False)]
    base["total_filas"] = len(tabla)
    base["truncado"] = len(tabla) > FILAS_PARA_EL_FRONTEND
    return base


def ejecutar(sql: str) -> Resultado:
    """Corre la consulta. Nunca lanza: el error viaja dentro del resultado."""
    if not sql.strip():
        return {**vacio(sql), "error": "El agente no propuso ninguna consulta."}

    try:
        tabla = consultar(sql)
    except Exception as error:  # de sqlite3, de pandas o de las validaciones
        return {**vacio(sql), "error": f"{type(error).__name__}: {error}"}

    return desde_tabla(tabla, sql)


def a_dataframe(resultado: Resultado) -> pd.DataFrame:
    """El camino de vuelta: de la forma que viaja en JSON a un DataFrame.

    Sirve para leer un resultado que ya está en el estado sin volver a consultar
    la base. Es lo que permite que el nodo de lectura opere sobre la tabla que la
    persona está viendo, no sobre una consulta nueva.
    """
    return pd.DataFrame(resultado.get("filas") or [], columns=resultado.get("columnas") or [])


def tabla_de(sql: str) -> pd.DataFrame:
    """El DataFrame crudo, para pasárselo al sandbox. Lanza si la consulta falla."""
    return consultar(sql)


def _conteos(resultado: Resultado) -> str:
    """Cuántas filas hay por cada valor de las columnas de pocos valores distintos.

    Existe por un modo de fallar conocido: un desglose de ámbito por mes viene
    ordenado por costo, y con Hospitalario y Ambulatorio intercalados el modelo no
    cuenta y escribe que a uno le faltan meses. Los tiene todos.

    Contar filas de una lista desordenada es exactamente lo que un modelo hace
    mal y una función hace bien. Se lo damos hecho: son pocos tokens y le saca
    de encima la única parte del trabajo donde no aporta criterio.
    """
    if resultado["total_filas"] < 2:
        return ""

    partes = []
    for indice, columna in enumerate(resultado["columnas"]):
        valores = [fila[indice] for fila in resultado["filas"]]
        distintos = {}
        for valor in valores:
            clave = "NULL" if valor is None else str(valor)
            distintos[clave] = distintos.get(clave, 0) + 1
        # Solo las que parecen dimensiones: una medida tiene un valor por fila.
        if 1 < len(distintos) <= 15:
            detalle = ", ".join(f"{k} ({n} fila/s)" for k, n in sorted(distintos.items()))
            partes.append(f"  {columna}: {detalle}")

    if not partes:
        return ""
    return (
        "\nConteo de filas por valor (contado sobre el resultado, no lo recuentes tú):\n"
        + "\n".join(partes)
    )


def para_el_modelo(resultado: Resultado) -> str:
    """El resultado como texto compacto, que es lo que se le manda al modelo."""
    if resultado["error"]:
        return f"La consulta falló.\n\nSQL:\n{resultado['sql']}\n\nError: {resultado['error']}"

    if resultado["total_filas"] == 0:
        return (
            f"La consulta se ejecutó bien pero no devolvió ninguna fila.\n\n"
            f"SQL:\n{resultado['sql']}"
        )

    filas = resultado["filas"][:FILAS_PARA_EL_MODELO]
    lineas = [" | ".join(resultado["columnas"])]
    lineas += [" | ".join("NULL" if v is None else str(v) for v in fila) for fila in filas]
    lineas.append(_conteos(resultado))
    aviso = ""
    if resultado["total_filas"] > len(filas):
        # Explícito y en mayúsculas a propósito: un corte que se lee al pasar se
        # confunde con una ausencia de datos, y el modelo termina reportando como
        # hallazgo algo que solo es una limitación de lo que le mostramos.
        aviso = (
            f"\n\nATENCIÓN: esto es un RECORTE. La consulta devolvió "
            f"{resultado['total_filas']} filas y aquí ves solo las primeras {len(filas)}. "
            f"NO concluyas que falta un período, una categoría o un valor por no verlo "
            f"en esta lista: puede estar en las filas que no se muestran."
        )
    return "Resultado de la consulta:\n\n" + "\n".join(lineas) + aviso


def columnas_reales() -> set[str]:
    """Los nombres de columna que la tabla tiene ahora. Es la lista blanca con la
    que `agente/reglas.py` valida todo lo que el modelo pide."""
    return set(consultar(f"SELECT name FROM pragma_table_info('{TABLA}')")["name"])


def esquema_real() -> str:
    """La lista de columnas que la tabla tiene de verdad.

    El diccionario describe las 25 columnas de la sábana, pero manda lo que hay en
    la base: si alguien regenera `reclamos.db` con otra versión de `sabana.py`,
    esta lista lo refleja sin tocar el diccionario. Sin ella el agente podría
    pedir una columna que no existe y la consulta moriría con "no such column".
    """
    columnas = consultar(
        f"SELECT name FROM pragma_table_info('{TABLA}') ORDER BY cid"
    )["name"].tolist()
    return (
        f"# Columnas que existen de verdad en la tabla `{TABLA}`\n\n"
        "Esta es la lista completa y es la que manda. Si necesitas una columna que no "
        "figura aquí, no la uses ni la inventes: di que ese dato no está disponible en "
        "la base.\n\n"
        + ", ".join(f"`{c}`" for c in columnas)
    )


def rango_temporal() -> tuple[int, int]:
    """(desde, hasta) como anio*100+mes. El mismo dato que `cobertura_temporal`,
    pero como números para poder comprobarlo con código en vez de pedírselo al
    modelo."""
    rango = consultar(
        f"SELECT MIN(anio*100+mes) AS desde, MAX(anio*100+mes) AS hasta FROM {TABLA}"
    )
    return int(rango.iloc[0]["desde"]), int(rango.iloc[0]["hasta"])


def cobertura_temporal() -> str:
    """Qué período cubren los datos realmente. Va en el prompt del agente.

    Sin esto, el agente resuelve "el mes pasado" contra el calendario y consulta
    un mes que no existe en la tabla: la consulta corre bien y devuelve vacío, que
    es peor que un error porque parece un cero legítimo.
    """
    tabla = consultar(
        f"SELECT anio, mes, COUNT(*) AS filas FROM {TABLA} GROUP BY anio, mes ORDER BY anio, mes"
    )
    meses = [f"{int(f.anio)}-{int(f.mes):02d}" for f in tabla.itertuples()]
    filas = f"{int(tabla['filas'].sum()):,}".replace(",", ".")
    return (
        f"# Períodos que existen en la tabla `{TABLA}`\n\n"
        f"La tabla tiene {filas} reclamos, repartidos en estos {len(meses)} meses y en "
        f"ningún otro:\n\n{', '.join(meses)}\n\n"
        f"Es una LISTA, no un rango: para saber si un mes tiene datos, búscalo en "
        f"ella. No deduzcas si un mes cae dentro o fuera comparando extremos. Si el mes "
        f"está aquí, hay datos. Si no está, no los hay y hay que decirlo en vez de "
        f"devolver cero. \"Hoy\" no tiene relación con estos datos: \"el último mes\" "
        f"es el último de esta lista, no el del calendario."
    )
