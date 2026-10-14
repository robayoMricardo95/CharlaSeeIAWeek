"""El estado que viaja por el grafo.

Un turno pasa por varios nodos y cada uno deja su rastro acá. El estado completo
se guarda por `thread_id` en el checkpointer, así que lo que es de este turno hay
que blanquearlo al empezar — lo hace el endpoint, no un nodo (ver `main.py`).

Los costos van en campos separados en vez de acumularse en uno solo: con el
checkpointer, un acumulador se sumaría entre turnos y el medidor mentiría.
"""

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from uso_tokens import UsoTurno


class EstadoChat(TypedDict):
    # `add_messages` agrega al historial en vez de reemplazarlo: es lo que hace
    # conversacional al grafo.
    mensajes: Annotated[list, add_messages]

    # --- Lo que decidió el modelo (ver decisiones.py) ---
    decisiones: dict
    revision: dict  # qué encontró el revisor de decisiones
    reintentos: int  # cuántas veces se rehizo la comprensión

    # --- El sondeo: consultas de reconocimiento antes de armar el plan ---
    # Viven aparte de `extracciones` porque no aportan al número final: solo le
    # dicen al modelo algo que no podía saber (el último mes cargado, qué valores
    # tiene una columna). Se guardan igual, para poder auditar qué miró.
    sondeos: dict  # nombre -> {proposito, sql, resultado}
    rondas_sondeo: int  # cuántas vueltas de sondeo se hicieron ya

    # --- La extracción: una entrada por consulta base, indexada por su nombre ---
    sqls: dict  # nombre -> SQL armado por reglas.construir_sql
    inyectados: dict  # nombre -> filtros que agregó el código por regla obligatoria
    extracciones: dict  # nombre -> Resultado (lo que devolvió la base)
    error_extraccion: str

    # --- La composición en Python, dentro del sandbox ---
    composicion: str  # el código que efectivamente se ejecutó
    impreso: str  # lo que el código imprimió, por si sirve para depurar
    error_composicion: str
    reparaciones: int

    # --- De dónde sale el número, en lenguaje de negocio ---
    # Se muestra junto a la respuesta, no escondido en el panel: un usuario de
    # negocio no puede aceptar una cifra que aparece sola.
    derivacion: dict
    # La lectura aritmetica del resultado (perfil.py). La calcula codigo,
    # no el modelo: brechas, concentracion y variacion entre periodos.
    perfil: dict
    # La lectura que el modelo decidio: que comparar lo elige el, la
    # cuenta la hace el sandbox. Es lo que puede atar esta tabla con lo
    # que se pregunto turnos atras, cosa que el perfil fijo no puede.
    lectura: dict
    # Lo que la pregunta daba por hecho ("el alza de 2009") contra lo que dice el
    # total por período. Lo calcula `reglas.contrastar_premisa`, no el modelo.
    premisa: dict

    # --- El resultado final que ve la persona ---
    resultado: dict | None
    revision_resultado: dict
    respuesta: str  # HTML saneado, listo para renderizar
    mostrar_tabla: bool  # si el resultado va como tabla o dentro del texto

    uso_comprension: UsoTurno | None
    uso_revision: UsoTurno | None
    uso_reparacion: UsoTurno | None
    uso_revision_resultado: UsoTurno | None
    uso_lectura: UsoTurno | None
    uso_explicacion: UsoTurno | None
    uso_redaccion: UsoTurno | None
