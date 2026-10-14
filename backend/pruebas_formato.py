"""Pruebas del saneado del HTML. No necesitan base de datos ni API.

Correr con:  backend\\.venv\\Scripts\\python.exe backend\\pruebas_formato.py

El HTML de la respuesta se renderiza con `dangerouslySetInnerHTML`, así que esto
es la única barrera. El vector realista no es el modelo poniéndose creativo: es
un valor de la tabla `sabana` — un identificador de prestador, un diagnóstico — que
traiga etiquetas y termine citado en la respuesta.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from formato import a_texto, sanear

fallos: list[str] = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre}  {detalle}")
        fallos.append(nombre)


print("Lo que sí tiene que pasar:")

salida = sanear("<p>El costo fue de <strong>$1.500</strong> en <em>enero</em>.</p>")
comprobar("párrafo con negrita y cursiva", salida.count("<strong>") == 1 and "<em>" in salida, salida)

salida = sanear("<ul><li>Hospitalario</li><li>Ambulatorio</li></ul>")
comprobar("listas", salida == "<ul><li>Hospitalario</li><li>Ambulatorio</li></ul>", salida)

salida = sanear("Primera línea<br>Segunda")
comprobar("el br sin cierre no rompe", "<br>" in salida and "</br>" not in salida, salida)

salida = sanear("<h4>Resumen</h4><p>Texto</p><code>SUM(x)</code>")
comprobar("h4 y code", "<h4>" in salida and "<code>" in salida, salida)

print("\nLo que NO tiene que pasar:")

salida = sanear("<p>Hola</p><script>alert(1)</script>")
comprobar("script se elimina", "script" not in salida and "alert" in salida, salida)

salida = sanear('<img src=x onerror="alert(1)">')
comprobar("img con onerror se elimina", "img" not in salida and "onerror" not in salida, salida)

salida = sanear('<a href="javascript:alert(1)">clic</a>')
comprobar("enlaces se eliminan", "href" not in salida and "javascript" not in salida, salida)

salida = sanear('<p onclick="robar()">Texto</p>')
comprobar("los atributos se descartan siempre", salida == "<p>Texto</p>", salida)

salida = sanear('<p style="position:fixed;top:0">Texto</p>')
comprobar("style se descarta", "style" not in salida, salida)

salida = sanear("<iframe src=http://malo></iframe><div>x</div>")
comprobar("iframe y div se eliminan", "iframe" not in salida and "<div>" not in salida, salida)

# El modelo no debe dibujar tablas: la tabla de datos la arma el frontend con
# los valores reales, y dejarlo duplicaba los mismos números dos veces.
salida = sanear("<table><tr><td>1</td></tr></table>")
comprobar("las tablas se eliminan", "<table" not in salida and "<td" not in salida, salida)

print("\nEl caso realista — un valor de la base con etiquetas adentro:")

sucio = "<p>El prestador con más costo fue <strong>CLINICA <script>alert(1)</script></strong>.</p>"
salida = sanear(sucio)
comprobar(
    "un nombre de prestador con script no ejecuta nada",
    "<script" not in salida and "CLINICA" in salida and "<strong>" in salida,
    salida,
)

comprobar("el texto suelto se escapa", sanear("5 < 10 y 20 > 3").startswith("5 &lt; 10"), sanear("5 < 10 y 20 > 3"))

print("\nRobustez:")

comprobar("HTML sin cerrar no rompe", sanear("<p>abierto") == "<p>abierto</p>", sanear("<p>abierto"))
comprobar("cadena vacía", sanear("") == "")
comprobar("texto plano pasa igual", sanear("solo texto") == "solo texto")

print("\nA texto plano (lo que va al historial):")

plano = a_texto("<p>Primero</p><ul><li>uno</li><li>dos</li></ul>")
comprobar("saca las etiquetas", "<" not in plano, plano)
comprobar("conserva las palabras", "Primero" in plano and "uno" in plano and "dos" in plano, plano)

print("\nLo que se le muestra al modelo del resultado:")

import consulta  # noqa: E402  (después de sys.path)

# 36 filas, 3 razas por 12 meses, ordenadas por costo. Con las categorías
# intercaladas, un modelo escribe que a la más chica le faltan meses. Los tiene.
regiones = ["Blanca", "Negra", "Hispana"]
# Los costos, únicos por fila, como en los datos reales: así la heurística los
# reconoce como medida y no los cuenta.
filas = [
    [r, m, 1_000_000.0 - i * 300_000 + m * 137.0]
    for i, r in enumerate(regiones)
    for m in range(1, 13)
]
filas.sort(key=lambda f: -f[2])
treinta_y_seis = {
    "sql": "",
    "columnas": ["raza", "mes", "costo"],
    "filas": filas,
    "total_filas": 36,
    "truncado": False,
    "error": None,
}

vista = consulta.para_el_modelo(treinta_y_seis)
comprobar("no se recorta un resultado de 36 filas", "RECORTE" not in vista, vista[-200:])
comprobar("se cuentan las filas por categoría", "Hispana (12 fila/s)" in vista, vista[-300:])
comprobar("se cuentan las filas por mes", "mes:" in vista, vista[-300:])
comprobar(
    "no se cuenta la medida, que tiene un valor por fila",
    "costo:" not in vista.split("Conteo de filas")[-1],
    vista[-300:],
)

# La heurística es "pocos valores distintos = dimensión". Una medida que
# casualmente tenga pocos valores (días cama: 1, 2, 3) también se cuenta. Se
# deja documentado a propósito: no molesta —es información cierta— y el costo
# son unos pocos tokens, pero conviene que no sorprenda a nadie.
pocos_valores = {
    **treinta_y_seis,
    "columnas": ["raza", "mes", "dias"],
    "filas": [[f[0], f[1], (indice % 3) + 1] for indice, f in enumerate(filas)],
}
comprobar(
    "una medida con pocos valores distintos también se cuenta (sabido, inofensivo)",
    "dias:" in consulta.para_el_modelo(pocos_valores),
    consulta.para_el_modelo(pocos_valores)[-200:],
)

muchas = {**treinta_y_seis, "filas": [[str(i), i, 1.0] for i in range(500)], "total_filas": 500}
vista = consulta.para_el_modelo(muchas)
comprobar("un resultado grande sí avisa que es un recorte", "RECORTE" in vista, vista[-200:])

una = {**treinta_y_seis, "filas": [["Blanca", 1, 5.0]], "total_filas": 1}
comprobar(
    "con una sola fila no se agrega conteo",
    "Conteo de filas" not in consulta.para_el_modelo(una),
    consulta.para_el_modelo(una),
)

print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallaron: {', '.join(fallos)}")
    sys.exit(1)
print("Todas las pruebas pasaron.")
