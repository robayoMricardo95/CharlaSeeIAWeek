"""Medición de tokens y costo de cada llamada al modelo.

Una responsabilidad: traducir el `usage_metadata` que devuelve LangChain a
tokens y dólares, y acumularlo por conversación. No sabe nada de FastAPI.

El acumulado vive en memoria: si se reinicia el backend, arranca en cero.
Cuando exista persistencia real (etapa 8 de la hoja de ruta) esto pasa a la base.
"""

from typing import TypedDict

# ---------------------------------------------------------------------------
# Tabla de precios: dólares por millón de tokens.
#
# OJO: hay que revisarla cada tanto contra las páginas oficiales.
#   Anthropic: https://www.anthropic.com/pricing#api
#   OpenAI:    https://openai.com/api/pricing/
# Última revisión: 2026-09-04.
#
# `cache_lectura` es lo que cuesta un token que ya estaba cacheado (mucho más
# barato). `cache_escritura` es grabarlo la primera vez: Anthropic cobra 25%
# extra; OpenAI no cobra por escribir, por eso ahí va al mismo precio que la
# entrada normal.
# ---------------------------------------------------------------------------


class Precios(TypedDict):
    entrada: float
    salida: float
    cache_lectura: float
    cache_escritura: float


PRECIOS: dict[str, Precios] = {
    # --- Anthropic (Claude) ---
    "claude-fable-5-1": {"entrada": 10.0, "salida": 50.0, "cache_lectura": 0.25, "cache_escritura": 12.5},
    "claude-fable-5": {"entrada": 10.0, "salida": 50.0, "cache_lectura": 1.0, "cache_escritura": 12.5},
    "claude-opus-5": {"entrada": 5.0, "salida": 25.0, "cache_lectura": 0.5, "cache_escritura": 6.25},
    "claude-opus-4-8": {"entrada": 5.0, "salida": 25.0, "cache_lectura": 0.5, "cache_escritura": 6.25},
    "claude-opus-4-7": {"entrada": 5.0, "salida": 25.0, "cache_lectura": 0.5, "cache_escritura": 6.25},
    "claude-opus-4-6": {"entrada": 5.0, "salida": 25.0, "cache_lectura": 0.5, "cache_escritura": 6.25},
    "claude-sonnet-5": {"entrada": 2.0, "salida": 10.0, "cache_lectura": 0.2, "cache_escritura": 2.5},
    "claude-sonnet-4-6": {"entrada": 3.0, "salida": 15.0, "cache_lectura": 0.3, "cache_escritura": 3.75},
    "claude-haiku-4-5": {"entrada": 1.0, "salida": 5.0, "cache_lectura": 0.1, "cache_escritura": 1.25},
    # --- OpenAI ---
    "gpt-5": {"entrada": 1.25, "salida": 10.0, "cache_lectura": 0.125, "cache_escritura": 1.25},
    "gpt-5-mini": {"entrada": 0.25, "salida": 2.0, "cache_lectura": 0.025, "cache_escritura": 0.25},
    "gpt-5-nano": {"entrada": 0.05, "salida": 0.4, "cache_lectura": 0.005, "cache_escritura": 0.05},
    "gpt-4.1": {"entrada": 2.0, "salida": 8.0, "cache_lectura": 0.5, "cache_escritura": 2.0},
    "gpt-4.1-mini": {"entrada": 0.4, "salida": 1.6, "cache_lectura": 0.1, "cache_escritura": 0.4},
    "gpt-4.1-nano": {"entrada": 0.1, "salida": 0.4, "cache_lectura": 0.025, "cache_escritura": 0.1},
    "gpt-4o": {"entrada": 2.5, "salida": 10.0, "cache_lectura": 1.25, "cache_escritura": 2.5},
    "gpt-4o-mini": {"entrada": 0.15, "salida": 0.6, "cache_lectura": 0.075, "cache_escritura": 0.15},
    "o3": {"entrada": 2.0, "salida": 8.0, "cache_lectura": 0.5, "cache_escritura": 2.0},
    "o4-mini": {"entrada": 1.1, "salida": 4.4, "cache_lectura": 0.275, "cache_escritura": 1.1},
}

# Si el modelo no está en la tabla no inventamos un precio: se cobra 0 y el
# frontend avisa que la medición está incompleta.
SIN_PRECIO: Precios = {"entrada": 0.0, "salida": 0.0, "cache_lectura": 0.0, "cache_escritura": 0.0}


def precios_de(modelo: str) -> tuple[Precios, bool]:
    """Devuelve (precios, conocido). Tolera sufijos de fecha como gpt-4o-2024-08-06."""
    if modelo in PRECIOS:
        return PRECIOS[modelo], True
    # El nombre más largo primero: así gpt-4o-mini-xxx no cae en gpt-4o.
    for nombre in sorted(PRECIOS, key=len, reverse=True):
        if modelo.startswith(nombre):
            return PRECIOS[nombre], True
    return SIN_PRECIO, False


class UsoTurno(TypedDict):
    modelo: str
    precio_conocido: bool
    tokens_entrada: int
    tokens_salida: int
    tokens_cache_lectura: int
    tokens_cache_escritura: int
    costo_entrada: float
    costo_salida: float
    costo_total: float


class ResumenUso(TypedDict):
    consultas: int
    respuestas: int
    tokens_entrada: int
    tokens_salida: int
    tokens_cache_lectura: int
    tokens_cache_escritura: int
    costo_entrada: float
    costo_salida: float
    costo_total: float
    precio_conocido: bool
    modelos: list[str]


def resumen_vacio() -> ResumenUso:
    return {
        "consultas": 0,
        "respuestas": 0,
        "tokens_entrada": 0,
        "tokens_salida": 0,
        "tokens_cache_lectura": 0,
        "tokens_cache_escritura": 0,
        "costo_entrada": 0.0,
        "costo_salida": 0.0,
        "costo_total": 0.0,
        "precio_conocido": True,
        "modelos": [],
    }


def medir(respuesta_del_modelo, modelo: str) -> UsoTurno:
    """Traduce la respuesta de LangChain (cualquier proveedor) a tokens y dólares.

    `usage_metadata` es el formato estándar de LangChain: lo llenan igual
    ChatOpenAI y ChatAnthropic, así que esto no depende del proveedor.
    """
    uso = getattr(respuesta_del_modelo, "usage_metadata", None) or {}
    detalle_entrada = uso.get("input_token_details") or {}

    cache_lectura = int(detalle_entrada.get("cache_read", 0))
    cache_escritura = int(detalle_entrada.get("cache_creation", 0))
    # input_tokens ya incluye lo cacheado: a precio pleno se cobra el resto.
    entrada_total = int(uso.get("input_tokens", 0))
    entrada_plena = max(entrada_total - cache_lectura - cache_escritura, 0)
    salida = int(uso.get("output_tokens", 0))

    precios, conocido = precios_de(modelo)
    costo_entrada = (
        entrada_plena * precios["entrada"]
        + cache_lectura * precios["cache_lectura"]
        + cache_escritura * precios["cache_escritura"]
    ) / 1_000_000
    costo_salida = salida * precios["salida"] / 1_000_000

    return {
        "modelo": modelo,
        "precio_conocido": conocido,
        "tokens_entrada": entrada_total,
        "tokens_salida": salida,
        "tokens_cache_lectura": cache_lectura,
        "tokens_cache_escritura": cache_escritura,
        "costo_entrada": costo_entrada,
        "costo_salida": costo_salida,
        "costo_total": costo_entrada + costo_salida,
    }


def sumar_usos(*usos: UsoTurno) -> UsoTurno:
    """Junta varias llamadas al modelo en un solo turno.

    Desde que el grafo tiene tres nodos, una pregunta puede gastar en analizar,
    en reparar el SQL y en redactar la respuesta. Para el medidor de costo eso
    sigue siendo una sola consulta del usuario.
    """
    presentes = [uso for uso in usos if uso]
    if not presentes:
        raise ValueError("sumar_usos necesita al menos un uso")
    total: UsoTurno = {
        "modelo": " + ".join(dict.fromkeys(uso["modelo"] for uso in presentes)),
        "precio_conocido": all(uso["precio_conocido"] for uso in presentes),
        "tokens_entrada": sum(uso["tokens_entrada"] for uso in presentes),
        "tokens_salida": sum(uso["tokens_salida"] for uso in presentes),
        "tokens_cache_lectura": sum(uso["tokens_cache_lectura"] for uso in presentes),
        "tokens_cache_escritura": sum(uso["tokens_cache_escritura"] for uso in presentes),
        "costo_entrada": sum(uso["costo_entrada"] for uso in presentes),
        "costo_salida": sum(uso["costo_salida"] for uso in presentes),
        "costo_total": sum(uso["costo_total"] for uso in presentes),
    }
    return total


class Contador:
    """Acumulado por conversación (thread_id) más el total de la sesión."""

    def __init__(self) -> None:
        self._por_conversacion: dict[str, ResumenUso] = {}

    def registrar(self, thread_id: str, uso: UsoTurno) -> ResumenUso:
        resumen = self._por_conversacion.setdefault(thread_id, resumen_vacio())
        resumen["consultas"] += 1
        resumen["respuestas"] += 1
        _sumar(resumen, uso)
        return resumen

    def olvidar(self, thread_id: str) -> None:
        """Saca una conversación borrada del acumulado de esta sesión."""
        self._por_conversacion.pop(thread_id, None)

    def de_conversacion(self, thread_id: str) -> ResumenUso:
        return self._por_conversacion.get(thread_id, resumen_vacio())

    def total(self) -> ResumenUso:
        total = resumen_vacio()
        for resumen in self._por_conversacion.values():
            total["consultas"] += resumen["consultas"]
            total["respuestas"] += resumen["respuestas"]
            _sumar(total, resumen)
        return total


def _sumar(destino: ResumenUso, origen) -> None:
    """Suma tokens, costos y modelos de `origen` sobre `destino`.

    Sirve tanto para un turno (UsoTurno) como para otro resumen: comparten
    los nombres de los campos numéricos.
    """
    for campo in (
        "tokens_entrada",
        "tokens_salida",
        "tokens_cache_lectura",
        "tokens_cache_escritura",
        "costo_entrada",
        "costo_salida",
        "costo_total",
    ):
        destino[campo] += origen[campo]
    destino["precio_conocido"] = destino["precio_conocido"] and origen["precio_conocido"]
    modelos = origen["modelos"] if "modelos" in origen else [origen["modelo"]]
    for modelo in modelos:
        if modelo not in destino["modelos"]:
            destino["modelos"].append(modelo)
