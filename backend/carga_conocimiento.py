"""Carga el diccionario de la base pública y lo arma como un solo bloque de texto.

Una responsabilidad: leer `datos_basePublica/diccionario_sabana.yaml` y devolverlo
listo para el prompt. No sabe de modelos ni de FastAPI.

Qué trae el diccionario: qué significa cada columna de `sabana`, sus valores
exactos, las trampas del dato, las métricas canónicas y consultas de ejemplo con
resultado verificado contra la base.

**Caché.** Este bloque es idéntico en cada pregunta, así que va PRIMERO en el
prompt: el proveedor lo reconoce como prefijo repetido y lo cobra mucho más
barato (OpenAI cachea solo desde 1024 tokens; este bloque los supera de sobra).
Todo lo que cambia entre preguntas — la fecha de hoy, la pregunta misma — tiene
que ir DESPUÉS. Un solo byte distinto adelante invalida la caché entera.
El ahorro se puede verificar en el medidor de costo del frontend: la línea de
"tokens leídos de caché" debería crecer a partir de la segunda pregunta.
"""

from pathlib import Path

DICCIONARIO = Path(__file__).parent / "datos_basePublica" / "diccionario_sabana.yaml"


def _sin_comentarios(texto: str) -> str:
    """Quita las líneas que son solo comentario.

    Los comentarios del YAML explican cómo se mantiene el archivo, no los datos:
    al modelo no le aportan y cada línea se paga en cada pregunta.
    """
    return "\n".join(
        linea for linea in texto.splitlines() if not linea.lstrip().startswith("#")
    ).strip()


def cargar() -> str:
    """Devuelve el diccionario como texto, rotulado.

    Si falta, revienta al arrancar. Es a propósito: un agente que responde sin
    el diccionario da números creíbles y falsos, que es el peor error posible.
    """
    if not DICCIONARIO.exists():
        raise FileNotFoundError(
            f"Falta el diccionario {DICCIONARIO}. El agente no puede arrancar sin él."
        )
    contenido = _sin_comentarios(DICCIONARIO.read_text(encoding="utf-8"))
    return f"<!-- {DICCIONARIO.name} -->\n```yaml\n{contenido}\n```"


def resumen() -> list[tuple[str, int]]:
    """(nombre, caracteres) del diccionario. Para diagnóstico."""
    return [(DICCIONARIO.name, len(cargar()))]
