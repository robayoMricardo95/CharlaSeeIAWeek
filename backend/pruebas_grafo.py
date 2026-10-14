"""Prueba de humo del grafo completo, sin base de datos y sin llamar a la API.

Reemplaza el modelo por uno de mentira que devuelve respuestas fijas, y la
extracción en SQLite por filas inventadas. La composición SÍ corre en el sandbox
de verdad si Docker está levantado.

Lo que se verifica es el cableado: que el estado fluya bien entre nodos, que el
reintento no se dispare de más, que no se inyecten filtros que nadie pidió, que
el cruce en Python funcione, y que el costo se sume de todos los nodos.

Correr con:  backend\\.venv\\Scripts\\python.exe backend\\pruebas_grafo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage

import consulta
from agente.decisiones import (
    Componente,
    ConsultaBase,
    Decisiones,
    Derivacion,
    Filtro,
    Hallazgo,
    Medida,
    Revision,
)
from agente.esquema_sabana import PERMITIDAS
from agente.grafo import construir_grafo
from agente.nodos import Nodos, filtros_en_lenguaje_de_negocio

fallos: list[str] = []
salteadas: list[str] = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre}  {detalle}")
        fallos.append(nombre)


def mensaje_falso(texto: str, entrada: int = 1000, salida: int = 100) -> AIMessage:
    return AIMessage(
        content=texto,
        usage_metadata={
            "input_tokens": entrada,
            "output_tokens": salida,
            "total_tokens": entrada + salida,
        },
    )


def una_consulta(nombre: str, medidas: list[Medida], **cambios) -> ConsultaBase:
    campos = dict(
        nombre=nombre,
        proposito="prueba",
        medidas=medidas,
        dimensiones=["mes"],
        filtros=[
            Filtro(
                columna="anio",
                operador="=",
                valores=["2009"],
                origen="pregunta",
                justificacion="2009",
            )
        ],
        orden_por="",
        orden_descendente=False,
        limite=0,
    )
    campos.update(cambios)
    return ConsultaBase(**campos)


def decisiones(**cambios) -> Decisiones:
    campos = dict(
        intencion="datos",
        periodo="2009 completo",
        interpretacion="costo por mes en 2009",
        premisa_direccion="",
        premisa="",
        rama="costo",
        metrica_pedida="Costo",
        metrica_es_aditiva=True,
        pide_participacion=False,
        dimension_de_la_parte="",
        universo_denominador="",
        necesita_aclaracion=False,
        pregunta_de_aclaracion="",
        sondeos=[],
        consultas=[
            una_consulta("costo", [Medida(agregacion="SUM", columna="monto_pagado", alias="costo")])
        ],
        composicion="",
        novedades=[],
    )
    campos.update(cambios)
    return Decisiones(**campos)


# Una razón NO aditiva, imposible de sumar por dimensión: reclamos por afiliado.
# Dos extracciones y una división.
FRECUENCIA = decisiones(
    rama="reclamos",
    metrica_pedida="Reclamos por afiliado",
    metrica_es_aditiva=False,
    consultas=[
        una_consulta(
            "reclamos",
            [Medida(agregacion="COUNT", columna="", alias="reclamos")],
        ),
        una_consulta(
            "afiliados",
            [Medida(agregacion="COUNT_DISTINCT", columna="id_afiliado", alias="afiliados")],
        ),
    ],
    composicion=(
        "juntas = consultas['reclamos'].merge(consultas['afiliados'], on='mes')\n"
        "juntas['frecuencia'] = juntas['reclamos'] / juntas['afiliados']\n"
        "resultado = juntas[['mes', 'frecuencia']]"
    ),
)


class ModeloEstructurado:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = 0

    def invoke(self, _mensajes):
        self.llamadas += 1
        indice = min(self.llamadas - 1, len(self.respuestas) - 1)
        return {"parsed": self.respuestas[indice], "raw": mensaje_falso("crudo")}


SIN_DERIVACION = Derivacion(formula="", componentes=[], supuestos=[], sugerencias=[])


class ModeloFalso:
    def __init__(
        self, planes, revisiones, texto_libre="El costo fue de $1.000.000.", derivaciones=None
    ):
        self.decisor = ModeloEstructurado(planes)
        self.revisor = ModeloEstructurado(revisiones)
        self.explicador = ModeloEstructurado(derivaciones or [SIN_DERIVACION])
        self.texto_libre = texto_libre
        self.llamadas_libres = 0

    def with_structured_output(self, esquema, include_raw=False):
        if esquema is Decisiones:
            return self.decisor
        if esquema is Derivacion:
            return self.explicador
        return self.revisor

    def invoke(self, mensajes):
        self.llamadas_libres += 1
        self.ultimos_mensajes = mensajes
        return mensaje_falso(self.texto_libre)


# La extracción contra SQLite se reemplaza por filas fijas, distintas por consulta.
# Se reconocen por el alias de la medida, que va entre comillas dobles en el SQL.
TABLAS = {
    "costo": pd.DataFrame({"mes": [1, 2], "costo": [1000.0, 1500.0]}),
    "reclamos": pd.DataFrame({"mes": [1, 2], "reclamos": [1000, 1100]}),
    "afiliados": pd.DataFrame({"mes": [1, 2], "afiliados": [400, 500]}),
    "ultimo_mes": pd.DataFrame({"ultimo_mes": [2]}),
    # El total por año baja: sirve para la premisa falsa del "alza".
    "total": pd.DataFrame({"anio": [2008, 2009], "total": [258063920.0, 245512790.0]}),
}


def _tabla_para(sql: str) -> pd.DataFrame:
    for nombre, tabla in TABLAS.items():
        if f'AS "{nombre}"' in sql:
            return tabla
    return TABLAS["costo"]


consulta.ejecutar = lambda sql: consulta.desde_tabla(_tabla_para(sql), sql)
consulta.tabla_de = _tabla_para


def correr(modelo, pregunta="¿Cuánto costó en 2009?", hilo="t"):
    nodos = Nodos(modelo, "gpt-4.1", "prefijo", "cierre", set(PERMITIDAS))
    grafo = construir_grafo(nodos)
    return grafo.invoke(
        {
            "mensajes": [HumanMessage(content=pregunta)],
            "reintentos": 0,
            "reparaciones": 0,
            "sondeos": {},
            "rondas_sondeo": 0,
            "derivacion": {},
    "perfil": {},
    "lectura": {},
            "perfil": {},
            "lectura": {},
            "revision": {},
            "uso_comprension": None,
            "uso_revision": None,
            "uso_reparacion": None,
            "uso_revision_resultado": None,
            "uso_lectura": None,
            "uso_lectura": None,
    "uso_explicacion": None,
            "uso_redaccion": None,
        },
        {"configurable": {"thread_id": hilo}},
    )


APROBADO = Revision(aprobado=True, hallazgos=[])

print("Una sola consulta, sin composición (el atajo barato):")

modelo = ModeloFalso([decisiones()], [APROBADO])
estado = correr(modelo, hilo="simple")

comprobar("el grafo llega hasta el final", bool(estado["respuesta"]), str(estado.get("respuesta")))
comprobar("se comprendió una sola vez", modelo.decisor.llamadas == 1, str(modelo.decisor.llamadas))
comprobar("se revisó decisiones y resultado", modelo.revisor.llamadas == 2, str(modelo.revisor.llamadas))
comprobar("el resultado sale directo de la extracción", estado["resultado"]["total_filas"] == 2)
comprobar("no se usó el sandbox", estado["composicion"] == "", estado["composicion"])

sql = estado["sqls"]["costo"]
comprobar("el SQL lee de la sábana", "FROM sabana" in sql, sql)
comprobar("el SQL lleva solo el filtro pedido", '"anio" = 2009' in sql and "AND" not in sql, sql)
comprobar(
    "no se inyecta nada: no hay reglas fijas",
    estado["inyectados"] == {"costo": []},
    str(estado["inyectados"]),
)

print("\nDos consultas cruzadas en Python (reclamos por afiliado, razón NO aditiva):")

modelo = ModeloFalso([FRECUENCIA], [APROBADO])
estado = correr(modelo, pregunta="¿Cuántos reclamos por afiliado hubo en 2009?", hilo="frecuencia")

comprobar("se armaron los dos SQL", len(estado["sqls"]) == 2, str(list(estado["sqls"])))
comprobar(
    "ninguna consulta lleva filtros que nadie pidió",
    all(s.count('"anio" = 2009') == 1 and "AND" not in s for s in estado["sqls"].values()),
    str(estado["sqls"]),
)

if estado.get("error_composicion", "").startswith(("Docker", "No encuentro", "Falta construir")):
    salteadas.append("el cruce en el sandbox")
    print(f"  (sandbox no disponible: {estado['error_composicion']})")
else:
    comprobar("la composición corrió sin error", not estado["error_composicion"], estado["error_composicion"])
    if estado["resultado"]:
        filas = {f[0]: f[1] for f in estado["resultado"]["filas"]}
        comprobar("la frecuencia se calculó bien", abs(filas[1] - 2.5) < 0.001, str(filas))
        comprobar("el resultado final reemplaza a las extracciones", "frecuencia" in estado["resultado"]["columnas"])

print("\nCosto sumado de todos los nodos:")
total = sum(
    (estado.get(campo) or {}).get("tokens_salida", 0)
    for campo in ("uso_comprension", "uso_revision", "uso_revision_resultado", "uso_redaccion")
)
comprobar("los cuatro nodos aportaron tokens", total == 400, f"tokens de salida: {total}")

print("\nEl revisor rechaza (se rehace la comprensión, una sola vez):")

rechazo = Revision(
    aprobado=False,
    hallazgos=[
        Hallazgo(
            gravedad="error",
            paso="variables",
            que_falta="La columna de ciudad elegida es la comercial",
            correccion="Usar la de hospitalización",
        )
    ],
)
modelo = ModeloFalso([decisiones()], [rechazo])
estado = correr(modelo, hilo="rechazo")

comprobar("se rehizo la comprensión", modelo.decisor.llamadas == 2, str(modelo.decisor.llamadas))
comprobar("el reintento se corta en MAX_REINTENTOS", modelo.decisor.llamadas == 2)
comprobar("igual se llega a una respuesta", bool(estado["respuesta"]))
comprobar(
    "el costo de las dos comprensiones se suma",
    estado["uso_comprension"]["tokens_salida"] == 200,
    str(estado["uso_comprension"]["tokens_salida"]),
)

print("\nComposición que falla (se repara una vez):")

rota = FRECUENCIA.model_copy(update={"composicion": "resultado = consultas['no_existe']"})
modelo = ModeloFalso(
    [rota],
    [APROBADO],
    texto_libre="```python\nresultado = consultas['afiliados']\n```",
)
estado = correr(modelo, pregunta="reclamos por afiliado", hilo="reparacion")

if "el cruce en el sandbox" in salteadas:
    print("  (salteado: sandbox no disponible)")
else:
    comprobar("se intentó reparar una vez", estado["reparaciones"] == 1, str(estado["reparaciones"]))
    comprobar("el código reparado se limpió del ```python", "```" not in estado["composicion"], estado["composicion"])
    comprobar("después de reparar hay resultado", estado["resultado"] is not None, str(estado.get("error_composicion")))

print("\nPregunta ambigua (no se toca la base):")

ambigua = decisiones(
    necesita_aclaracion=True,
    pregunta_de_aclaracion="¿Monto pagado por la aseguradora o costo completo?",
)
modelo = ModeloFalso([ambigua], [APROBADO])
estado = correr(modelo, hilo="ambigua")

comprobar("no se armó SQL", estado["sqls"] == {}, str(estado["sqls"]))
comprobar("no se ejecutó nada", estado["resultado"] is None)
comprobar("no se gastó en redacción", modelo.llamadas_libres == 0, str(modelo.llamadas_libres))
comprobar(
    "la respuesta es la pregunta de aclaración",
    "Monto pagado por la aseguradora" in estado["respuesta"],
    estado["respuesta"],
)

print()
print("Sondeo: preguntarle a los datos antes que al usuario:")

SONDEO_ULTIMO_MES = una_consulta(
    "ultimo_mes",
    [Medida(agregacion="MAX", columna="mes", alias="ultimo_mes")],
    dimensiones=[],
    proposito="cuál es el último mes cargado",
)

CON_SONDEO = decisiones(sondeos=[SONDEO_ULTIMO_MES])

# Primera vuelta declara un sondeo; la segunda ya viene con el dato y arma el plan.
modelo = ModeloFalso([CON_SONDEO, decisiones()], [APROBADO])
estado = correr(modelo, pregunta="dame el costo del último mes disponible", hilo="sondeo")

comprobar(
    "se volvió a comprender con el dato en la mano",
    modelo.decisor.llamadas == 2,
    str(modelo.decisor.llamadas),
)
comprobar("se hizo una sola ronda de sondeo", estado["rondas_sondeo"] == 1, str(estado["rondas_sondeo"]))
comprobar(
    "el sondeo quedó guardado con su SQL",
    bool(estado["sondeos"]["ultimo_mes"]["sql"]),
    str(estado["sondeos"]),
)
comprobar(
    "el sondeo trajo el dato",
    estado["sondeos"]["ultimo_mes"]["resultado"]["filas"] == [[2]],
    str(estado["sondeos"]["ultimo_mes"]["resultado"]),
)
comprobar(
    "el sondeo lleva solo lo que se pidió",
    '"anio" = 2009' in estado["sondeos"]["ultimo_mes"]["sql"]
    and "AND" not in estado["sondeos"]["ultimo_mes"]["sql"],
    estado["sondeos"]["ultimo_mes"]["sql"],
)
comprobar("igual se llegó a una respuesta", bool(estado["respuesta"]))
comprobar(
    "el sondeo no se mezcla con las extracciones",
    list(estado["sqls"]) == ["costo"],
    str(list(estado["sqls"])),
)

# Un modelo que sondea siempre no puede dejar el grafo dando vueltas.
modelo = ModeloFalso([CON_SONDEO], [APROBADO])
estado = correr(modelo, pregunta="dame el costo del último mes", hilo="sondeo_infinito")

comprobar(
    "el sondeo se corta en MAX_RONDAS_SONDEO",
    estado["rondas_sondeo"] == 2,
    str(estado["rondas_sondeo"]),
)
comprobar("aun sondeando de más, se responde", bool(estado["respuesta"]))

print()
print("Un porcentaje sin denominador se bloquea antes de gastar el revisor:")


def con_filtros(nombre, filtros) -> ConsultaBase:
    return una_consulta(
        nombre,
        [Medida(agregacion="SUM", columna="monto_pagado", alias="costo")],
        dimensiones=[],
        filtros=[
            Filtro(
                columna=columna,
                operador="IN",
                valores=valores,
                origen="pregunta",
                justificacion="prueba",
            )
            for columna, valores in filtros
        ],
    )


# El modo de fallar, reproducido: "qué porcentaje del costo hospitalario es de
# aparato circulatorio" resuelto como hospitalario-circulatorio contra
# ambulatorio-circulatorio. Las dos consultas fijan el grupo: no hay total
# hospitalario.
CIRCULATORIO = "Enfermedades del aparato circulatorio"
INVERTIDO = decisiones(
    pide_participacion=True,
    dimension_de_la_parte="grupo_enfermedad",
    universo_denominador="todo el costo de aparato circulatorio",
    consultas=[
        con_filtros("hosp", [("ambito", ["Hospitalario"]), ("grupo_enfermedad", [CIRCULATORIO])]),
        con_filtros("amb", [("ambito", ["Ambulatorio"]), ("grupo_enfermedad", [CIRCULATORIO])]),
    ],
    composicion="resultado = consultas['hosp']",
)

CORREGIDO = decisiones(
    pide_participacion=True,
    dimension_de_la_parte="grupo_enfermedad",
    universo_denominador="todo el costo hospitalario",
    consultas=[
        con_filtros("parte", [("ambito", ["Hospitalario"]), ("grupo_enfermedad", [CIRCULATORIO])]),
        con_filtros("todo", [("ambito", ["Hospitalario"])]),
    ],
    composicion="resultado = consultas['parte']",
)

modelo = ModeloFalso([INVERTIDO, CORREGIDO], [APROBADO])
estado = correr(
    modelo,
    pregunta="qué porcentaje del costo hospitalario es de aparato circulatorio",
    hilo="participacion",
)

comprobar("se rehizo la comprensión", modelo.decisor.llamadas == 2, str(modelo.decisor.llamadas))
comprobar(
    "no se gastó el revisor en el plan mecánicamente inválido",
    modelo.revisor.llamadas == 2,
    f"llamadas al revisor: {modelo.revisor.llamadas} (1 de decisiones + 1 de resultado)",
)
comprobar(
    "el plan corregido llega a construir las dos consultas",
    sorted(estado["sqls"]) == ["parte", "todo"],
    str(sorted(estado["sqls"])),
)
comprobar(
    "la consulta del total no filtra el grupo que se preguntó",
    CIRCULATORIO not in estado["sqls"]["todo"],
    estado["sqls"]["todo"],
)

# Y un plan bien armado no se traba.
modelo = ModeloFalso([CORREGIDO], [APROBADO])
estado = correr(
    modelo, pregunta="porcentaje del costo hospitalario de circulatorio", hilo="participacion_ok"
)
comprobar(
    "un porcentaje bien planteado no se rehace",
    modelo.decisor.llamadas == 1,
    str(modelo.decisor.llamadas),
)


print()
print("Pregunta general (saludo, capacidades): se contesta con cordialidad:")

CHARLA = decisiones(
    intencion="conversacion",
    consultas=[],
    composicion="",
    interpretacion="El usuario saluda y pregunta qué se puede consultar.",
)
modelo = ModeloFalso(
    [CHARLA], [APROBADO], texto_libre="<p>Buenos días. Puedo ayudarle con los costos.</p>"
)
estado = correr(modelo, pregunta="hola, ¿qué puedes hacer?", hilo="charla")

comprobar("no se armó ningún SQL", estado["sqls"] == {}, str(estado["sqls"]))
comprobar("no se tocó la base", estado["resultado"] is None)
comprobar(
    "no se gastó en revisar un plan que no existe",
    modelo.revisor.llamadas == 0,
    str(modelo.revisor.llamadas),
)
comprobar("se contestó igual", "Buenos d" in estado["respuesta"], estado["respuesta"])
comprobar("no se ofrece tabla", estado["mostrar_tabla"] is False, str(estado["mostrar_tabla"]))

print()
print("De dónde sale el número: los valores citados se verifican contra la base:")

# Los datos que devuelve la extracción de prueba son costo 1000 y 1500 (TABLAS).
INVENTADA = Derivacion(
    formula="Costo total = suma del costo de cada mes",
    componentes=[
        Componente(
            nombre="Costo de enero",
            valor=1000.0,
            unidad="dolares",
            de_donde="Reclamos de enero",
        ),
        Componente(
            nombre="Costo de marzo",
            valor=99999.0,
            unidad="dolares",
            de_donde="Un mes que no se consultó",
        ),
    ],
    supuestos=["Se tomó el año 2009 completo"],
    sugerencias=["¿Quiere ver el desglose por región?"],
)
modelo = ModeloFalso([decisiones()], [APROBADO], derivaciones=[INVENTADA])
estado = correr(modelo, hilo="derivacion")

componentes = estado["derivacion"]["componentes"]
comprobar("la derivación llega al estado", len(componentes) == 2, str(estado["derivacion"]))
comprobar(
    "un valor que sí salió de la base queda verificado",
    componentes[0]["verificado"] is True,
    str(componentes[0]),
)
comprobar(
    "un valor inventado se marca como no verificado",
    componentes[1]["verificado"] is False,
    str(componentes[1]),
)
comprobar(
    "el componente inventado NO se borra, se marca",
    componentes[1]["nombre"] == "Costo de marzo",
    "un número que desaparece esconde que el modelo se lo inventó",
)
comprobar("los supuestos viajan", estado["derivacion"]["supuestos"] == ["Se tomó el año 2009 completo"])

# El factor 100 se acepta SOLO en porcentajes. Admitirlo en todo hacía que 99.999
# "coincidiera" con 1.000 y la verificación no verificaba nada.
CIEN_VECES = Derivacion(
    formula="x",
    componentes=[
        Componente(nombre="Cien veces", valor=100000.0, unidad="dolares", de_donde="-"),
        Componente(nombre="Como fracción", valor=10.0, unidad="porcentaje", de_donde="-"),
    ],
    supuestos=[],
    sugerencias=[],
)
modelo = ModeloFalso([decisiones()], [APROBADO], derivaciones=[CIEN_VECES])
estado = correr(modelo, hilo="derivacion-escala")
escala = estado["derivacion"]["componentes"]
comprobar(
    "un monto cien veces mayor NO cuenta como verificado",
    escala[0]["verificado"] is False,
    str(escala[0]),
)
comprobar(
    "un porcentaje sí puede venir en la otra escala (10 % contra 1000)",
    escala[1]["verificado"] is True,
    str(escala[1]),
)

# Un porcentaje viaja como fracción (0,2609) y el modelo suele citarlo como 26,09.
PORCENTAJE = Derivacion(
    formula="Participación",
    componentes=[
        Componente(nombre="Parte", valor=1000.0, unidad="dolares", de_donde="enero"),
    ],
    supuestos=[],
    sugerencias=[],
)
modelo = ModeloFalso([decisiones()], [APROBADO], derivaciones=[PORCENTAJE])
estado = correr(modelo, hilo="derivacion-pct")
comprobar(
    "no se llama al explicador de más",
    modelo.explicador.llamadas == 1,
    str(modelo.explicador.llamadas),
)

print()
print("Los filtros se cuentan en lenguaje de negocio:")

filtros = filtros_en_lenguaje_de_negocio(estado["decisiones"])
comprobar(
    "se lista exactamente el filtro que se aplicó",
    [(f["columna"], f["valores"]) for f in filtros] == [("anio", "2009")],
    str(filtros),
)
comprobar(
    "sin reglas fijas, ningún filtro figura como puesto por el sistema",
    not any(f["automatico"] for f in filtros),
    str([(f["columna"], f["automatico"]) for f in filtros]),
)
comprobar(
    "el filtro explica por qué está",
    all(f["justificacion"] for f in filtros),
    str(filtros),
)


print()
print("Lectura dinámica: el modelo elige qué comparar, el sandbox lo calcula:")

# La objeción de diseño: un perfil fijo calcula siempre lo mismo, sin mirar
# la pregunta. Acá el modelo escribe la comparación y se ejecuta de verdad.
CODIGO_LECTURA = """
tabla = consultas['resultado'].sort_values('costo', ascending=False)
lider = float(tabla['costo'].iloc[0])
segundo = float(tabla['costo'].iloc[1])
resultado = pd.DataFrame({
    'observacion': ['El mes más caro supera al siguiente en'],
    'valor': [round((lider - segundo) / segundo * 100, 1)],
})
"""
modelo = ModeloFalso([decisiones()], [APROBADO], texto_libre=CODIGO_LECTURA)
estado = correr(modelo, pregunta="costo por mes de 2009", hilo="lectura")

if "el cruce en el sandbox" in salteadas:
    print("  (salteado: sandbox no disponible)")
else:
    lectura = estado["lectura"]
    comprobar("la lectura se ejecutó", not lectura.get("error"), str(lectura.get("error")))
    comprobar(
        "el número lo calculó el sandbox, no el modelo",
        lectura["tabla"]["filas"] == [["El mes más caro supera al siguiente en", 50.0]],
        str(lectura.get("tabla")),
    )
    comprobar("el código queda para auditar", "sort_values" in lectura["codigo"], lectura["codigo"])

# Lo que de verdad importa del sandbox: que el número salga de una operación y no
# de la cabeza del modelo. Medido contra la API: escribió el valor como
# constante y el sandbox ejecutó código que solo repetía un número. Si el valor
# coincide con un literal del propio código, se descarta.
COPIADO = """
resultado = pd.DataFrame({
    'observacion': ['El primero supera al segundo por', 'Filas leidas'],
    'valor': [1234.5, float(len(consultas['resultado']))],
})
"""
modelo = ModeloFalso([decisiones()], [APROBADO], texto_libre=COPIADO)
estado = correr(modelo, pregunta="costo por mes", hilo="lectura-copiada")
filas = (estado["lectura"].get("tabla") or {}).get("filas") or []
comprobar(
    "un número escrito a mano en el código se descarta",
    all(f[1] != 1234.5 for f in filas),
    str(filas),
)
comprobar(
    "el que sí se calculó sobrevive",
    any(f[0] == "Filas leidas" for f in filas),
    str(filas),
)
comprobar(
    "queda registrado cuántas se descartaron, para poder medirlo",
    estado["lectura"].get("descartadas") == 1,
    str(estado["lectura"].get("descartadas")),
)

# El guardián no puede ser demasiado entusiasta: `nlargest(3)`, `iloc[1]` y
# `round(x, 1)` meten enteros chicos en el código que son parámetros, no
# resultados. Si una observación diera justo 3, borrarla sería un falso positivo.
PARAMETROS = """
tabla = consultas['resultado'].nlargest(3, 'costo')
resultado = pd.DataFrame({
    'observacion': ['Filas del top', 'Costo del primero'],
    'valor': [float(len(tabla)), round(float(tabla['costo'].iloc[0]), 1)],
})
"""
modelo = ModeloFalso([decisiones()], [APROBADO], texto_libre=PARAMETROS)
estado = correr(modelo, pregunta="costo por mes", hilo="lectura-parametros")
filas = (estado["lectura"].get("tabla") or {}).get("filas") or []
comprobar(
    "un 3 que es parámetro del código no borra una observación que da 3",
    any(f[0] == "Filas del top" for f in filas),
    str(filas),
)
comprobar("no se descartó nada", estado["lectura"].get("descartadas") == 0,
          str(estado["lectura"].get("descartadas")))

# Y si TODO se descarta, se reintenta una vez diciéndole qué pasó, en vez de
# perder la observación en silencio.
class DosVueltas(ModeloFalso):
    """Primero copia los números; en la segunda vuelta los calcula."""

    def invoke(self, _mensajes):
        self.llamadas_libres += 1
        if self.llamadas_libres == 1:
            return mensaje_falso("resultado = pd.DataFrame({'observacion': ['x'], "
                                 "'valor': [1234.5]})")
        return mensaje_falso("resultado = pd.DataFrame({'observacion': ['x'], "
                             "'valor': [float(consultas['resultado']['costo'].sum())]})")


modelo = DosVueltas([decisiones()], [APROBADO])
estado = correr(modelo, pregunta="costo por mes", hilo="lectura-reintento")
filas = (estado["lectura"].get("tabla") or {}).get("filas") or []
comprobar(
    "si se descarta todo, se reintenta y el segundo intento se calcula",
    filas == [["x", 2500.0]],
    str(filas),
)

# Lo más importante del acuerdo: si no hay nada que comparar, no se compara. Un
# resultado de una fila no llega ni a llamar al modelo.
UNA_FILA = {"costo": pd.DataFrame({"costo": [1000.0]})}
TABLAS["costo"] = UNA_FILA["costo"]
modelo = ModeloFalso([decisiones(consultas=[una_consulta("costo",
    [Medida(agregacion="SUM", columna="monto_pagado", alias="costo")], dimensiones=[])])],
    [APROBADO], texto_libre="")
estado = correr(modelo, pregunta="costo total de 2009", hilo="lectura-una-fila")
comprobar(
    "con una sola fila no se pide lectura",
    estado["lectura"] == {},
    str(estado["lectura"]),
)
comprobar(
    "y no se gasta la llamada",
    estado.get("uso_lectura") is None,
    "una fila no se compara con nada",
)
TABLAS["costo"] = pd.DataFrame({"mes": [1, 2], "costo": [1000.0, 1500.0]})

# Si el modelo decide que no hay nada que decir, devuelve vacío y se respeta.
modelo = ModeloFalso([decisiones()], [APROBADO], texto_libre="   ")
estado = correr(modelo, pregunta="costo por mes", hilo="lectura-vacia")
comprobar(
    "si el modelo no ve nada que comparar, no se inventa nada",
    estado["lectura"] == {},
    str(estado["lectura"]),
)

# Y si el código falla, la respuesta sigue saliendo: el número ya estaba.
modelo = ModeloFalso([decisiones()], [APROBADO], texto_libre="resultado = consultas['no_existe']")
estado = correr(modelo, pregunta="costo por mes", hilo="lectura-rota")
if "el cruce en el sandbox" not in salteadas:
    comprobar("una lectura rota no rompe la respuesta", bool(estado["respuesta"]))
    comprobar("y el motivo queda registrado", bool(estado["lectura"].get("error")),
              str(estado["lectura"]))


print()
print("Un plan vacío no deja al usuario sin explicación:")

# "necesito las variaciones vs enero": el modelo no propone consultas ni pide
# aclaración, y la persona recibiría "No pude completar el análisis. No se llegó
# a ningún resultado."
SIN_PLAN = decisiones(consultas=[], composicion="")
modelo = ModeloFalso([SIN_PLAN], [APROBADO])
estado = correr(modelo, pregunta="necesito las variaciones vs enero 2009", hilo="plan-vacio")

comprobar(
    "el chequeo mecánico lo rechaza y se rehace",
    modelo.decisor.llamadas == 2,
    str(modelo.decisor.llamadas),
)
comprobar(
    "no se gastó el revisor en un plan que no existe",
    modelo.revisor.llamadas <= 1,
    str(modelo.revisor.llamadas),
)
comprobar(
    "al usuario se le dice QUÉ quedó sin definir",
    "quedó sin definir" in estado["respuesta"],
    estado["respuesta"][:200],
)
comprobar(
    "y no el mensaje genérico de siempre",
    "No se llegó a ningún resultado" not in estado["respuesta"],
    estado["respuesta"][:200],
)


print()
print("Premisa falsa: la pregunta supone un alza que no existe:")

POR_GRUPO = una_consulta(
    "costo",
    [Medida(agregacion="SUM", columna="monto_pagado", alias="costo")],
    dimensiones=["grupo_enfermedad", "anio"],
    filtros=[],
)
TOTAL_POR_ANIO = una_consulta(
    "total",
    [Medida(agregacion="SUM", columna="monto_pagado", alias="total")],
    dimensiones=["anio"],
    filtros=[],
)
CON_PREMISA_SIN_TOTAL = decisiones(
    premisa_direccion="sube",
    premisa="el monto pagado hospitalario subió de 2008 a 2009",
    consultas=[POR_GRUPO],
)
CON_PREMISA_Y_TOTAL = decisiones(
    premisa_direccion="sube",
    premisa="el monto pagado hospitalario subió de 2008 a 2009",
    consultas=[POR_GRUPO, TOTAL_POR_ANIO],
    composicion="resultado = consultas['costo']",
)

modelo = ModeloFalso([CON_PREMISA_SIN_TOTAL, CON_PREMISA_Y_TOTAL], [APROBADO])
estado = correr(modelo, pregunta="¿en qué grupos se concentró el alza de 2009?", hilo="premisa")
comprobar(
    "sin la consulta del total, el plan se rechaza y se rehace",
    modelo.decisor.llamadas == 2,
    str(modelo.decisor.llamadas),
)
comprobar(
    "el plan rehecho trae el total por año",
    "total" in estado["sqls"],
    str(list(estado["sqls"])),
)
if "el cruce en el sandbox" in salteadas:
    print("  (salteado: sandbox no disponible)")
else:
    comprobar(
        "el código detecta que la premisa no se cumple",
        estado["premisa"].get("se_cumple") is False and estado["premisa"].get("real") == "baja",
        str(estado["premisa"]),
    )
    al_redactor = str(modelo.ultimos_mensajes[-1].content)
    comprobar(
        "al redactor se le exige empezar diciendo que la premisa es falsa",
        "PREMISA FALSA" in al_redactor and "258.063.920,00" in al_redactor and "-4,9 %" in al_redactor,
        al_redactor[-600:],
    )

# Último recurso, visto contra la API: el modelo no declara la premisa ni trae
# el total aunque el revisor se lo pida dos veces. El grafo sigue igual, y el
# código arma y ejecuta la consulta del total por año antes de redactar.
SIN_DECLARAR = decisiones(consultas=[POR_GRUPO], composicion="resultado = consultas['costo']")
modelo = ModeloFalso([SIN_DECLARAR], [APROBADO])
estado = correr(modelo, pregunta="¿en qué grupos se concentró el alza de 2009?", hilo="premisa-terca")
comprobar(
    "la red de seguridad por palabras rechaza el plan (se intenta rehacer)",
    modelo.decisor.llamadas == 2,
    str(modelo.decisor.llamadas),
)
if "el cruce en el sandbox" in salteadas:
    print("  (salteado: sandbox no disponible)")
else:
    comprobar(
        "aun sin cooperación del modelo, el código verifica la premisa",
        estado["premisa"].get("agregada_por_el_sistema") is True
        and estado["premisa"].get("se_cumple") is False,
        str(estado["premisa"]),
    )
    comprobar(
        "la consulta del sistema agrupa solo por año",
        'GROUP BY "anio"' in estado["premisa"].get("sql", "")
        and "grupo_enfermedad" not in estado["premisa"].get("sql", ""),
        estado["premisa"].get("sql", ""),
    )
    comprobar(
        "y el redactor recibe la premisa falsa igual",
        "PREMISA FALSA" in str(modelo.ultimos_mensajes[-1].content),
        str(modelo.ultimos_mensajes[-1].content)[-300:],
    )

# Sin premisa, no se exige nada y no se le agrega nada al redactor.
modelo = ModeloFalso([decisiones()], [APROBADO])
estado = correr(modelo, hilo="sin-premisa")
comprobar("sin premisa, una sola comprensión", modelo.decisor.llamadas == 1, str(modelo.decisor.llamadas))
comprobar("sin premisa, no hay contraste", estado.get("premisa") == {}, str(estado.get("premisa")))

print("\nSegundo turno en el mismo hilo (memoria + reset entre turnos):")

modelo = ModeloFalso([decisiones()], [APROBADO])
nodos = Nodos(modelo, "gpt-4.1", "prefijo", "cierre", set(PERMITIDAS))
grafo = construir_grafo(nodos)
entrada = {
    "reintentos": 0,
    "reparaciones": 0,
    "sondeos": {},
    "rondas_sondeo": 0,
    "derivacion": {},
    "revision": {},
    "uso_comprension": None,
    "uso_revision": None,
    "uso_reparacion": None,
    "uso_revision_resultado": None,
    "uso_explicacion": None,
    "uso_redaccion": None,
}
config = {"configurable": {"thread_id": "memoria"}}
grafo.invoke({**entrada, "mensajes": [HumanMessage(content="primera")]}, config)
estado = grafo.invoke({**entrada, "mensajes": [HumanMessage(content="y en 2008?")]}, config)

comprobar("el historial creció", len(estado["mensajes"]) == 4, str(len(estado["mensajes"])))
comprobar(
    "el segundo turno no arrastra el reintento del primero",
    modelo.decisor.llamadas == 2,
    f"comprensiones totales: {modelo.decisor.llamadas}",
)

print()
if salteadas:
    print(f"SALTEADAS (falta Docker): {', '.join(salteadas)}")
if fallos:
    print(f"{len(fallos)} prueba(s) fallaron: {', '.join(fallos)}")
    sys.exit(1)
print("Todas las pruebas del grafo pasaron.")
