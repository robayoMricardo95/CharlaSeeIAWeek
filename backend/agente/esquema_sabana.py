"""Las 25 columnas de `sabana`, tal como están en `reclamos.db`.

Es una copia congelada del esquema real (leído el 2026-10-13). Existe por dos
motivos:

- Las pruebas de `reglas.py` corren sin base de datos.
- Si la tabla cambia, `verificar_contra_la_base()` lo grita en vez de dejar que
  el agente escriba SQL contra un esquema viejo.

En producción manda siempre lo que dice la base: `main.py` lee el esquema al
arrancar. Esta lista es el respaldo y el testigo.
"""

COLUMNAS = (
    # Identificación
    "id_reclamo",
    "id_afiliado",
    "ambito",
    # Tiempo
    "fecha_inicio",
    "anio",
    "mes",
    # Dinero
    "monto_pagado",
    "deducible",
    "coaseguro",
    "monto_pagado_tercero",
    "monto_total",
    # Afiliado
    "sexo",
    "raza",
    "edad",
    "banda_edad",
    "estado",
    "codigo_condado",
    # Clínica
    "diagnostico_principal",
    "categoria",
    "grupo_enfermedad",
    "procedimiento_principal",
    # Atención
    "medico_tratante",
    "id_prestador",
    "grupo_drg",
    "dias_estancia",
)

# Tipos según lo que guarda SQLite (`typeof`), no según la declaración: varias
# columnas de la sábana se crearon sin tipo declarado. Importan para dos cosas:
# no escribir los números entre comillas (`anio = 2009`, no `anio = '2009'`, que
# en SQLite NO es lo mismo: compara texto contra entero y no devuelve nada), y no
# filtrar texto en una columna numérica.
NUMERICAS = frozenset(
    {
        "anio",
        "mes",
        "monto_pagado",
        "deducible",
        "coaseguro",
        "monto_pagado_tercero",
        "monto_total",
        "edad",
        "dias_estancia",
    }
)
TEXTO = frozenset(COLUMNAS) - NUMERICAS
PERMITIDAS = frozenset(COLUMNAS)


def verificar_contra_la_base(reales: set[str]) -> list[str]:
    """Compara esta copia con las columnas reales. Devuelve las diferencias."""
    avisos = []
    faltan = PERMITIDAS - reales
    sobran = reales - PERMITIDAS
    if faltan:
        avisos.append(f"Columnas de la copia que ya no están en la base: {sorted(faltan)}")
    if sobran:
        avisos.append(f"Columnas nuevas en la base que la copia no conoce: {sorted(sobran)}")
    return avisos
