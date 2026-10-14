"""El armado del SQL y los chequeos mecánicos del plan.

**No hay reglas de negocio fijas.** Lo que significa cada columna, qué métrica
usar por defecto y qué trampas tiene el dato están en el diccionario de la
sábana, y el modelo lo aplica con criterio. Acá solo vive lo que se puede
comprobar sin criterio, contando o comparando números:

- que el plan tenga algo que ejecutar o algo que preguntar,
- que el período pedido exista en la tabla,
- que un porcentaje tenga de verdad un denominador,
- que un sondeo sea chico y no pida una combinación imposible.

Lo que el modelo decide (qué columna, qué métrica, cómo se cruzan dos
extracciones) sigue siendo suyo: ahí hay juicio y por eso se paga un modelo.

**Alcance de lo que se arma acá**: extracción sobre una tabla — filtrar, agrupar,
agregar. Nada más. Cruces, razones y comparaciones entre extracciones se hacen en
Python, en el sandbox. Es la frontera de diseño del proyecto.

El motor es SQLite: identificadores entre comillas dobles y texto entre comillas
simples. La barra invertida no escapa nada en SQLite, así que no se toca.
"""

import re

from .decisiones import ConsultaBase, Filtro, Medida
from .esquema_sabana import NUMERICAS

TABLA = "sabana"

AGREGACIONES = {
    "SUM": "SUM({c})",
    "COUNT": "COUNT(*)",
    "COUNT_DISTINCT": "COUNT(DISTINCT {c})",
    "AVG": "AVG({c})",
    "MIN": "MIN({c})",
    "MAX": "MAX({c})",
}


class ErrorDeConstruccion(Exception):
    """La consulta no se puede armar: una columna no existe, un filtro está mal."""


def _literal(valor: str, columna: str = "") -> str:
    """Escapa un valor para meterlo en el SQL.

    Las columnas van contra una lista blanca (el esquema real), pero los valores
    vienen del modelo y pueden traer comillas. En SQLite basta con duplicar la
    comilla simple.

    En columnas numéricas el número va sin comillas, y en SQLite no es un detalle
    de estilo: `anio = '2009'` compara un entero contra un texto y no devuelve
    ninguna fila. La consulta "funciona" y da cero, que es el peor error posible.
    """
    texto = str(valor)
    if columna in NUMERICAS:
        try:
            return str(int(texto))
        except ValueError:
            try:
                return str(float(texto))
            except ValueError:
                pass  # no es un número: se trata como texto y el resultado lo dirá
    escapado = texto.replace("'", "''")
    return f"'{escapado}'"


def _identificador(nombre: str) -> str:
    return '"' + nombre.replace('"', '""') + '"'


def _columna(nombre: str, permitidas: set[str]) -> str:
    if nombre not in permitidas:
        raise ErrorDeConstruccion(
            f"La columna `{nombre}` no existe en la tabla. "
            f"Solo se pueden usar las columnas reales del esquema."
        )
    return _identificador(nombre)


def _alias(texto: str, por_defecto: str = "medida") -> str:
    limpio = re.sub(r"\W+", "_", texto).strip("_")
    return limpio or por_defecto


# Un sondeo es reconocimiento, no análisis: trae un máximo, una lista de valores
# distintos o un conteo. Si el modelo pide 5.000 filas "para ver", se le recorta.
LIMITE_SONDEO = 50


def preparar_sondeo(consulta: ConsultaBase) -> tuple[ConsultaBase, list[str]]:
    """Acota y corrige una consulta de sondeo antes de ejecutarla.

    Dos cosas, y las dos por el mismo motivo: un sondeo mal armado es peor que no
    sondear, porque devuelve un número plausible que después se cita como cierto.

    1. **El tamaño.** Los sondeos se pagan dos veces: la fila que vuelve y la
       vuelta entera de comprensión que disparan. Que sean chicos es lo que los
       distingue de una extracción.

    2. **El máximo de varias columnas.** `MAX(anio)` junto a `MAX(mes)` no da el
       último período: da el año más alto pegado al mes más alto de cualquier año.
       Con una tabla que terminara en 2009-06, ese sondeo contestaría 2009-12, un
       mes que no existe, y la consulta siguiente devolvería vacío.

       En un sondeo, pedir el máximo de dos columnas a la vez es pedir la última
       combinación de las dos — no hay otra lectura razonable. Así que se reescribe
       a la forma que sí lo da: agrupar, ordenar por las dos y traer una fila.

    Devuelve la consulta lista y qué se le tocó, para que quede a la vista.
    """
    ajustes: list[str] = []
    limite = consulta.limite if 0 < consulta.limite <= LIMITE_SONDEO else LIMITE_SONDEO
    cambios: dict = {"limite": limite}

    extremos = [m for m in consulta.medidas if m.agregacion in ("MAX", "MIN")]
    columnas = [m.columna for m in extremos if m.columna]
    agregaciones = {m.agregacion for m in extremos}
    if (
        len(extremos) >= 2
        and len(extremos) == len(consulta.medidas)
        and len(agregaciones) == 1
        and not consulta.dimensiones
        and len(set(columnas)) == len(columnas) == len(extremos)
    ):
        agregacion = extremos[0].agregacion
        cambios.update(
            medidas=[Medida(agregacion="COUNT", columna="", alias="filas")],
            dimensiones=columnas,
            orden_por=", ".join(columnas),
            orden_descendente=(agregacion == "MAX"),
            limite=1,
        )
        ajustes.append(
            f"{agregacion} sobre {' y '.join(columnas)} por separado da la combinación "
            f"que no existe: se reemplazó por la última combinación real de esas columnas."
        )

    return consulta.model_copy(update=cambios), ajustes


def verificar_plan_no_vacio(decisiones: dict) -> list[dict]:
    """Un plan de datos sin consultas y sin pregunta es un callejón sin salida.

    El modelo puede clasificar la pregunta como `datos`, no proponer ninguna
    consulta y tampoco poner `necesita_aclaracion`. El grafo seguiría su curso, no
    habría SQL ni resultado, y el usuario recibiría "No pude completar el
    análisis" — que no dice qué pasó ni qué hacer.

    Son tres estados posibles y solo dos son coherentes: o hay consultas, o hay
    una pregunta para el usuario. Quedarse sin ninguna de las dos es un plan que
    no existe, y eso se detecta contando.
    """
    if decisiones.get("intencion") == "conversacion":
        return []
    if decisiones.get("consultas") or decisiones.get("sondeos"):
        return []
    if decisiones.get("necesita_aclaracion"):
        return []
    return [
        {
            "gravedad": "error",
            "paso": "interpretacion",
            "que_falta": "El plan no tiene ninguna consulta, ningún sondeo y tampoco pide "
            "una aclaración. Así no hay nada que ejecutar ni nada que preguntar.",
            "correccion": "O declare las consultas que responden la pregunta, o ponga "
            "`necesita_aclaracion` en true y escriba qué necesita saber. Si el problema "
            "es que faltan datos para el período pedido, dígalo en la aclaración.",
        }
    ]


def verificar_medidas_repetidas(decisiones: dict) -> list[dict]:
    """Dos medidas idénticas en una consulta dan el mismo número dos veces.

    El modo de fallar, visto contra la API: para comparar 2008 contra 2009 el
    modelo copió el patrón `CASE WHEN anio = 2008` de los ejemplos del
    diccionario y declaró `SUM(monto_pagado) AS pagado_2008` junto a
    `SUM(monto_pagado) AS pagado_2009`. Una medida no lleva filtro propio, así que
    las dos sumaron lo mismo, la diferencia dio cero y la respuesta fue "no hubo
    variación": un número creíble y falso.

    Pedir dos veces la misma agregación sobre la misma columna nunca aporta nada,
    así que se rechaza contando, sin criterio.
    """
    hallazgos = []
    for cruda in decisiones.get("consultas") or []:
        vistas: dict[tuple, str] = {}
        for medida in cruda.get("medidas") or []:
            clave = (medida.get("agregacion"), medida.get("columna") or "")
            alias = medida.get("alias") or medida.get("agregacion", "")
            if clave in vistas:
                hallazgos.append(
                    {
                        "gravedad": "error",
                        "paso": "composicion",
                        "que_falta": f"La consulta `{cruda.get('nombre')}` pide dos veces "
                        f"{clave[0]}({clave[1]}) (`{vistas[clave]}` y `{alias}`). Una medida no "
                        f"lleva filtro propio: las dos van a dar exactamente el mismo número.",
                        "correccion": "Para comparar períodos o categorías, deja UNA medida y "
                        "agrupa por la columna que distingue los lados (por ejemplo `anio`); la "
                        "diferencia o la variación se calcula en `composicion`. O usa una "
                        "consulta por lado, cada una con su filtro.",
                    }
                )
            else:
                vistas[clave] = alias
    return hallazgos


PERIODO = ("anio", "mes")


def consulta_del_total_por_periodo(decisiones: dict) -> dict | None:
    """La consulta que trae el total agrupado SOLO por período, si el plan la tiene.

    Es la que permite verificar una premisa: "el alza de 2009" se confirma o se
    desmiente comparando el total de 2009 con el de 2008, no mirando grupos.
    """
    for cruda in decisiones.get("consultas") or []:
        dimensiones = cruda.get("dimensiones") or []
        if dimensiones and set(dimensiones) <= set(PERIODO) and cruda.get("medidas"):
            return cruda
    return None


# Palabras con las que una pregunta da por hecho un cambio. Sin "baja" ni "bajo":
# también son preposición y adjetivo ("bajo el ámbito hospitalario"), y marcarlas
# haría rehacer planes que no suponen nada.
_SUBE = re.compile(
    r"\b(alza|alzas|aumento|aumentos|incremento|incrementos|subida|"
    r"crecimiento|crecio|subio|aumentaron|subieron|crecieron|encarecimiento)\b"
)
_BAJA = re.compile(
    r"\b(caida|caidas|disminucion|reduccion|descenso|disminuyo|cayo|se redujo|"
    r"bajaron|disminuyeron|cayeron|abaratamiento)\b"
)


def premisa_en_la_pregunta(pregunta: str) -> str:
    """'sube', 'baja' o '' según lo que la pregunta da por hecho, por sus palabras.

    Es la red de seguridad de `premisa_direccion`: medido contra la API, el
    modelo a veces no completa ese campo aunque la pregunta diga "el alza".
    Si la pregunta nombra las dos direcciones, no se adivina.
    """
    sin_tildes = (
        (pregunta or "").lower()
        .translate(str.maketrans("áéíóúü", "aeiouu"))
    )
    sube, baja = bool(_SUBE.search(sin_tildes)), bool(_BAJA.search(sin_tildes))
    if sube == baja:
        return ""
    return "sube" if sube else "baja"


def verificar_premisa(decisiones: dict, pregunta: str = "") -> list[dict]:
    """Si la pregunta supone un alza o una caída, el plan tiene que poder verificarla.

    Por qué en código: el diccionario lo marca como trampa (`verificar_la_premisa`)
    y el modelo igual contestó "¿en qué se concentró el alza hospitalaria?" con
    las alzas por grupo, sin notar que el total había bajado. Que exista una
    consulta con el total por período se comprueba mirando las dimensiones.

    Dos fallas posibles, las dos mecánicas: que el modelo no declare la premisa
    aunque la pregunta la nombre, o que la declare y no traiga el total.
    """
    if not decisiones.get("consultas"):
        return []
    if not decisiones.get("premisa_direccion"):
        en_el_texto = premisa_en_la_pregunta(pregunta)
        if not en_el_texto:
            return []
        return [
            {
                "gravedad": "error",
                "paso": "trampas",
                "que_falta": "La pregunta da por hecho que algo "
                f"{'subió' if en_el_texto == 'sube' else 'bajó'}, y el plan no declaró esa "
                "premisa ni la verifica.",
                "correccion": f"Complete `premisa_direccion` con '{en_el_texto}' y `premisa` con "
                "qué total se supone que cambió, y agregue una consulta con ese total agrupado "
                "SOLO por `anio`, con los mismos filtros del universo.",
            }
        ]
    if consulta_del_total_por_periodo(decisiones):
        return []
    return [
        {
            "gravedad": "error",
            "paso": "trampas",
            "que_falta": "La pregunta da por hecho que algo "
            f"{'subió' if decisiones['premisa_direccion'] == 'sube' else 'bajó'} "
            f"({decisiones.get('premisa') or 'sin detallar'}), y ninguna consulta trae el "
            "total por período para comprobarlo.",
            "correccion": "Agregue una consulta con la misma medida y los mismos filtros del "
            "universo, agrupada SOLO por `anio` (o `anio` y `mes`), sin abrir por otra "
            "dimensión. Con ella se confirma o se desmiente la premisa antes de explicarla.",
        }
    ]


NOMBRE_VERIFICACION = "verificacion_premisa"


def consulta_para_verificar_premisa(decisiones: dict) -> ConsultaBase | None:
    """La consulta del total por año, armada por código a partir del plan.

    Es el último recurso: si el modelo no la incluyó ni después de que el
    revisor se la pidiera, la premisa igual se verifica. Toma la primera medida
    de la primera consulta y sus filtros de universo (sin los de período, que son
    justamente lo que se quiere comparar), y agrupa solo por `anio`.
    """
    for cruda in decisiones.get("consultas") or []:
        medidas = cruda.get("medidas") or []
        if not medidas:
            continue
        return ConsultaBase(
            nombre=NOMBRE_VERIFICACION,
            proposito="Total por año, para comprobar lo que la pregunta da por hecho",
            medidas=[Medida(**{**medidas[0], "alias": "total"})],
            dimensiones=["anio"],
            filtros=[
                Filtro(**f) for f in cruda.get("filtros") or [] if f.get("columna") not in PERIODO
            ],
            orden_por="anio",
            orden_descendente=False,
            limite=0,
        )
    return None


def contrastar_premisa(decisiones: dict, extracciones: dict) -> dict:
    """Compara lo que la pregunta supone con lo que dice el total por período.

    Lo calcula el código, no el modelo: es restar dos números. Devuelve vacío si
    no hay premisa o no se puede comprobar. Si se puede, devuelve el último y el
    anterior período, la variación, y si la premisa se cumple.
    """
    direccion = decisiones.get("premisa_direccion")
    cruda = consulta_del_total_por_periodo(decisiones) if direccion else None
    resultado = (extracciones or {}).get((cruda or {}).get("nombre", ""))
    if not resultado or resultado.get("error") or not resultado.get("filas"):
        return {}

    columnas = resultado["columnas"]
    periodos = [i for i, c in enumerate(columnas) if c in PERIODO]
    medidas = [i for i, c in enumerate(columnas) if c not in PERIODO]
    if not periodos or not medidas:
        return {}
    medida = medidas[0]

    filas = sorted(
        (f for f in resultado["filas"] if f[medida] is not None),
        key=lambda f: tuple(f[i] for i in periodos),
    )
    if len(filas) < 2:
        return {}

    def etiqueta(fila) -> str:
        return "-".join(
            f"{int(fila[i]):02d}" if columnas[i] == "mes" else str(int(fila[i])) for i in periodos
        )

    previo, ultimo = filas[-2], filas[-1]
    antes, despues = float(previo[medida]), float(ultimo[medida])
    variacion = (despues - antes) / antes if antes else None
    real = "sube" if despues > antes else ("baja" if despues < antes else "igual")
    return {
        "premisa": decisiones.get("premisa", ""),
        "supone": direccion,
        "real": real,
        "se_cumple": real == direccion,
        "medida": columnas[medida],
        "previo": {"periodo": etiqueta(previo), "valor": antes},
        "ultimo": {"periodo": etiqueta(ultimo), "valor": despues},
        "variacion": variacion,
    }


def verificar_periodo(decisiones: dict, desde: int, hasta: int) -> list[dict]:
    """Comprueba que el período pedido exista en la tabla.

    Por qué en código: un modelo resuelve "el mes pasado" contra el calendario, y
    la sábana termina en diciembre de 2009. La consulta corre bien, devuelve
    vacío, y el modelo tiende a explicar el vacío con una causa inventada
    ("todavía no se cargó ese mes"). Una causa plausible y falsa es la peor
    forma de equivocarse.

    El rango real se lee de la base al arrancar, así que esto no es una opinión:
    es una comparación de dos números. Y comparar dos números no se le pide a un
    modelo, se hace.

    `desde` y `hasta` van como anio*100+mes.
    """
    hallazgos = []
    for cruda in decisiones.get("consultas") or []:
        anios, meses = [], []
        for filtro in cruda.get("filtros", []):
            destino = anios if filtro.get("columna") == "anio" else (
                meses if filtro.get("columna") == "mes" else None
            )
            if destino is None or filtro.get("operador") not in ("=", "IN", "BETWEEN"):
                continue
            for valor in filtro.get("valores") or []:
                try:
                    destino.append(int(float(valor)))
                except (TypeError, ValueError):
                    continue

        if not anios:
            continue

        # Sin mes se mira el año entero: alcanza con que algún mes caiga adentro.
        # Con mes, cada combinación año-mes tiene que existir.
        if meses:
            pedidos = [a * 100 + m for a in anios for m in meses]
            fuera = [p for p in pedidos if not (desde <= p <= hasta)]
        else:
            fuera = [a * 100 + 1 for a in anios if not (desde // 100 <= a <= hasta // 100)]
        if not fuera:
            continue

        legible = ", ".join(
            f"{p // 100}-{p % 100:02d}" if meses else str(p // 100) for p in sorted(set(fuera))
        )
        hallazgos.append(
            {
                "gravedad": "error",
                "paso": "temporalidad",
                "que_falta": f"La consulta `{cruda.get('nombre')}` pide {legible}, y la "
                f"tabla solo tiene datos entre {desde // 100}-{desde % 100:02d} y "
                f"{hasta // 100}-{hasta % 100:02d}. Va a devolver vacío.",
                "correccion": f"Use un período dentro del rango cargado. Si el usuario no "
                f"pidió un período explícito, el más reciente disponible es "
                f"{hasta // 100}-{hasta % 100:02d}, no el del calendario. Si lo pidió "
                f"explícitamente, ponga `necesita_aclaracion` y dígale que ese período no "
                f"está en la base.",
            }
        )
    return hallazgos


def verificar_participacion(decisiones: dict) -> list[dict]:
    """Comprueba que un porcentaje tenga de verdad un denominador.

    Por qué en código y no en el prompt: a "¿qué porcentaje del costo
    hospitalario es de aparato circulatorio?" un modelo puede filtrar
    `grupo_enfermedad` en las dos consultas y dividir lo hospitalario contra lo
    ambulatorio de ese mismo grupo: toma la parte como universo y contesta otra
    pregunta. El plan es internamente coherente, así que un revisor que mira el
    razonamiento lo aprueba.

    Lo que sí es mecánico: si TODAS las consultas filtran la dimensión de la parte,
    no existe forma de obtener el total, y el porcentaje que salga va a ser sobre
    otra cosa. Eso se comprueba mirando los filtros, sin criterio y sin modelo.

    Devuelve hallazgos con la forma de `Hallazgo`, para sumarlos a la revisión.
    """
    if not decisiones.get("pide_participacion"):
        return []

    consultas = decisiones.get("consultas") or []
    if not consultas:
        return []

    parte = (decisiones.get("dimension_de_la_parte") or "").strip()
    if not parte:
        return [
            {
                "gravedad": "error",
                "paso": "participacion",
                "que_falta": "Se pide un porcentaje pero no se declaró sobre qué dimensión "
                "se calcula la parte.",
                "correccion": "Complete `dimension_de_la_parte` con la columna cuyo valor es "
                "la parte, y deje esa columna SIN filtrar en la consulta del denominador.",
            }
        ]

    def filtra_la_parte(cruda: dict) -> bool:
        return any(f.get("columna") == parte for f in cruda.get("filtros", []))

    numeradores = [c for c in consultas if filtra_la_parte(c)]
    denominadores = [c for c in consultas if not filtra_la_parte(c)]

    # Todas las consultas fijan la parte, así que el "todo" que se calcule va a
    # ser sobre otro eje. No hay denominador posible.
    #
    # La ausencia de numerador NO es un problema: una sola consulta agrupada por la
    # dimensión de la parte, sin filtrarla, da todas las participaciones de una.
    if not denominadores:
        return [
            {
                "gravedad": "error",
                "paso": "participacion",
                "que_falta": f"Todas las consultas filtran `{parte}`, que es la dimensión de "
                f"la parte. Sin una consulta que la deje libre no hay total contra el cual "
                f"calcular el porcentaje.",
                "correccion": f"Agregue la consulta del denominador: los mismos filtros que el "
                f"numerador pero SIN el filtro de `{parte}`. O agrupe por `{parte}` en una "
                f"sola consulta sin filtrarla.",
            }
        ]

    # El otro modo de fallar: el numerador y el denominador no describen el mismo
    # universo. Va como aviso y no como error porque a veces es deliberado — el
    # porcentaje de un mes sobre el año, por ejemplo.
    def universo(cruda: dict) -> set:
        return {
            (f.get("columna"), f.get("operador"), tuple(sorted(f.get("valores") or [])))
            for f in cruda.get("filtros", [])
            if f.get("columna") != parte
        }

    universos_del_todo = [universo(c) for c in denominadores]
    hallazgos = []
    for cruda in numeradores:
        propio = universo(cruda)
        if any(propio == u for u in universos_del_todo):
            continue
        diferencias = sorted(
            {c for c, _, _ in propio.symmetric_difference(set().union(*universos_del_todo))}
        )
        hallazgos.append(
            {
                "gravedad": "aviso",
                "paso": "participacion",
                "que_falta": f"La consulta `{cruda.get('nombre')}` (la parte) y la del total no "
                f"comparten los mismos filtros: difieren en {', '.join(diferencias)}.",
                "correccion": "Si el total tenía que ser el mismo universo, iguale los filtros "
                "salvo el de la parte. Si la diferencia es a propósito, dígalo en la respuesta.",
            }
        )
    return hallazgos


def _condicion(filtro: Filtro, permitidas: set[str]) -> str:
    columna = _columna(filtro.columna, permitidas)
    operador = filtro.operador

    if operador == "IS NOT NULL":
        return f"{columna} IS NOT NULL"

    if not filtro.valores:
        raise ErrorDeConstruccion(f"El filtro sobre `{filtro.columna}` no trae valores.")

    if operador == "BETWEEN":
        if len(filtro.valores) != 2:
            raise ErrorDeConstruccion(
                f"BETWEEN sobre `{filtro.columna}` necesita exactamente dos valores."
            )
        desde, hasta = (_literal(v, filtro.columna) for v in filtro.valores)
        return f"{columna} BETWEEN {desde} AND {hasta}"

    if operador in ("IN", "NOT IN"):
        lista = ", ".join(_literal(v, filtro.columna) for v in filtro.valores)
        # NOT IN con NULL nunca es verdadero en SQL: hay que dejar pasar los NULL
        # aparte, si no el filtro borra filas legítimas sin que se vea.
        if operador == "NOT IN":
            return f"({columna} IS NULL OR {columna} NOT IN ({lista}))"
        return f"{columna} IN ({lista})"

    if operador == "!=":
        # Mismo problema que arriba: `col != 'x'` descarta las filas con NULL.
        return f"({columna} IS NULL OR {columna} != {_literal(filtro.valores[0], filtro.columna)})"

    return f"{columna} {operador} {_literal(filtro.valores[0], filtro.columna)}"


def construir_sql(consulta: ConsultaBase, permitidas: set[str]) -> str:
    """Arma el SELECT desde una consulta ya revisada.

    El modelo no escribe una sola letra de este SQL: elige qué columna y qué
    filtro, y esta función lo traduce. Por eso no puede salir una columna que no
    existe ni un valor sin escapar.
    """
    dimensiones = [_columna(d, permitidas) for d in consulta.dimensiones]

    if not consulta.medidas:
        # Listado sin agregar: se traen las dimensiones tal cual.
        if not dimensiones:
            raise ErrorDeConstruccion(
                "Una consulta sin medidas necesita al menos una dimensión que traer."
            )
        seleccion = list(dimensiones)
        alias_medidas: list[str] = []
    else:
        seleccion = list(dimensiones)
        alias_medidas = []
        # Los nombres del resultado tienen que ser únicos. Si una medida se llama
        # igual que una dimensión —`MAX(mes) AS mes` junto a `GROUP BY mes`—
        # vuelven dos columnas con el mismo nombre, y ahí `tabla[columna]` deja de
        # devolver una Serie y el cruce en Python revienta.
        usados = set(consulta.dimensiones)
        for medida in consulta.medidas:
            plantilla = AGREGACIONES[medida.agregacion]
            if medida.agregacion == "COUNT" and not medida.columna:
                expresion = plantilla
            else:
                if not medida.columna:
                    raise ErrorDeConstruccion(
                        f"{medida.agregacion} necesita una columna sobre la que agregar."
                    )
                expresion = plantilla.format(c=_columna(medida.columna, permitidas))
            alias = _alias(medida.alias or medida.agregacion.lower())
            propuesto, repeticion = alias, 2
            while alias in usados:
                alias = f"{propuesto}_{repeticion}"
                repeticion += 1
            usados.add(alias)
            alias_medidas.append(alias)
            seleccion.append(f"{expresion} AS {_identificador(alias)}")

    partes = [f"SELECT {', '.join(seleccion)}", f"FROM {TABLA}"]

    condiciones = [_condicion(filtro, permitidas) for filtro in consulta.filtros]
    if condiciones:
        partes.append("WHERE " + "\n  AND ".join(condiciones))

    if consulta.dimensiones and consulta.medidas:
        partes.append("GROUP BY " + ", ".join(dimensiones))

    orden = _orden(consulta, dimensiones, alias_medidas, permitidas)
    if orden:
        partes.append(orden)

    if consulta.limite > 0:
        partes.append(f"LIMIT {int(consulta.limite)}")

    return "\n".join(partes)


def _orden(consulta: ConsultaBase, dimensiones: list[str], alias_medidas: list[str], permitidas):
    """El ORDER BY. Acepta un alias de medida, una columna, o varias separadas por coma.

    Lo de la coma no es cosmético. `anio` y `mes` son dos columnas, así que "el
    último período" es `ORDER BY anio DESC, mes DESC LIMIT 1` y no hay otra forma
    de escribirlo. Si solo se admitiera una columna, el modelo caería en pedir
    `MAX(anio)` y `MAX(mes)` como dos medidas sueltas — y eso devuelve el año más
    alto pegado al mes más alto de cualquier año.
    """
    pedido = (consulta.orden_por or "").strip()
    direccion = " DESC" if consulta.orden_descendente else ""

    if pedido:
        columnas = []
        for parte in (p.strip() for p in pedido.split(",")):
            # El modelo escribe "anio DESC, mes DESC" en cuanto se le permite una
            # lista: la dirección se le sale sola porque así se escribe en SQL. La
            # dirección ya viene en `orden_descendente`, así que acá se ignora.
            parte = re.sub(r"\s+(asc|desc)$", "", parte, flags=re.IGNORECASE).strip()
            if not parte:
                continue
            alias = _alias(parte)
            if alias in alias_medidas:
                columnas.append(f"{_identificador(alias)}{direccion}")
            elif parte in permitidas:
                columnas.append(f"{_columna(parte, permitidas)}{direccion}")
            else:
                raise ErrorDeConstruccion(
                    f"No se puede ordenar por `{parte}`: no es una columna ni una medida "
                    f"de esta consulta."
                )
        if columnas:
            return "ORDER BY " + ", ".join(columnas)

    if consulta.orden_descendente and alias_medidas:
        return f"ORDER BY {_identificador(alias_medidas[0])} DESC"
    if dimensiones:
        return "ORDER BY " + ", ".join(dimensiones)
    return ""
