"""Ejecuta el código que escribe el modelo, adentro de un contenedor descartable.

Por qué un contenedor y no `exec()` en este proceso: este proceso tiene
tu `OPENAI_API_KEY`, tus credenciales de Azure y acceso completo a tu disco. Bloquear `import`
revisando el código no alcanza — pandas sola ya te da `to_csv` para escribir
archivos y `read_csv("http://...")` para salir a la red. Cerrar método por método
es una carrera que se pierde.

El contenedor, en cambio, no tiene ninguna de esas cosas:

- `--network none`: no hay salida a internet. Ni DNS. Nada.
- sin credenciales adentro: nunca ve la base de datos. Recibe DataFrames YA
  agregados — doce números, no tres millones de filas.
- `--read-only` + `--tmpfs /tmp`: no puede dejar nada escrito.
- `--memory` y `--pids-limit`: un cruce mal hecho no tumba el servidor.
- `--user 65534` (nobody) y `--cap-drop ALL`: sin privilegios.

El canal es stdin/stdout con JSON. Se eligió así en vez de montar un volumen
porque en Windows los bind mounts traen líos de traducción de rutas y de permisos
al correr como `nobody`. Y en JSON y no en parquet porque parquet necesita
`pyarrow` de los dos lados, y los datos que viajan son agregados de decenas de
filas: no justifica una dependencia nueva. De paso, las trazas quedan legibles.

Si Docker no está corriendo, esto falla y lo dice. **No hay plan B en proceso**:
un plan B silencioso que además es inseguro es peor que un error.
"""

import json
import subprocess
import uuid
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import TypedDict

import pandas as pd

IMAGEN = "analitica-sandbox"
# Absoluta, no relativa: el backend se levanta con `--app-dir backend` desde la
# raíz, pero `evaluacion.py` y las pruebas se corren desde otros directorios.
CARPETA_IMAGEN = str(Path(__file__).parent / "sandbox")

# Segundos de reloj. Si se pasa, se mata el contenedor y el error entra al ciclo
# de reparación como cualquier otro.
TIEMPO_LIMITE = 30

# Los flags que hacen del contenedor una caja y no una extensión de tu máquina.
# Cambiar cualquiera de estos es cambiar el modelo de amenaza: pensarlo dos veces.
ENCIERRO = [
    "--rm",
    "--interactive",
    "--network", "none",
    "--memory", "512m",
    "--memory-swap", "512m",  # sin esto, --memory permite swap y el límite se escapa
    "--cpus", "1",
    "--pids-limit", "64",
    "--read-only",
    "--tmpfs", "/tmp:size=64m",
    "--user", "65534:65534",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
]


class Ejecucion(TypedDict):
    ok: bool
    resultado: pd.DataFrame | None
    impreso: str
    error: str
    script: str


def _indice_como_columna(tabla: pd.DataFrame) -> pd.DataFrame:
    """Misma regla que `sandbox/runner.py`: el índice con etiquetas pasa a columna,
    el numérico sin nombre se descarta."""
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
        return tabla.reset_index(drop=True)


def _normalizar(tabla: pd.DataFrame) -> pd.DataFrame:
    """Deja la tabla en condiciones de viajar como JSON con esquema.

    Misma función que `sandbox/runner.py`: el índice con etiquetas pasa a columna,
    los nombres de columna pasan a texto y se desduplican, y los `Decimal` que
    pueda traer la base pasan a float. Está repetida a propósito — el contenedor
    no puede importar nada del backend, y duplicar treinta líneas es preferible a
    montarle código de afuera.
    """
    limpia = _indice_como_columna(tabla).copy()

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

    for nombre in limpia.columns:
        columna = limpia[nombre]
        if columna.dtype == object and columna.map(lambda v: isinstance(v, Decimal)).any():
            limpia[nombre] = columna.astype(float)
    return limpia


def _serializar(tabla: pd.DataFrame) -> str:
    """DataFrame -> JSON con esquema, para que los tipos sobrevivan el viaje."""
    return _normalizar(tabla).to_json(orient="table", index=False, date_format="iso")


def _deserializar(texto: str) -> pd.DataFrame:
    return pd.read_json(StringIO(texto), orient="table").reset_index(drop=True)


def _docker(*argumentos: str, entrada: str | None = None, tiempo: int = 20):
    return subprocess.run(
        ["docker", *argumentos],
        input=entrada,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=tiempo,
    )


def _matar(nombre: str) -> None:
    """Mata el contenedor si sigue vivo. Nunca lanza: es limpieza, no una operación."""
    try:
        _docker("kill", nombre, tiempo=15)
    except Exception:
        pass
    try:
        # `--rm` normalmente lo borra solo al morir, pero si el kill llegó tarde
        # o el contenedor quedó a medias, esto asegura que no se acumulen.
        _docker("rm", "--force", nombre, tiempo=15)
    except Exception:
        pass


def disponible() -> tuple[bool, str]:
    """¿Está Docker corriendo y la imagen construida? Devuelve (sí/no, motivo)."""
    try:
        prueba = _docker("image", "inspect", IMAGEN, tiempo=15)
    except FileNotFoundError:
        return False, "No encuentro el comando `docker`. ¿Está instalado?"
    except subprocess.TimeoutExpired:
        return False, "Docker no responde."

    if prueba.returncode == 0:
        return True, "listo"
    if "daemon" in (prueba.stderr or "").lower() or "pipe" in (prueba.stderr or "").lower():
        return False, "Docker no está corriendo. Abrí Docker Desktop."
    return False, (
        f"Falta construir la imagen. Corré:  docker build -t {IMAGEN} {CARPETA_IMAGEN}"
    )


def construir_imagen() -> tuple[bool, str]:
    """Construye la imagen. Tarda un par de minutos la primera vez."""
    try:
        salida = _docker("build", "-t", IMAGEN, CARPETA_IMAGEN, tiempo=600)
    except FileNotFoundError:
        return False, "No encuentro el comando `docker`."
    except subprocess.TimeoutExpired:
        return False, "La construcción de la imagen tardó demasiado."
    if salida.returncode != 0:
        return False, (salida.stderr or salida.stdout)[-1500:]
    return True, "Imagen construida."


def ejecutar(script: str, consultas: dict[str, pd.DataFrame]) -> Ejecucion:
    """Corre `script` en el contenedor con `consultas` disponibles.

    Adentro, el script tiene `pd`, `np` y el diccionario `consultas`, y debe
    dejar el resultado final en una variable llamada `resultado`.

    Nunca lanza: todo problema vuelve como `ok: False` con el error adentro, para
    que el nodo de reparación pueda leerlo.
    """
    vacio: Ejecucion = {
        "ok": False,
        "resultado": None,
        "impreso": "",
        "error": "",
        "script": script,
    }

    listo, motivo = disponible()
    if not listo:
        return {**vacio, "error": motivo}

    pedido = json.dumps(
        {"script": script, "datos": {n: _serializar(t) for n, t in consultas.items()}},
        ensure_ascii=False,
    )

    # El contenedor va con nombre propio porque hace falta para poder matarlo.
    # `subprocess.run(timeout=...)` mata al CLIENTE `docker run`, no al
    # contenedor: el daemon lo sigue corriendo. Medido — un `while True` dejaba
    # un contenedor quemando un núcleo entero de forma indefinida, y `--rm` no
    # ayuda porque solo limpia cuando el proceso termina, cosa que nunca pasa.
    nombre = f"analitica-{uuid.uuid4().hex[:12]}"

    try:
        salida = _docker(
            "run", "--name", nombre, *ENCIERRO, IMAGEN, entrada=pedido, tiempo=TIEMPO_LIMITE
        )
    except subprocess.TimeoutExpired:
        _matar(nombre)
        return {
            **vacio,
            "error": f"El código tardó más de {TIEMPO_LIMITE} segundos y se canceló. "
            f"Puede ser un bucle infinito o un cruce demasiado grande.",
        }
    except Exception as error:
        # Cualquier otra cosa que corte el `docker run` deja el contenedor vivo igual.
        _matar(nombre)
        return {**vacio, "error": f"No se pudo ejecutar el contenedor: {error}"}

    if salida.returncode != 0:
        # 137 es lo que devuelve el kernel cuando mata el proceso por memoria.
        if salida.returncode == 137:
            return {**vacio, "error": "El código se pasó de memoria y se canceló."}
        return {**vacio, "error": (salida.stderr or "El contenedor falló.")[-800:]}

    try:
        respuesta = json.loads(salida.stdout)
    except json.JSONDecodeError:
        return {**vacio, "error": f"Respuesta ilegible del contenedor: {salida.stdout[:400]}"}

    if not respuesta.get("ok"):
        return {
            **vacio,
            "error": respuesta.get("error", "Error desconocido."),
            "impreso": respuesta.get("impreso", ""),
        }

    tabla = _deserializar(respuesta["resultado"])
    return {
        "ok": True,
        "resultado": tabla,
        "impreso": respuesta.get("impreso", ""),
        "error": "",
        "script": script,
    }
