"""
preparar.py — convierte los CSV crudos del CMS DE-SynPUF en una base SQLite
lista para que un agente la consulte en lenguaje natural.

Qué hace:
  1. Renombra las columnas crípticas del CMS a nombres de negocio en español.
  2. Convierte las fechas AAAAMMDD a texto ISO (AAAA-MM-DD) y además las parte
     en columnas `anio`, `mes` y `dia` para poder filtrar por cada una.
  3. Traduce los valores al español: sexo, raza, estado de residencia.
  4. Normaliza los indicadores de condición crónica: el origen trae 1 = sí, 2 = no.
  5. Arma una tabla larga de diagnósticos, con su capítulo de la CIE-9 en
     español y su categoría de tres dígitos.
  6. Crea los índices de las llaves de unión y de las columnas de filtro.
  7. Deja todo en reclamos.db, en esta misma carpeta

No usa pandas ni librerías externas: solo sqlite3 y csv de la biblioteca
estándar. Así el paso de datos corre en cualquier Python 3.11+ sin instalar nada.

Desde la terminal:
    python preparar.py

Desde un notebook:
    from preparar import main
    main()
"""

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

import catalogos

CSV_ORIGEN = {
    "afiliados": "DE1_0_2008_Beneficiary_Summary_File_Sample_1.csv",
    "hospitalario": "DE1_0_2008_to_2010_Inpatient_Claims_Sample_1.csv",
    "ambulatorio": "DE1_0_2008_to_2010_Outpatient_Claims_Sample_1.csv",
}

LOTE = 20_000


# --------------------------------------------------------------- conversores

def texto(v):
    """Texto limpio; vacío se guarda como NULL, no como cadena vacía."""
    v = (v or "").strip()
    return v or None


def fecha(v):
    """AAAAMMDD -> 'AAAA-MM-DD'. Vacío o malformado -> NULL."""
    v = (v or "").strip()
    if len(v) != 8 or not v.isdigit():
        return None
    return f"{v[:4]}-{v[4:6]}-{v[6:]}"


def entero(v):
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


def decimal(v):
    v = (v or "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def cronico(v):
    """El origen trae 1 = sí, 2 = no. Lo dejamos en 1/0 para que sumar sea contar."""
    return {"1": 1, "2": 0}.get((v or "").strip())


def sexo(v):
    """El origen trae 1 = hombre, 2 = mujer."""
    return catalogos.SEXO.get((v or "").strip())


def raza(v):
    return catalogos.RAZA.get((v or "").strip())


def estado(v):
    """Código SSA de estado -> nombre en español."""
    return catalogos.ESTADO.get((v or "").strip().zfill(2))


def esrd(v):
    """Enfermedad renal terminal: el origen trae 'Y' o '0'."""
    return 1 if (v or "").strip().upper() == "Y" else 0


# ------------------------------------------------------------------ esquemas
# Cada entrada: (columna_destino, columna_origen, tipo_sql, conversor)

CRONICOS = [
    ("cronico_alzheimer", "SP_ALZHDMTA"),
    ("cronico_insuficiencia_cardiaca", "SP_CHF"),
    ("cronico_renal", "SP_CHRNKIDN"),
    ("cronico_cancer", "SP_CNCR"),
    ("cronico_epoc", "SP_COPD"),
    ("cronico_depresion", "SP_DEPRESSN"),
    ("cronico_diabetes", "SP_DIABETES"),
    ("cronico_cardiopatia_isquemica", "SP_ISCHMCHT"),
    ("cronico_osteoporosis", "SP_OSTEOPRS"),
    ("cronico_artritis", "SP_RA_OA"),
    ("cronico_acv", "SP_STRKETIA"),
]

AFILIADOS = [
    ("id_afiliado", "DESYNPUF_ID", "TEXT", texto),
    ("fecha_nacimiento", "BENE_BIRTH_DT", "TEXT", fecha),
    ("fecha_defuncion", "BENE_DEATH_DT", "TEXT", fecha),
    ("sexo", "BENE_SEX_IDENT_CD", "TEXT", sexo),
    ("raza", "BENE_RACE_CD", "TEXT", raza),
    ("enfermedad_renal_terminal", "BENE_ESRD_IND", "INTEGER", esrd),
    ("estado", "SP_STATE_CODE", "TEXT", estado),
    ("codigo_estado", "SP_STATE_CODE", "TEXT", texto),
    ("codigo_condado", "BENE_COUNTY_CD", "TEXT", texto),
    ("meses_cobertura_hospitalaria", "BENE_HI_CVRAGE_TOT_MONS", "INTEGER", entero),
    ("meses_cobertura_medica", "BENE_SMI_CVRAGE_TOT_MONS", "INTEGER", entero),
    ("meses_cobertura_hmo", "BENE_HMO_CVRAGE_TOT_MONS", "INTEGER", entero),
    ("meses_cobertura_farmacia", "PLAN_CVRG_MOS_NUM", "INTEGER", entero),
    *[(dest, orig, "INTEGER", cronico) for dest, orig in CRONICOS],
    ("pago_anual_hospitalario", "MEDREIMB_IP", "REAL", decimal),
    ("copago_anual_hospitalario", "BENRES_IP", "REAL", decimal),
    ("pago_tercero_hospitalario", "PPPYMT_IP", "REAL", decimal),
    ("pago_anual_ambulatorio", "MEDREIMB_OP", "REAL", decimal),
    ("copago_anual_ambulatorio", "BENRES_OP", "REAL", decimal),
    ("pago_tercero_ambulatorio", "PPPYMT_OP", "REAL", decimal),
    ("pago_anual_honorarios", "MEDREIMB_CAR", "REAL", decimal),
    ("copago_anual_honorarios", "BENRES_CAR", "REAL", decimal),
    ("pago_tercero_honorarios", "PPPYMT_CAR", "REAL", decimal),
]

DIAGNOSTICOS = [(f"diagnostico_{i}", f"ICD9_DGNS_CD_{i}", "TEXT", texto)
                for i in range(1, 11)]
PROCEDIMIENTOS = [(f"procedimiento_{i}", f"ICD9_PRCDR_CD_{i}", "TEXT", texto)
                  for i in range(1, 7)]

HOSPITALARIO = [
    ("id_afiliado", "DESYNPUF_ID", "TEXT", texto),
    ("id_reclamo", "CLM_ID", "TEXT", texto),
    ("segmento", "SEGMENT", "INTEGER", entero),
    ("fecha_inicio", "CLM_FROM_DT", "TEXT", fecha),
    ("fecha_fin", "CLM_THRU_DT", "TEXT", fecha),
    ("fecha_ingreso", "CLM_ADMSN_DT", "TEXT", fecha),
    ("fecha_alta", "NCH_BENE_DSCHRG_DT", "TEXT", fecha),
    ("dias_estancia", "CLM_UTLZTN_DAY_CNT", "INTEGER", entero),
    ("id_prestador", "PRVDR_NUM", "TEXT", texto),
    ("medico_tratante", "AT_PHYSN_NPI", "TEXT", texto),
    ("medico_operante", "OP_PHYSN_NPI", "TEXT", texto),
    ("monto_pagado", "CLM_PMT_AMT", "REAL", decimal),
    ("monto_pagado_tercero", "NCH_PRMRY_PYR_CLM_PD_AMT", "REAL", decimal),
    ("deducible", "NCH_BENE_IP_DDCTBL_AMT", "REAL", decimal),
    ("coaseguro", "NCH_BENE_PTA_COINSRNC_LBLTY_AM", "REAL", decimal),
    ("deducible_sangre", "NCH_BENE_BLOOD_DDCTBL_LBLTY_AM", "REAL", decimal),
    ("per_diem", "CLM_PASS_THRU_PER_DIEM_AMT", "REAL", decimal),
    ("grupo_drg", "CLM_DRG_CD", "TEXT", texto),
    ("diagnostico_ingreso", "ADMTNG_ICD9_DGNS_CD", "TEXT", texto),
    *DIAGNOSTICOS,
    *PROCEDIMIENTOS,
]

AMBULATORIO = [
    ("id_afiliado", "DESYNPUF_ID", "TEXT", texto),
    ("id_reclamo", "CLM_ID", "TEXT", texto),
    ("segmento", "SEGMENT", "INTEGER", entero),
    ("fecha_inicio", "CLM_FROM_DT", "TEXT", fecha),
    ("fecha_fin", "CLM_THRU_DT", "TEXT", fecha),
    ("id_prestador", "PRVDR_NUM", "TEXT", texto),
    ("medico_tratante", "AT_PHYSN_NPI", "TEXT", texto),
    ("medico_operante", "OP_PHYSN_NPI", "TEXT", texto),
    ("monto_pagado", "CLM_PMT_AMT", "REAL", decimal),
    ("monto_pagado_tercero", "NCH_PRMRY_PYR_CLM_PD_AMT", "REAL", decimal),
    ("deducible", "NCH_BENE_PTB_DDCTBL_AMT", "REAL", decimal),
    ("coaseguro", "NCH_BENE_PTB_COINSRNC_AMT", "REAL", decimal),
    ("deducible_sangre", "NCH_BENE_BLOOD_DDCTBL_LBLTY_AM", "REAL", decimal),
    ("diagnostico_ingreso", "ADMTNG_ICD9_DGNS_CD", "TEXT", texto),
    *DIAGNOSTICOS,
    *PROCEDIMIENTOS,
]

TABLAS = {
    "afiliados": AFILIADOS,
    "hospitalario": HOSPITALARIO,
    "ambulatorio": AMBULATORIO,
}

# A las tablas de reclamos les agregamos anio, mes y dia, derivados de
# fecha_inicio, para poder filtrar por cada uno sin parsear texto.
DERIVADAS_FECHA = {"hospitalario", "ambulatorio"}


def cargar(con, tabla, esquema, ruta_csv):
    """Crea la tabla y carga el CSV por lotes, convirtiendo cada columna."""
    columnas = [f"{dest} {tipo}" for dest, _, tipo, _ in esquema]
    if tabla in DERIVADAS_FECHA:
        columnas += ["anio INTEGER", "mes INTEGER", "dia INTEGER"]

    con.execute(f"DROP TABLE IF EXISTS {tabla}")
    con.execute(f"CREATE TABLE {tabla} ({', '.join(columnas)})")

    nombres = [dest for dest, _, _, _ in esquema]
    if tabla in DERIVADAS_FECHA:
        nombres += ["anio", "mes", "dia"]
    inserta = (f"INSERT INTO {tabla} ({', '.join(nombres)}) "
               f"VALUES ({', '.join('?' * len(nombres))})")

    pos_fecha_inicio = next(
        (i for i, (d, _, _, _) in enumerate(esquema) if d == "fecha_inicio"), None)

    n = 0
    with open(ruta_csv, encoding="latin-1", newline="") as fh:
        lector = csv.DictReader(fh)
        faltan = [orig for _, orig, _, _ in esquema if orig not in lector.fieldnames]
        if faltan:
            sys.exit(f"{ruta_csv.name}: faltan columnas en el origen: {faltan}")

        lote = []
        for fila in lector:
            valores = [conv(fila.get(orig)) for _, orig, _, conv in esquema]
            if tabla in DERIVADAS_FECHA:
                f = valores[pos_fecha_inicio]
                if f:
                    valores += [int(f[:4]), int(f[5:7]), int(f[8:10])]
                else:
                    valores += [None, None, None]
            lote.append(valores)
            if len(lote) >= LOTE:
                con.executemany(inserta, lote)
                n += len(lote)
                lote = []
        if lote:
            con.executemany(inserta, lote)
            n += len(lote)

    con.commit()
    return n


def tabla_capitulos(con):
    """Los 19 capítulos de la CIE-9 como tabla de referencia, en español."""
    con.execute("DROP TABLE IF EXISTS capitulos_cie9")
    con.execute("""CREATE TABLE capitulos_cie9 (
        capitulo INTEGER PRIMARY KEY, nombre TEXT, desde TEXT, hasta TEXT)""")
    con.executemany("INSERT INTO capitulos_cie9 VALUES (?,?,?,?)",
                    catalogos.tabla_capitulos())
    con.commit()


def tabla_diagnosticos(con):
    """Tabla larga: una fila por diagnóstico de cada reclamo.

    Agrega el capítulo de la CIE-9 (19 grupos con nombre en español) y la
    categoría de tres dígitos. Así una pregunta como "¿en qué se concentró el
    alza?" se responde con 'Enfermedades del aparato circulatorio' en lugar de
    con el código 41401.
    """
    con.execute("DROP TABLE IF EXISTS diagnosticos")
    con.execute("""CREATE TABLE diagnosticos (
        id_reclamo TEXT, ambito TEXT, orden INTEGER, codigo TEXT,
        categoria TEXT, capitulo INTEGER, grupo_enfermedad TEXT)""")

    # El capítulo se calcula en Python una sola vez por código distinto, no por
    # fila: hay 2,6 millones de filas y menos de 12 000 códigos.
    cache = {}

    def clasificado(cod):
        if cod not in cache:
            num, nombre, cat = catalogos.clasificar_cie9(cod)
            cache[cod] = (cat, num, nombre)
        return cache[cod]

    for ambito in ("hospitalario", "ambulatorio"):
        for i in range(1, 11):
            filas = con.execute(
                f"SELECT id_reclamo, diagnostico_{i} FROM {ambito} "
                f"WHERE diagnostico_{i} IS NOT NULL").fetchall()
            con.executemany(
                "INSERT INTO diagnosticos VALUES (?,?,?,?,?,?,?)",
                [(rec, ambito, i, cod, *clasificado(cod)) for rec, cod in filas])
    con.commit()
    return con.execute("SELECT count(*) FROM diagnosticos").fetchone()[0]


def indices(con):
    """SQLite sí necesita índices: sin ellos los joins hacen barrido completo."""
    for sql in [
        "CREATE INDEX idx_afi_id ON afiliados(id_afiliado)",
        "CREATE INDEX idx_hos_afi ON hospitalario(id_afiliado)",
        "CREATE INDEX idx_hos_rec ON hospitalario(id_reclamo)",
        "CREATE INDEX idx_hos_anio ON hospitalario(anio, mes)",
        "CREATE INDEX idx_hos_drg ON hospitalario(grupo_drg)",
        "CREATE INDEX idx_amb_afi ON ambulatorio(id_afiliado)",
        "CREATE INDEX idx_amb_rec ON ambulatorio(id_reclamo)",
        "CREATE INDEX idx_amb_anio ON ambulatorio(anio, mes)",
        "CREATE INDEX idx_dx_rec ON diagnosticos(id_reclamo)",
        "CREATE INDEX idx_dx_cod ON diagnosticos(codigo)",
        "CREATE INDEX idx_dx_cap ON diagnosticos(capitulo)",
        "CREATE INDEX idx_dx_cat ON diagnosticos(categoria)",
    ]:
        con.execute(sql)
    con.commit()


def construir(entrada: Path, salida: Path):
    salida.parent.mkdir(parents=True, exist_ok=True)
    if salida.exists():
        salida.unlink()

    con = sqlite3.connect(str(salida))
    # Solo durante la construcción: sin journal y sin fsync va mucho más rápido.
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA cache_size=-200000")

    for tabla, esquema in TABLAS.items():
        ruta = entrada / CSV_ORIGEN[tabla]
        print(f"  cargando {tabla:<14} ...", end=" ", flush=True)
        print(f"{cargar(con, tabla, esquema, ruta):,} filas")

    tabla_capitulos(con)
    print("  cargando capitulos_cie9 ... 19 filas")
    print(f"  derivando diagnosticos ...", end=" ", flush=True)
    print(f"{tabla_diagnosticos(con):,} filas")
    print("  creando índices ...")
    indices(con)
    return con


def verificar(con):
    """Comprueba sobre los datos reales las siete trampas del diccionario."""
    q = lambda s: con.execute(s).fetchone()

    print("\n--- Volúmenes ---")
    for t in ("afiliados", "hospitalario", "ambulatorio", "diagnosticos"):
        print(f"  {t:<14} {q(f'SELECT count(*) FROM {t}')[0]:>10,} filas")

    print("\n--- Trampa 1: crónicos (1=sí, 2=no en el origen) ---")
    n, d = q("SELECT count(*), sum(cronico_diabetes) FROM afiliados")
    print(f"  afiliados con diabetes: {d:,} de {n:,} ({d/n:.1%})"
          f"   [el codebook del CMS dice 37.96%]")

    print("\n--- Trampa 2: no existe 'costo total' ---")
    pag, ded, coa, ter = q("""SELECT sum(monto_pagado), sum(deducible),
                              sum(coaseguro), sum(monto_pagado_tercero)
                              FROM hospitalario""")
    print(f"  pagado por el asegurador : {pag:>16,.2f}")
    print(f"  deducible del afiliado   : {ded:>16,.2f}")
    print(f"  coaseguro del afiliado   : {coa:>16,.2f}")
    print(f"  pagado por un tercero    : {ter:>16,.2f}")
    tot = pag + ded + coa + ter
    print(f"  costo del episodio       : {tot:>16,.2f}  ({tot/pag - 1:+.1%})")

    print("\n--- Trampa 3: montos negativos y en cero ---")
    for t in ("hospitalario", "ambulatorio"):
        neg, mn = q(f"SELECT count(*), min(monto_pagado) FROM {t} WHERE monto_pagado < 0")
        cero = q(f"SELECT count(*) FROM {t} WHERE monto_pagado = 0")[0]
        print(f"  {t:<14} {neg:>6,} negativos (mín {mn:,.2f}), {cero:>7,} en cero")

    print("\n--- Trampa 4: fechas y periodo real ---")
    for t in ("hospitalario", "ambulatorio"):
        a, b, nul = q(f"""SELECT min(fecha_inicio), max(fecha_inicio),
                          sum(fecha_inicio IS NULL) FROM {t}""")
        fuera = q(f"SELECT count(*) FROM {t} WHERE anio NOT BETWEEN 2008 AND 2010")[0]
        print(f"  {t:<14} {a} a {b}, {nul or 0} nulas, {fuera:,} fuera de 2008-2010")

    print("\n--- Trampa 5: contar filas no es contar reclamos ---")
    for t in ("hospitalario", "ambulatorio"):
        filas, recl = q(f"SELECT count(*), count(DISTINCT id_reclamo) FROM {t}")
        print(f"  {t:<14} {filas:>9,} filas -> {recl:>9,} reclamos "
              f"(diferencia {filas-recl:,})")

    print("\n--- Trampa 6: afiliados sin ningún reclamo ---")
    sin = q("""SELECT count(*) FROM afiliados a
               WHERE NOT EXISTS (SELECT 1 FROM hospitalario h WHERE h.id_afiliado=a.id_afiliado)
                 AND NOT EXISTS (SELECT 1 FROM ambulatorio o WHERE o.id_afiliado=a.id_afiliado)""")[0]
    tot_afi = q("SELECT count(*) FROM afiliados")[0]
    print(f"  {sin:,} de {tot_afi:,} ({sin/tot_afi:.1%}) — un INNER JOIN los borra")

    print("\n--- Trampa 7: los montos anuales de afiliados son solo de 2008 ---")
    anual = q("SELECT sum(pago_anual_hospitalario) FROM afiliados")[0]
    r2008 = q("SELECT sum(monto_pagado) FROM hospitalario WHERE anio=2008")[0]
    print(f"  suma anual en afiliados : {anual:>16,.2f}")
    print(f"  reclamos de 2008        : {r2008:>16,.2f}  (no son la misma cosa)")

    print("\n--- Traducción al español ---")
    for col in ("sexo", "raza"):
        v = con.execute(f"SELECT {col}, count(*) FROM afiliados "
                        f"GROUP BY {col} ORDER BY 2 DESC").fetchall()
        print(f"  {col}: " + ", ".join(f"{k} ({n:,})" for k, n in v))
    est = con.execute("""SELECT estado, count(*) c FROM afiliados
                         GROUP BY estado ORDER BY c DESC LIMIT 5""").fetchall()
    print("  estado (top 5): " + ", ".join(f"{k} ({n:,})" for k, n in est))
    sin_trad = q("SELECT count(*) FROM afiliados WHERE estado IS NULL")[0]
    print(f"  afiliados sin estado traducido: {sin_trad}")

    print("\n--- Capítulos de la CIE-9 (top 5 por monto hospitalario) ---")
    for grupo, monto, nrec in con.execute("""
        SELECT d.grupo_enfermedad, sum(h.monto_pagado), count(*)
        FROM diagnosticos d JOIN hospitalario h ON h.id_reclamo = d.id_reclamo
        WHERE d.ambito = 'hospitalario' AND d.orden = 1
        GROUP BY d.grupo_enfermedad ORDER BY 2 DESC LIMIT 5"""):
        print(f"  {monto:>15,.2f}  {nrec:>7,} recl.  {grupo}")
    sin_cap = q("SELECT count(*) FROM diagnosticos WHERE capitulo IS NULL")[0]
    tot_dx = q("SELECT count(*) FROM diagnosticos")[0]
    print(f"  diagnósticos sin capítulo: {sin_cap:,} de {tot_dx:,} "
          f"({sin_cap/tot_dx:.2%})")

    print("\n--- Columnas de fecha partidas ---")
    a, m, d = q("""SELECT anio, mes, dia FROM hospitalario
                   WHERE fecha_inicio IS NOT NULL LIMIT 1""")
    f = q("""SELECT fecha_inicio FROM hospitalario
             WHERE fecha_inicio IS NOT NULL LIMIT 1""")[0]
    print(f"  ejemplo: fecha_inicio={f} -> anio={a}, mes={m}, dia={d}")
    print("  reclamos hospitalarios por mes (2009): " + ", ".join(
        f"{mm}:{n:,}" for mm, n in con.execute(
            "SELECT mes, count(*) FROM hospitalario WHERE anio=2009 "
            "GROUP BY mes ORDER BY mes")))

    print("\n--- Las preguntas de referencia ---")
    r = q("SELECT sum(monto_pagado) FROM hospitalario WHERE anio = 2009")[0]
    print(f"  1. Pagado en hospitalización 2009: {r:,.2f}")

    print("  2. Costo promedio por reclamo hospitalario, por año:")
    for anio, suma, nrec in con.execute("""
        SELECT anio, sum(monto_pagado), count(DISTINCT id_reclamo)
        FROM hospitalario WHERE anio IS NOT NULL GROUP BY anio ORDER BY anio"""):
        print(f"       {anio}  {suma/nrec:>10,.2f}  ({nrec:,} reclamos)")

    print("  3. Top 5 diagnósticos principales por monto pagado (hospitalario):")
    for cod, monto, nrec in con.execute("""
        SELECT d.codigo, sum(h.monto_pagado), count(*)
        FROM diagnosticos d JOIN hospitalario h ON h.id_reclamo = d.id_reclamo
        WHERE d.ambito = 'hospitalario' AND d.orden = 1
        GROUP BY d.codigo ORDER BY 2 DESC LIMIT 5"""):
        print(f"       {cod:<8} {monto:>14,.2f}  ({nrec:,} reclamos)")


def main(entrada="extraidos", salida="reclamos.db"):
    """Construye la base y verifica las trampas. Sirve igual desde la terminal
    que desde un notebook:  from preparar import main; main()"""
    entrada = Path(entrada)
    faltan = [f for f in CSV_ORIGEN.values() if not (entrada / f).exists()]
    if faltan:
        raise SystemExit(
            f"Faltan archivos en {entrada}/: {faltan}\n"
            f"Corre primero:  python descargar.py --descargar")

    print(f"Construyendo {salida} desde {entrada}/ ...")
    con = construir(entrada, Path(salida))
    verificar(con)
    con.close()
    print(f"\nListo: {salida} ({os.path.getsize(salida)/1_048_576:.1f} MB)")
    return salida


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--entrada", default="extraidos")
    p.add_argument("--salida", default="reclamos.db")
    a = p.parse_args()
    main(a.entrada, a.salida)
