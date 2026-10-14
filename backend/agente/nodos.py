"""Los nodos del grafo, uno por etapa del protocolo de las instrucciones del agente.

    comprender ─┬─(hay dudas sobre los datos)─> sondear ─> comprender
                 │
                 └─> revisar ─┬─(error y queda intento)─> comprender
                              └─> construir ─> extraer ─> componer ─┬─(falla)─> reparar
                                                                    │
                                                       revisar_resultado ─> redactar

`sondear` existe para las preguntas cuya respuesta depende de algo que solo los
datos saben: qué valores tiene una columna, si existe un prestador o un código,
cuál es el último período cargado. Antes había dos salidas y las dos eran malas — inventar el
dato, o pararse a preguntarle al usuario algo que la base contesta sola.

La frontera de diseño está entre `extraer` y `componer`:

- **`extraer`** corre SQL contra los 656 mil reclamos de la sábana. Un filtro
  faltante ahí es invisible, por eso el SQL lo arma código y no el modelo.
- **`componer`** corre Python sobre los agregados que ya volvieron — decenas de
  números que la persona ve en el panel. Un error ahí es un error que se detecta.

Por eso el SQL lo arma código y el Python lo escribe el modelo: el riesgo de cada
uno es de naturaleza distinta.
"""

from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import aislamiento
import consulta
import formato
import perfil as lectura_fija
from uso_tokens import medir, sumar_usos

from .decisiones import ConsultaBase, Decisiones, Derivacion, Revision
from .reglas import (
    ErrorDeConstruccion,
    construir_sql,
    consulta_para_verificar_premisa,
    contrastar_premisa,
    premisa_en_la_pregunta,
    preparar_sondeo,
    verificar_medidas_repetidas,
    verificar_participacion,
    verificar_periodo,
    verificar_plan_no_vacio,
    verificar_premisa,
)

# Cuántas veces se rehace la comprensión si el revisor la rechaza. Uno alcanza
# para los olvidos típicos y acota el gasto: cada vuelta son dos llamadas más.
MAX_REINTENTOS = 1

# Cuántas veces se le deja corregir el código de composición que falló.
MAX_REPARACIONES = 1

# Cuántas vueltas de sondeo se permiten. Cada una es una comprensión más, así que
# el tope es lo que separa "averiguar un dato" de "explorar la base a ciegas".
# Con dos alcanza: si después de dos consultas de reconocimiento la duda sigue,
# no es una duda sobre los datos y hay que preguntarle a la persona.
MAX_RONDAS_SONDEO = 2


class Nodos:
    """Los nodos comparten modelo, prompts y esquema; por eso van juntos acá.

    Se arma una vez al arrancar (`main.py`) y se le pasa al grafo.
    """

    def __init__(
        self,
        modelo,
        modelo_nombre: str,
        prefijo: str,
        cierre: str,
        permitidas: set[str],
        capacidades: str = "",
        rango: tuple[int, int] | None = None,
    ):
        self.modelo = modelo
        self.nombre = modelo_nombre
        self.prefijo = prefijo  # rol + diccionario; estable, se cachea
        self.cierre = cierre  # esquema real; va al final, donde el modelo sí lo lee
        self.permitidas = permitidas
        # Qué contestar cuando la pregunta no es de datos. Corto: se usa en una
        # llamada aparte que no lleva el diccionario.
        self.capacidades = capacidades
        # (desde, hasta) como anio*100+mes, leido de la base al arrancar.
        # Sirve para comprobar con codigo que el periodo pedido existe.
        self.rango = rango
        self.decisor = modelo.with_structured_output(Decisiones, include_raw=True)
        self.revisor = modelo.with_structured_output(Revision, include_raw=True)
        self.explicador = modelo.with_structured_output(Derivacion, include_raw=True)

    def _hoy(self) -> SystemMessage:
        return SystemMessage(content=f"Hoy es {date.today().isoformat()}.")

    # -- 1. Comprender ------------------------------------------------------

    def comprender(self, estado) -> dict:
        mensajes = [
            SystemMessage(content=self.prefijo),
            self._hoy(),
            *estado["mensajes"],
            SystemMessage(content=self.cierre),
        ]

        # Si venimos de un rechazo del revisor, se le dice qué corregir.
        revision = estado.get("revision") or {}
        errores = [h for h in revision.get("hallazgos", []) if h["gravedad"] == "error"]
        if errores:
            mensajes.append(
                SystemMessage(
                    content=(
                        "Un revisor encontró estos problemas en tus decisiones anteriores. "
                        "Corrígelos y devuelve las decisiones completas de nuevo:\n"
                        + "\n".join(f"- [{h['paso']}] {h['que_falta']} → {h['correccion']}" for h in errores)
                    )
                )
            )

        # Lo que volvió de los sondeos va ÚLTIMO, después del esquema: es el dato
        # más fresco y el que motivó esta segunda vuelta. Puesto antes del
        # diccionario se pierde (probado con el esquema, mismo efecto).
        sondeos = estado.get("sondeos") or {}
        if sondeos:
            mensajes.append(SystemMessage(content=_resumen_de_sondeos(sondeos)))

        respuesta = self.decisor.invoke(mensajes)
        decisiones: Decisiones = respuesta["parsed"]

        # En un reintento el costo se suma al de la primera vuelta: para el
        # medidor, una pregunta del usuario sigue siendo un solo gasto.
        costo = medir(respuesta["raw"], self.nombre)
        previo = estado.get("uso_comprension")
        return {
            "decisiones": decisiones.model_dump(),
            "sqls": {},
            "inyectados": {},
            "extracciones": {},
            "error_extraccion": "",
            "composicion": "",
            "impreso": "",
            "error_composicion": "",
            "reparaciones": 0,
            "resultado": None,
            "revision_resultado": {},
            "uso_comprension": sumar_usos(previo, costo) if previo else costo,
        }

    def despues_de_comprender(self, estado) -> str:
        """Sondear, revisar, o ir derecho a contestar.

        Tres salidas, en orden de prioridad:

        - **`redactar`** si la pregunta no es de datos. Un saludo o un "¿qué sabes
          hacer?" no tiene plan que revisar ni SQL que armar, y hacerlo pasar por
          el protocolo terminaba en una consulta sin sentido o en una pregunta de
          aclaración desconcertante.
        - **`sondear`** si hay dudas sobre los datos. Manda sobre
          `necesita_aclaracion`: si el modelo pidió una aclaración Y declaró un
          sondeo, primero se mira la base. Preguntarle a la persona algo que la
          tabla contesta sola es una parada mal puesta.
        - **`revisar`** en el caso normal.
        """
        decisiones = estado.get("decisiones") or {}
        if decisiones.get("intencion") == "conversacion":
            return "redactar"
        if decisiones.get("sondeos") and estado.get("rondas_sondeo", 0) < MAX_RONDAS_SONDEO:
            return "sondear"
        return "revisar"

    # -- 1b. Sondear: preguntarle a los datos ------------------------------

    def sondear(self, estado) -> dict:
        """Ejecuta las consultas de reconocimiento y vuelve a `comprender`.

        No llama al modelo: solo arma el SQL y lo corre. Los sondeos pasan por el
        mismo `construir_sql` que las extracciones — misma lista blanca de
        columnas, mismo escapado de valores — para que lo que se averigüe sea
        cierto con los mismos filtros que después se van a usar.
        """
        hechos = dict(estado.get("sondeos") or {})
        ya_corridos = {h.get("sql") for h in hechos.values() if h.get("sql")}

        for cruda in estado["decisiones"].get("sondeos", []):
            base, inyectados = preparar_sondeo(ConsultaBase(**cruda))
            try:
                sql = construir_sql(base, self.permitidas)
            except ErrorDeConstruccion as error:
                hechos[base.nombre] = {
                    "proposito": base.proposito,
                    "sql": "",
                    "inyectados": inyectados,
                    "resultado": None,
                    "error": str(error),
                }
                continue

            # Si en la ronda anterior ya se preguntó exactamente esto, no se
            # vuelve a pagar: la respuesta no cambió en los últimos dos segundos.
            if sql in ya_corridos:
                continue

            resultado = consulta.ejecutar(sql)
            ya_corridos.add(sql)
            hechos[base.nombre] = {
                "proposito": base.proposito,
                "sql": sql,
                "inyectados": inyectados,
                "resultado": resultado,
                "error": resultado["error"],
            }

        return {"sondeos": hechos, "rondas_sondeo": estado.get("rondas_sondeo", 0) + 1}

    # -- 2. Revisar las decisiones -----------------------------------------

    def revisar(self, estado) -> dict:
        decisiones = estado["decisiones"]
        if decisiones["necesita_aclaracion"]:
            return {"revision": {"aprobado": True, "hallazgos": []}}

        # Primero lo que se comprueba sin criterio. Que un porcentaje tenga
        # denominador se decide mirando qué columnas filtra cada consulta: no hace
        # falta un modelo, y un modelo además tiende a aprobarlo, porque el plan
        # está bien armado — solo contesta otra pregunta.
        mecanicos = (
            verificar_plan_no_vacio(decisiones)
            + verificar_medidas_repetidas(decisiones)
            + verificar_premisa(decisiones, estado["mensajes"][-1].content)
            + verificar_participacion(decisiones)
        )
        if self.rango:
            mecanicos = verificar_periodo(decisiones, *self.rango) + mecanicos
        if any(h["gravedad"] == "error" for h in mecanicos):
            # La corrección es exacta, así que no se paga la llamada al revisor:
            # se vuelve a comprender directo. En esa vuelta el revisor corre igual.
            return {
                "revision": {"aprobado": False, "hallazgos": mecanicos},
                "reintentos": estado.get("reintentos", 0) + 1,
            }

        # Al revisor NO se le muestra la interpretación ni las justificaciones:
        # si ve el razonamiento que llevó a una decisión, la aprueba. Solo ve la
        # pregunta y el plan desnudo, y busca omisiones contra el diccionario.
        mensajes = [
            SystemMessage(content=self.prefijo),
            self._hoy(),
            HumanMessage(content=f"Pregunta original: {estado['mensajes'][-1].content}"),
            SystemMessage(
                content=(
                    "Eres el revisor. NO resuelvas la pregunta de nuevo: tu único trabajo es "
                    "encontrar lo que falta o está mal en el plan de abajo, contra el "
                    "protocolo y el diccionario de la tabla.\n\n"
                    "**Empieza por lo más importante: ¿este plan responde LA pregunta que se "
                    "hizo?** Un plan puede estar impecable y contestar otra cosa. Lee la "
                    "pregunta original, mira los filtros de cada consulta y decide si el "
                    "número que va a salir es el que se pidió.\n\n"
                    "Si se pide un porcentaje o participación, verifica parte y todo con los "
                    "filtros a la vista: el universo que la pregunta nombra ('el costo "
                    "hospitalario') es el DENOMINADOR y tiene que estar filtrado en las dos "
                    "consultas; la categoría de la que se pide el porcentaje ('aparato "
                    "circulatorio') es la PARTE y solo puede estar filtrada en la del "
                    "numerador. Invertirlos da un número creíble y equivocado: eso es "
                    "'error', no 'aviso'.\n\n"
                    "Revisa también las trampas del diccionario que apliquen: un promedio "
                    "que mezcla hospitalario y ambulatorio, un filtro `monto_pagado > 0` "
                    "que descarta reclamos válidos, un gasto per cápita que la tabla no "
                    "permite calcular, o una pregunta cuya premisa (un alza, una caída) no "
                    "se verificó antes de explicarla.\n\n"
                    "No revises todos los pasos: evalúa cuáles aplican a esta pregunta. "
                    "Marca 'error' solo si el resultado sería incorrecto — columna "
                    "equivocada, período mal resuelto, métrica que no corresponde, razón no "
                    "aditiva que se está sumando por dimensión en vez de recalcularse. Todo "
                    "lo demás es 'aviso'.\n\n"
                    + _resumen_del_plan(decisiones)
                    + f"\n\n{self.cierre}"
                )
            ),
        ]
        respuesta = self.revisor.invoke(mensajes)
        revision: Revision = respuesta["parsed"]
        encontrado = revision.model_dump()
        return {
            # Los avisos mecánicos van adelante: son los únicos que no dependen
            # de que el revisor haya mirado para el lado correcto.
            "revision": {
                "aprobado": encontrado["aprobado"],
                "hallazgos": mecanicos + encontrado["hallazgos"],
            },
            "reintentos": estado.get("reintentos", 0) + 1,
            "uso_revision": medir(respuesta["raw"], self.nombre),
        }

    def hay_que_rehacer(self, estado) -> str:
        """¿Se vuelve a comprender, o se sigue a construir?"""
        revision = estado.get("revision") or {}
        hay_error = any(h["gravedad"] == "error" for h in revision.get("hallazgos", []))
        if hay_error and estado.get("reintentos", 0) <= MAX_REINTENTOS:
            return "comprender"
        return "construir"

    # -- 3. Construir el SQL (sin modelo) ----------------------------------

    def construir(self, estado) -> dict:
        decisiones = estado["decisiones"]
        if decisiones["necesita_aclaracion"]:
            return {"sqls": {}, "inyectados": {}}

        # No hay reglas fijas que inyectar: lo que se ejecuta es exactamente lo que
        # declaró el modelo. `inyectados` queda vacío y se mantiene para que el
        # frontend y el historial sigan teniendo la misma forma.
        sqls, inyectados = {}, {}
        for cruda in decisiones["consultas"]:
            base = ConsultaBase(**cruda)
            inyectados[base.nombre] = []
            try:
                sqls[base.nombre] = construir_sql(base, self.permitidas)
            except ErrorDeConstruccion as error:
                return {
                    "sqls": {},
                    "inyectados": inyectados,
                    "error_extraccion": f"En la consulta `{base.nombre}`: {error}",
                }

        return {"sqls": sqls, "inyectados": inyectados, "error_extraccion": ""}

    # -- 4. Extraer de la base (SQLite) ------------------------------------

    def extraer(self, estado) -> dict:
        if not estado["sqls"]:
            return {"extracciones": {}}

        extracciones = {}
        for nombre, sql in estado["sqls"].items():
            extracciones[nombre] = consulta.ejecutar(sql)

        fallidas = [n for n, r in extracciones.items() if r["error"]]
        return {
            "extracciones": extracciones,
            "error_extraccion": (
                f"Falló la consulta `{fallidas[0]}`: {extracciones[fallidas[0]]['error']}"
                if fallidas
                else ""
            ),
        }

    # -- 5. Componer en Python (sandbox) -----------------------------------

    def componer(self, estado) -> dict:
        extracciones = estado["extracciones"]
        if not extracciones or estado["error_extraccion"]:
            return {"resultado": None}

        codigo = (estado.get("composicion") or estado["decisiones"].get("composicion") or "").strip()

        # Sin código y una sola extracción: el resultado ya está. Nos ahorramos
        # el contenedor entero, que es un segundo de reloj.
        if not codigo and len(extracciones) == 1:
            unica = next(iter(extracciones.values()))
            return {"resultado": unica, "composicion": "", "error_composicion": ""}

        if not codigo:
            return {
                "resultado": None,
                "error_composicion": (
                    "Hay varias consultas pero no se escribió el código que las combina."
                ),
            }

        tablas = {nombre: consulta.tabla_de(sql) for nombre, sql in estado["sqls"].items()}
        ejecucion = aislamiento.ejecutar(codigo, tablas)

        if not ejecucion["ok"]:
            return {
                "resultado": None,
                "composicion": codigo,
                "impreso": ejecucion["impreso"],
                "error_composicion": ejecucion["error"],
            }

        return {
            "resultado": consulta.desde_tabla(ejecucion["resultado"]),
            "composicion": codigo,
            "impreso": ejecucion["impreso"],
            "error_composicion": "",
        }

    def hay_que_reparar(self, estado) -> str:
        if estado.get("error_composicion") and estado.get("reparaciones", 0) < MAX_REPARACIONES:
            return "reparar"
        return "revisar_resultado"

    # -- 5b. Reparar el código que falló ------------------------------------

    def reparar(self, estado) -> dict:
        """Le muestra al modelo el error y las columnas reales de cada extracción.

        Escribir el cruce a ciegas falla sobre todo por nombres de columna: acá
        se los damos medidos, no supuestos.
        """
        formas = "\n".join(
            f"consultas['{nombre}'] -> {r['total_filas']} filas, columnas: {r['columnas']}"
            for nombre, r in estado["extracciones"].items()
        )
        mensajes = [
            SystemMessage(
                content=(
                    "Escribiste código Python para combinar los resultados de unas consultas "
                    "y falló. Corrígelo.\n\n"
                    "Tienes `pd`, `np` y el diccionario `consultas`. Deja el resultado final "
                    "en una variable `resultado`. No uses imports ni acceso a archivos: el "
                    "código corre aislado, sin red y sin disco."
                )
            ),
            HumanMessage(
                content=(
                    f"Pregunta: {estado['mensajes'][-1].content}\n\n"
                    f"Formas reales de cada consulta:\n{formas}\n\n"
                    f"Código que falló:\n{estado['composicion']}\n\n"
                    f"Error: {estado['error_composicion']}"
                )
            ),
        ]
        respuesta = self.modelo.invoke(mensajes)
        return {
            "composicion": _solo_codigo(respuesta.content),
            "reparaciones": estado.get("reparaciones", 0) + 1,
            "error_composicion": "",
            "uso_reparacion": medir(respuesta, self.nombre),
        }

    # -- 6. Revisar el resultado -------------------------------------------

    def revisar_resultado(self, estado) -> dict:
        resultado = estado["resultado"]
        if resultado is None:
            return {"revision_resultado": {}}

        # La lectura del resultado se calcula acá, en el mismo paso, porque es
        # aritmética nuestra sobre una tabla chica: milisegundos, sin llamada al
        # modelo y sin contenedor. El sandbox existe para aislar código que
        # escribe el LLM; este lo escribimos nosotros.
        #
        # Va con la aditividad de la métrica: sumar una razón entre dimensiones
        # es el error caro del proyecto, y `perfil` se calla en vez de cometerlo.
        perfil_calculado = lectura_fija.perfilar(
            resultado, bool(estado["decisiones"].get("metrica_es_aditiva"))
        )

        # Prompt corto a propósito: acá no hace falta el diccionario entero,
        # solo la pregunta y lo que volvió.
        mensajes = [
            SystemMessage(
                content=(
                    "Eres el revisor del resultado. Te paso una pregunta y lo que devolvió "
                    "la base. Decide si el resultado responde la pregunta.\n\n"
                    "Marca 'error' si vino vacío cuando debería traer datos, si faltan "
                    "períodos que se pidieron, o si el número es imposible. Marca 'aviso' "
                    "para cosas raras pero defendibles. Si está todo bien, aprueba sin "
                    "hallazgos. Usa el paso 'metricas' o 'composicion' según corresponda."
                )
            ),
            HumanMessage(
                content=(
                    f"Pregunta: {estado['mensajes'][-1].content}\n\n"
                    f"{consulta.para_el_modelo(resultado)}"
                )
            ),
        ]
        respuesta = self.revisor.invoke(mensajes)
        revision: Revision = respuesta["parsed"]
        return {
            "revision_resultado": revision.model_dump(),
            "perfil": perfil_calculado,
            "uso_revision_resultado": medir(respuesta["raw"], self.nombre),
        }

    # -- 6b. Leer el resultado: qué mirar lo decide el modelo ---------------

    def leer(self, estado) -> dict:
        """El modelo elige qué comparación importa; Python la calcula.

        Nace de una objeción de diseño, y era buena: `perfil.py` calcula
        siempre lo mismo —líder, brecha, concentración, variación— sin importar
        qué se preguntó. Eso es una plantilla, y las plantillas fueron lo primero
        que este proyecto descartó.

        La frontera correcta no era "código contra modelo" sino **cálculo contra
        criterio**. Qué vale la pena mirar depende de la pregunta, del historial y
        del dato: eso es criterio y es del modelo. Restar y dividir es cálculo
        y es del código. Acá el modelo escribe la operación y el sandbox la
        ejecuta, así que el número sigue saliendo de una máquina.

        Y esto sí puede hacer lo que una función pura sobre una tabla no podrá
        nunca: atar el resultado con lo que se preguntó tres turnos atrás.

        No consulta la base. Trabaja sobre la tabla que ya volvió, que es la que
        la persona está viendo. Si no hay nada que valga la pena calcular, el
        modelo devuelve código vacío y no se ejecuta nada.
        """
        resultado = estado.get("resultado")
        decisiones = estado["decisiones"]
        if resultado is None or decisiones.get("intencion") == "conversacion":
            return {"lectura": {}}
        # Una fila sola no se compara con nada. Nos ahorramos la llamada entera.
        if (resultado.get("total_filas") or 0) < 2:
            return {"lectura": {}}

        # El historial en corto: es lo que permite atar esta tabla con lo anterior.
        previos = [
            m.content for m in estado["mensajes"][:-1] if getattr(m, "type", "") == "human"
        ][-3:]
        contexto = (
            "\n\nAntes en esta conversación se preguntó:\n"
            + "\n".join(f"- {p}" for p in previos)
            + "\nSi algo de eso se conecta de verdad con esta tabla, esa es la lectura "
            "que más vale. Pero NO fuerces la conexión: es normal que alguien cierre un "
            "tema y abra otro distinto, y en ese caso lo anterior no viene al caso. "
            "Atar por atar confunde más de lo que aporta."
            if previos
            else ""
        )

        mensajes = [
            SystemMessage(
                content=(
                    "Eres analista de datos de seguros de salud. Tienes delante una tabla "
                    "de resultados que YA se calculó, y tu trabajo es decidir qué vale la "
                    "pena comparar en ella para esta pregunta en concreto.\n\n"
                    "No consultes nada: solo tienes esta tabla, en `consultas['resultado']` "
                    "como DataFrame de pandas. Tienes `pd` y `np`.\n\n"
                    "Escribe código Python que calcule esas comparaciones y deje en la "
                    "variable `resultado` una tabla con dos columnas: `observacion` (texto "
                    "corto, en lenguaje claro) y `valor` (el número calculado).\n\n"
                    "Qué buscar depende de la pregunta. Brechas entre los primeros puestos, "
                    "cuánto concentra un grupo, variación entre períodos, un valor que se "
                    "sale de la línea, una relación entre dos columnas de la tabla. Elige "
                    "dos o tres, las que de verdad le dirían algo a quien preguntó.\n\n"
                    "REGLAS DURAS:\n"
                    "- Si la métrica NO es aditiva (una razón, un promedio, un porcentaje) "
                    "no la sumes ni la promedies entre filas: el resultado no significa nada.\n"
                    "- No inventes datos ni uses columnas que no estén en la tabla.\n"
                    "- Si en esta tabla no hay nada que comparar que aporte algo, responde "
                    "con una línea vacía y ya. Es una respuesta válida y a veces la correcta: "
                    "más vale no decir nada que llenar la pantalla de cuentas sin sentido.\n\n"
                    "Devuelve solo el código, sin explicación y sin ```."
                )
            ),
            HumanMessage(
                content=(
                    f"Pregunta: {estado['mensajes'][-1].content}{contexto}\n\n"
                    f"Métrica: {decisiones.get('metrica_pedida', '')}"
                    + ("" if decisiones.get("metrica_es_aditiva") else " (NO aditiva)")
                    # Los valores SÍ van, y es una corrección deliberada. Probamos
                    # ocultarlos para que no hiciera la cuenta de cabeza, y salió
                    # peor: sin ver los datos no hay nada que lo lleve a mirar una
                    # cosa en vez de otra, así que escribía siempre lo mismo
                    # —máximo, brecha, top 3— y volvíamos a la plantilla que
                    # queríamos evitar. Lo que impide la aritmética mental es el
                    # guardián de `_numeros_escritos_a_mano`, no la ceguera.
                    + f"\n\n{consulta.para_el_modelo(resultado)}"
                    + _forma_de(resultado)
                )
            ),
        ]
        tabla_entrada = {"resultado": consulta.a_dataframe(resultado)}
        uso_total = None
        descartadas_antes = 0

        # Dos vueltas como mucho: si en la primera escribió los números a mano, se
        # le dice exactamente eso y vuelve a intentar. Antes se perdía la
        # observación en silencio, que es la peor de las salidas.
        for intento in (1, 2):
            respuesta = self.modelo.invoke(mensajes)
            uso = medir(respuesta, self.nombre)
            uso_total = sumar_usos(uso_total, uso) if uso_total else uso
            codigo = _solo_codigo(respuesta.content)

            if not codigo.strip():
                # El modelo decidió que no hay nada que decir. Se respeta.
                return {"lectura": {}, "uso_lectura": uso_total}

            ejecucion = aislamiento.ejecutar(codigo, tabla_entrada)
            if not ejecucion["ok"]:
                # Una lectura que falla no arruina la respuesta: el número que se
                # pidió ya está calculado. Se registra el motivo y se sigue.
                return {
                    "lectura": {
                        "codigo": codigo,
                        "error": ejecucion["error"],
                        "tabla": None,
                        "descartadas": descartadas_antes,
                    },
                    "uso_lectura": uso_total,
                }

            tabla, descartadas = _sin_numeros_copiados(
                consulta.desde_tabla(ejecucion["resultado"]), codigo
            )
            descartadas_antes += descartadas

            quedan = bool(tabla.get("filas"))
            if quedan or intento == 2 or not descartadas:
                return {
                    "lectura": {
                        "codigo": codigo,
                        "error": "",
                        "tabla": tabla if quedan else None,
                        # Cuántas observaciones traían el número escrito a mano.
                        # Se guarda para poder medir con qué frecuencia pasa, en
                        # vez de suponerlo.
                        "descartadas": descartadas_antes,
                    },
                    "uso_lectura": uso_total,
                }

            # Se descartó todo: segunda vuelta, diciéndole qué pasó.
            mensajes = mensajes + [
                AIMessage(content=codigo),
                SystemMessage(
                    content=(
                        "Escribiste los números a mano en vez de calcularlos, así que se "
                        "descartaron todos. Rehazlo: cada valor tiene que salir de una "
                        "operación sobre `consultas['resultado']`. Nada de constantes "
                        "numéricas en la columna `valor`."
                    )
                ),
            ]

        return {"lectura": {}, "uso_lectura": uso_total}

    # -- 6c. Explicar el cálculo en lenguaje claro -------------------------

    def explicar(self, estado) -> dict:
        """Traduce la cuenta a los términos de quien pregunta.

        El SQL y el Python ya están en el panel de razonamiento, pero eso sirve
        para auditar, no para leer. Quien pregunta no puede aceptar una
        cifra que aparece sola: necesita ver qué números entraron, de qué universo
        salieron y qué se supuso por él.

        Los números que devuelve el modelo se verifican contra las extracciones
        reales antes de mostrarse. Si inventa uno, se marca — que es exactamente
        el riesgo de pedirle a un modelo que explique de dónde sale una cifra.
        """
        resultado = estado.get("resultado")
        if resultado is None or estado["decisiones"].get("intencion") == "conversacion":
            return {"derivacion": {}}

        extracciones = estado.get("extracciones") or {}
        insumos = "\n\n".join(
            f"Consulta `{nombre}` — {_proposito_de(estado, nombre)}\n"
            + consulta.para_el_modelo(datos)
            for nombre, datos in extracciones.items()
        )

        mensajes = [
            SystemMessage(
                content=(
                    "Tu trabajo es explicarle a una persona que no es técnica de dónde sale "
                    "el número, sin tecnicismos. No recalcules nada: los números ya están.\n\n"
                    "Escribe en español de Ecuador, tratando al usuario de usted. Nada de "
                    "voseo ni de modismos rioplatenses.\n\n"
                    "En `formula` pon la cuenta con nombres claros, no con columnas de "
                    "base de datos. En `componentes` pon los números que entraron a esa "
                    "cuenta, cada uno con el universo del que salió.\n\n"
                    "REGLA DURA: cada valor de `componentes` tiene que aparecer tal cual en "
                    "los resultados de abajo. No lo redondees, no lo estimes y no lo "
                    "deduzcas. Si un número que te gustaría citar no está, no lo pongas.\n\n"
                    "Si el resultado es una suma directa y no hay ninguna cuenta que "
                    "explicar, deja `formula` y `componentes` vacíos.\n\n"
                    "En `sugerencias` van como máximo dos preguntas de seguimiento, "
                    "específicas de lo que salió en ESTE resultado y sobre columnas que "
                    "existen en la tabla (la lista va al final). **Sin una sola cifra**: "
                    "una sugerencia propone mirar algo, no afirma nada. Si no se te ocurre "
                    "nada que aporte de verdad, déjalo vacío.\n\n"
                    "En `supuestos` va solo lo que decidiste tú y el usuario no pidió: el "
                    "período que se asumió, qué definición de costo se usó (monto pagado o "
                    "monto total), una desambiguación entre dos columnas posibles."
                )
            ),
            HumanMessage(
                content=(
                    f"Pregunta: {estado['mensajes'][-1].content}\n\n"
                    f"Métrica: {estado['decisiones'].get('metrica_pedida', '')}\n"
                    f"Período: {estado['decisiones'].get('periodo', '')}\n\n"
                    f"Lo que devolvió cada consulta:\n\n{insumos}\n\n"
                    f"Código que las combinó:\n{estado.get('composicion') or '(ninguno)'}\n\n"
                    f"Resultado final:\n{consulta.para_el_modelo(resultado)}"
                    # La lectura ya calculada, para que las sugerencias apunten a lo
                    # que de verdad llamó la atención y no a cualquier cosa.
                    + lectura_fija.para_el_modelo(estado.get("perfil") or {})
                    + _lectura_del_modelo(estado.get("lectura") or {})
                    + f"\n\n{self.cierre}"
                )
            ),
        ]
        respuesta = self.explicador.invoke(mensajes)
        derivacion: Derivacion = respuesta["parsed"]
        return {
            "derivacion": _verificar_derivacion(derivacion.model_dump(), estado),
            "uso_explicacion": medir(respuesta["raw"], self.nombre),
        }

    # -- 7. Redactar --------------------------------------------------------

    def _conversar(self, estado) -> dict:
        """Responde con cordialidad cuando la pregunta no es de datos.

        Prompt corto a propósito: acá no hace falta el diccionario, y meterlo
        sería pagar miles de tokens para contestar "buenos días". Solo el resumen de
        capacidades y el historial, para que un "gracias" después de un análisis no
        se conteste como si fuera el primer mensaje.
        """
        mensajes = [
            SystemMessage(
                content=(
                    "Es usted un asistente de analítica de seguros de salud que trabaja "
                    "con datos públicos. El usuario le escribió algo que no es una consulta "
                    "de datos: un saludo, un agradecimiento, una pregunta sobre qué sabe "
                    "hacer, o un pedido que esta base no puede responder.\n\n"
                    "Conteste con amabilidad y brevedad, y déjele claro en qué sí puede "
                    "ayudarlo. Si el pedido era de datos pero está fuera de lo que hay en "
                    "la base, dígalo con claridad y ofrezca la alternativa más parecida "
                    "que sí puede hacer.\n\n"
                    "IDIOMA: español de Ecuador, tratando al usuario de usted. Nada de "
                    "voseo ni de modismos rioplatenses.\n\n"
                    "No invente cifras ni ofrezca análisis concretos con números: aquí no "
                    "consultó la base.\n\n"
                    "Sea breve: dos o tres frases y, si viene al caso, una lista corta con "
                    "ejemplos de preguntas que puede responder. No recite la lista entera "
                    "de capacidades si el usuario solo saludó.\n\n"
                    "Esto es lo que sabe hacer:\n\n"
                    f"{self.capacidades}\n\n"
                    "Formato: escriba HTML, sin bloque de código ni ```. Puede usar solo "
                    "estas etiquetas: <p>, <strong>, <em>, <ul>, <ol>, <li>, <code>, <h4>, "
                    "<br>. Nada de <table>, <div>, <span>, atributos ni estilos."
                )
            ),
            *estado["mensajes"],
        ]
        respuesta = self.modelo.invoke(mensajes)
        html = formato.sanear(respuesta.content)
        return {
            "mensajes": [AIMessage(content=formato.a_texto(html))],
            "respuesta": html,
            "mostrar_tabla": False,
            "uso_redaccion": medir(respuesta, self.nombre),
        }

    def _verificar_premisa_por_codigo(self, decisiones: dict, pregunta: str) -> dict:
        """Verifica la premisa con una consulta que arma el código, si hace falta.

        Llega acá cuando la pregunta supone un alza o una caída y el plan no
        trajo el total por período ni después de que el revisor lo pidiera: el
        grafo no se traba, sigue con lo que tiene. Medido contra la API, en ese
        caso el redactor contestaba "el alza se concentró en..." sobre un total
        que había bajado. Una consulta más por año es barata; esa respuesta, no.
        """
        direccion = decisiones.get("premisa_direccion") or premisa_en_la_pregunta(pregunta)
        if not direccion:
            return {}
        verificacion = consulta_para_verificar_premisa(decisiones)
        if verificacion is None:
            return {}
        try:
            sql = construir_sql(verificacion, self.permitidas)
        except ErrorDeConstruccion:
            return {}
        resultado = consulta.ejecutar(sql)
        contraste = contrastar_premisa(
            {
                **decisiones,
                "premisa_direccion": direccion,
                "consultas": [verificacion.model_dump()],
            },
            {verificacion.nombre: resultado},
        )
        if contraste:
            contraste.update(sql=sql, agregada_por_el_sistema=True)
        return contraste

    def redactar(self, estado) -> dict:
        decisiones = estado["decisiones"]

        # Un saludo, un agradecimiento o un "¿qué sabes hacer?". No hay plan ni
        # números: se contesta con amabilidad y se cuenta en qué puede ayudar.
        # Antes esto entraba al protocolo y salía una consulta sin sentido.
        if decisiones.get("intencion") == "conversacion":
            return self._conversar(estado)

        # Casos donde no hay números que redactar: nos ahorramos la llamada.
        if decisiones["necesita_aclaracion"]:
            texto = decisiones["pregunta_de_aclaracion"] or decisiones["interpretacion"]
            return {
                "mensajes": [AIMessage(content=texto)],
                "respuesta": f"<p>{formato.sanear(texto)}</p>",
                "mostrar_tabla": False,
            }

        problema = estado.get("error_extraccion") or estado.get("error_composicion")
        if problema or estado["resultado"] is None:
            # Si el revisor dejó dicho qué estaba mal, eso vale mucho más que el
            # mensaje genérico. Si alguien pregunta "necesito las variaciones vs
            # enero" y el revisor señala que falta decir de qué métrica, la
            # persona recibiría "No pude completar el análisis. No se llegó a
            # ningún resultado" — que no dice qué pasó ni qué hacer.
            pendientes = [
                h["que_falta"]
                for h in (estado.get("revision") or {}).get("hallazgos", [])
                if h.get("gravedad") == "error"
            ]
            if not problema and pendientes:
                texto = (
                    "No pude resolver la pregunta con lo que me dio. Esto es lo que "
                    "quedó sin definir:\n\n"
                    + "\n".join(f"- {p}" for p in pendientes)
                    + "\n\nSi me precisa eso, la vuelvo a intentar."
                )
            else:
                texto = (
                    "No pude completar el análisis. "
                    + (problema or "No se llegó a ningún resultado.")
                    + "\n\nSi desea, reformule la pregunta o indíqueme qué columna usar."
                )
            return {
                "mensajes": [AIMessage(content=texto)],
                "respuesta": f"<p>{formato.sanear(texto)}</p>",
                "mostrar_tabla": False,
            }

        avisos = [
            f"{h['paso']}: {h['que_falta']}"
            for h in (estado.get("revision_resultado") or {}).get("hallazgos", [])
        ]
        resultado = estado["resultado"]
        hay_tabla = resultado["total_filas"] > consulta.FILAS_PARA_MOSTRAR_TABLA

        # Lo que la pregunta daba por hecho, contra el total real. Se calcula acá
        # y no se le pide al modelo: el revisor ya exigió que exista la consulta
        # del total, y comparar dos números es trabajo de código.
        contraste = contrastar_premisa(decisiones, estado.get("extracciones") or {})
        if not contraste:
            contraste = self._verificar_premisa_por_codigo(
                decisiones, estado["mensajes"][-1].content
            )

        # La instrucción cambia según si la persona va a ver la tabla al lado.
        # Sin esto el modelo enumera todas las filas en prosa Y encima se muestra
        # la tabla: los mismos números dos veces.
        if hay_tabla:
            que_escribir = (
                f"DEBAJO DE SU RESPUESTA se muestra una tabla con las "
                f"{resultado['total_filas']} filas completas. NO las enumere ni repita "
                f"la tabla: sería decir lo mismo dos veces.\n"
                "En vez de eso, interprete: el total, cómo se reparte, los extremos, la "
                "tendencia si hay período, y cualquier cosa llamativa. Puede citar dos o "
                "tres números puntuales para apoyar lo que dice, no más."
            )
        else:
            que_escribir = (
                "El resultado es pequeño y NO se va a mostrar como tabla: los números van "
                "dentro de su respuesta. Déjelos completos, con separador de miles."
            )

        # Los componentes ya verificados se muestran aparte, en su propia tarjeta.
        # Se le avisa para que no los repita en la prosa: el mismo número dicho dos
        # veces en la misma pantalla se lee como dos números.
        derivacion = estado.get("derivacion") or {}
        aviso_derivacion = ""
        if derivacion.get("componentes"):
            nombres = ", ".join(c["nombre"] for c in derivacion["componentes"])
            aviso_derivacion = (
                f"\n\nJUNTO A SU RESPUESTA se muestra un detalle de cómo se calculó, con "
                f"estos valores: {nombres}. Puede mencionar el más importante, pero no los "
                f"enumere todos: ya están a la vista."
            )

        mensajes = [
            SystemMessage(
                content=(
                    "Es usted un analista de seguros de salud que trabaja con datos "
                    "públicos. Le paso la pregunta y el resultado real de la base de "
                    "datos.\n\n"
                    "IDIOMA: español de Ecuador, tratando al usuario de usted. Nada de "
                    "voseo ('vos', 'tenés', 'podés', 'fijate') ni de modismos rioplatenses "
                    "('acá', 'plata', 'recién'). Escriba como un informe de análisis: "
                    "claro, sobrio y profesional.\n\n"
                    f"{que_escribir}{aviso_derivacion}\n\n"
                    "Escriba como le hablaría a un gerente, no como un informe de "
                    "sistemas. Nada de nombres de columnas (`anio`, `monto_pagado`, "
                    "`grupo_enfermedad`), nada de aclaraciones técnicas entre "
                    "paréntesis, nada de contar cuántas filas se procesaron ni qué "
                    "pasos internos se siguieron. Esa información existe y está a un "
                    "clic en el panel de razonamiento: repetirla aquí solo estorba.\n\n"
                    "Vaya al grano. Si la respuesta es un número, dígalo en la primera "
                    "frase y no lo reparta en dos oraciones con una introducción en el "
                    "medio.\n\n"
                    "Una excepción a la brevedad: si el período NO lo pidió el usuario "
                    "sino que lo resolvió el sistema —«el último disponible», «el año "
                    "pasado»— nombre el período en palabras dentro de esa misma frase. "
                    "Quien pregunta tiene derecho a saber de qué período le están "
                    "hablando, y es la única forma de que detecte si el supuesto era "
                    "otro. Lo mismo con la definición de costo: si se usó el monto pagado "
                    "por la aseguradora, dígalo.\n\n"
                    "Reglas:\n"
                    "- Los montos son dólares. Separador de miles con punto.\n"
                    "- Los datos son sintéticos (Medicare, Estados Unidos). Si la pregunta "
                    "pide sacar conclusiones sobre personas o sobre la población real, "
                    "recuerde en una frase que estos datos no sirven para eso.\n"
                    "- No agregue ningún número que no esté en el resultado.\n"
                    "- Si el resultado vino vacío, dígalo y plantee una posible causa.\n"
                    "- NUNCA afirme que falta un período, una categoría o un valor porque "
                    "no lo ve en la lista. Las filas no vienen ordenadas por dimensión y "
                    "contarlas de memoria sale mal. Para eso está el bloque 'Conteo de "
                    "filas por valor': si ahí una categoría figura con 12 filas, tiene las "
                    "12 aunque estén desperdigadas. Y si la lista dice que es un recorte, "
                    "lo que no ve puede estar igual.\n\n"
                    "Formato: escriba HTML, sin bloque de código ni ```. Puede usar solo "
                    "estas etiquetas: <p>, <strong>, <em>, <ul>, <ol>, <li>, <code>, <h4>, "
                    "<br>. Nada de <table>, <div>, <span>, atributos ni estilos."
                )
            ),
            HumanMessage(
                content=(
                    f"Pregunta: {estado['mensajes'][-1].content}\n\n"
                    # El período resuelto. Sin esto el redactor no sabe qué mes se
                    # usó cuando lo decidió el sistema, y contesta un número suelto
                    # sin decir de cuándo es.
                    f"Período que se usó: {decisiones.get('periodo', '')}\n\n"
                    f"{consulta.para_el_modelo(resultado)}"
                    # Las brechas y variaciones vienen calculadas por código. El
                    # modelo las narra; no las recalcula, que es donde se equivoca.
                    + lectura_fija.para_el_modelo(estado.get("perfil") or {})
                    + _lectura_del_modelo(estado.get("lectura") or {})
                    + _sondeos_en_una_linea(estado.get("sondeos") or {})
                    + _premisa_para_el_redactor(contraste)
                    + (f"\n\nEl revisor marcó: {'; '.join(avisos)}" if avisos else "")
                )
            ),
        ]
        respuesta = self.modelo.invoke(mensajes)

        # Se sanea siempre: el texto puede arrastrar valores de la base, y de ahí
        # podría venir HTML que no queremos renderizar (ver `formato.py`).
        html = formato.sanear(respuesta.content)

        # Al historial va la respuesta en texto plano más el plan: es lo que
        # necesita un seguimiento ("¿y en 2008?") para partir de lo anterior, y
        # las etiquetas ahí solo gastarían tokens.
        acta = f"{formato.a_texto(html)}\n\n[Plan usado]\n{_resumen_del_plan(decisiones)}"
        return {
            "mensajes": [AIMessage(content=acta)],
            "respuesta": html,
            "mostrar_tabla": hay_tabla,
            "premisa": contraste,
            "uso_redaccion": medir(respuesta, self.nombre),
        }


# Cuánto puede alejarse un número citado del que salió de la base para seguir
# contando como el mismo. Los montos llevan centavos; en porcentajes y razones el
# modelo a veces cita menos decimales de los que devolvió el cálculo.
TOLERANCIA_ABSOLUTA = 0.01
TOLERANCIA_RELATIVA = 1e-4


def _proposito_de(estado: dict, nombre: str) -> str:
    for cruda in estado["decisiones"].get("consultas", []):
        if cruda.get("nombre") == nombre:
            return cruda.get("proposito", "")
    return ""


def _numeros_reales(estado: dict) -> list[float]:
    """Todos los números que de verdad salieron de la base en este turno."""
    tablas = list((estado.get("extracciones") or {}).values())
    if estado.get("resultado"):
        tablas.append(estado["resultado"])
    numeros = []
    for tabla in tablas:
        for fila in tabla.get("filas") or []:
            for celda in fila:
                if celda is None or isinstance(celda, bool):
                    continue
                try:
                    numeros.append(float(celda))
                except (TypeError, ValueError):
                    continue
    return numeros


def _verificar_derivacion(derivacion: dict, estado: dict) -> dict:
    """Marca los componentes cuyo valor no aparece en ninguna extracción.

    Le estamos pidiendo a un modelo que explique de dónde sale una cifra, y la
    forma natural de fallar es citar un número redondeado o directamente inventado
    — que es peor que no explicar nada, porque se muestra como evidencia. Acá se
    comprueba contra los números que realmente devolvió la base.

    No se borra el componente: se marca `verificado: False`. Un número que el
    sistema no pudo confirmar y lo dice es honesto; uno que desaparece esconde que
    el modelo se lo inventó.
    """
    reales = _numeros_reales(estado)
    componentes = []
    for componente in derivacion.get("componentes", []):
        try:
            valor = float(componente.get("valor"))
        except (TypeError, ValueError):
            componentes.append({**componente, "verificado": False})
            continue
        # Los porcentajes viajan como fracción (0,2609) y el modelo suele citarlos
        # como 26,09, así que ahí se aceptan las dos lecturas. SOLO ahí: admitir
        # el factor 100 en cualquier número hace que 99.999 "coincida" con 1.000
        # y la verificación deja de verificar nada (lo encontró una prueba, no la
        # producción, que es donde uno quiere encontrar estas cosas).
        if componente.get("unidad") == "porcentaje":
            candidatos = (valor, valor / 100.0, valor * 100.0)
        else:
            candidatos = (valor,)
        verificado = any(
            abs(real - candidato) <= max(
                TOLERANCIA_ABSOLUTA, abs(candidato) * TOLERANCIA_RELATIVA
            )
            for real in reales
            for candidato in candidatos
        )
        componentes.append({**componente, "verificado": verificado})
    return {**derivacion, "componentes": componentes}


def filtros_en_lenguaje_de_negocio(decisiones: dict) -> list[dict]:
    """Los filtros que se aplicaron, listos para mostrar. Los arma el código.

    A propósito no se los pedimos al modelo. Es la parte de "de dónde sale el
    número" que se puede afirmar con certeza —está en el WHERE que se ejecutó— y
    lo que se puede afirmar no se pregunta.
    """
    consultas = decisiones.get("consultas") or []
    vistos: dict[tuple, dict] = {}
    for cruda in consultas:
        for filtro in cruda.get("filtros", []):
            valores = ", ".join(str(v) for v in filtro.get("valores", []))
            clave = (filtro.get("columna"), filtro.get("operador"), valores)
            if clave not in vistos:
                vistos[clave] = {
                    "columna": filtro.get("columna", ""),
                    "operador": filtro.get("operador", ""),
                    "valores": valores,
                    "justificacion": filtro.get("justificacion", ""),
                    # SOLO `inyectado` cuenta como automático, porque es el único
                    # origen que pone el código. Hoy no hay reglas fijas, así que
                    # ningún filtro lo es. Los demás orígenes los rellena el modelo
                    # y se equivoca: puede marcar como regla un filtro que el
                    # usuario pidió. Decirle a alguien que el sistema le agregó un
                    # filtro que él pidió rompe la confianza que esta tarjeta busca.
                    "automatico": filtro.get("origen") == "inyectado",
                    "consultas": [],
                }
            vistos[clave]["consultas"].append(cruda.get("nombre", ""))

    # Un porcentaje son dos consultas que se diferencian en un filtro. Mezclados
    # en una sola lista se leen como contradicción — `grupo_enfermedad = X` en
    # una y sin filtrar en la otra — así que se dice a cuál pertenece cada uno.
    salida = []
    for datos in vistos.values():
        alcance = "" if len(datos["consultas"]) == len(consultas) else ", ".join(datos["consultas"])
        salida.append({**datos, "alcance": alcance})
    # Primero lo común, que describe el universo; después lo que distingue a cada
    # consulta, que es donde está la cuenta.
    return sorted(salida, key=lambda f: (bool(f["alcance"]), f["automatico"]))


def _forma_de(resultado: dict) -> str:
    """La tabla descrita SIN sus valores: columnas, tipos y cuántas filas.

    Es lo único que el nodo de lectura necesita para decidir qué comparar, y
    negarle los números es lo que lo obliga a escribir la operación en vez de
    resolverla de memoria.
    """
    columnas = resultado.get("columnas") or []
    filas = resultado.get("filas") or []
    descripcion = []
    for indice, nombre in enumerate(columnas):
        valores = [f[indice] for f in filas if indice < len(f) and f[indice] is not None]
        numerica = bool(valores) and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in valores
        )
        descripcion.append(f"- {nombre} ({'número' if numerica else 'texto'})")

    return (
        f"\n\nEsa misma tabla está en `consultas['resultado']` como DataFrame, con "
        f"{len(filas)} fila(s) y estas columnas:\n" + "\n".join(descripcion) + "\n\n"
        "LOS VALORES DE ARRIBA SON PARA QUE DECIDAS QUÉ MIRAR, NO PARA QUE CALCULES "
        "CON ELLOS. Todo número que aparezca en tu respuesta tiene que salir de una "
        "operación sobre el DataFrame. Si escribes una cifra a mano —aunque la hayas "
        "calculado bien— el sistema la detecta y la descarta, y esa observación se "
        "pierde. No es una recomendación: el código se revisa y los números escritos "
        "como constante no llegan al usuario."
    )


# Por debajo de esto, un entero en el código es casi seguro un parámetro y no un
# resultado copiado: `nlargest(3)`, `iloc[1]`, `round(x, 1)`, `* 100`. Desconfiar
# de ellos haría que una observación que da justo 3 —"tres prestadores explican la
# mitad"— se descarte siendo correcta.
ENTERO_QUE_ES_PARAMETRO = 100


def _numeros_escritos_a_mano(codigo: str) -> set[float]:
    """Los números que el código trae como constante, para desconfiar de ellos.

    Si un valor del resultado coincide con un literal del propio código, no lo
    calculó: lo copió. Medido contra la API, el modelo devolvió un
    `{"valor": 1234.5}` escrito a mano y el sandbox ejecutó código que solo
    repetía un número — aritmética mental disfrazada de ejecución.

    Se usa `ast` y no una búsqueda de texto, porque el texto encuentra también los
    números que están dentro de las cadenas de las observaciones.

    Los enteros chicos quedan fuera: son parámetros. Nadie escribe `1234.5` ni
    `987654.32` como parámetro, y ahí es donde vale desconfiar.
    """
    import ast

    try:
        arbol = ast.parse(codigo)
    except SyntaxError:
        return set()

    sospechosos = set()
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Constant):
            continue
        valor = nodo.value
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            continue
        es_entero_chico = isinstance(valor, int) and abs(valor) <= ENTERO_QUE_ES_PARAMETRO
        if es_entero_chico:
            continue
        sospechosos.add(float(valor))
    return sospechosos


def _sin_numeros_copiados(tabla: dict, codigo: str) -> tuple[dict, int]:
    """Saca las observaciones cuyo valor está escrito como constante en el código.

    Mostrarlas sería presentar la aritmética mental del modelo como si la hubiera
    hecho una máquina, que es peor que no mostrar nada: el usuario confía en el
    número justamente porque cree que salió de una ejecución.
    """
    literales = _numeros_escritos_a_mano(codigo)
    filas = tabla.get("filas") or []
    if not literales or not filas:
        return tabla, 0

    limpias = [
        fila
        for fila in filas
        if not any(
            isinstance(celda, (int, float))
            and not isinstance(celda, bool)
            and float(celda) in literales
            for celda in fila
        )
    ]
    descartadas = len(filas) - len(limpias)
    if descartadas:
        print(f"AVISO: {descartadas} observación(es) traían el número escrito a mano.")
    return {**tabla, "filas": limpias, "total_filas": len(limpias)}, descartadas


def _lectura_del_modelo(lectura: dict) -> str:
    """Las comparaciones que el modelo eligió, ya ejecutadas, listas para narrar.

    Va con la misma instrucción que el perfil fijo: los números están hechos y se
    citan tal cual. La diferencia es de dónde salieron — estos los eligió el
    modelo mirando la pregunta, no una lista fija.
    """
    tabla = (lectura or {}).get("tabla")
    if not tabla or not tabla.get("filas"):
        return ""
    lineas = [
        "\n\nCOMPARACIONES QUE PEDISTE SOBRE ESTA TABLA (ya ejecutadas). Los números "
        "están calculados: cítelos tal cual. Comente solo las que aporten a la "
        "pregunta; si alguna quedó sin sentido, ignórela."
    ]
    for fila in tabla["filas"]:
        lineas.append("- " + " | ".join("—" if v is None else str(v) for v in fila))
    return "\n".join(lineas)


def _sondeos_en_una_linea(sondeos: dict) -> str:
    """Los sondeos comprimidos para el redactor.

    El redactor no necesita las filas: necesita saber que el período (o el valor)
    que aparece en la respuesta salió de mirar la base, para poder decirlo. Sin
    esto, a "el último mes disponible" se contesta con un número suelto y la
    persona no sabe de qué mes le están hablando.
    """
    if not sondeos:
        return ""
    lineas = []
    for hecho in sondeos.values():
        resultado = hecho.get("resultado") or {}
        primera = resultado.get("filas") or []
        valores = (
            ", ".join(
                f"{c}={v}" for c, v in zip(resultado.get("columnas", []), primera[0])
            )
            if primera
            else (hecho.get("error") or "sin filas")
        )
        lineas.append(f"- {hecho.get('proposito', '')} → {valores}")
    return (
        "\n\nPara resolver la pregunta hizo falta averiguar esto primero:\n"
        + "\n".join(lineas)
        + "\nSi ese dato condiciona la respuesta —típicamente qué período se usó— "
        "dígalo como un HECHO y en las mismas palabras de la pregunta: «el último "
        "mes disponible es diciembre de 2009». Nunca cuente el procedimiento («se "
        "consultó la base», «según la revisión previa»), ni los nombres técnicos "
        "de arriba, ni cuántas filas se procesaron. A quien pregunta le importa el "
        "dato, no cómo se averiguó."
    )


def _premisa_para_el_redactor(contraste: dict) -> str:
    """La premisa de la pregunta contra el total real, lista para el redactor.

    Si se cumple, alcanza con una línea de contexto. Si NO se cumple, es lo
    primero que tiene que decir la respuesta: contestar "dónde se concentró el
    alza" con alzas por grupo, cuando el total bajó, es correcto en cada número y
    engañoso en el conjunto.
    """
    if not contraste:
        return ""
    previo, ultimo = contraste["previo"], contraste["ultimo"]
    variacion = contraste.get("variacion")
    cambio = f" ({variacion * 100:+.1f} %)" if variacion is not None else ""
    hecho = (
        f"el total ({contraste['medida']}) pasó de {previo['valor']:,.2f} en "
        f"{previo['periodo']} a {ultimo['valor']:,.2f} en {ultimo['periodo']}{cambio}"
    ).replace(",", "_").replace(".", ",").replace("_", ".")
    supone = "subió" if contraste["supone"] == "sube" else "bajó"
    if contraste["se_cumple"]:
        return (
            f"\n\nLA PREGUNTA SUPONE QUE {contraste['premisa'] or 'el total ' + supone}. "
            f"Se verificó y es cierto: {hecho}. Puede mencionarlo en una frase."
        )
    real = {"sube": "subió", "baja": "bajó", "igual": "no cambió"}[contraste["real"]]
    return (
        f"\n\nATENCIÓN — LA PREGUNTA PARTE DE UNA PREMISA FALSA. Supone que "
        f"{contraste['premisa'] or 'el total ' + supone}, pero en realidad {real}: {hecho}.\n"
        "Su respuesta TIENE QUE EMPEZAR diciéndolo, con esas cifras, en la primera "
        "frase. Recién después puede mostrar lo que pidió (por ejemplo, qué grupos "
        "subieron aunque el total bajara), dejando claro que eso no es un alza del "
        "total. No hable de 'el incremento total' ni de 'la caída total' si el total "
        "se movió en sentido contrario."
    )


def _resumen_de_sondeos(sondeos: dict) -> str:
    """Lo que devolvieron los sondeos, para que `comprender` los lea.

    Va con el texto completo del resultado porque un sondeo trae pocas filas por
    construcción (`reglas.LIMITE_SONDEO`): el gasto está en la vuelta de
    comprensión, no en estas líneas.
    """
    partes = [
        "Sondeaste la base para despejar estas dudas y esto es lo que devolvió. "
        "Es el dato real: úsalo tal cual, no lo redondees ni lo supongas de nuevo. "
        "No vuelvas a pedir un sondeo que ya está contestado acá."
    ]
    for nombre, hecho in sondeos.items():
        partes.append(f"\n--- `{nombre}`: {hecho.get('proposito', '')}")
        if hecho.get("error"):
            partes.append(f"El sondeo falló: {hecho['error']}")
        elif hecho.get("resultado"):
            partes.append(consulta.para_el_modelo(hecho["resultado"]))
    return "\n".join(partes)


def _resumen_del_plan(decisiones: dict) -> str:
    """El plan en texto plano, sin las justificaciones.

    Lo usa el revisor (que no debe ver el razonamiento, para no auto-aprobarlo)
    y el historial de la conversación.
    """
    lineas = [
        f"Período: {decisiones.get('periodo', '')}",
        f"Métrica: {decisiones.get('metrica_pedida', '')}"
        + ("" if decisiones.get("metrica_es_aditiva", True) else " (NO aditiva)"),
    ]
    for cruda in decisiones.get("consultas", []):
        medidas = ", ".join(
            f"{m['agregacion']}({m['columna']}) AS {m['alias']}" for m in cruda.get("medidas", [])
        )
        lineas.append(f"\nConsulta `{cruda['nombre']}` — {cruda.get('proposito', '')}")
        lineas.append(f"  medidas: {medidas or '(listado sin agregar)'}")
        lineas.append(f"  agrupado por: {', '.join(cruda.get('dimensiones', [])) or '(nada)'}")
        for filtro in cruda.get("filtros", []):
            lineas.append(f"  filtro: {filtro['columna']} {filtro['operador']} {filtro['valores']}")
    if decisiones.get("composicion"):
        lineas.append(f"\nComposición en Python:\n{decisiones['composicion']}")
    return "\n".join(lineas)


def _solo_codigo(texto: str) -> str:
    """Saca el ```python que el modelo suele poner alrededor del código."""
    limpio = texto.strip()
    if "```" not in limpio:
        return limpio
    partes = limpio.split("```")
    if len(partes) < 2:
        return limpio
    bloque = partes[1]
    if bloque.startswith("python"):
        bloque = bloque[len("python") :]
    return bloque.strip()
