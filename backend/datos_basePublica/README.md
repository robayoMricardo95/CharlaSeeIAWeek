# Base pública de reclamos (SQLite)

Los datos que consulta el agente. Son **públicos y sintéticos**: reclamos de
Medicare (Estados Unidos) del *CMS 2008-2010 Data Entrepreneurs' Synthetic Public
Use File* (DE-SynPUF), muestra 1, publicados por el CMS para construir y probar
herramientas.

> **Siempre que se muestre un resultado:** los datos son sintéticos y el CMS
> alteró a propósito las relaciones entre variables. Sirven para analizar y
> construir herramientas, no para sacar conclusiones sobre la población real.

Página de origen:
<https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-claims-synthetic-public-use-files/cms-2008-2010-data-entrepreneurs-synthetic-public-use-file-de-synpuf/de10-sample-1>

## Qué hay en esta carpeta

| Archivo | Qué es | ¿Va a git? |
|---|---|---|
| `diccionario_sabana.yaml` | **Lo que lee el agente.** Columnas, valores exactos, trampas, métricas y consultas de ejemplo con resultado verificado | Sí |
| `diccionario.yaml` | Documentación del modelo desagregado (cuatro tablas) y de las trampas del origen. El agente no lo lee | Sí |
| `ejecucion.py` | La única puerta del agente a la base: solo lectura, solo `SELECT` | Sí |
| `descargar.py` | Baja los tres ZIP del CMS | Sí |
| `preparar.py` · `catalogos.py` | Del CSV del CMS a las tablas desagregadas, con las traducciones al español | Sí |
| `sabana.py` | Arma la tabla `sabana` y la cuadra contra el origen | Sí |
| `enriquecer.py` | Opcional: descripciones oficiales en inglés de los códigos CIE-9 | Sí |
| `reclamos.db` | La base (unos 800 MB) | **No**: se regenera |
| `crudos/` · `extraidos/` | ZIP y CSV del CMS (unos 190 MB) | **No**: se vuelven a bajar |

## Regenerar la base desde cero

Python 3.11+ con `requests`. Desde esta carpeta:

```bash
python descargar.py --descargar   # baja y descomprime los 3 archivos del CMS
python preparar.py                # tablas desagregadas en reclamos.db  (~45 s)
python sabana.py                  # tabla sabana + cuadre contra el origen (~30 s)
```

No correr estos scripts con `python -I`: aísla el path e impide que
`preparar.py` importe `catalogos.py`.

## El modelo

`preparar.py` deja las tablas desagregadas, y `sabana.py` las aplana en la única
tabla que consulta el agente.

| Tabla | Grano | Filas |
|---|---|---:|
| `afiliados` | un asegurado | 116 352 |
| `hospitalario` | un segmento de reclamo | 66 773 |
| `ambulatorio` | un segmento de reclamo | 790 790 |
| `diagnosticos` | un diagnóstico de un reclamo | 2 611 067 |
| `capitulos_cie9` | un capítulo de la CIE-9, en español | 20 |
| **`sabana`** | **un reclamo** (hospitalario y ambulatorio juntos) | **656 147** |

Decisiones al construirlas:

- Los valores categóricos están **en español** (`sexo` es 'Masculino'/'Femenino';
  los estados llevan su nombre). Las traducciones viven en `catalogos.py`.
- Las fechas pasaron a texto ISO, más `anio` y `mes` como enteros.
- Cada diagnóstico tiene su capítulo CIE-9 en español (`grupo_enfermedad`): 11 897
  códigos se reducen a 19 grupos legibles. Los códigos individuales, los de
  procedimiento y los grupos DRG **no** se tradujeron.
- La sábana tiene **solo 2008 y 2009**: 2007 trae solo dos meses y 2010 está
  incompleto. Se excluyeron también los montos negativos y los reclamos sin fecha.
  Los montos en cero se quedan.
- `monto_total` = `monto_pagado` + `deducible` + `coaseguro` + `monto_pagado_tercero`.
  La métrica de costo por defecto es `monto_pagado`.

## Las trampas del dato

Están en `diccionario_sabana.yaml → trampas` y el agente las lee en cada pregunta.
Las que más cambian un número:

| Trampa | Qué hay que hacer |
|---|---|
| Costo ambiguo | Usar `monto_pagado` por defecto y decir qué definición se usó (`monto_total` es 20,7 % mayor en 2009) |
| Montos en cero | No filtrar `monto_pagado > 0`: son reclamos válidos |
| Sin per cápita | La tabla solo tiene afiliados con algún reclamo: no se puede calcular gasto per cápita |
| Ámbitos no comparables | Un reclamo hospitalario promedia ~9 500 y uno ambulatorio ~290: no promediarlos juntos |
| Códigos sin traducción | Hablar de enfermedades con `grupo_enfermedad`, no inventar qué significa un código |
| Premisa falsa | "¿Dónde se concentró el alza hospitalaria de 2009?": el pagado hospitalario **bajó** 4,9 %. Confirmar el signo antes de explicarlo |

## Cifras de control

Después de copiar o regenerar la base, esta consulta tiene que coincidir al entero:

```sql
SELECT anio, ambito, count(*) AS reclamos,
       sum(monto_pagado) AS pagado, sum(monto_total) AS total
FROM sabana GROUP BY anio, ambito ORDER BY anio, ambito;
```

| Año | Ámbito | Reclamos | `sum(monto_pagado)` | `sum(monto_total)` |
|---|---|---:|---:|---:|
| 2008 | Ambulatorio | 281 971 | 78 449 490 | 106 026 240 |
| 2008 | Hospitalario | 27 656 | 258 063 920 | 299 344 636 |
| 2009 | Ambulatorio | 321 310 | 94 184 290 | 125 977 830 |
| 2009 | Hospitalario | 25 210 | 245 512 790 | 284 055 506 |
| **Total** | | **656 147** | | |

`backend/pruebas_reglas.py` hace esta misma comprobación con el SQL que arma el
agente.
