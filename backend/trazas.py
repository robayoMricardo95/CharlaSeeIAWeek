"""Seguimiento de lo que hace el agente: LangSmith o archivo local.

Una sola variable en `.env` decide, y el grafo no se entera de cuál está activa:

    TRAZAS=langsmith   sube todo a la nube de LangSmith
    TRAZAS=local       escribe backend/trazas/AAAA-MM-DD.jsonl
    TRAZAS=ninguna     no registra nada (default)

Qué sale de la máquina con cada opción — importa decidirlo a conciencia:

- `langsmith` manda los prompts completos, y eso incluye los cuatro diccionarios
  de negocio, que son el activo del proyecto. También los resultados de las
  consultas. A cambio se ven los seis nodos con sus tiempos y tokens en una
  interfaz hecha para esto.
- `local` guarda la misma información en un archivo de texto y no sale nada.
  Menos cómodo de leer, cero exposición.

Cambiar de una a otra es editar el `.env` y reiniciar. Nada más.
"""

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

CARPETA = Path(__file__).parent / "trazas"


class TrazaLocal(BaseCallbackHandler):
    """Escribe una línea JSON por llamada al modelo, con nodo, tokens y tiempo.

    Es el equivalente casero de lo que muestra LangSmith. No pretende competir
    con su interfaz: pretende que se pueda auditar sin mandar nada afuera.
    """

    def __init__(self, carpeta: Path = CARPETA):
        self.carpeta = carpeta
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self._inicios: dict[Any, float] = {}
        self._contexto: dict[Any, dict] = {}

    def _archivo(self) -> Path:
        return self.carpeta / f"{date.today().isoformat()}.jsonl"

    def _anotar(self, registro: dict) -> None:
        registro["momento"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._archivo().open("a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
        metadata = kwargs.get("metadata") or {}
        self._inicios[run_id] = time.perf_counter()
        self._contexto[run_id] = {
            # LangGraph pone el nombre del nodo acá: es lo que permite saber si
            # el gasto vino de comprender, de revisar o de redactar.
            "nodo": metadata.get("langgraph_node", "desconocido"),
            "thread_id": metadata.get("thread_id", ""),
            "mensajes": [
                {"tipo": m.type, "texto": m.content[:2000]}
                for tanda in messages
                for m in tanda
            ],
        }

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        contexto = self._contexto.pop(run_id, {})
        inicio = self._inicios.pop(run_id, None)
        generacion = response.generations[0][0] if response.generations else None
        mensaje = getattr(generacion, "message", None)
        uso = getattr(mensaje, "usage_metadata", None) or {}
        self._anotar(
            {
                "evento": "llamada_al_modelo",
                "nodo": contexto.get("nodo"),
                "thread_id": contexto.get("thread_id"),
                "segundos": round(time.perf_counter() - inicio, 2) if inicio else None,
                "tokens_entrada": uso.get("input_tokens"),
                "tokens_salida": uso.get("output_tokens"),
                "tokens_cacheados": (uso.get("input_token_details") or {}).get("cache_read"),
                "prompt": contexto.get("mensajes"),
                "respuesta": str(getattr(mensaje, "content", ""))[:4000],
                "tool_calls": getattr(mensaje, "tool_calls", None),
            }
        )

    def on_llm_error(self, error, *, run_id=None, **kwargs):
        contexto = self._contexto.pop(run_id, {})
        self._inicios.pop(run_id, None)
        self._anotar(
            {
                "evento": "error_del_modelo",
                "nodo": contexto.get("nodo"),
                "thread_id": contexto.get("thread_id"),
                "error": f"{type(error).__name__}: {error}",
            }
        )


def _fijar(**variables: str) -> None:
    """Escribe variables de entorno y hace que LangSmith las relea.

    Hace falta el segundo paso. `langsmith` cachea la lectura de sus variables
    la primera vez que se le pregunta, y para cuando `configurar()` corre, el
    paquete ya se importó con `langchain_openai` — así que un `os.environ[...]`
    a secas no cambia nada. Medido: encender el trazado después
    del import dejaba `tracing_is_enabled()` en False, y todo el modo langsmith
    era letra muerta.

    Importa en las dos direcciones, y la peligrosa es apagar: si el entorno
    trajera `LANGSMITH_TRACING=true`, poner `TRAZAS=local` no lo apagaría y los
    diccionarios de negocio se subirían igual, en silencio.

    Si una versión de langsmith cambia el nombre de la caché, esto no rompe: se
    avisa y se sigue. Quedarse sin trazas es molesto; romper el arranque, peor.
    """
    os.environ.update(variables)
    try:
        from langsmith import utils as ls_utils

        ls_utils.get_env_var.cache_clear()
    except Exception as error:  # noqa: BLE001 - versión distinta de langsmith
        print(f"AVISO: no se pudo refrescar la config de LangSmith ({error}).")


def configurar() -> tuple[list[BaseCallbackHandler], str]:
    """Devuelve (callbacks, modo_efectivo). Llamar una vez al arrancar.

    Si se pide `langsmith` y falta la clave, cae a `local` en vez de arrancar sin
    ningún registro: quedarse ciego en silencio es la peor de las opciones.
    """
    modo = (os.getenv("TRAZAS") or "ninguna").strip().lower()

    if modo == "langsmith":
        if not os.getenv("LANGSMITH_API_KEY"):
            print("TRAZAS=langsmith pero falta LANGSMITH_API_KEY. Se usa el modo local.")
            _fijar(LANGSMITH_TRACING="false")
            return [TrazaLocal()], "local"
        # LangChain lee estas variables por su cuenta: con esto los seis nodos
        # quedan instrumentados sin tocar una línea del grafo.
        _fijar(
            LANGSMITH_TRACING="true",
            LANGSMITH_PROJECT=os.getenv("LANGSMITH_PROJECT") or "agente-analitica",
        )
        return [], "langsmith"

    if modo == "local":
        # Apagar LangSmith explícitamente: si quedó encendido, seguiría subiendo
        # los prompts en silencio aunque acá se pida el modo local.
        _fijar(LANGSMITH_TRACING="false")
        return [TrazaLocal()], "local"

    if modo not in ("ninguna", ""):
        print(f"TRAZAS={modo} no se reconoce. Valores: langsmith, local, ninguna.")
    # Apagar explícitamente: si quedó encendido de una corrida anterior del
    # mismo proceso, esta línea lo desactiva.
    _fijar(LANGSMITH_TRACING="false")
    return [], "ninguna"
