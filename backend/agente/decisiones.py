"""Las decisiones del agente, como datos y no como SQL.

Dos ideas sostienen este archivo.

**Primera: el modelo no escribe SQL.** Declara QUÉ decidió — qué columna, qué
filtro, qué medida — y `reglas.py` traduce eso a la consulta. Auditar el texto de
un SQL se rompe apenas aparece un subquery; auditar decisiones no, porque filtrar
por un producto es filtrar por un producto por retorcida que sea la consulta.

**Segunda: la frontera entre SQL y Python.** El SQL hace *extracción* — filtra,
agrupa, agrega sobre la tabla. Todo lo que venga después — cruzar dos resultados,
dividir para una razón, comparar dos períodos — es Python sobre las tablas ya
extraídas. Por eso `Decisiones` no describe una consulta sino varias, más el
código que las combina.

Eso resuelve sin plantillas los casos que un solo `SELECT` no puede: costo
promedio por afiliado (pagado / afiliados distintos), participación sobre el
total, 2009 contra 2008. Todos son dos o tres extracciones simples y una cuenta.

La estructura sigue los pasos del protocolo que está en las instrucciones del
agente (`main.py`): cada paso es un campo de acá, así el razonamiento queda
auditable como dato en vez de como un bloque de texto que después hay que
interpretar.
"""

from typing import Literal

from pydantic import BaseModel, Field

Operador = Literal["=", "!=", "IN", "NOT IN", ">=", "<=", "BETWEEN", "LIKE", "IS NOT NULL"]

# De dónde salió cada filtro. Lo usa el revisor para distinguir un filtro que
# pidió el usuario de uno que el agente puso porque una regla lo obliga, y el
# frontend para marcar cuáles garantiza el código.
Origen = Literal["pregunta", "default", "obligatoria", "inyectado"]

# Los pasos del protocolo. Sirven para ubicar cada novedad en el punto del
# razonamiento donde apareció.
Paso = Literal[
    "temporalidad",
    "diccionario",
    "trampas",
    "interpretacion",
    "sondeo",
    "participacion",
    "variables",
    "metricas",
    "composicion",
]


class Filtro(BaseModel):
    columna: str = Field(description="Nombre exacto de una columna que exista en la tabla")
    operador: Operador
    valores: list[str] = Field(
        description="Valores del filtro. Para BETWEEN, exactamente dos. "
        "Para IS NOT NULL, lista vacía."
    )
    origen: Origen = Field(
        description="'pregunta' si lo pidió el usuario; 'default' si lo decidiste tú "
        "porque la pregunta no lo dice; 'obligatoria' si el diccionario lo exige para "
        "que la métrica tenga sentido (por ejemplo ambito = 'Hospitalario' para la "
        "estancia promedio)"
    )
    justificacion: str = Field(description="Una línea: por qué está este filtro")


class Medida(BaseModel):
    """Una columna calculada de la consulta. Una consulta puede traer varias.

    Pedir `SUM(monto_pagado)` y `COUNT(DISTINCT id_afiliado)` en la misma
    consulta es una sola extracción, no dos: aprovéchela.
    """

    agregacion: Literal["SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"]
    columna: str = Field(description="Columna sobre la que se agrega. Vacío solo para COUNT.")
    alias: str = Field(
        description="Nombre de la columna en el resultado. Es el que vas a usar en "
        "el código de composición, así que elígelo claro: `pagado`, `afiliados`."
    )


class ConsultaBase(BaseModel):
    """Una extracción simple: filtrar, agrupar, agregar. Nada más.

    Todo lo que un `SELECT` sobre `sabana` puede sacar entra acá. Lo que necesite
    cruzar dos extracciones, dividir, comparar períodos o calcular una razón NO
    va acá: va en `composicion`, en Python.
    """

    nombre: str = Field(
        description="Identificador corto en minúsculas, sin espacios. Con este nombre "
        "queda disponible en el código de composición: `consultas['<nombre>']`."
    )
    proposito: str = Field(description="Qué aporta esta consulta al resultado final")
    medidas: list[Medida] = Field(
        description="Vacío para un listado sin agregar (entonces se traen las dimensiones)"
    )
    dimensiones: list[str] = Field(
        description="Columnas por las que se agrupa (GROUP BY). Vacío para un total único."
    )
    filtros: list[Filtro]
    orden_por: str = Field(
        description="Alias de medida o nombre de dimensión por el que ordenar. Puedes poner "
        "varias separadas por coma y se ordena por todas en ese orden: 'anio, mes'. Vacío "
        "para ordenar por las dimensiones."
    )
    orden_descendente: bool
    limite: int = Field(description="Máximo de filas. 0 para no limitar.")


class Novedad(BaseModel):
    """Algo digno de mención que apareció en un paso del razonamiento.

    No se reportan los pasos que salieron sin nada que decir: solo lo que tiene
    valor para quien audita. Un supuesto tomado, una regla que se aplicó y cambia
    el número, una ambigüedad que se resolvió de una manera y pudo ser de otra.
    """

    paso: Paso = Field(description="En qué paso del protocolo apareció")
    nivel: Literal["info", "aviso", "error"] = Field(
        description="'info' se aplicó una regla y conviene saberlo; 'aviso' hay un "
        "supuesto o algo incompleto; 'error' el resultado sería incorrecto"
    )
    regla: str = Field(description="Qué regla o criterio del diccionario está en juego")
    detalle: str = Field(description="Qué pasó concretamente, en una o dos líneas")


class Decisiones(BaseModel):
    """El plan completo: qué se entendió, qué se extrae y cómo se combina."""

    # --- Paso 0: ¿esto es una pregunta de datos? ---
    #
    # Va primero a propósito. Antes, un "hola" o un "¿qué sabes hacer?" entraba
    # igual al protocolo y terminaba en una consulta SQL sin sentido o en una
    # pregunta de aclaración desconcertante. Distinguirlo es una decisión, y las
    # decisiones se declaran.
    intencion: Literal["datos", "conversacion"] = Field(
        description="'datos' si se pide información de la tabla `sabana` (el caso normal). "
        "'conversacion' para un saludo, un agradecimiento, una pregunta sobre qué sabes "
        "hacer, o algo que esta base no puede responder. Con 'conversacion' deja "
        "`consultas` y `sondeos` vacíos: el sistema contesta cordialmente."
    )

    # --- Pasos 1 a 4 del protocolo ---
    periodo: str = Field(
        description="Cómo se resolvió el componente temporal a anio/mes concretos, y si "
        "fue explícito o por default. 'Todo el período (2008 y 2009)' si la pregunta no "
        "pide uno."
    )
    interpretacion: str = Field(
        description="La pregunta reformulada en términos del diccionario: qué métrica, "
        "sobre qué universo, con qué dimensiones y qué comparación"
    )

    # --- La premisa: lo que la pregunta da por hecho ---
    #
    # Existe por un error real contra la API: a "¿en qué grupos se concentró el
    # alza del costo hospitalario de 2009?" el agente contestó con las alzas por
    # grupo y habló del "incremento total", cuando el costo hospitalario BAJÓ
    # 4,9 %. Los números por grupo eran ciertos; la respuesta, engañosa.
    #
    # El modelo declara qué supone la pregunta, `reglas.verificar_premisa` exige
    # que el plan traiga el total por período, y el código compara los dos
    # números antes de redactar. Si la premisa no se cumple, se dice primero.
    premisa_direccion: Literal["", "sube", "baja"] = Field(
        description="Si la pregunta da por hecho que algo SUBIÓ ('el alza', 'el aumento', "
        "'por qué creció') pon 'sube'; si da por hecho que BAJÓ ('la caída', 'la "
        "reducción') pon 'baja'. Vacío si la pregunta no supone ningún cambio. Ojo: "
        "'¿cómo cambió?' no supone dirección, va vacío."
    )
    premisa: str = Field(
        description="Solo si premisa_direccion no está vacío: qué total se supone que "
        "cambió y entre qué períodos, en palabras: 'el monto pagado hospitalario subió "
        "de 2008 a 2009'. Para verificarla, incluye una consulta con ESE total "
        "agrupado solo por el período (anio, o anio y mes), con los mismos filtros del "
        "universo y sin abrir por otra dimensión. Vacío si no aplica."
    )

    rama: Literal["costo", "reclamos", "afiliados", "estancia"] = Field(
        description="Familia de la métrica: 'costo' (montos), 'reclamos' (conteos de "
        "reclamos), 'afiliados' (personas distintas), 'estancia' (días de internación)"
    )
    metrica_pedida: str = Field(
        description="Cómo se llama la métrica, con la definición usada: 'Costo (monto "
        "pagado por la aseguradora)', 'Costo promedio por reclamo hospitalario'"
    )
    metrica_es_aditiva: bool = Field(
        description="False para razones y conteos DISTINCT. Si es False, el total NO se "
        "obtiene sumando por dimensión: se recalcula al grano total en `composicion`."
    )

    # --- Parte y todo: solo cuando se pide un porcentaje ---
    #
    # Existe por un modo de fallar conocido. A "¿qué porcentaje del costo
    # hospitalario es de aparato circulatorio?" un modelo puede responder con lo
    # hospitalario de circulatorio sobre TODO circulatorio (hospitalario más
    # ambulatorio): toma la parte como universo. La respuesta es coherente, está
    # bien construida, y contesta otra pregunta.
    #
    # La causa era un vacío, no una distracción: "porcentaje" no tenía denominador
    # definido en ningún lado, así que el modelo tenía que inventar el todo. Estos
    # tres campos lo obligan a decirlo, y `reglas.verificar_participacion` lo
    # comprueba contra los filtros que efectivamente pidió.
    pide_participacion: bool = Field(
        description="True si la pregunta pide un porcentaje, participación, peso o "
        "'cuánto representa' de una categoría sobre un total"
    )
    dimension_de_la_parte: str = Field(
        description="Solo si pide_participacion: la columna cuyo valor es la PARTE. El "
        "denominador es el mismo universo sin filtrar esa columna. En '¿qué porcentaje "
        "del costo hospitalario es de aparato circulatorio?' es `grupo_enfermedad`, NO "
        "`ambito`: el ámbito describe el universo y va en los dos lados. Vacío si no aplica."
    )
    universo_denominador: str = Field(
        description="Solo si pide_participacion: el TODO en palabras, con los filtros que "
        "lo definen — 'todo el monto pagado hospitalario de 2009'. Vacío si no aplica."
    )

    # --- Parada ---
    necesita_aclaracion: bool = Field(
        description="True si la pregunta no se puede resolver sin adivinar algo que "
        "cambia el número, o si pide un dato que la tabla no tiene (por ejemplo gasto "
        "per cápita, o un año fuera de 2008-2009)"
    )
    pregunta_de_aclaracion: str = Field(
        description="Qué hay que preguntarle al usuario. Vacío si no hace falta."
    )

    # --- Sondeo: lo que solo los datos saben ---
    sondeos: list[ConsultaBase] = Field(
        description="Consultas CHICAS de reconocimiento, para despejar una duda sobre los "
        "datos antes de armar el plan: qué valores tiene una columna (listado sin "
        "medidas), si existe un prestador o un código de diagnóstico, cuál es el último "
        "período cargado. Se ejecutan y vuelves a ver esto con el resultado en la mano. "
        "Usa el campo `proposito` para decir QUÉ DUDA resuelve cada una. "
        "Sondea en vez de adivinar, y sondea ANTES de pedir una aclaración: preguntarle "
        "al usuario algo que la base contesta sola es una parada mal puesta. "
        "Déjalo VACÍO si no tienes ninguna duda sobre los datos: es el caso normal."
    )

    # --- Pasos 5 y 6: extracción y cálculo ---
    consultas: list[ConsultaBase] = Field(
        description="Las extracciones necesarias. Una sola si la pregunta se responde con "
        "una agregación directa; varias si hay que cruzar, dividir o comparar."
    )
    composicion: str = Field(
        description="Código Python que combina las consultas y deja el resultado final en "
        "una variable `resultado`. Disponibles: `pd`, `np` y el diccionario `consultas` "
        "con los nombres que elegiste. Déjalo VACÍO si hay una sola consulta y su "
        "resultado ya es la respuesta."
    )

    # --- Auditoría ---
    novedades: list[Novedad] = Field(
        description="Solo lo que vale la pena reportar. No inventes una entrada por paso: "
        "si un paso no tuvo nada digno de mención, no lo incluyas."
    )


class Componente(BaseModel):
    """Un número que entró a la fórmula, con nombre de negocio."""

    nombre: str = Field(
        description="Cómo se llama para una persona, no en la base. 'Monto pagado "
        "hospitalario de 2009', no 'SUM(monto_pagado) WHERE ambito=Hospitalario'."
    )
    valor: float = Field(
        description="El número, tal cual salió de la base. Sin redondear y sin formato: "
        "el formato lo pone el frontend. TIENE que ser un valor que esté en los "
        "resultados que te pasaron; si no está, no lo pongas."
    )
    unidad: Literal["dolares", "porcentaje", "cantidad", "dias"] = Field(
        description="Para que el frontend lo muestre bien"
    )
    de_donde: str = Field(
        description="De qué universo salió, en una línea y en lenguaje de negocio: "
        "'Reclamos hospitalarios de 2009, enfermedades del aparato circulatorio'"
    )


class Derivacion(BaseModel):
    """De dónde sale el número final, contado para alguien de negocio.

    Existe por una razón de confianza, no técnica: un usuario de negocio no puede
    aceptar una cifra que aparece sola. El SQL y el Python ya están en el panel
    de razonamiento, pero eso es para auditar, no para leer. Esto es lo mismo
    dicho en el idioma de quien pregunta, y va junto a la respuesta.

    Los valores se verifican contra los resultados reales antes de mostrarse
    (`nodos._verificar_derivacion`): si un número no aparece en ninguna
    extracción, se marca en vez de mostrarse como si fuera cierto.
    """

    formula: str = Field(
        description="La cuenta en palabras de negocio, con nombres, no con columnas: "
        "'Participación = Pagado de circulatorio ÷ Pagado hospitalario total'. Vacío si el "
        "resultado sale de una sola suma y no hay ninguna cuenta que explicar."
    )
    componentes: list[Componente] = Field(
        description="Los números que entraron a la fórmula, en orden. Vacío si no hubo "
        "fórmula. No incluyas el resultado final acá."
    )
    supuestos: list[str] = Field(
        description="Decisiones que cambian el número y el usuario no pidió explícitamente, "
        "en lenguaje claro: 'Se usó el monto pagado por la aseguradora como costo', 'Se "
        "tomó 2009 por ser el último año disponible'."
    )
    sugerencias: list[str] = Field(
        description="Como máximo dos preguntas de seguimiento que ayudarían a entender "
        "mejor este resultado, escritas para ofrecerlas al usuario: '¿Quiere ver qué "
        "diagnósticos concentran ese costo?'.\n"
        "REGLA DURA: **no pueden contener ni un solo número.** Una sugerencia propone "
        "mirar algo, no afirma nada. Si quieres decir una cifra, va en `componentes`.\n"
        "Tienen que ser específicas de lo que salió en este resultado y usar columnas "
        "que existen en la tabla. Si no se te ocurre nada que aporte de verdad, déjalo "
        "vacío: una sugerencia genérica molesta más de lo que ayuda."
    )


class Hallazgo(BaseModel):
    gravedad: Literal["error", "aviso"] = Field(
        description="'error' bloquea y obliga a rehacer; 'aviso' solo se informa"
    )
    paso: Paso = Field(description="A qué paso del protocolo corresponde el problema")
    que_falta: str = Field(description="Qué regla o decisión está mal o ausente")
    correccion: str = Field(description="Qué habría que cambiar, concretamente")


class Revision(BaseModel):
    """La salida de un nodo revisor.

    El revisor no vuelve a resolver la pregunta: solo busca lo que falta. Por eso
    recibe las decisiones y el protocolo, pero NO la interpretación ni las
    justificaciones del nodo que las tomó — si le muestras el razonamiento que
    llevó a una decisión, la aprueba.

    Tampoco revisa todos los pasos siempre: evalúa cuáles aplican a esta pregunta.
    """

    aprobado: bool
    hallazgos: list[Hallazgo] = Field(description="Vacío si está todo bien")
