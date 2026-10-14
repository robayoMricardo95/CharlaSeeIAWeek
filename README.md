# Agente de Analítica Avanzada

**Preguntas de negocio en lenguaje natural. Respuestas con cifras exactas, contexto y criterio.**

Un agente de inteligencia artificial que analiza datos como lo haría un analista senior: entiende la
pregunta, consulta la base, calcula sin estimar, verifica el resultado y lo explica con una conclusión
que se puede usar para decidir.

📊 **Presentación:** [Del agente al producto](https://robayomricardo95.github.io/CharlaSeeIAWeek/presentacion/) · IA Week 2026

---

## El problema

Hoy, una pregunta de negocio tarda días en responderse: alguien la entiende, escribe la consulta, arma la
planilla, revisa las cifras y redacta el resumen. Y muchas veces lo que llega es **reportería**: tablas y
totales que el usuario todavía tiene que interpretar.

Los chatbots genéricos no resuelven esto: un modelo de lenguaje **estima** los números en vez de
calcularlos, y la misma pregunta puede dar cifras distintas en cada intento.

## Lo que hace el agente

Se le pregunta como a una persona:

> ¿Cómo cambió el costo promedio por reclamo hospitalario entre 2008 y 2009?

Y responde con un análisis, no con una tabla:

> El costo por reclamo **subió 4,4 %**: pasó de **9.331,21** a **9.738,71** dólares. Es importante mencionar
> que esto sucede aun cuando los reclamos cayeron **8,8 %** (de 27.656 a 25.210), pero lo pagado solo se
> redujo **4,9 %** (de USD 258,1 M a 245,5 M).
> **Lo que nos muestra: hay menos reclamos, pero cada uno nos cuesta más.**

Cada cifra sale de una ejecución real sobre los datos, y el usuario puede abrir el panel de razonamiento
para ver de dónde salió: la consulta, los filtros, el cálculo y la fórmula.

## Por qué es distinto

| | Reportería tradicional | Chatbot genérico | **Este agente** |
|---|---|---|---|
| Tiempo de respuesta | Días | Segundos | **Segundos** |
| Cifras | Exactas | Estimadas, cambian | **Exactas y repetibles** |
| Interpretación | La hace el usuario | Sin respaldo | **Con contexto y conclusión** |
| Trazabilidad | Depende de quién lo hizo | Ninguna | **Consulta, cálculo y fórmula a la vista** |
| Seguridad de los datos | Alta | Baja | **Solo lectura y cálculo aislado** |

## Cómo piensa

Un equipo de agentes especializados, donde cada pieza hace una sola cosa y se revisa a la siguiente:

1. **Entender** — arma un plan de análisis (qué medir, cómo, sobre qué datos) y lo revisa antes de gastar.
2. **Traer y calcular** — traduce el plan a SQL con código (el modelo nunca escribe SQL), lo ejecuta en solo
   lectura y calcula la respuesta en un sandbox aislado.
3. **Verificar y enriquecer** — detecta resultados vacíos o imposibles, suma contexto que nadie pidió pero que
   cambia la lectura, y explica de dónde sale cada número.
4. **Comunicar** — redacta en lenguaje de negocio, con una conclusión clara.

Si un cálculo falla, se corrige solo; si el plan tiene dudas, mira la base antes de adivinar.

## Seguro por diseño

- **La base es de solo lectura**: el agente no puede modificar ni borrar datos.
- **Los cálculos corren en un contenedor cerrado**: sin internet, sin disco, sin credenciales, con tope de
  30 segundos y 512 MB. Aunque alguien intentara inyectar código malicioso, no tiene a dónde salir.
- **Todo queda registrado**: cada pregunta, su SQL, su resultado y su costo.

## El valor para la empresa

El retorno se mide en lo que el agente le devuelve a la estrategia:

- **Horas de análisis ahorradas**, frente a lo que cuesta operar el agente.
- **Análisis realizados** cada mes, incluidas las preguntas que antes no se alcanzaban a responder.
- **Hallazgos con valor monetario**: costos que suben, fugas, oportunidades.
- **Estrategias y decisiones** que nacen de un análisis del agente.

El costo de cada pregunta se mide en tiempo real y queda a la vista en la interfaz.

## Hacia dónde crece

El sandbox crece sumando librerías: hoy calcula variaciones y comparaciones; el siguiente paso es machine
learning (clasificar, segmentar, estimar) y después proyecciones. Cada capacidad nueva se suma con
instrucciones mínimas para que el usuario la aplique bien.

## Stack

**Backend:** Python · FastAPI · LangGraph · LangChain · Azure OpenAI (gpt-4.1) · SQLite · Docker
**Frontend:** React · TypeScript · Vite · Tailwind

---

## Para desarrolladores

### Los datos de la demo

Reclamos **sintéticos** de Medicare (Estados Unidos), muestra 1 del DE-SynPUF del CMS, años 2008 y 2009.
Sirven para analizar y construir herramientas, no para sacar conclusiones sobre personas reales. Origen,
regeneración y cifras de control: [`backend/datos_basePublica/README.md`](backend/datos_basePublica/README.md).

### Estructura

```
├── frontend/                  React + Vite + TypeScript + Tailwind
│   └── src/
│       ├── api/               tipos.ts y cliente.ts (única puerta al backend)
│       └── componentes/       barra lateral, hilo, panel de razonamiento, medidor de costo
├── presentacion/              la presentación (reveal.js)
└── backend/
    ├── main.py                FastAPI: instrucciones del agente, rutas y CORS
    ├── agente/                el grafo de LangGraph (ver su README)
    ├── carga_conocimiento.py  carga el diccionario de la tabla como prefijo cacheado
    ├── consulta.py            ejecuta el SQL, serializa el resultado, lee esquema y cobertura
    ├── datos_basePublica/     la base SQLite, su diccionario y los scripts que la generan
    ├── registro.py            historial en SQLite de cada pregunta, su SQL y su costo
    ├── aislamiento.py         corre el Python del agente en un contenedor Docker
    ├── sandbox/               la imagen del contenedor (Dockerfile + runner.py)
    ├── perfil.py              lectura aritmética del resultado (brechas, variaciones)
    ├── formato.py             saneado del HTML que redacta el agente
    ├── uso_tokens.py          conteo de tokens y costo en dólares por conversación
    ├── trazas.py              seguimiento: LangSmith, archivo local o nada
    ├── evaluacion.py          corre un set de preguntas y mide cuántas salen bien
    └── pruebas_*.py           pruebas sin API (ver abajo)
```

Dos bases SQLite, cada una con un solo trabajo:

| Archivo | Qué guarda | Acceso |
|---|---|---|
| `backend/datos_basePublica/reclamos.db` | Los reclamos que consulta el agente | Solo lectura |
| `backend/datos_app/historial.db` | Conversaciones, consultas ejecutadas y costo | Lo crea el backend al arrancar |

Ninguna de las dos va a git.

### Cómo levantarlo

**0. Requisitos, una sola vez:**

- La base: ver [`backend/datos_basePublica/README.md`](backend/datos_basePublica/README.md) para regenerar `reclamos.db`.
- `backend/.env`: copiarlo de `backend/.env.example` y completarlo.
- La imagen del sandbox, con Docker Desktop abierto:
  ```
  docker build -t analitica-sandbox backend/sandbox
  ```

**1. Docker Desktop abierto.** Los cálculos del agente corren en el contenedor; sin
Docker, las preguntas que necesitan un cálculo responden con un error.

**2. Backend** (terminal 1):
```
backend\.venv\Scripts\python.exe -m uvicorn main:app --reload --app-dir backend --port 8000
```

**3. Frontend** (terminal 2):
```
cd frontend
npm run dev
```

Abrir `http://localhost:5173`.

### Configuración

`backend/.env`:

| Variable | Para qué |
|---|---|
| `AZURE_OPENAI_BASE_URL` · `AZURE_OPENAI_API_KEY` | Modelo en Azure. La URL termina en `/openai/v1` (si termina en `/responses` o `/chat/completions`, el backend la recorta) |
| `OPENAI_API_KEY` | OpenAI directo, si no hay credenciales de Azure |
| `SQLITE_RUTA` | Opcional: otra ubicación para `reclamos.db` |
| `HISTORIAL_RUTA` | Opcional: otra ubicación para `historial.db` |
| `TRAZAS` | `langsmith`, `local` o `ninguna`. Ver `backend/trazas.py` |
| `LANGSMITH_API_KEY` · `LANGSMITH_PROJECT` | Solo si `TRAZAS=langsmith` |

El modelo se define en la constante `MODELO` de `backend/main.py` (`gpt-4.1`). Con
Azure, el deployment tiene que llamarse igual.

### Endpoints

| Ruta | Devuelve |
|---|---|
| `POST /api/chat` | La respuesta, el análisis completo (consultas, SQL, filtros, cálculo, premisa) y el costo |
| `GET /api/conversaciones` | Las conversaciones guardadas, para la barra lateral |
| `GET /api/conversaciones/{id}` | Los turnos de una conversación, para volver a pintarla |
| `DELETE /api/conversaciones/{id}` | Borra la conversación: su historial y la memoria del agente |
| `GET /api/historial` · `GET /api/historial/{id}` | Las últimas preguntas y el detalle de un turno |
| `GET /api/uso` · `GET /api/uso/{id}` | El gasto total y el de una conversación |

### Pruebas

Ninguna llama a la API del modelo. Las del sandbox necesitan Docker abierto:

```
backend\.venv\Scripts\python.exe backend\pruebas_reglas.py       # armado del SQL y chequeos; incluye las cifras de control contra la base
backend\.venv\Scripts\python.exe backend\pruebas_grafo.py        # cableado del grafo, con un modelo falso
backend\.venv\Scripts\python.exe backend\pruebas_aislamiento.py  # el contenedor
backend\.venv\Scripts\python.exe backend\pruebas_perfil.py       # lectura aritmética del resultado
backend\.venv\Scripts\python.exe backend\pruebas_formato.py      # saneado del HTML
```

`backend/evaluacion.py` sí llama a la API (y gasta): corre un set de preguntas
reales y compara decisiones y cifras contra lo esperado.

### Documentación

| Archivo | Para qué |
|---|---|
| [`backend/agente/README.md`](backend/agente/README.md) | Cómo piensa el agente: nodos, frontera SQL/Python, chequeos mecánicos |
| [`backend/datos_basePublica/README.md`](backend/datos_basePublica/README.md) | Los datos: origen, modelo, trampas, cifras de control |
| [`presentacion/`](presentacion/) | La presentación de IA Week 2026 |
