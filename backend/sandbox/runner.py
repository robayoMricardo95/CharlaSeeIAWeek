"""Punto de entrada dentro del contenedor. Lo escribimos nosotros, no el modelo.

Lee un JSON por stdin, ejecuta el script generado, y devuelve un JSON por stdout.
El script del modelo nunca es el programa principal: corre adentro de este.

Entrada:
    {"script": "<código python>", "datos": {"c1": "<json con esquema>", ...}}

Salida:
    {"ok": true,  "resultado": "<json con esquema>", "impreso": "...", "filas": 12}
    {"ok": false, "error": "KeyError: 'Mes'", "impreso": "..."}

Nunca lanza: cualquier problema sale como `ok: false`, para que el nodo de
reparación pueda leer el error y reintentar.
"""

import io
import json
import sys
import traceback
from contextlib import redirect_stdout
from decimal import Decimal

import numpy as np
import pandas as pd

# Tope de la respuesta. Un cruce mal hecho puede explotar en filas; mejor
# cortarlo acá que mandarle cientos de MB al backend.
LIMITE_SALIDA = 8 * 1024 * 1024


def a_dataframe(texto: str) -> pd.DataFrame:
    return pd.read_json(io.StringIO(texto), orient="table").reset_index(drop=True)


def indice_como_columna(tabla: pd.DataFrame) -> pd.DataFrame:
    """Pasa el índice a columna cuando trae etiquetas, y lo descarta cuando no.

    Medido contra la API: el modelo hace `pivot(index='grupo_enfermedad', ...)` o
    `set_index(...)` y deja los grupos en el índice. Descartarlo siempre perdía
    los nombres, y la respuesta terminaba diciendo "no se informaron los grupos".

    Un índice numérico sin nombre, en cambio, es el que deja un filtro o un orden
    (0, 3, 7...): no significa nada y convertirlo ensuciaría la tabla con una
    columna "index".
    """
    indice = tabla.index
    con_etiquetas = (
        isinstance(indice, pd.MultiIndex)
        or any(nombre is not None for nombre in indice.names)
        or indice.dtype == object
    )
    if not con_etiquetas:
        return tabla.reset_index(drop=True)
    try:
        return tabla.reset_index()
    except ValueError:
        # El índice se llama igual que una columna que ya está: la columna manda.
        return tabla.reset_index(drop=True)


def normalizar(tabla: pd.DataFrame) -> pd.DataFrame:
    """Deja la tabla en condiciones de viajar como JSON con esquema.

    Tres casos que rompen `to_json(orient="table")` o pierden datos, y que el
    modelo produce sin querer todo el tiempo:

    - **Etiquetas en el índice**: `pivot` y `set_index` dejan los nombres ahí; se
      pasan a columna (ver `indice_como_columna`).
    - **Columnas con nombre no textual**: `pd.DataFrame([[1],[2]])` las numera
      con enteros, y al volver del JSON el índice de columnas no se reconstruye.
    - **Columnas duplicadas**: `pd.concat(..., axis=1)` las genera sin avisar, y
      entonces `tabla[columna]` devuelve un DataFrame en vez de una Serie.
    """
    limpia = indice_como_columna(tabla).copy()

    nombres, vistos = [], {}
    for columna in limpia.columns:
        nombre = str(columna)
        if nombre in vistos:
            vistos[nombre] += 1
            nombre = f"{nombre}_{vistos[nombre]}"
        else:
            vistos[nombre] = 0
        nombres.append(nombre)
    limpia.columns = nombres

    # Una columna DECIMAL puede llegar como `Decimal`, que no es serializable.
    for nombre in limpia.columns:
        columna = limpia[nombre]
        if columna.dtype == object and columna.map(lambda v: isinstance(v, Decimal)).any():
            limpia[nombre] = columna.astype(float)
    return limpia


def serializar(tabla: pd.DataFrame) -> str:
    return normalizar(tabla).to_json(orient="table", index=False, date_format="iso")


def responder(carga: dict) -> None:
    """Escribe la respuesta en el stdout real y termina."""
    sys.stdout.write(json.dumps(carga, ensure_ascii=False))
    sys.stdout.flush()


def main() -> None:
    try:
        pedido = json.load(sys.stdin)
    except Exception as error:
        responder({"ok": False, "error": f"No se pudo leer la entrada: {error}", "impreso": ""})
        return

    try:
        consultas = {
            nombre: a_dataframe(datos) for nombre, datos in (pedido.get("datos") or {}).items()
        }
    except Exception as error:
        responder({"ok": False, "error": f"No se pudieron leer los datos: {error}", "impreso": ""})
        return

    # El namespace del script: solo pandas, numpy y los resultados de las
    # consultas. Nada de os, requests ni acceso a archivos.
    entorno = {
        "pd": pd,
        "np": np,
        "consultas": consultas,
        "resultado": None,
    }

    # Lo que el script imprima se captura aparte: el stdout real lo necesitamos
    # limpio para el JSON de respuesta.
    impreso = io.StringIO()
    try:
        with redirect_stdout(impreso):
            exec(pedido.get("script", ""), entorno)
    except Exception:
        responder(
            {
                "ok": False,
                # Solo las últimas líneas: el traceback completo trae rutas del
                # contenedor que no le dicen nada a nadie.
                "error": "".join(traceback.format_exception_only(*sys.exc_info()[:2])).strip(),
                "impreso": impreso.getvalue()[:4000],
            }
        )
        return

    resultado = entorno.get("resultado")
    if resultado is None:
        responder(
            {
                "ok": False,
                "error": "El script terminó sin dejar nada en la variable `resultado`.",
                "impreso": impreso.getvalue()[:4000],
            }
        )
        return

    # `resultado` puede venir de varias formas y todas son razonables. Se
    # normalizan a DataFrame acá, porque exigirle una sola al modelo es pedirle
    # que se acuerde de algo que no cambia la respuesta.
    #
    # El caso del dict aparece de verdad: el modelo devuelve algo como
    # `{'reclamos': 1000, 'afiliados': 400, 'frecuencia': 2.5}`, y sin esto
    # terminaba entero adentro de UNA celda llamada "valor". Un dict es una fila
    # con columnas nombradas, no un valor.
    if not isinstance(resultado, pd.DataFrame):
        try:
            if isinstance(resultado, pd.Series):
                # Una Serie con nombres (`groupby(...).sum()`) es índice + valor.
                resultado = resultado.reset_index()
            elif isinstance(resultado, dict):
                resultado = pd.DataFrame([resultado])
            elif isinstance(resultado, (list, tuple)) and resultado:
                if all(isinstance(item, dict) for item in resultado):
                    resultado = pd.DataFrame(list(resultado))
                else:
                    resultado = pd.DataFrame({"valor": list(resultado)})
            else:
                resultado = pd.DataFrame({"valor": [resultado]})
        except Exception as error:
            responder(
                {
                    "ok": False,
                    "error": f"`resultado` es {type(resultado).__name__} y no se pudo "
                    f"convertir a tabla: {error}",
                    "impreso": impreso.getvalue()[:4000],
                }
            )
            return

    # Serializar puede fallar por un tipo raro (un objeto de Python suelto en una
    # celda). Tiene que volver como error, no como excepción sin JSON: si el
    # runner muere sin escribir nada, el backend recibe basura en vez de un motivo.
    try:
        codificado = serializar(resultado)
    except Exception as error:
        responder(
            {
                "ok": False,
                "error": f"El resultado no se pudo convertir a tabla: "
                f"{type(error).__name__}: {error}",
                "impreso": impreso.getvalue()[:4000],
            }
        )
        return

    if len(codificado) > LIMITE_SALIDA:
        responder(
            {
                "ok": False,
                "error": f"El resultado tiene {len(resultado):,} filas y no entra en la "
                f"respuesta. Agregá o filtrá antes de devolverlo.",
                "impreso": impreso.getvalue()[:4000],
            }
        )
        return

    responder(
        {
            "ok": True,
            "resultado": codificado,
            "filas": len(resultado),
            "impreso": impreso.getvalue()[:4000],
        }
    )


if __name__ == "__main__":
    main()
