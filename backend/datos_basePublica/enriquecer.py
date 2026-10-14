"""
enriquecer.py — opcional. Agrega a la base las descripciones oficiales de los
códigos CIE-9 de diagnóstico, bajándolas del catálogo público de la Biblioteca
Nacional de Medicina de Estados Unidos (NLM).

¿Por qué es un script aparte y opcional?

Porque son 11 897 códigos de diagnóstico distintos y el catálogo está **en
inglés**. Traducir ese volumen de terminología clínica sin una fuente oficial en
español sería inventar significados médicos, y eso en un repositorio público es
peor que no tener descripciones. Así que:

  - `preparar.py` deja los códigos tal cual, más el capítulo de la CIE-9 en
    español (19 grupos) y la categoría de tres dígitos. Con eso ya se puede
    hablar de los diagnósticos en términos de negocio.
  - Este script, si lo corres, agrega una tabla `cie9_descripciones` con el
    texto oficial en inglés. Útil para pasárselo al agente como contexto: un
    LLM sí puede leer 'Acute myocardial infarction' y explicarlo en español en
    el momento, sin que nosotros hayamos fijado una traducción en la base.

Requiere red hacia clinicaltables.nlm.nih.gov. Si tu entorno la bloquea, el
proyecto funciona igual sin esto.

Uso:
    python enriquecer.py                 # sobre reclamos.db
    python enriquecer.py --base otra.db

Desde un notebook:
    from enriquecer import main
    main()
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import requests

API = "https://clinicaltables.nlm.nih.gov/api/icd9cm_dx/v3/search"
LOTE = 400          # códigos por consulta
PAUSA = 0.3         # segundos entre consultas, para no golpear el servicio


def codigos_de(base: Path):
    con = sqlite3.connect(str(base))
    cods = [r[0] for r in con.execute(
        "SELECT DISTINCT codigo FROM diagnosticos "
        "WHERE codigo IS NOT NULL AND codigo <> 'OTHER' ORDER BY codigo")]
    con.close()
    return cods


def con_punto(codigo):
    """La API espera el código con punto decimal: '41401' -> '414.01'."""
    c = codigo.strip().upper()
    if c.startswith(("V", "E")):
        corte = 4 if c.startswith("E") else 3
    else:
        corte = 3
    return c if len(c) <= corte else f"{c[:corte]}.{c[corte:]}"


def descargar(codigos):
    """Devuelve {codigo_original: descripcion}. Los que no aparezcan se omiten."""
    hallados = {}
    por_punto = {con_punto(c): c for c in codigos}

    for i in range(0, len(codigos), LOTE):
        tramo = codigos[i:i + LOTE]
        consulta = " ".join(con_punto(c) for c in tramo)
        try:
            r = requests.get(API, timeout=60, params={
                "terms": consulta, "maxList": LOTE * 2, "sf": "code",
                "df": "code,long_name"})
            r.raise_for_status()
            # La respuesta es [total, [codigos], null, [[code, long_name], ...]]
            for fila in (r.json()[3] or []):
                cod_api, texto = fila[0], fila[1]
                original = por_punto.get(cod_api.upper())
                if original:
                    hallados[original] = texto
        except requests.RequestException as e:
            print(f"  tramo {i//LOTE + 1}: falló ({type(e).__name__}). Sigo.")
        print(f"  {min(i + LOTE, len(codigos)):>6,} de {len(codigos):,} "
              f"consultados, {len(hallados):,} con descripción", end="\r")
        time.sleep(PAUSA)

    print()
    return hallados


def guardar(base: Path, descripciones):
    con = sqlite3.connect(str(base))
    con.execute("DROP TABLE IF EXISTS cie9_descripciones")
    con.execute("""CREATE TABLE cie9_descripciones (
        codigo TEXT PRIMARY KEY, descripcion_en TEXT)""")
    con.executemany("INSERT INTO cie9_descripciones VALUES (?,?)",
                    sorted(descripciones.items()))
    con.commit()
    n = con.execute("SELECT count(*) FROM cie9_descripciones").fetchone()[0]
    con.close()
    return n


def main(base="reclamos.db"):
    base = Path(base)
    if not base.exists():
        raise SystemExit(f"No existe {base}. Corre primero:  python preparar.py")

    cods = codigos_de(base)
    print(f"{len(cods):,} códigos CIE-9 distintos en la base.")
    print(f"Consultando el catálogo de la NLM en tramos de {LOTE} ...")

    desc = descargar(cods)
    if not desc:
        raise SystemExit(
            "No se obtuvo ninguna descripción. Puede ser que tu red bloquee\n"
            "clinicaltables.nlm.nih.gov. El proyecto funciona igual sin esto:\n"
            "los diagnósticos ya tienen su capítulo de la CIE-9 en español.")

    n = guardar(base, desc)
    print(f"\nTabla cie9_descripciones: {n:,} códigos "
          f"({n/len(cods):.0%} de cobertura).")
    print("Las descripciones están en inglés, como las publica la NLM.")
    return n


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="reclamos.db")
    main(p.parse_args().base)
