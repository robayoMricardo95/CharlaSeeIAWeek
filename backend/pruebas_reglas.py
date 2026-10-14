"""Pruebas del armado del SQL y de los chequeos mecánicos del plan.

Correr con:  backend\\.venv\\Scripts\\python.exe backend\\pruebas_reglas.py

Las secciones de armado no necesitan base de datos. La última sección ejecuta el
SQL armado contra `datos_basePublica/reclamos.db` y lo compara con las cifras de
control de la sábana; si la base no está, se saltea y lo dice.

Cada prueba nace de un modo de fallar concreto, no de un caso imaginado. Si
alguna vez vuelve a fallar algo en producción, el arreglo empieza agregando la
prueba acá.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agente.decisiones import ConsultaBase, Filtro, Medida
from agente.esquema_sabana import PERMITIDAS
from agente.reglas import (
    LIMITE_SONDEO,
    ErrorDeConstruccion,
    construir_sql,
    contrastar_premisa,
    premisa_en_la_pregunta,
    preparar_sondeo,
    verificar_medidas_repetidas,
    verificar_participacion,
    verificar_periodo,
    verificar_plan_no_vacio,
    verificar_premisa,
)


def consulta(**cambios) -> ConsultaBase:
    campos = dict(
        nombre="c1",
        proposito="prueba",
        medidas=[Medida(agregacion="SUM", columna="monto_pagado", alias="pagado")],
        dimensiones=[],
        filtros=[],
        orden_por="",
        orden_descendente=False,
        limite=0,
    )
    campos.update(cambios)
    return ConsultaBase(**campos)


def filtro(columna, valores, operador="=") -> Filtro:
    return Filtro(
        columna=columna,
        operador=operador,
        valores=valores,
        origen="pregunta",
        justificacion="pedido en la pregunta",
    )


def armar(**cambios) -> str:
    return construir_sql(consulta(**cambios), PERMITIDAS)


fallos: list[str] = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre}  {detalle}")
        fallos.append(nombre)


print("Sin reglas fijas: se ejecuta exactamente lo que se pidió:")

sql = armar(dimensiones=["mes"], filtros=[filtro("anio", ["2009"])])
comprobar("lee de la sábana", "FROM sabana" in sql, sql)
comprobar("el único filtro es el pedido", sql.count("WHERE") == 1 and "AND" not in sql, sql)
comprobar("no aparece ningún filtro que nadie pidió", "NOT IN" not in sql and "!=" not in sql, sql)

sql = armar(dimensiones=["grupo_enfermedad"])
comprobar("agrupar por texto no agrega filtros de relleno", "WHERE" not in sql, sql)

print("\nTipos de SQLite: un número entre comillas no encuentra nada:")

# En SQLite `anio = '2009'` compara un entero contra un texto y devuelve cero
# filas sin error. La consulta "funciona" y el número es falso.
sql = armar(filtros=[filtro("anio", ["2009"])])
comprobar("el año va sin comillas", '"anio" = 2009' in sql, sql)
sql = armar(filtros=[filtro("dias_estancia", ["3"], ">=")])
comprobar("un entero con operador también", '"dias_estancia" >= 3' in sql, sql)
sql = armar(filtros=[filtro("ambito", ["Hospitalario"])])
comprobar("el texto sí va entre comillas", "\"ambito\" = 'Hospitalario'" in sql, sql)
sql = armar(filtros=[filtro("codigo_condado", ["200"])])
comprobar("un código que parece número pero es texto va entre comillas", "'200'" in sql, sql)
sql = armar(filtros=[filtro("anio", ["2008", "2009"], "IN")])
comprobar("IN con números sin comillas", '"anio" IN (2008, 2009)' in sql, sql)

print("\nVarias medidas en una sola extracción:")

# Pagado y afiliados juntos es UNA extracción, no dos. La razón entre ambos se
# calcula después en Python, que es donde vive lo no aditivo.
sql = armar(
    medidas=[
        Medida(agregacion="SUM", columna="monto_pagado", alias="pagado"),
        Medida(agregacion="COUNT_DISTINCT", columna="id_afiliado", alias="afiliados"),
    ],
    dimensiones=["mes"],
)
comprobar("dos medidas en la misma consulta", '"pagado"' in sql and '"afiliados"' in sql, sql)
comprobar("COUNT DISTINCT se arma bien", 'COUNT(DISTINCT "id_afiliado")' in sql, sql)

sql = armar(medidas=[Medida(agregacion="COUNT", columna="", alias="reclamos")])
comprobar("COUNT(*) no necesita columna", 'COUNT(*) AS "reclamos"' in sql, sql)

# Si el modelo pide `MAX(mes) AS mes` agrupando por `mes`, el resultado vuelve
# con dos columnas llamadas igual. Eso es lo que hace que `tabla[columna]`
# devuelva un DataFrame en vez de una Serie y se caiga el cruce.
sql = armar(
    medidas=[Medida(agregacion="MAX", columna="mes", alias="mes")],
    dimensiones=["anio", "mes"],
)
comprobar("un alias de medida que choca con una dimensión se renombra", 'AS "mes_2"' in sql, sql)

sql = armar(
    medidas=[
        Medida(agregacion="SUM", columna="monto_pagado", alias="pagado"),
        Medida(agregacion="MAX", columna="monto_pagado", alias="pagado"),
    ],
)
comprobar("dos medidas con el mismo alias también se distinguen", 'AS "pagado_2"' in sql, sql)

print("\nOrden y límite:")

sql = armar(dimensiones=["id_prestador"], orden_por="pagado", orden_descendente=True, limite=10)
comprobar("ordena por el alias de la medida", 'ORDER BY "pagado" DESC' in sql, sql)
comprobar("aplica el límite", "LIMIT 10" in sql, sql)

sql = armar(dimensiones=["mes"])
comprobar("sin orden pedido, ordena por dimensión", 'ORDER BY "mes"' in sql, sql)

try:
    armar(orden_por="columna_que_no_existe")
    comprobar("ordenar por algo inexistente se rechaza", False)
except ErrorDeConstruccion:
    comprobar("ordenar por algo inexistente se rechaza", True)

# El último período son DOS columnas. Sin poder ordenar por las dos, el modelo cae
# en pedir MAX(anio) y MAX(mes) como medidas sueltas, que devuelve el año más alto
# pegado al mes más alto de cualquier año.
sql = armar(
    medidas=[Medida(agregacion="COUNT", columna="", alias="reclamos")],
    dimensiones=["anio", "mes"],
    orden_por="anio, mes",
    orden_descendente=True,
    limite=1,
)
comprobar("se puede ordenar por dos columnas", 'ORDER BY "anio" DESC, "mes" DESC' in sql, sql)
comprobar("el último período se resuelve con LIMIT 1", "LIMIT 1" in sql, sql)

# En cuanto se le permite una lista, el modelo escribe la dirección adentro —
# "anio DESC, mes DESC" — porque así se escribe en SQL.
sql = armar(
    dimensiones=["anio", "mes"],
    orden_por="anio DESC, mes DESC",
    orden_descendente=True,
)
comprobar("la dirección escrita dentro del texto no rompe nada",
          'ORDER BY "anio" DESC, "mes" DESC' in sql, sql)
sql = armar(dimensiones=["mes"], orden_por="pagado asc", orden_descendente=False)
comprobar("también en minúsculas y con una sola columna", 'ORDER BY "pagado"' in sql, sql)
try:
    armar(dimensiones=["anio"], orden_por="anio, inventada")
    comprobar("una columna inexistente en la lista de orden se rechaza", False)
except ErrorDeConstruccion:
    comprobar("una columna inexistente en la lista de orden se rechaza", True)

print("\nSeguridad del armado:")

try:
    armar(filtros=[filtro("columna_inventada", ["x"])])
    comprobar("una columna inexistente es rechazada", False)
except ErrorDeConstruccion:
    comprobar("una columna inexistente es rechazada", True)

try:
    armar(medidas=[Medida(agregacion="SUM", columna="no_existe", alias="x")])
    comprobar("una medida sobre columna inexistente es rechazada", False)
except ErrorDeConstruccion:
    comprobar("una medida sobre columna inexistente es rechazada", True)

sql = armar(filtros=[filtro("estado", ["O'Brien'; DROP TABLE sabana --"])])
comprobar("las comillas del valor se escapan", "O''Brien" in sql, sql)

# En SQLite la barra invertida no escapa nada. Duplicarla (como pide MySQL)
# cambiaría el valor buscado y la consulta no encontraría lo que existe.
sql = armar(filtros=[filtro("estado", ["a\\b"])])
comprobar("la barra invertida queda tal cual", "'a\\b'" in sql, sql)

sql = armar(filtros=[filtro("procedimiento_principal", ["x"], "!=")])
comprobar("!= deja pasar los NULL", "IS NULL OR" in sql, sql)

print()
print("Un plan de datos sin consultas y sin pregunta es un callejón sin salida:")

VACIO = {"intencion": "datos", "consultas": [], "sondeos": [], "necesita_aclaracion": False}
comprobar("un plan sin nada se rechaza",
          any(h["gravedad"] == "error" for h in verificar_plan_no_vacio(VACIO)),
          str(verificar_plan_no_vacio(VACIO)))
comprobar("con consultas pasa",
          verificar_plan_no_vacio({**VACIO, "consultas": [{"nombre": "c"}]}) == [])
comprobar("con un sondeo pendiente también",
          verificar_plan_no_vacio({**VACIO, "sondeos": [{"nombre": "s"}]}) == [])
comprobar("pedir una aclaración es una salida válida",
          verificar_plan_no_vacio({**VACIO, "necesita_aclaracion": True}) == [])
comprobar("un saludo no necesita consultas",
          verificar_plan_no_vacio({**VACIO, "intencion": "conversacion"}) == [])

print()
print("Dos medidas idénticas en una consulta dan el mismo número:")

# El caso visto contra la API: copiar el CASE WHEN del diccionario como dos
# medidas iguales con alias de año. Las dos suman lo mismo y la variación da 0.
def medidas_plan(*medidas) -> dict:
    return {"consultas": [{"nombre": "variacion", "medidas": [
        {"agregacion": a, "columna": c, "alias": al} for a, c, al in medidas]}]}


hallazgos = verificar_medidas_repetidas(
    medidas_plan(("SUM", "monto_pagado", "pagado_2008"), ("SUM", "monto_pagado", "pagado_2009"))
)
comprobar("dos SUM(monto_pagado) con alias de año se rechazan",
          any(h["gravedad"] == "error" for h in hallazgos), str(hallazgos))
comprobar("la corrección dice que se agrupe por la columna que distingue",
          bool(hallazgos) and "agrupa" in hallazgos[0]["correccion"], str(hallazgos))
comprobar("medidas distintas pasan",
          verificar_medidas_repetidas(medidas_plan(("SUM", "monto_pagado", "pagado"),
                                                   ("COUNT", "", "reclamos"))) == [])
comprobar("la misma columna con otra agregación pasa",
          verificar_medidas_repetidas(medidas_plan(("SUM", "monto_pagado", "pagado"),
                                                   ("AVG", "monto_pagado", "promedio"))) == [])
comprobar("un plan sin consultas no se toca", verificar_medidas_repetidas({"consultas": []}) == [])

print()
print("Premisa: lo que la pregunta da por hecho se comprueba con el total:")

POR_GRUPO = {"nombre": "grupos", "dimensiones": ["grupo_enfermedad", "anio"],
             "medidas": [{"agregacion": "SUM", "columna": "monto_pagado", "alias": "pagado"}]}
TOTAL = {"nombre": "total", "dimensiones": ["anio"],
         "medidas": [{"agregacion": "SUM", "columna": "monto_pagado", "alias": "pagado"}]}
ALZA = {"premisa_direccion": "sube", "premisa": "el pagado hospitalario subió"}

hallazgos = verificar_premisa({**ALZA, "consultas": [POR_GRUPO]})
comprobar("una premisa sin el total por período se rechaza",
          any(h["gravedad"] == "error" for h in hallazgos), str(hallazgos))
comprobar("con el total por año pasa", verificar_premisa({**ALZA, "consultas": [POR_GRUPO, TOTAL]}) == [])
comprobar("con el total por año y mes también",
          verificar_premisa({**ALZA, "consultas": [{**TOTAL, "dimensiones": ["anio", "mes"]}]}) == [])
comprobar("sin premisa no se exige nada",
          verificar_premisa({"premisa_direccion": "", "consultas": [POR_GRUPO]}) == [])

# Visto contra la API: el modelo no completó `premisa_direccion` aunque la
# pregunta decía "el alza". La red de seguridad lee la pregunta.
P_ALZA = "¿En qué grupos de enfermedad se concentró el alza del costo hospitalario de 2009?"
hallazgos = verificar_premisa({"premisa_direccion": "", "consultas": [POR_GRUPO]}, P_ALZA)
comprobar("si la pregunta dice 'alza' y el plan no la declara, se rechaza",
          any(h["gravedad"] == "error" for h in hallazgos), str(hallazgos))
comprobar("la corrección dice qué dirección declarar",
          bool(hallazgos) and "'sube'" in hallazgos[0]["correccion"], str(hallazgos))
comprobar("declarada y con el total, la misma pregunta pasa",
          verificar_premisa({**ALZA, "consultas": [POR_GRUPO, TOTAL]}, P_ALZA) == [])
for texto, esperado in [
    (P_ALZA, "sube"),
    ("¿Qué explica el aumento del costo ambulatorio?", "sube"),
    ("¿Por qué creció el pagado en 2009?", "sube"),
    ("¿A qué se debe la caída de reclamos hospitalarios?", "baja"),
    ("¿Dónde se concentró la reducción del costo?", "baja"),
    ("¿Cómo cambió el costo entre 2008 y 2009?", ""),
    ("¿Cuánto se pagó bajo el ámbito hospitalario?", ""),
    ("¿Hubo alza o caída en 2009?", ""),
]:
    obtenido = premisa_en_la_pregunta(texto)
    comprobar(f"premisa de «{texto}» = {esperado or 'ninguna'}", obtenido == esperado, obtenido)

# Las cifras reales: el pagado hospitalario BAJÓ de 2008 a 2009.
extracciones = {"total": {"columnas": ["anio", "pagado"], "error": None,
                          "filas": [[2009, 245512790.0], [2008, 258063920.0]]}}
contraste = contrastar_premisa({**ALZA, "consultas": [POR_GRUPO, TOTAL]}, extracciones)
comprobar("el alza supuesta se desmiente", contraste.get("se_cumple") is False and contraste.get("real") == "baja",
          str(contraste))
comprobar("ordena por período aunque las filas vengan desordenadas",
          contraste.get("previo", {}).get("periodo") == "2008" and contraste.get("ultimo", {}).get("periodo") == "2009",
          str(contraste))
comprobar("la variación es -4,86 %", abs(contraste.get("variacion", 0) + 0.048636) < 1e-5, str(contraste))
comprobar("una caída supuesta sobre esos datos se confirma",
          contrastar_premisa({"premisa_direccion": "baja", "consultas": [TOTAL]}, extracciones).get("se_cumple") is True)
comprobar("sin premisa no se contrasta nada",
          contrastar_premisa({"premisa_direccion": "", "consultas": [TOTAL]}, extracciones) == {})
comprobar("si la consulta del total falló, no se inventa un contraste",
          contrastar_premisa({**ALZA, "consultas": [TOTAL]},
                             {"total": {"columnas": [], "filas": [], "error": "x"}}) == {})

print()
print("El período pedido tiene que existir en la tabla:")

# Un modelo resuelve "el mes pasado" contra el calendario. La sábana termina en
# diciembre de 2009: la consulta corre bien, devuelve vacío, y el modelo explica
# el vacío con una causa inventada.
DESDE, HASTA = 200801, 200912


def plan_periodo(anios, meses) -> dict:
    filtros = [{"columna": "anio", "operador": "=", "valores": [str(a)]} for a in anios]
    filtros += [{"columna": "mes", "operador": "=", "valores": [str(m)]} for m in meses]
    return {"consultas": [{"nombre": "pagado", "filtros": filtros}]}


hallazgos = verificar_periodo(plan_periodo([2026], [8]), DESDE, HASTA)
comprobar("un mes fuera del rango cargado se rechaza",
          any(h["gravedad"] == "error" for h in hallazgos), str(hallazgos))
comprobar("el hallazgo dice cuál es el último mes real",
          bool(hallazgos) and "2009-12" in hallazgos[0]["correccion"], str(hallazgos))
comprobar("un mes dentro del rango pasa limpio",
          verificar_periodo(plan_periodo([2009], [12]), DESDE, HASTA) == [])
comprobar("el primer mes del rango también",
          verificar_periodo(plan_periodo([2008], [1]), DESDE, HASTA) == [])
comprobar("un acumulado enero-marzo 2009 pasa",
          verificar_periodo(plan_periodo([2009], [1, 2, 3]), DESDE, HASTA) == [])

# Un año entero también se puede comprobar: "¿cuánto se pagó en 2010?" no tiene
# ningún mes en la tabla.
hallazgos = verificar_periodo(plan_periodo([2010], []), DESDE, HASTA)
comprobar("un año fuera del rango se rechaza aunque no traiga mes",
          any(h["gravedad"] == "error" for h in hallazgos), str(hallazgos))
comprobar("un año dentro del rango sin mes pasa",
          verificar_periodo(plan_periodo([2009], []), DESDE, HASTA) == [])
comprobar("sin filtro de año no se marca nada",
          verificar_periodo(plan_periodo([], [8]), DESDE, HASTA) == [])
comprobar("una pregunta sin período no se toca",
          verificar_periodo({"consultas": [{"nombre": "c", "filtros": []}]}, DESDE, HASTA) == [])

print()
print("Parte y todo: que un porcentaje tenga denominador:")


def plan(consultas, **cambios) -> dict:
    """El mínimo de un plan para `verificar_participacion`: filtros por consulta."""
    campos = {
        "pide_participacion": True,
        "dimension_de_la_parte": "grupo_enfermedad",
        "consultas": consultas,
    }
    campos.update(cambios)
    return campos


def cruda(nombre, filtros) -> dict:
    return {
        "nombre": nombre,
        "filtros": [
            {"columna": c, "operador": "=", "valores": list(v)} for c, v in filtros
        ],
    }


HOSP = ("ambito", ["Hospitalario"])
AMB = ("ambito", ["Ambulatorio"])
CIRC = ("grupo_enfermedad", ["Enfermedades del aparato circulatorio"])

# Se pregunta qué porcentaje del costo HOSPITALARIO es de circulatorio, y el plan
# compara hospitalario-circulatorio contra ambulatorio-circulatorio. Las dos
# consultas fijan el grupo, así que el total hospitalario no existe en ningún lado.
invertido = plan([cruda("hosp", [HOSP, CIRC]), cruda("amb", [AMB, CIRC])])
hallazgos = verificar_participacion(invertido)
comprobar(
    "se detecta que ninguna consulta deja libre la dimensión de la parte",
    any(h["gravedad"] == "error" for h in hallazgos),
    str(hallazgos),
)
comprobar(
    "el hallazgo nombra la columna que hay que soltar",
    bool(hallazgos) and "grupo_enfermedad" in hallazgos[0]["que_falta"],
    str(hallazgos),
)

# Lo correcto: mismo universo en los dos lados, el grupo solo en el numerador.
correcto = plan([cruda("parte", [HOSP, CIRC]), cruda("todo", [HOSP])])
comprobar(
    "el plan correcto (numerador con grupo, denominador sin) pasa limpio",
    verificar_participacion(correcto) == [],
    str(verificar_participacion(correcto)),
)

# Una sola consulta agrupada por el grupo, sin filtrarlo, da todas las
# participaciones de una. No tiene numerador y está bien.
agrupado = plan([cruda("todo", [HOSP])])
comprobar(
    "una sola consulta agrupada por la parte también es válida",
    verificar_participacion(agrupado) == [],
    str(verificar_participacion(agrupado)),
)

# Universos distintos: el numerador es hospitalario y el total es ambulatorio.
# Puede ser deliberado, así que se avisa pero no se bloquea.
desparejo = plan([cruda("parte", [HOSP, CIRC]), cruda("todo", [AMB])])
hallazgos = verificar_participacion(desparejo)
comprobar(
    "numerador y denominador con universos distintos se avisa, no se bloquea",
    bool(hallazgos) and all(h["gravedad"] == "aviso" for h in hallazgos),
    str(hallazgos),
)

comprobar(
    "sin porcentaje pedido no se revisa nada",
    verificar_participacion({**invertido, "pide_participacion": False}) == [],
)
comprobar(
    "pedir un porcentaje sin decir cuál es la parte es error",
    any(
        h["gravedad"] == "error"
        for h in verificar_participacion({**correcto, "dimension_de_la_parte": ""})
    ),
)

print()
print("Sondeos: reconocimiento, no extracción:")

comprobar(
    "un sondeo sin límite se acota",
    preparar_sondeo(consulta(limite=0))[0].limite == LIMITE_SONDEO,
)
comprobar(
    "un sondeo que pide miles de filas se recorta",
    preparar_sondeo(consulta(limite=5000))[0].limite == LIMITE_SONDEO,
)
comprobar(
    "un límite chico se respeta tal cual",
    preparar_sondeo(consulta(limite=1))[0].limite == 1,
)

# MAX(anio) y MAX(mes) en la misma consulta para averiguar el último mes
# devuelve el año más alto pegado al mes más alto de cualquier año: una
# combinación que puede no existir.
mal = consulta(
    medidas=[
        Medida(agregacion="MAX", columna="anio", alias="max_anio"),
        Medida(agregacion="MAX", columna="mes", alias="max_mes"),
    ],
    dimensiones=[],
)
arreglado, ajustes = preparar_sondeo(mal)
comprobar("el máximo de dos columnas se reescribe", bool(ajustes), str(ajustes))
comprobar(
    "se agrupa por las columnas en vez de agregarlas",
    arreglado.dimensiones == ["anio", "mes"],
    str(arreglado.dimensiones),
)
comprobar(
    "ordena por las dos, descendente, y trae una fila",
    arreglado.orden_por == "anio, mes"
    and arreglado.orden_descendente
    and arreglado.limite == 1,
    f"{arreglado.orden_por} desc={arreglado.orden_descendente} limite={arreglado.limite}",
)
sql_ultimo = construir_sql(arreglado, set(PERMITIDAS))
comprobar(
    "el SQL reescrito da la última combinación real",
    'GROUP BY "anio", "mes"' in sql_ultimo and 'ORDER BY "anio" DESC, "mes" DESC' in sql_ultimo,
    sql_ultimo,
)

# Un MIN de dos columnas es lo mismo al revés: la primera combinación.
_, ajustes = preparar_sondeo(
    consulta(
        medidas=[
            Medida(agregacion="MIN", columna="anio", alias="a"),
            Medida(agregacion="MIN", columna="mes", alias="m"),
        ],
        dimensiones=[],
    )
)
comprobar("el mínimo de dos columnas también", bool(ajustes), str(ajustes))

# Y lo que NO hay que tocar: un solo máximo es un máximo y punto.
_, ajustes = preparar_sondeo(
    consulta(medidas=[Medida(agregacion="MAX", columna="anio", alias="a")], dimensiones=[])
)
comprobar("un solo MAX se deja como está", ajustes == [], str(ajustes))

_, ajustes = preparar_sondeo(
    consulta(
        medidas=[
            Medida(agregacion="MAX", columna="anio", alias="a"),
            Medida(agregacion="SUM", columna="monto_pagado", alias="c"),
        ],
        dimensiones=[],
    )
)
comprobar("un MAX mezclado con otra medida no se toca", ajustes == [], str(ajustes))

_, ajustes = preparar_sondeo(
    consulta(
        medidas=[
            Medida(agregacion="MAX", columna="anio", alias="a"),
            Medida(agregacion="MAX", columna="mes", alias="m"),
        ],
        dimensiones=["ambito"],
    )
)
comprobar("con dimensiones ya agrupa, no se toca", ajustes == [], str(ajustes))

print()
print("Contra la base real: el SQL armado da las cifras de control de la sábana:")

try:
    from datos_basePublica.ejecucion import RUTA, consultar
except Exception as error:  # sin pandas, por ejemplo
    RUTA, consultar = None, None
    print(f"  (salteado: no se pudo importar la ejecución: {error})")

if consultar is not None and not RUTA.exists():
    print(f"  (salteado: no existe {RUTA})")
elif consultar is not None:
    control = consultar(
        armar(
            medidas=[
                Medida(agregacion="COUNT", columna="", alias="reclamos"),
                Medida(agregacion="SUM", columna="monto_pagado", alias="pagado"),
                Medida(agregacion="SUM", columna="monto_total", alias="total"),
            ],
            dimensiones=["anio", "ambito"],
        )
    )
    obtenido = {
        (int(f.anio), f.ambito): (int(f.reclamos), round(f.pagado), round(f.total))
        for f in control.itertuples()
    }
    esperado = {
        (2008, "Ambulatorio"): (281971, 78449490, 106026240),
        (2008, "Hospitalario"): (27656, 258063920, 299344636),
        (2009, "Ambulatorio"): (321310, 94184290, 125977830),
        (2009, "Hospitalario"): (25210, 245512790, 284055506),
    }
    comprobar("reclamos, pagado y total por año y ámbito coinciden al entero",
              obtenido == esperado, str(obtenido))

    pagado = consultar(armar(filtros=[filtro("ambito", ["Hospitalario"]), filtro("anio", ["2009"])]))
    comprobar("pagado hospitalario 2009 = 245.512.790",
              round(pagado.iloc[0]["pagado"]) == 245512790, str(pagado))

    ultimo = consultar(sql_ultimo)
    comprobar("el sondeo reescrito encuentra 2009-12 como último mes",
              (int(ultimo.iloc[0]["anio"]), int(ultimo.iloc[0]["mes"])) == (2009, 12), str(ultimo))

    # El error silencioso de SQLite: si el año fuera entre comillas, esto daría 0.
    reclamos = consultar(
        armar(
            medidas=[Medida(agregacion="COUNT", columna="", alias="reclamos")],
            filtros=[filtro("anio", ["2009"])],
        )
    )
    comprobar("filtrar por año encuentra filas (no compara texto contra entero)",
              int(reclamos.iloc[0]["reclamos"]) == 321310 + 25210, str(reclamos))

print("\nSQL de ejemplo — pagado y afiliados por mes, para calcular la razón en Python:\n")
print(
    armar(
        medidas=[
            Medida(agregacion="SUM", columna="monto_pagado", alias="pagado"),
            Medida(agregacion="COUNT_DISTINCT", columna="id_afiliado", alias="afiliados"),
        ],
        dimensiones=["mes"],
        filtros=[
            filtro("ambito", ["Hospitalario"]),
            filtro("estado", ["California"]),
            filtro("anio", ["2009"]),
        ],
    )
)

print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallaron: {', '.join(fallos)}")
    sys.exit(1)
print("Todas las pruebas pasaron.")
