"""Corre un set de preguntas contra el agente y mide cuántas salen bien.

Sin esto, cada cambio de prompt se evalúa con una o dos consultas sueltas — que
es adivinar. Con esto, un cambio se reporta como "pasa 18 de 20" y se puede
comparar contra la corrida anterior.

Uso:
    backend\\.venv\\Scripts\\python.exe backend\\evaluacion.py

Necesita la base `datos_basePublica/reclamos.db` y **gasta dinero**: son N preguntas completas contra la
API. Calculá entre $0,07 y $0,14 por pregunta. El script avisa el costo estimado
y pide confirmación antes de arrancar.

Las preguntas y lo que se espera de cada una van en `preguntas_eval.json`.

**Qué se valida acá.** Sobre todo decisiones, no texto. Un número correcto por el
camino equivocado vuelve a fallar la semana que viene, y el nombre que el modelo
le pone a una columna de salida es libre y cambia entre corridas. Por eso lo que
se afirma es: qué columnas se filtraron, cuál se dejó libre, qué se midió, y qué
números aparecen en el resultado — sin depender de cómo se llamen.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ARCHIVO = Path(__file__).parent / "preguntas_eval.json"
COSTO_ESTIMADO_POR_PREGUNTA = 0.10

# Cuánto puede alejarse un número medido del esperado para seguir contando como
# igual. Los montos son dólares con centavos; la tolerancia relativa cubre los
# porcentajes y las razones, donde el redondeo del modelo varía.
TOLERANCIA_ABSOLUTA = 0.01
TOLERANCIA_RELATIVA = 1e-4

# Valores verificados contra `reclamos.db` el 2026-10-13.
EJEMPLO = [
    {
        "id": "participacion-circulatorio-hospitalario",
        "pregunta": "qué porcentaje del costo hospitalario de 2009 es de enfermedades "
        "del aparato circulatorio",
        "nota": "Parte y todo: el ámbito y el año describen el universo y van en las dos "
        "consultas; el grupo de enfermedad es la parte y solo filtra el numerador.",
        "espera": {
            "pide_participacion": True,
            "dimension_de_la_parte": "grupo_enfermedad",
            "columnas_en_filtros": ["ambito", "anio"],
            "columna_sin_filtro": "grupo_enfermedad",
            "columna_metrica": "monto_pagado",
            "valores": [64053490.0, 245512790.0],
        },
    },
    {
        "id": "ultimo-mes-disponible",
        "pregunta": "dame el costo del ultimo mes disponible en la informacion",
        "nota": "El último mes cargado es 2009-12, no el del calendario. La cobertura "
        "va en el prompt, así que puede resolverlo sin sondear.",
        "espera": {
            "columnas_en_filtros": ["anio", "mes"],
            "columna_metrica": "monto_pagado",
            "agregacion": "SUM",
            "valor": 25599750.0,
        },
    },
]


def cargar_preguntas() -> list[dict]:
    if not ARCHIVO.exists():
        print(f"Falta {ARCHIVO}.")
        print()
        print("Es una lista de objetos. Todos los campos de `espera` son opcionales:")
        print("solo se comprueba lo que pongas, así que una pregunta cuyo valor exacto")
        print("no conocés igual sirve para validar las decisiones.")
        print()
        print(json.dumps(EJEMPLO, ensure_ascii=False, indent=2))
        print()
        print("Campos disponibles en `espera`:")
        for campo, para_que in CAMPOS.items():
            print(f"  {campo:24} {para_que}")
        sys.exit(1)
    return json.loads(ARCHIVO.read_text(encoding="utf-8"))


CAMPOS = {
    "necesita_aclaracion": "bool — tenía que parar y preguntar en vez de responder",
    "pide_participacion": "bool — reconoció que se pide un porcentaje",
    "dimension_de_la_parte": "str — la columna que es la PARTE del porcentaje",
    "sondea": "bool — tenía que consultar la base antes de armar el plan",
    "consultas": "int — cuántas extracciones hacen falta",
    "columnas_en_filtros": "[str] — cada una filtrada en alguna consulta",
    "columna_sin_filtro": "str — alguna consulta la deja libre (el denominador)",
    "columna_metrica": "str — sobre qué columna se agrega",
    "agregacion": "str — SUM, COUNT_DISTINCT, ...",
    "dimensiones": "[str] — por qué se agrupa, sumando todas las consultas",
    "valor": "número que tiene que aparecer en el resultado",
    "valores": "[número] — todos tienen que aparecer",
    "filas": "int — cuántas filas trae el resultado final",
}


def _numeros_del_resultado(estado: dict) -> list[float]:
    """Todos los números del resultado final, sin mirar cómo se llaman.

    El modelo elige libremente los nombres de las columnas que salen del cruce en
    Python — `Porcentaje`, `pct`, `participacion` — y esos nombres cambian entre
    corridas sin que cambie la respuesta. Afirmar contra la posición de una celda
    hace fallar pruebas que están bien. Se busca el número, no la etiqueta.
    """
    numeros = []
    for fila in (estado.get("resultado") or {}).get("filas") or []:
        for celda in fila:
            if isinstance(celda, bool) or celda is None:
                continue
            try:
                numeros.append(float(celda))
            except (TypeError, ValueError):
                continue
    return numeros


def _aparece(buscado: float, numeros: list[float]) -> bool:
    margen = max(TOLERANCIA_ABSOLUTA, abs(buscado) * TOLERANCIA_RELATIVA)
    return any(abs(n - buscado) <= margen for n in numeros)


def comparar(caso: dict, estado: dict) -> list[str]:
    """Devuelve la lista de diferencias contra lo esperado. Vacía = pasa."""
    espera = caso["espera"]
    decisiones = estado.get("decisiones") or {}
    consultas = decisiones.get("consultas") or []
    fallas = []

    # --- La parada: si tenía que preguntar, no hay plan que revisar -------
    if "necesita_aclaracion" in espera:
        obtenido = bool(decisiones.get("necesita_aclaracion"))
        if obtenido != espera["necesita_aclaracion"]:
            fallas.append(
                f"necesita_aclaracion={obtenido}, se esperaba {espera['necesita_aclaracion']}"
            )
        if espera["necesita_aclaracion"]:
            return fallas

    # --- El sondeo: ¿fue a mirar la base antes de decidir? ----------------
    if "sondea" in espera:
        sondeo = bool(estado.get("sondeos"))
        if sondeo != espera["sondea"]:
            fallas.append(
                "no sondeó la base y tenía que hacerlo"
                if espera["sondea"]
                else "sondeó la base sin necesidad"
            )

    # --- Parte y todo -----------------------------------------------------
    if "pide_participacion" in espera:
        obtenido = bool(decisiones.get("pide_participacion"))
        if obtenido != espera["pide_participacion"]:
            fallas.append(
                f"pide_participacion={obtenido}, se esperaba {espera['pide_participacion']}"
            )

    if "dimension_de_la_parte" in espera:
        obtenido = decisiones.get("dimension_de_la_parte", "")
        if obtenido != espera["dimension_de_la_parte"]:
            fallas.append(
                f"dimension_de_la_parte={obtenido!r}, "
                f"se esperaba {espera['dimension_de_la_parte']!r}"
            )

    # --- Las extracciones -------------------------------------------------
    if "consultas" in espera and len(consultas) != espera["consultas"]:
        fallas.append(f"{len(consultas)} consulta(s), se esperaban {espera['consultas']}")

    # Los filtros se miran sobre el conjunto: en un porcentaje el período está en
    # las dos consultas, pero en un cruce cada una aporta lo suyo.
    filtradas = {f["columna"] for c in consultas for f in c.get("filtros", [])}
    if "columnas_en_filtros" in espera:
        faltan = set(espera["columnas_en_filtros"]) - filtradas
        if faltan:
            fallas.append(f"faltan filtros sobre: {', '.join(sorted(faltan))}")

    # El reverso, y es el que atrapa el error del denominador: tiene que existir
    # una consulta que NO filtre esa columna, o no hay total contra el cual dividir.
    if "columna_sin_filtro" in espera:
        columna = espera["columna_sin_filtro"]
        libre = any(
            all(f["columna"] != columna for f in c.get("filtros", [])) for c in consultas
        )
        if not libre:
            fallas.append(f"todas las consultas filtran `{columna}`: no hay universo total")

    medidas = [m for c in consultas for m in c.get("medidas", [])]
    if "columna_metrica" in espera:
        usadas = {m.get("columna") for m in medidas}
        if espera["columna_metrica"] not in usadas:
            fallas.append(
                f"no se midió sobre `{espera['columna_metrica']}` (se midió: "
                f"{', '.join(sorted(u for u in usadas if u)) or 'nada'})"
            )

    if "agregacion" in espera:
        usadas = {m.get("agregacion") for m in medidas}
        if espera["agregacion"] not in usadas:
            fallas.append(
                f"no se usó {espera['agregacion']} (se usó: {', '.join(sorted(usadas)) or 'nada'})"
            )

    if "dimensiones" in espera:
        agrupadas = sorted({d for c in consultas for d in c.get("dimensiones", [])})
        if agrupadas != sorted(espera["dimensiones"]):
            fallas.append(f"dimensiones={agrupadas}, se esperaba {sorted(espera['dimensiones'])}")

    # --- El resultado -----------------------------------------------------
    resultado = estado.get("resultado") or {}
    if "filas" in espera and resultado.get("total_filas") != espera["filas"]:
        fallas.append(f"{resultado.get('total_filas')} fila(s), se esperaban {espera['filas']}")

    buscados = list(espera.get("valores", []))
    if "valor" in espera:
        buscados.append(espera["valor"])
    if buscados:
        numeros = _numeros_del_resultado(estado)
        if not numeros:
            fallas.append("el resultado no trajo ningún número")
        else:
            for buscado in buscados:
                if not _aparece(float(buscado), numeros):
                    fallas.append(f"no aparece {buscado} en el resultado (vino: {numeros})")

    return fallas


CAMPOS_DE_USO = (
    "uso_comprension",
    "uso_revision",
    "uso_reparacion",
    "uso_revision_resultado",
    "uso_redaccion",
)


def main() -> None:
    casos = cargar_preguntas()
    costo = len(casos) * COSTO_ESTIMADO_POR_PREGUNTA
    print(f"{len(casos)} preguntas. Costo estimado: ~${costo:.2f}.")
    if input("¿Correr? (s/n) ").strip().lower() != "s":
        print("Cancelado.")
        return

    # Se importa acá y no arriba: `main` levanta el grafo y lee el esquema de
    # la base al importarse, y no queremos ese costo si el usuario cancela.
    from langchain_core.messages import HumanMessage

    from main import app_grafo

    pasaron = 0
    gasto = 0.0
    fallidos = []
    for numero, caso in enumerate(casos, 1):
        estado = app_grafo.invoke(
            {
                "mensajes": [HumanMessage(content=caso["pregunta"])],
                "reintentos": 0,
                "reparaciones": 0,
                "sondeos": {},
                "rondas_sondeo": 0,
                "revision": {},
                "uso_comprension": None,
                "uso_revision": None,
                "uso_reparacion": None,
                "uso_revision_resultado": None,
                "uso_redaccion": None,
            },
            # Un hilo por caso: no queremos que una pregunta contamine la siguiente.
            {"configurable": {"thread_id": f"eval-{caso['id']}"}},
        )
        for campo in CAMPOS_DE_USO:
            gasto += (estado.get(campo) or {}).get("costo_total", 0.0)

        fallas = comparar(caso, estado)
        marcas = []
        if estado.get("rondas_sondeo"):
            marcas.append(f"{estado['rondas_sondeo']} sondeo(s)")
        if estado.get("reintentos", 0) > 1:
            marcas.append(f"{estado['reintentos'] - 1} rehecho(s)")
        sufijo = f"  [{', '.join(marcas)}]" if marcas else ""

        if fallas:
            fallidos.append(caso["id"])
            print(f"\n[{numero}/{len(casos)}] FALLA  {caso['id']}{sufijo}")
            print(f"    {caso['pregunta']}")
            for falla in fallas:
                print(f"    - {falla}")
            for nombre, sql in (estado.get("sqls") or {}).items():
                print(f"    SQL `{nombre}`: {' '.join(sql.split())}")
            if not estado.get("sqls"):
                print("    (no se armó ningún SQL)")
        else:
            pasaron += 1
            print(f"[{numero}/{len(casos)}] ok     {caso['id']}{sufijo}")

    print(f"\n{pasaron} de {len(casos)} pasaron. Gasto real: ${gasto:.4f}.")
    if fallidos:
        print(f"Fallaron: {', '.join(fallidos)}")


if __name__ == "__main__":
    main()
