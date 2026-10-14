"""
catalogos.py — tablas de traducción de los códigos del CMS a español.

Las usa preparar.py para que la base no tenga códigos crípticos. Todo lo que
está aquí sale de fuentes autoritativas:

  - Los códigos de sexo, raza y estado, del codebook oficial del DE-SynPUF.
  - Los capítulos de la CIE-9, de la estructura estándar de la clasificación.

Lo que NO está aquí, a propósito: las descripciones de los 11 897 códigos de
diagnóstico y los 739 grupos DRG. Traducir ese volumen de terminología clínica
sin una fuente oficial en español sería inventar. Para eso está
`enriquecer.py`, que baja el catálogo oficial cuando hay red disponible.
"""

# ---------------------------------------------------------------------------
# Valores de columnas chicas
# ---------------------------------------------------------------------------

SEXO = {"1": "Masculino", "2": "Femenino"}

# El codebook agrupa varias categorías en "Otras": desconocida, otra, asiática
# y nativa norteamericana. No existe el código 4.
RAZA = {"1": "Blanca", "2": "Negra", "3": "Otras", "5": "Hispana"}

SI_NO = {1: "Sí", 0: "No"}

# Códigos de estado del sistema SSA (no FIPS), según el codebook del DE-SynPUF.
# No existen el 40 ni el 48. El 54 agrupa territorios y residentes en el exterior.
ESTADO = {
    "01": "Alabama", "02": "Alaska", "03": "Arizona", "04": "Arkansas",
    "05": "California", "06": "Colorado", "07": "Connecticut", "08": "Delaware",
    "09": "Distrito de Columbia", "10": "Florida", "11": "Georgia",
    "12": "Hawái", "13": "Idaho", "14": "Illinois", "15": "Indiana",
    "16": "Iowa", "17": "Kansas", "18": "Kentucky", "19": "Luisiana",
    "20": "Maine", "21": "Maryland", "22": "Massachusetts", "23": "Michigan",
    "24": "Minnesota", "25": "Misisipi", "26": "Misuri", "27": "Montana",
    "28": "Nebraska", "29": "Nevada", "30": "Nuevo Hampshire",
    "31": "Nueva Jersey", "32": "Nuevo México", "33": "Nueva York",
    "34": "Carolina del Norte", "35": "Dakota del Norte", "36": "Ohio",
    "37": "Oklahoma", "38": "Oregón", "39": "Pensilvania", "41": "Rhode Island",
    "42": "Carolina del Sur", "43": "Dakota del Sur", "44": "Tennessee",
    "45": "Texas", "46": "Utah", "47": "Vermont", "49": "Virginia",
    "50": "Washington", "51": "Virginia Occidental", "52": "Wisconsin",
    "53": "Wyoming", "54": "Otros territorios y exterior",
}

# ---------------------------------------------------------------------------
# Capítulos de la CIE-9
#
# Los 17 capítulos numéricos más las dos clasificaciones suplementarias (V y E).
# Esta es la agrupación que vuelve interpretable un análisis de diagnósticos:
# 11 897 códigos distintos se vuelven 19 grupos con nombre de negocio.
# ---------------------------------------------------------------------------

CAPITULOS_CIE9 = [
    #  desde, hasta, número, nombre
    (  1,  139,  1, "Enfermedades infecciosas y parasitarias"),
    (140,  239,  2, "Neoplasias"),
    (240,  279,  3, "Enfermedades endocrinas, nutricionales y metabólicas"),
    (280,  289,  4, "Enfermedades de la sangre y órganos hematopoyéticos"),
    (290,  319,  5, "Trastornos mentales"),
    (320,  389,  6, "Enfermedades del sistema nervioso y de los sentidos"),
    (390,  459,  7, "Enfermedades del aparato circulatorio"),
    (460,  519,  8, "Enfermedades del aparato respiratorio"),
    (520,  579,  9, "Enfermedades del aparato digestivo"),
    (580,  629, 10, "Enfermedades del aparato genitourinario"),
    (630,  679, 11, "Complicaciones del embarazo, parto y puerperio"),
    (680,  709, 12, "Enfermedades de la piel y del tejido subcutáneo"),
    (710,  739, 13, "Enfermedades del sistema osteomuscular y tejido conjuntivo"),
    (740,  759, 14, "Anomalías congénitas"),
    (760,  779, 15, "Afecciones originadas en el periodo perinatal"),
    (780,  799, 16, "Síntomas, signos y estados mal definidos"),
    (800,  999, 17, "Lesiones y envenenamientos"),
]

CAPITULO_V = (18, "Factores que influyen en el estado de salud")
CAPITULO_E = (19, "Causas externas de lesiones y envenenamientos")

# El CMS dejó la cadena literal 'OTHER' en las columnas de diagnóstico del
# archivo sintético: no es un código CIE-9, es un marcador de agrupación. Le
# damos capítulo propio para que no desaparezca de un GROUP BY sin que nadie
# lo note. Aparece 5 675 veces.
CAPITULO_OTHER = (99, "Sin clasificar (marcador 'OTHER' del CMS)")


def clasificar_cie9(codigo):
    """Devuelve (numero_capitulo, nombre_capitulo, categoria) para un código CIE-9.

    La `categoria` son los tres primeros caracteres del código, que es el nivel
    al que la CIE-9 agrupa enfermedades afines. Reduce 11 897 códigos a unos mil
    y es el nivel al que normalmente se habla de un diagnóstico.

    Ejemplos:
        '41401' -> (7, 'Enfermedades del aparato circulatorio', '414')
        'V5789' -> (18, 'Factores que influyen en el estado de salud', 'V57')
        'E9330' -> (19, 'Causas externas de lesiones y envenenamientos', 'E93')
        'OTHER' -> (99, "Sin clasificar (marcador 'OTHER' del CMS)", 'OTHER')
    """
    if not codigo:
        return None, None, None
    c = codigo.strip().upper()
    if not c:
        return None, None, None

    if c == "OTHER":
        return CAPITULO_OTHER[0], CAPITULO_OTHER[1], "OTHER"

    if c[0] == "V":
        return CAPITULO_V[0], CAPITULO_V[1], c[:3]
    if c[0] == "E":
        return CAPITULO_E[0], CAPITULO_E[1], c[:4]

    # Los códigos numéricos vienen sin punto decimal: '41401' es 414.01, así que
    # el capítulo lo determinan los tres primeros dígitos.
    cat = c[:3]
    if not cat.isdigit():
        return None, None, cat
    n = int(cat)
    for desde, hasta, num, nombre in CAPITULOS_CIE9:
        if desde <= n <= hasta:
            return num, nombre, cat
    return None, None, cat


def tabla_capitulos():
    """Los 19 capítulos como filas, para guardarlos como tabla de referencia."""
    filas = [(num, nombre, f"{desde:03d}", f"{hasta:03d}")
             for desde, hasta, num, nombre in CAPITULOS_CIE9]
    filas.append((CAPITULO_V[0], CAPITULO_V[1], "V01", "V91"))
    filas.append((CAPITULO_E[0], CAPITULO_E[1], "E000", "E999"))
    filas.append((CAPITULO_OTHER[0], CAPITULO_OTHER[1], "OTHER", "OTHER"))
    return filas
