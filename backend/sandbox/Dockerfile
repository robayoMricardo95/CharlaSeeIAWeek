# La caja donde corre el código que escribe el modelo.
#
# Se construye una sola vez:
#     docker build -t analitica-sandbox backend/sandbox
#
# Adentro NO hay credenciales, ni el .env, ni acceso a la base. El contenedor
# recibe DataFrames ya agregados por stdin y devuelve uno por stdout. Aunque
# alguien tomara control completo de esta caja, no hay nada que robar.
#
# Para sumar capacidad de análisis (matplotlib, scipy, statsmodels...), se
# agrega acá y se reconstruye la imagen. Empezamos con lo mínimo a propósito:
# cada librería nueva es superficie nueva.

FROM python:3.12-slim

# Sin bytecode: el disco del contenedor se monta de solo lectura y Python
# se quejaría al intentar escribir __pycache__.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp

RUN pip install --no-cache-dir \
    "pandas==2.2.3" \
    "numpy==2.1.3" \
    "pyarrow==18.1.0"

# El punto de entrada es nuestro, no el código del modelo. El script generado
# se ejecuta adentro de este runner, nunca como programa principal.
COPY runner.py /runner.py

USER 65534:65534

ENTRYPOINT ["python", "/runner.py"]
