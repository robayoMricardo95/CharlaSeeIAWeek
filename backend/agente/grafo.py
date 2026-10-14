"""El grafo: qué nodo va después de cuál.

    comprender ─┬─(dudas sobre los datos)─> sondear ──> comprender
                │
                └─> revisar ─┬─(error y queda intento)─> comprender
                             └─> construir ─> extraer ─> componer ─┬─(falla)─> reparar
                                                                   │            │
                                                                   │            v
                                                                   │        componer
                                                                   v
                                                      revisar_resultado ─> redactar ─> FIN

Los dos ciclos vuelven al mismo nodo pero por motivos opuestos: `revisar` lo manda
de vuelta porque el plan está mal; `sondear`, porque le faltaba un dato que solo
la base tenía. El primero corrige, el segundo informa.

`construir` no llama al modelo: arma el SQL desde las decisiones ya revisadas.
Es el nodo que hace imposible olvidarse un filtro obligatorio.

`componer` corre el Python del modelo en un contenedor aislado (`aislamiento.py`).
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .estado import EstadoChat
from .nodos import Nodos


def construir_grafo(nodos: Nodos):
    grafo = StateGraph(EstadoChat)
    grafo.add_node("comprender", nodos.comprender)
    grafo.add_node("sondear", nodos.sondear)
    grafo.add_node("revisar", nodos.revisar)
    grafo.add_node("construir", nodos.construir)
    grafo.add_node("extraer", nodos.extraer)
    grafo.add_node("componer", nodos.componer)
    grafo.add_node("reparar", nodos.reparar)
    grafo.add_node("revisar_resultado", nodos.revisar_resultado)
    grafo.add_node("leer", nodos.leer)
    grafo.add_node("explicar", nodos.explicar)
    grafo.add_node("redactar", nodos.redactar)

    grafo.add_edge(START, "comprender")
    grafo.add_conditional_edges(
        "comprender", nodos.despues_de_comprender, ["sondear", "revisar", "redactar"]
    )
    grafo.add_edge("sondear", "comprender")
    grafo.add_conditional_edges("revisar", nodos.hay_que_rehacer, ["comprender", "construir"])
    grafo.add_edge("construir", "extraer")
    grafo.add_edge("extraer", "componer")
    grafo.add_conditional_edges("componer", nodos.hay_que_reparar, ["reparar", "revisar_resultado"])
    grafo.add_edge("reparar", "componer")
    grafo.add_edge("revisar_resultado", "leer")
    grafo.add_edge("leer", "explicar")
    grafo.add_edge("explicar", "redactar")
    grafo.add_edge("redactar", END)

    # El checkpointer guarda el estado por `thread_id`, así cada conversación del
    # frontend tiene su propio historial. MemorySaver es en RAM: se pierde al
    # reiniciar el backend (ojo con --reload). Cuando haga falta que sobreviva, se
    # cambia por un checkpointer contra SQLite sin tocar el resto del grafo.
    return grafo.compile(checkpointer=MemorySaver())
