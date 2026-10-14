"""Lectura aritmética del resultado: lo que la tabla dice y nadie preguntó.

Este es el "nivel 2" de los tres niveles de afirmación del agente. Cada nivel
tiene una regla distinta:

1. **Respuesta** — el número que se pidió. Sale del SQL y del cruce en Python.
2. **Lectura** — brechas, concentración, variación. Sale de *esta* función:
   aritmética sobre la tabla que YA se trajo. Lleva números y son ciertos.
3. **Sugerencia** — qué mirar después. Sale del diccionario de negocio y
   **nunca lleva números**.

Por qué esto vive en código y no en el prompt: un modelo con una tabla adelante
la describe con adjetivos ("se observa una concentración importante"), y cuando
intenta calcular una brecha la calcula mal — con el conteo de filas de una
categoría intercalada, o con el máximo de dos columnas. Restar y dividir lo hace
bien una función y mal un modelo. Acá se le entregan los números hechos y el
modelo solo los narra.

Dos límites deliberados:

- **Solo mira la tabla que volvió.** No consulta nada. Si el usuario pidió el
  costo de seis meses, la variación de febrero contra enero está a la mano y se
  calcula; si pidió solo febrero, no hay contra qué comparar y no se inventa.
- **Se calla cuando no hay nada que decir.** Una fila sola no tiene brechas. Una
  distribución pareja no tiene concentración. Callarse es la mitad del trabajo.

No importa `consulta.py` ni el modelo: recibe un dict con `columnas` y `filas`,
así que se prueba sin base de datos y sin API.
"""

# Nombres de columna que son período y no medida. `Mes` es numérico pero
# promediarlo no significa nada.
PERIODO = {"anio", "año", "mes", "periodo", "período", "fecha", "trimestre"}

# Debajo de esta diferencia relativa, dos valores son "parecidos" y decir que uno
# supera al otro es ruido. 5% es el mínimo que a alguien de negocio le mueve algo.
BRECHA_MINIMA = 0.05

# Cuántas posiciones del ranking se comentan. Más que esto es enumerar la tabla,
# que es justo lo que no queremos.
POSICIONES = 3


def _es_numero(valor) -> bool:
    if valor is None or isinstance(valor, bool):
        return False
    try:
        float(valor)
        return True
    except (TypeError, ValueError):
        return False


def _clasificar(resultado: dict) -> tuple[list[int], list[int], list[int]]:
    """Separa las columnas en período, etiqueta y medida, por posición.

    Se decide mirando los datos y el nombre, no el plan: el resultado final sale
    del cruce en Python y ahí el modelo bautiza las columnas como quiere.
    """
    columnas = resultado.get("columnas") or []
    filas = resultado.get("filas") or []
    periodos, etiquetas, medidas = [], [], []

    for indice, nombre in enumerate(columnas):
        valores = [fila[indice] for fila in filas if indice < len(fila)]
        # Los nulos no descalifican a una medida: un costo con un NULL en una
        # fila sigue siendo un costo. Sin esta distinción, una sola celda vacía
        # convertía toda la columna en "etiqueta" y el perfil se iba en blanco.
        presentes = [v for v in valores if v is not None]
        numericos = [v for v in presentes if _es_numero(v)]
        es_numerica = bool(presentes) and len(numericos) == len(presentes)

        if str(nombre).strip().lower() in PERIODO:
            periodos.append(indice)
        elif es_numerica:
            medidas.append(indice)
        else:
            etiquetas.append(indice)
    return periodos, etiquetas, medidas


def _porcentaje(parte: float, todo: float) -> float | None:
    return None if not todo else parte / todo


def _agrupar(pares: list[tuple], aditiva: bool) -> list[tuple] | None:
    """Junta las filas que comparten etiqueta. Devuelve None si no se puede.

    Una consulta de costo por región Y por mes trae cada región varias veces. Para
    leer el ranking de regiones hay que sumar los meses de cada una — y **sumar
    solo es válido si la métrica es aditiva**.

    Con una razón (frecuencia, costo por afiliado) sumar da un número sin sentido:
    es exactamente el error contra el que está construido todo el proyecto. Así
    que si la métrica no es aditiva y las etiquetas se repiten, no se lee nada.
    Preferimos callarnos antes que publicar una suma de promedios.
    """
    claves = [clave for clave, _ in pares]
    if len(set(claves)) == len(claves):
        return pares
    if not aditiva:
        return None

    juntados: dict = {}
    for clave, valor in pares:
        juntados[clave] = juntados.get(clave, 0.0) + valor
    return list(juntados.items())


def _filas_para_la_mitad(ordenados: list[tuple], total: float) -> int | None:
    """Cuántas filas hacen falta para llegar a la mitad del total.

    Un solo número que dice mucho: "dos prestadores explican la mitad del gasto"
    describe la concentración mejor que cualquier porcentaje suelto. Es un
    indicador estándar en análisis de seguros de salud.
    """
    acumulado = 0.0
    for cuantas, (_, valor) in enumerate(ordenados, start=1):
        acumulado += valor
        if acumulado >= total / 2:
            return cuantas
    return None


def _ranking(resultado: dict, etiqueta: int, medida: int, aditiva: bool) -> dict | None:
    """Las seis lecturas de un ranking, cada una solo si su condición se cumple.

    Son estas: participación por posición, concentración acumulada,
    diferencias entre puestos consecutivos, cuántas filas hacen la mitad, y el
    rango del resto. Ninguna se calcula "por si acaso": si con tres filas el top 3
    es obviamente el 100%, esa lectura no existe.
    """
    filas = resultado.get("filas") or []
    pares = [
        (str(fila[etiqueta]), float(fila[medida]))
        for fila in filas
        if _es_numero(fila[medida]) and fila[etiqueta] is not None
    ]
    pares = _agrupar(pares, aditiva)
    if not pares or len(pares) < 2:
        return None

    pares.sort(key=lambda par: par[1], reverse=True)
    total = sum(valor for _, valor in pares)
    cuantos = len(pares)

    # Con un total en cero o negativo (devoluciones, ajustes) las proporciones
    # engañan más de lo que aportan.
    if pares[0][1] <= 0 or total <= 0:
        return None

    lectura: dict = {
        "columna": (resultado.get("columnas") or [])[etiqueta],
        "cuantos": cuantos,
        "total": total,
        # 1. Participación de cada posición sobre el total.
        "posiciones": [
            {
                "puesto": puesto,
                "nombre": nombre,
                "valor": valor,
                "participacion": _porcentaje(valor, total),
            }
            for puesto, (nombre, valor) in enumerate(pares[:POSICIONES], start=1)
        ],
    }

    # 3. Diferencia entre posiciones consecutivas, en valor y en porcentaje.
    brechas = []
    for puesto in range(1, min(POSICIONES, cuantos)):
        (_, arriba), (nombre_abajo, abajo) = pares[puesto - 1], pares[puesto]
        if abajo <= 0:
            continue
        relativa = (arriba - abajo) / abajo
        if relativa >= BRECHA_MINIMA:
            brechas.append(
                {
                    "de": puesto,
                    "a": puesto + 1,
                    "nombre": nombre_abajo,
                    "absoluta": arriba - abajo,
                    "relativa": relativa,
                }
            )
    if brechas:
        lectura["brechas"] = brechas

    # 2. Concentración acumulada. Con pocas filas es una obviedad: el top 3 de
    # tres filas es el 100% y decirlo es ruido.
    concentracion = {}
    if cuantos >= 4:
        concentracion["top3"] = _porcentaje(sum(v for _, v in pares[:3]), total)
    if cuantos >= 6:
        concentracion["top5"] = _porcentaje(sum(v for _, v in pares[:5]), total)
    if concentracion:
        lectura["concentracion"] = concentracion

    # 4. Cuántas filas explican la mitad del total.
    if cuantos >= 3:
        mitad = _filas_para_la_mitad(pares, total)
        if mitad is not None:
            lectura["filas_para_la_mitad"] = mitad

    # 5. El rango de los que quedaron fuera del podio: es lo que permite decir
    # "el líder se despega y los demás son parejos". Con menos de dos filas
    # afuera no hay rango que describir.
    resto = [valor for _, valor in pares[POSICIONES:]]
    if len(resto) >= 2:
        lectura["resto"] = {"cuantos": len(resto), "desde": min(resto), "hasta": max(resto)}

    return lectura


def _ordenable(valor):
    """Clave de orden que no explota mezclando números con texto."""
    try:
        return (0, float(valor), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(valor))


def _mes_solo_da_vuelta(etiquetas: list) -> bool:
    """¿La serie de meses cruza un fin de año y no hay Anio para desempatar?

    Con `Mes` como única columna de período, los valores 9, 10, 11, 12, 1, 2 son
    de septiembre a febrero — pero ordenados como números dan 1, 2, 9, 10, 11, 12
    y "el último" sale diciembre. La variación que se calcule ahí es falsa.

    Lo detectamos por continuidad: seis meses seguidos van de un mínimo a un
    máximo sin huecos. Si hay hueco, la serie da la vuelta al año (o le faltan
    meses) y no se puede ordenar sin saber el año. En ese caso no se dice nada,
    que es mejor que decir algo falso con seguridad.
    """
    try:
        numeros = sorted(int(float(e)) for e in etiquetas)
    except (TypeError, ValueError):
        return False
    return (numeros[-1] - numeros[0] + 1) != len(numeros)


def _serie(resultado: dict, periodos: list[int], medida: int, aditiva: bool) -> dict | None:
    """Variación del último período contra el anterior, y contra el promedio.

    Es el caso del acuerdo: si alguien pidió el costo de seis meses, comparar
    febrero contra enero NO es traer datos de más — ya están en la tabla.

    Ordena por TODAS las columnas de período (Anio antes que Mes), porque el mes
    solo no alcanza para ordenar una serie que cruza de diciembre a enero.
    """
    columnas = resultado.get("columnas") or []
    filas = resultado.get("filas") or []

    # Anio manda sobre Mes, sin importar en qué orden vengan las columnas.
    orden = {"anio": 0, "año": 0, "trimestre": 1, "mes": 2}
    periodos = sorted(periodos, key=lambda i: orden.get(str(columnas[i]).strip().lower(), 3))

    pares = [
        (tuple(fila[i] for i in periodos), float(fila[medida]))
        for fila in filas
        if _es_numero(fila[medida]) and all(fila[i] is not None for i in periodos)
    ]
    if len(pares) < 2:
        return None

    # Primero juntar: si la tabla tiene otra dimensión (costo por región Y por
    # mes), cada mes aparece varias veces y hay que sumarlo — cuando se puede.
    pares = _agrupar(pares, aditiva)
    if not pares or len(pares) < 2:
        return None

    # Y recién después mirar si la serie da la vuelta al año. Al revés, los meses
    # repetidos [1, 2, 1, 2] parecían tener huecos y se descartaba una serie sana.
    if len(periodos) == 1 and str(columnas[periodos[0]]).strip().lower() == "mes":
        if _mes_solo_da_vuelta([clave[0] for clave, _ in pares]):
            return None

    pares.sort(key=lambda par: tuple(_ordenable(v) for v in par[0]))
    # La etiqueta que se muestra es la combinación, sin paréntesis si es una sola.
    pares = [
        (clave[0] if len(clave) == 1 else "-".join(str(v) for v in clave), valor)
        for clave, valor in pares
    ]

    (etiqueta_previa, previo), (etiqueta_ultima, ultimo) = pares[-2], pares[-1]
    if previo <= 0:
        return None

    anteriores = [valor for _, valor in pares[:-1]]
    promedio = sum(anteriores) / len(anteriores)

    return {
        "columna": " + ".join(str(columnas[i]) for i in periodos),
        "ultimo": {"periodo": etiqueta_ultima, "valor": ultimo},
        "previo": {"periodo": etiqueta_previa, "valor": previo},
        "variacion": (ultimo - previo) / previo,
        "contra_promedio": _porcentaje(ultimo - promedio, promedio),
        "periodos": len(pares),
        "maximo": max(pares, key=lambda par: par[1]),
        "minimo": min(pares, key=lambda par: par[1]),
    }


def perfilar(resultado: dict | None, metrica_es_aditiva: bool = False) -> dict:
    """Todo lo que se puede afirmar de la tabla sin consultar nada más.

    Devuelve `{}` cuando no hay nada que decir, que es el caso más común y el que
    más importa respetar: un resultado de una fila no tiene lectura posible.
    """
    if not resultado or resultado.get("error"):
        return {}
    filas = resultado.get("filas") or []
    if len(filas) < 2:
        return {}

    periodos, etiquetas, medidas = _clasificar(resultado)
    if not medidas:
        return {}

    medida = medidas[0]  # la primera medida es la que se pidió
    perfil: dict = {"columna_medida": (resultado.get("columnas") or [])[medida]}

    if etiquetas:
        ranking = _ranking(resultado, etiquetas[0], medida, metrica_es_aditiva)
        if ranking:
            perfil["ranking"] = ranking

    if periodos:
        serie = _serie(resultado, periodos, medida, metrica_es_aditiva)
        if serie:
            perfil["serie"] = serie

    return perfil if len(perfil) > 1 else {}


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{valor * 100:.1f}%"


def para_el_modelo(perfil: dict) -> str:
    """El perfil como texto, para pegarlo al prompt del redactor.

    Se entrega calculado y con una instrucción explícita: narrar, no recalcular.
    """
    if not perfil:
        return ""

    lineas = [
        "\n\nLECTURA DEL RESULTADO (calculada por el sistema sobre la tabla de arriba).",
        "Estos números son correctos y ya están hechos: cítelos tal cual, no los "
        "recalcule ni los redondee a ojo. Comente solo lo que aporte algo; si nada "
        "de esto es relevante para la pregunta, ignórelo.",
    ]

    ranking = perfil.get("ranking")
    if ranking:
        # El total va explícito. Sin él, el redactor suma las filas de cabeza y le
        # sale distinto cada vez: la misma pregunta da un total en una corrida y
        # otro en la siguiente. Un total sumado a mano es un número inventado con
        # aire de resumen.
        lineas.append(
            f"\nReparto por {ranking['columna']} ({ranking['cuantos']} valores). "
            f"El total de la tabla es {ranking['total']:,.2f} — usá este, no lo sumes:"
        )
        for posicion in ranking["posiciones"]:
            lineas.append(
                f"- {posicion['puesto']}º «{posicion['nombre']}»: "
                f"{_pct(posicion['participacion'])} del total."
            )
        for brecha in ranking.get("brechas", []):
            lineas.append(
                f"- El {brecha['de']}º supera al {brecha['a']}º («{brecha['nombre']}») "
                f"por {_pct(brecha['relativa'])} ({brecha['absoluta']:,.2f} de diferencia)."
            )
        concentracion = ranking.get("concentracion") or {}
        if "top3" in concentracion:
            lineas.append(f"- Los tres primeros concentran el {_pct(concentracion['top3'])}.")
        if "top5" in concentracion:
            lineas.append(f"- Los cinco primeros concentran el {_pct(concentracion['top5'])}.")
        if "filas_para_la_mitad" in ranking:
            lineas.append(
                f"- Con {ranking['filas_para_la_mitad']} de los {ranking['cuantos']} valores "
                f"ya se llega a la mitad del total."
            )
        resto = ranking.get("resto")
        if resto:
            lineas.append(
                f"- Los {resto['cuantos']} restantes van de {resto['desde']:,.2f} "
                f"a {resto['hasta']:,.2f}."
            )

    serie = perfil.get("serie")
    if serie:
        signo = "subió" if serie["variacion"] >= 0 else "bajó"
        lineas.append(
            f"\nEvolución por {serie['columna']} ({serie['periodos']} períodos):"
            f"\n- El último ({serie['ultimo']['periodo']}) {signo} "
            f"{_pct(abs(serie['variacion']))} respecto del anterior "
            f"({serie['previo']['periodo']})."
            f"\n- Está {_pct(abs(serie['contra_promedio'] or 0))} "
            f"{'por encima' if (serie['contra_promedio'] or 0) >= 0 else 'por debajo'} "
            f"del promedio de los períodos anteriores."
            f"\n- Máximo en {serie['maximo'][0]}, mínimo en {serie['minimo'][0]}."
        )

    return "\n".join(lineas)
