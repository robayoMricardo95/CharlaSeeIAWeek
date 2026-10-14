"""Pruebas del sandbox. Las de la caja necesitan Docker; el resto no.

Correr con:  backend\\.venv\\Scripts\\python.exe backend\\pruebas_aislamiento.py

Si Docker está apagado, las pruebas del contenedor se saltean y se avisa cuáles
faltaron. Nunca se dan por buenas.
"""

import io
import json
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "sandbox"))

import pandas as pd

import aislamiento
import runner

fallos: list[str] = []
salteadas: list[str] = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre}  {detalle}")
        fallos.append(nombre)


def contar_contenedores() -> int:
    """Cuántos contenedores del sandbox están corriendo ahora."""
    salida = subprocess.run(
        ["docker", "ps", "--quiet", "--filter", f"ancestor={aislamiento.IMAGEN}"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return len([linea for linea in salida.stdout.splitlines() if linea.strip()])


def correr_runner(script: str, datos: dict[str, pd.DataFrame]) -> dict:
    """Ejecuta el runner en este proceso, sin contenedor.

    Sirve para probar la lógica de serialización y de ejecución. NO prueba el
    aislamiento: eso solo se puede verificar con la caja de verdad.
    """
    pedido = json.dumps(
        {
            "script": script,
            "datos": {n: aislamiento._serializar(t) for n, t in datos.items()},
        }
    )
    capturado = io.StringIO()
    entrada_original = sys.stdin
    sys.stdin = io.StringIO(pedido)
    try:
        with redirect_stdout(capturado):
            runner.main()
    finally:
        sys.stdin = entrada_original
    return json.loads(capturado.getvalue())


RECLAMOS = pd.DataFrame({"mes": [1, 2, 3], "reclamos": [1000, 1100, 900]})
AFILIADOS = pd.DataFrame({"mes": [1, 2, 3], "afiliados": [400, 500, 300]})

print("El runner, sin contenedor (lógica de ejecución y serialización):")

# El caso típico: frecuencia = reclamos / afiliados distintos.
# Es una razón NO aditiva, imposible en un solo SELECT agrupado.
salida = correr_runner(
    """
juntas = consultas['reclamos'].merge(consultas['afiliados'], on='mes')
juntas['frecuencia'] = juntas['reclamos'] / juntas['afiliados']
resultado = juntas[['mes', 'frecuencia']]
""",
    {"reclamos": RECLAMOS, "afiliados": AFILIADOS},
)
comprobar("un cruce de dos consultas devuelve tabla", salida["ok"], str(salida.get("error")))
if salida["ok"]:
    tabla = aislamiento._deserializar(salida["resultado"])
    comprobar("la frecuencia se calculó bien", abs(tabla["frecuencia"][0] - 2.5) < 0.001, str(tabla))
    comprobar("vuelven las 3 filas", len(tabla) == 3, str(len(tabla)))

salida = correr_runner("resultado = 42", {})
comprobar("un escalar se normaliza a tabla", salida["ok"], str(salida.get("error")))

# Devolver un dict con valores nombrados es lo natural para el modelo. Antes
# terminaba entero adentro de UNA celda.
salida = correr_runner(
    "resultado = {'reclamos': 1000, 'afiliados': 400, 'frecuencia': 2.5}", {}
)
if salida["ok"]:
    tabla = aislamiento._deserializar(salida["resultado"])
    comprobar(
        "un dict se abre en columnas, no queda en una celda",
        list(tabla.columns) == ["reclamos", "afiliados", "frecuencia"] and len(tabla) == 1,
        str(list(tabla.columns)) + " " + str(tabla.values.tolist()),
    )
    comprobar(
        "los valores del dict quedan como números",
        abs(float(tabla["frecuencia"][0]) - 2.5) < 0.001,
        str(tabla.values.tolist()),
    )
else:
    comprobar("un dict se abre en columnas, no queda en una celda", False, str(salida["error"]))

salida = correr_runner("resultado = [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]", {})
if salida["ok"]:
    tabla = aislamiento._deserializar(salida["resultado"])
    comprobar(
        "una lista de dicts es una tabla de dos filas",
        list(tabla.columns) == ["a", "b"] and len(tabla) == 2,
        str(tabla.values.tolist()),
    )
else:
    comprobar("una lista de dicts es una tabla de dos filas", False, str(salida["error"]))

salida = correr_runner("resultado = [10, 20, 30]", {})
if salida["ok"]:
    tabla = aislamiento._deserializar(salida["resultado"])
    comprobar("una lista de números es una columna", len(tabla) == 3, str(tabla.values.tolist()))
else:
    comprobar("una lista de números es una columna", False, str(salida["error"]))

salida = correr_runner("resultado = consultas['afiliados']['afiliados'].sum()", {"afiliados": AFILIADOS})
comprobar("una Serie/escalar de pandas también", salida["ok"], str(salida.get("error")))

print("\nErrores que tienen que volver como dato, no como excepción:")

salida = correr_runner("resultado = consultas['no_existe']", {})
comprobar("una clave inexistente vuelve como error", not salida["ok"] and "KeyError" in salida["error"], str(salida))

salida = correr_runner("esto no es python valido !!", {})
comprobar("un error de sintaxis vuelve como error", not salida["ok"], str(salida))

salida = correr_runner("x = 1", {})
comprobar(
    "olvidarse `resultado` se avisa claro",
    not salida["ok"] and "resultado" in salida["error"],
    str(salida),
)

salida = correr_runner("print('hola'); resultado = 1", {})
comprobar(
    "lo que imprime el script no ensucia el JSON",
    salida["ok"] and "hola" in salida["impreso"],
    str(salida),
)

print("\nTablas raras que el modelo produce sin querer:")

# Visto contra la API: el modelo deja los grupos en el índice con `pivot` y no
# hace `reset_index()`. Si el índice se descarta, la respuesta pierde los nombres.
GRUPOS = pd.DataFrame({
    "grupo_enfermedad": ["Neoplasias", "Neoplasias", "Trastornos mentales", "Trastornos mentales"],
    "anio": [2008, 2009, 2008, 2009],
    "pagado": [100.0, 120.0, 50.0, 40.0],
})
salida = correr_runner(
    "p = consultas['g'].pivot(index='grupo_enfermedad', columns='anio', values='pagado')\n"
    "p['diferencia'] = p[2009] - p[2008]\n"
    "resultado = p.sort_values('diferencia', ascending=False)",
    {"g": GRUPOS},
)
if salida["ok"]:
    tabla = aislamiento._deserializar(salida["resultado"])
    comprobar("un pivot sin reset_index conserva los nombres como columna",
              list(tabla["grupo_enfermedad"]) == ["Neoplasias", "Trastornos mentales"],
              str(tabla.to_dict("list")))
else:
    comprobar("un pivot sin reset_index conserva los nombres como columna", False, salida["error"])

salida = correr_runner("resultado = consultas['g'].set_index(['grupo_enfermedad', 'anio'])", {"g": GRUPOS})
comprobar("un índice de dos niveles vuelve como dos columnas",
          salida["ok"] and list(aislamiento._deserializar(salida["resultado"]).columns)
          == ["grupo_enfermedad", "anio", "pagado"], str(salida.get("resultado") or salida.get("error"))[:200])

# Lo que NO hay que convertir: el índice numérico que deja un filtro o un orden.
salida = correr_runner(
    "resultado = consultas['g'][consultas['g']['pagado'] > 45].sort_values('pagado')", {"g": GRUPOS}
)
comprobar("un filtro no agrega una columna 'index'",
          salida["ok"] and "index" not in aislamiento._deserializar(salida["resultado"]).columns,
          str(salida.get("resultado") or salida.get("error"))[:200])

# Y si el índice se llama igual que una columna, la columna manda y no se rompe.
salida = correr_runner(
    "g = consultas['g']\nresultado = g.set_index(g['anio'].rename('anio'))", {"g": GRUPOS}
)
comprobar("un índice con el nombre de una columna no rompe", salida["ok"], str(salida.get("error")))

# `pd.concat(axis=1)` duplica nombres, y ahí `tabla[columna]` deja de devolver
# una Serie. Antes esto reventaba el runner sin dejar JSON, y el backend recibía
# basura en vez de un motivo.
salida = correr_runner("a = consultas['x']\nresultado = pd.concat([a, a], axis=1)", {"x": AFILIADOS})
comprobar("columnas duplicadas se desduplican", salida["ok"], str(salida.get("error")))
if salida["ok"]:
    comprobar(
        "el nombre repetido queda distinguible",
        "mes_1" in list(aislamiento._deserializar(salida["resultado"]).columns),
        str(list(aislamiento._deserializar(salida["resultado"]).columns)),
    )

salida = correr_runner("resultado = pd.DataFrame([[1],[2]])", {})
comprobar("columnas con nombre numérico sobreviven el viaje", salida["ok"], str(salida.get("error")))

salida = correr_runner("resultado = pd.DataFrame({'o': [object()]})", {})
comprobar("un objeto no serializable no rompe el runner", isinstance(salida.get("ok"), bool), str(salida))

salida = correr_runner("resultado = pd.DataFrame({'a': []})", {})
comprobar("una tabla vacía viaja bien", salida["ok"], str(salida.get("error")))

salida = correr_runner("resultado = pd.DataFrame({'a': [1.0, float('nan')]})", {})
comprobar("los NaN sobreviven el viaje", salida["ok"], str(salida.get("error")))

print("\nEstado de Docker:")
listo, motivo = aislamiento.disponible()
print(f"  {motivo}")

if not listo:
    salteadas = [
        "el contenedor ejecuta y devuelve",
        "no hay red adentro",
        "no puede escribir en disco",
        "un bucle infinito se corta por timeout",
        "no se filtran claves al contenedor",
        "no ve los archivos del proyecto",
        "no puede llegar a un servicio del host",
        "el límite de memoria lo mata",
    ]
    print("\n  Las pruebas del aislamiento real se saltearon.")
    print(f"  Para correrlas: docker build -t {aislamiento.IMAGEN} {aislamiento.CARPETA_IMAGEN}")
else:
    print("\nLa caja de verdad:")

    ejecucion = aislamiento.ejecutar(
        "resultado = consultas['a'].assign(doble=consultas['a']['n'] * 2)",
        {"a": pd.DataFrame({"n": [1, 2, 3]})},
    )
    comprobar("el contenedor ejecuta y devuelve", ejecucion["ok"], ejecucion["error"])
    if ejecucion["ok"]:
        comprobar("la cuenta llegó bien", list(ejecucion["resultado"]["doble"]) == [2, 4, 6])

    ejecucion = aislamiento.ejecutar(
        "import urllib.request\n"
        "resultado = urllib.request.urlopen('http://example.com', timeout=5).status",
        {},
    )
    comprobar("no hay red adentro", not ejecucion["ok"], str(ejecucion["resultado"]))

    ejecucion = aislamiento.ejecutar("open('/prueba.txt','w').write('x')\nresultado = 1", {})
    comprobar("no puede escribir en el disco del contenedor", not ejecucion["ok"], ejecucion["error"])

    vivos_antes = contar_contenedores()
    ejecucion = aislamiento.ejecutar("while True:\n    pass\nresultado = 1", {})
    comprobar(
        "un bucle infinito se corta por timeout",
        not ejecucion["ok"] and "segundos" in ejecucion["error"],
        ejecucion["error"],
    )
    # Regresión de un bug real: `subprocess.run(timeout=...)` mata al cliente
    # `docker run`, no al contenedor. Sin matarlo a mano quedaban zombis
    # quemando un núcleo cada uno — se encontraron cinco, cuatro con 4 horas.
    time.sleep(3)
    comprobar(
        "el timeout no deja contenedores zombis",
        contar_contenedores() <= vivos_antes,
        f"antes {vivos_antes}, después {contar_contenedores()}",
    )

    # Lo que de verdad importa: que adentro no haya nada nuestro que robar.
    # Se buscan nuestros secretos por nombre. `GPG_KEY` sí está y no molesta:
    # viene de la imagen base de Python, es la clave pública con la que se
    # verifica el propio Python, y es información pública.
    NUESTROS = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "LANGSMITH_API_KEY"]
    ejecucion = aislamiento.ejecutar(
        "import os\n"
        f"resultado = str([k for k in {NUESTROS!r} if k in os.environ])",
        {},
    )
    comprobar(
        "no se filtran nuestras claves al contenedor",
        ejecucion["ok"] and ejecucion["resultado"].iloc[0, 0] == "[]",
        str(ejecucion["resultado"].iloc[0, 0]) if ejecucion["ok"] else ejecucion["error"],
    )

    ejecucion = aislamiento.ejecutar(
        "import os\nresultado = os.path.exists('/backend/.env') or os.path.exists('/.env')", {}
    )
    comprobar(
        "no ve los archivos del proyecto",
        ejecucion["ok"] and not bool(ejecucion["resultado"].iloc[0, 0]),
        str(ejecucion.get("error") or ejecucion["resultado"].iloc[0, 0]),
    )

    ejecucion = aislamiento.ejecutar(
        "import socket\n"
        "s = socket.socket(); s.settimeout(3)\n"
        "resultado = s.connect_ex(('host.docker.internal', 3306))",
        {},
    )
    comprobar(
        "no puede llegar a un servicio del host", not ejecucion["ok"], str(ejecucion.get("resultado"))
    )

    ejecucion = aislamiento.ejecutar("x = bytearray(900 * 1024 * 1024)\nresultado = len(x)", {})
    comprobar("el límite de memoria lo mata", not ejecucion["ok"], ejecucion["error"])

print()
if salteadas:
    print(f"{len(salteadas)} prueba(s) SALTEADAS (falta Docker): {', '.join(salteadas)}")
if fallos:
    print(f"{len(fallos)} prueba(s) fallaron: {', '.join(fallos)}")
    sys.exit(1)
print(f"{'Las que se pudieron correr pasaron.' if salteadas else 'Todas las pruebas pasaron.'}")
