"""
sabana.py — arma la tabla `sabana`: una sola tabla, una fila por reclamo, con
hospitalario y ambulatorio juntos y todo lo que el agente necesita ya resuelto.
Es el modelo AGREGADO; las tablas de preparar.py siguen siendo el DESAGREGADO.

Por qué existe: cada unión que el agente tiene que escribir es una oportunidad
de equivocarse. En la sábana no hay uniones: el agente solo filtra y agrupa.

Qué hace:
  1. Consolida los segmentos: un reclamo puede venir partido en dos filas y el
     segundo segmento no trae fecha ni diagnóstico. Los montos se suman; fecha,
     diagnóstico, médico y prestador salen del segmento 1.
  2. Junta hospitalario y ambulatorio, distinguidos por la columna `ambito`.
  3. Agrega del afiliado: sexo, raza, estado, condado, edad y banda de edad.
  4. Agrega del diagnóstico principal: categoría y grupo de enfermedad (CIE-9).
  5. Calcula `monto_total` = pagado + deducible + coaseguro + pagado por tercero.
  6. Excluye: los años 2007 (solo nov-dic) y 2010 (incompleto), los reclamos
     con monto pagado negativo, y 278 reclamos ambulatorios que solo traen el
     segmento 2 y por eso no tienen fecha. Los montos en cero se quedan.

Requiere que preparar.py ya haya construido reclamos.db en esta carpeta.
Solo usa sqlite3 de la biblioteca estándar.

Desde la terminal:
    python sabana.py

Desde un notebook:
    from sabana import main
    main()
"""

import argparse
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent / "reclamos.db"
ANIOS = (2008, 2009)

# Consolidación de un reclamo de un ámbito. {t} es la tabla y {ambito} la
# etiqueta. El segmento 1 aporta los atributos; la subconsulta suma los montos
# de todos los segmentos del reclamo.
RECLAMOS = """
SELECT '{ambito}'                 AS ambito,
       s.id_reclamo, s.id_afiliado,
       s.fecha_inicio, s.anio, s.mes,
       m.monto_pagado, m.deducible, m.coaseguro, m.monto_pagado_tercero,
       s.diagnostico_1            AS diagnostico_principal,
       s.procedimiento_1          AS procedimiento_principal,
       s.medico_tratante, s.id_prestador,
       {drg}                      AS grupo_drg,
       {estancia}                 AS dias_estancia
FROM {t} s
JOIN (SELECT id_reclamo,
             sum(monto_pagado)                AS monto_pagado,
             sum(coalesce(deducible, 0))      AS deducible,
             sum(coalesce(coaseguro, 0))      AS coaseguro,
             sum(coalesce(monto_pagado_tercero, 0)) AS monto_pagado_tercero
      FROM {t} GROUP BY id_reclamo) m USING (id_reclamo)
WHERE s.segmento = 1
"""

# Edad exacta a la fecha del reclamo: diferencia de años, menos uno si aún no
# cumplía años ese día. Las fechas ISO se comparan bien como texto ('MM-DD').
EDAD = """(r.anio - CAST(substr(a.fecha_nacimiento, 1, 4) AS INTEGER)
           - (substr(r.fecha_inicio, 6) < substr(a.fecha_nacimiento, 6)))"""

# Bandas: 0-5, 6-10 y de 10 en 10 hasta 71+. OJO: la edad mínima en los
# reclamos es 24 (Medicare cubre a mayores de 65 y a personas con
# discapacidad), así que las bandas de 0 a 20 existen pero quedan vacías.
BANDA = f"""CASE
    WHEN {EDAD} <= 5  THEN '00-05'
    WHEN {EDAD} <= 10 THEN '06-10'
    WHEN {EDAD} <= 20 THEN '11-20'
    WHEN {EDAD} <= 30 THEN '21-30'
    WHEN {EDAD} <= 40 THEN '31-40'
    WHEN {EDAD} <= 50 THEN '41-50'
    WHEN {EDAD} <= 60 THEN '51-60'
    WHEN {EDAD} <= 70 THEN '61-70'
    ELSE '71+' END"""

SABANA = f"""
CREATE TABLE sabana AS
WITH reclamos AS (
    {RECLAMOS.format(t="hospitalario", ambito="Hospitalario",
                     drg="s.grupo_drg", estancia="s.dias_estancia")}
    UNION ALL
    {RECLAMOS.format(t="ambulatorio", ambito="Ambulatorio",
                     drg="NULL", estancia="NULL")}
),
-- Un código de diagnóstico tiene un solo grupo: se resuelve una vez por código.
dx AS (SELECT DISTINCT codigo, categoria, grupo_enfermedad FROM diagnosticos)
SELECT r.id_reclamo, r.id_afiliado, r.ambito,
       r.fecha_inicio, r.anio, r.mes,
       r.monto_pagado, r.deducible, r.coaseguro, r.monto_pagado_tercero,
       r.monto_pagado + r.deducible + r.coaseguro + r.monto_pagado_tercero
                                   AS monto_total,
       a.sexo, a.raza,
       {EDAD}                      AS edad,
       {BANDA}                     AS banda_edad,
       a.estado, a.codigo_condado,
       r.diagnostico_principal, dx.categoria, dx.grupo_enfermedad,
       r.procedimiento_principal,
       r.medico_tratante, r.id_prestador, r.grupo_drg, r.dias_estancia
FROM reclamos r
JOIN afiliados a USING (id_afiliado)
LEFT JOIN dx ON dx.codigo = r.diagnostico_principal
WHERE r.anio IN {ANIOS}
  AND r.monto_pagado >= 0
"""

INDICES = {
    "idx_sab_periodo": "anio, mes",
    "idx_sab_ambito": "ambito",
    "idx_sab_grupo": "grupo_enfermedad",
    "idx_sab_banda": "banda_edad",
    "idx_sab_estado": "estado",
}


def construir(con):
    con.execute("DROP TABLE IF EXISTS sabana")
    con.execute(SABANA)
    for nombre, columnas in INDICES.items():
        con.execute(f"CREATE INDEX {nombre} ON sabana ({columnas})")
    con.commit()


def verificar(con):
    """Cuadra la sábana contra las tablas de origen: mismos reclamos y mismo
    dinero, una vez aplicadas las exclusiones."""
    q = lambda s: con.execute(s).fetchone()

    print("\n--- Cuadre contra el origen (2008-2009, sin negativos) ---")
    for tabla, ambito in [("hospitalario", "Hospitalario"), ("ambulatorio", "Ambulatorio")]:
        origen = q(f"""
            SELECT count(*), sum(m) FROM (
                SELECT sum(monto_pagado) AS m FROM {tabla}
                GROUP BY id_reclamo
                HAVING max(anio) IN {ANIOS} AND sum(monto_pagado) >= 0)""")
        sab = q(f"""SELECT count(*), sum(monto_pagado) FROM sabana
                    WHERE ambito = '{ambito}'""")
        ok = "OK" if origen == sab else "NO CUADRA"
        print(f"  {ambito:<13} origen {origen[0]:>9,} reclamos {origen[1]:>16,.2f}"
              f"  | sábana {sab[0]:>9,} {sab[1]:>16,.2f}  {ok}")

    dup = q("SELECT count(*) - count(DISTINCT id_reclamo) FROM sabana")[0]
    print(f"  reclamos repetidos en la sábana: {dup}")

    print("\n--- Totales por año y ámbito ---")
    for anio, ambito, n, pag, tot in con.execute("""
            SELECT anio, ambito, count(*), sum(monto_pagado), sum(monto_total)
            FROM sabana GROUP BY anio, ambito ORDER BY anio, ambito"""):
        print(f"  {anio} {ambito:<13} {n:>9,} reclamos  pagado {pag:>15,.2f}"
              f"  total {tot:>15,.2f}")

    print("\n--- Bandas de edad ---")
    for banda, n in con.execute(
            "SELECT banda_edad, count(*) FROM sabana GROUP BY 1 ORDER BY 1"):
        print(f"  {banda:<6} {n:>9,}")


def main(base=BASE):
    base = Path(base)
    if not base.exists():
        raise SystemExit(f"No existe {base}. Corre primero:  python preparar.py")

    print(f"Armando la sábana en {base} ...")
    con = sqlite3.connect(base)
    construir(con)
    verificar(con)
    n, cols = (con.execute("SELECT count(*) FROM sabana").fetchone()[0],
               len(con.execute("SELECT * FROM sabana LIMIT 1").description))
    con.close()
    print(f"\nListo: tabla sabana con {n:,} reclamos y {cols} columnas.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default=BASE)
    main(p.parse_args().base)
