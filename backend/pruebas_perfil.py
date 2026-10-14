"""Pruebas de la lectura del resultado. No necesitan base de datos ni API.

Correr con:  backend\\.venv\\Scripts\\python.exe backend\\pruebas_perfil.py

Lo que se verifica es el "nivel 2" de afirmación del agente: que las
brechas y variaciones se calculen bien, y —tan importante como eso— que la
función se calle cuando no hay nada que decir.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import perfil

fallos: list[str] = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre}  {detalle}")
        fallos.append(nombre)


def tabla(columnas, filas) -> dict:
    return {
        "sql": "",
        "columnas": columnas,
        "filas": filas,
        "total_filas": len(filas),
        "truncado": False,
        "error": None,
    }


print("Un top de prestadores y sus brechas")

# Ante un top de prestadores, poder decir que el primero supera al segundo en 10%
# y al siguiente en 40%.
TOP = tabla(
    ["id_prestador", "costo"],
    [
        ["0100AB", 1_100_000.0],
        ["0200CD", 1_000_000.0],
        ["0300EF", 785_714.29],
        ["0400GH", 500_000.0],
    ],
)
p = perfil.perfilar(TOP)
r = p["ranking"]
comprobar("se detecta el líder", r["posiciones"][0]["nombre"] == "0100AB",
          str(r["posiciones"][0]))
comprobar(
    "el 1º supera al 2º en 10%",
    abs(r["brechas"][0]["relativa"] - 0.10) < 0.001,
    str(r["brechas"][0]),
)
comprobar(
    "y la diferencia también va en valor absoluto",
    abs(r["brechas"][0]["absoluta"] - 100_000.0) < 1,
    str(r["brechas"][0]),
)
comprobar(
    "el 2º supera al 3º en 27,3%",
    abs(r["brechas"][1]["relativa"] - 0.2727) < 0.001,
    str(r["brechas"][1]),
)
comprobar(
    "cada posición trae su participación",
    abs(r["posiciones"][0]["participacion"] - 0.3249) < 0.001,
    str(r["posiciones"][0]),
)
comprobar(
    "se calcula cuánto concentran los tres primeros",
    abs(r["concentracion"]["top3"] - 0.8523) < 0.001,
    str(r.get("concentracion")),
)
comprobar(
    "cuántas filas explican la mitad del total",
    r["filas_para_la_mitad"] == 2,
    str(r.get("filas_para_la_mitad")),
)
comprobar(
    "con solo 4 filas no se reporta el top 5 (sería una obviedad)",
    "top5" not in r["concentracion"],
    str(r["concentracion"]),
)

texto = perfil.para_el_modelo(p)
comprobar("el texto para el modelo trae las brechas", "10.0%" in texto, texto)
# Sin el total explícito, el redactor suma las filas de cabeza y da distinto en
# cada corrida para la misma pregunta.
comprobar(
    "el total va explícito, para que nadie lo sume a mano",
    "3,385,714.29" in texto and "no lo sumes" in texto,
    texto,
)
comprobar(
    "se le dice al modelo que NO recalcule",
    "no los recalcule" in texto,
    "es lo que evita que invente aritmética",
)

print("\nSeis meses pedidos: febrero contra enero")

# Si la pregunta pide el costo de seis meses, se puede decir que en febrero subió
# 12% respecto a enero. Los datos YA están en la tabla: no se consulta nada nuevo.
SEIS_MESES = tabla(
    ["anio", "mes", "costo"],
    [[2008, 9, 500_000.0], [2008, 10, 520_000.0], [2008, 11, 510_000.0],
     [2008, 12, 540_000.0], [2009, 1, 550_000.0], [2009, 2, 616_000.0]],
)
p = perfil.perfilar(SEIS_MESES)
s = p["serie"]
comprobar("el último período es febrero 2009", s["ultimo"]["periodo"] == "2009-2", str(s["ultimo"]))
comprobar("subió 12% contra el anterior", abs(s["variacion"] - 0.12) < 0.001, str(s["variacion"]))
comprobar("se compara contra el promedio previo", s["contra_promedio"] > 0, str(s["contra_promedio"]))
comprobar("se ubica el máximo", s["maximo"][0] == "2009-2", str(s["maximo"]))

# Las filas suelen venir ordenadas por la medida, no por el período: si no se
# reordena, "el último" sería el más caro y la variación saldría inventada.
DESORDENADO = tabla(
    ["anio", "mes", "costo"],
    [[2009, 2, 616_000.0], [2009, 1, 550_000.0], [2008, 12, 540_000.0],
     [2008, 11, 510_000.0], [2008, 10, 520_000.0], [2008, 9, 500_000.0]],
)
s2 = perfil.perfilar(DESORDENADO)["serie"]
comprobar(
    "se ordena por anio y mes aunque la tabla venga por costo",
    abs(s2["variacion"] - 0.12) < 0.001,
    str(s2),
)

# El caso que destapó un bug real: los mismos meses SIN la columna anio. Ordenados
# como números dan 1, 2, 9, 10, 11, 12, así que "el último" saldría diciembre y la
# variación sería falsa. Sin el año no se puede ordenar, así que no se dice nada.
SIN_ANIO = tabla(
    ["mes", "costo"],
    [[9, 500_000.0], [10, 520_000.0], [11, 510_000.0],
     [12, 540_000.0], [1, 550_000.0], [2, 616_000.0]],
)
comprobar(
    "una serie de meses que cruza el año, sin anio, no se interpreta",
    "serie" not in perfil.perfilar(SIN_ANIO),
    str(perfil.perfilar(SIN_ANIO).get("serie")),
)
comprobar(
    "pero meses seguidos dentro del mismo año sí",
    perfil.perfilar(
        tabla(["mes", "costo"], [[1, 100.0], [2, 110.0], [3, 130.0]])
    )["serie"]["ultimo"]["periodo"] == 3,
)

print("\nCallarse cuando no hay nada que decir:")

comprobar("un resultado de una fila no tiene lectura", perfil.perfilar(
    tabla(["costo"], [[245_512_790.0]])) == {}, "sin comparación posible")
comprobar("una tabla vacía tampoco", perfil.perfilar(tabla(["costo"], [])) == {})
comprobar("un resultado nulo tampoco", perfil.perfilar(None) == {})
comprobar("un error de consulta tampoco", perfil.perfilar(
    {**tabla(["costo"], [[1.0], [2.0]]), "error": "Unknown column"}) == {})
comprobar("sin ninguna medida numérica tampoco", perfil.perfilar(
    tabla(["ambito", "estado"], [["Hospitalario", "Texas"], ["Ambulatorio", "Florida"]])) == {})

# Diferencias chicas son ruido: decir que alguien "supera por 1%" no le mueve
# nada a quien decide, y llena la respuesta de observaciones vacías.
PAREJO = tabla(["ambito", "costo"], [["Hospitalario", 1_000_000.0], ["Ambulatorio", 990_000.0]])
comprobar(
    "una diferencia menor al mínimo no se comenta",
    "brechas" not in perfil.perfilar(PAREJO)["ranking"],
    str(perfil.perfilar(PAREJO)["ranking"]),
)

print("\nCasos que rompen la aritmética:")

comprobar(
    "un total en cero no divide",
    perfil.perfilar(tabla(["ambito", "costo"], [["A", 0.0], ["B", 0.0]])) == {},
    "sin esto sería una división por cero",
)
comprobar(
    "valores negativos (devoluciones) no producen brechas falsas",
    perfil.perfilar(tabla(["ambito", "costo"], [["A", -100.0], ["B", -200.0]])) == {},
)
NULOS = tabla(["ambito", "costo"], [["A", 1_000_000.0], ["B", None], ["C", 500_000.0]])
comprobar("las filas con nulo se saltean", perfil.perfilar(NULOS)["ranking"]["cuantos"] == 2,
          str(perfil.perfilar(NULOS).get("ranking")))

print("\nDos dimensiones a la vez: hay que juntar antes de leer")

# Costo por región Y por mes: cada región aparece dos veces y cada mes también.
# Comparar filas sueltas no significa nada — hay que sumar primero. Y sumar solo
# vale si la métrica es aditiva, que es la distinción de siempre.
MIXTO = tabla(
    ["ambito", "mes", "costo"],
    [["Hospitalario", 1, 3_000_000.0], ["Hospitalario", 2, 3_300_000.0],
     ["Ambulatorio", 1, 2_000_000.0], ["Ambulatorio", 2, 2_100_000.0]],
)
p = perfil.perfilar(MIXTO, metrica_es_aditiva=True)
comprobar("con métrica aditiva se leen las dos cosas", "ranking" in p and "serie" in p, str(list(p)))
comprobar("el mes no se toma como medida", p["columna_medida"] == "costo", p["columna_medida"])
comprobar(
    "las regiones se suman entre meses",
    p["ranking"]["cuantos"] == 2
    and abs(p["ranking"]["posiciones"][0]["valor"] - 6_300_000.0) < 1,
    str(p["ranking"]),
)
comprobar(
    "los meses se suman entre regiones",
    abs(p["serie"]["ultimo"]["valor"] - 5_400_000.0) < 1,
    str(p["serie"]),
)

# La misma tabla con una razón (frecuencia, costo por afiliado): sumar daría un
# número sin sentido. Es el error contra el que está construido todo el proyecto.
comprobar(
    "con métrica NO aditiva no se lee nada",
    perfil.perfilar(MIXTO, metrica_es_aditiva=False) == {},
    str(perfil.perfilar(MIXTO)),
)
comprobar(
    "y no aditiva es el default: sumar por accidente es el error caro",
    perfil.perfilar(MIXTO) == {},
)

print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallaron: {', '.join(fallos)}")
    sys.exit(1)
print("Todas las pruebas del perfil pasaron.")
