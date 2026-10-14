"""Saneado del HTML que escribe el modelo para la respuesta final.

El modelo redacta en HTML para que la respuesta se lea bien (negritas, listas,
párrafos) en vez de un bloque de texto plano. Pero ese HTML pasa por dos manos
poco confiables antes de llegar al navegador:

1. El modelo, que puede equivocarse.
2. **Los datos**. El texto de la respuesta menciona valores que salen de la
   tabla `sabana` — identificadores de prestador, diagnósticos. Si alguien escribió
   `<img src=x onerror=...>` adentro de un campo de texto de la base, ese string
   llega al prompt y puede terminar en la respuesta.

Por eso acá no se confía en nada: se permite una lista corta de etiquetas y
**ningún atributo**. Sin atributos no hay `onerror`, no hay `href="javascript:"`
ni `style`. Todo lo demás se descarta y su contenido se escapa como texto.

No usa librerías: `html.parser` es de la biblioteca estándar.
"""

from html.parser import HTMLParser
from html import escape

# Deliberadamente cortas. No se permiten tablas: la tabla de resultados ya la
# arma el frontend con los datos reales, y dejar que el modelo dibuje otra es lo
# que hacía que repitiera los mismos números dos veces.
ETIQUETAS = {"p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "code", "h4"}
SIN_CIERRE = {"br"}


class _Saneador(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        # Etiquetas abiertas que sí dejamos pasar, para no cerrar de más.
        self.abiertas: list[str] = []

    def handle_starttag(self, tag, _atributos):
        # Los atributos se descartan siempre, sin excepción.
        if tag in ETIQUETAS:
            if tag in SIN_CIERRE:
                self.partes.append(f"<{tag}>")
            else:
                self.abiertas.append(tag)
                self.partes.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in ETIQUETAS and tag not in SIN_CIERRE and tag in self.abiertas:
            # Se cierra lo que quedó abierto por dentro, para no dejar HTML roto.
            while self.abiertas:
                abierta = self.abiertas.pop()
                self.partes.append(f"</{abierta}>")
                if abierta == tag:
                    break

    def handle_startendtag(self, tag, _atributos):
        if tag in ETIQUETAS:
            self.partes.append(f"<{tag}>" if tag in SIN_CIERRE else f"<{tag}></{tag}>")

    def handle_data(self, dato):
        self.partes.append(escape(dato))

    def resultado(self) -> str:
        cierres = "".join(f"</{t}>" for t in reversed(self.abiertas))
        return "".join(self.partes) + cierres


def sanear(html: str) -> str:
    """Deja solo las etiquetas permitidas, sin atributos. Nunca lanza."""
    if not html:
        return ""
    saneador = _Saneador()
    try:
        saneador.feed(html)
        saneador.close()
    except Exception:
        # Si el parser se atraganta, se devuelve todo escapado: peor es dejar
        # pasar HTML a medio parsear.
        return escape(html)
    return saneador.resultado().strip()


class _ATexto(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []

    def handle_data(self, dato):
        self.partes.append(dato)

    def handle_starttag(self, tag, _atributos):
        if tag in ("br", "p", "li"):
            self.partes.append("\n")


def a_texto(html: str) -> str:
    """El HTML sin etiquetas. Va al historial de la conversación, que no se renderiza."""
    if not html:
        return ""
    plano = _ATexto()
    try:
        plano.feed(html)
        plano.close()
    except Exception:
        return html
    lineas = [linea.strip() for linea in "".join(plano.partes).splitlines()]
    return "\n".join(linea for linea in lineas if linea)
