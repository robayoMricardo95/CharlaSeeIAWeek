export interface Artefacto {
  tipo: 'html' | 'imagen'
  contenido: string // HTML crudo (para srcDoc) o URL de imagen
}

// Un filtro que terminó en el WHERE. `origen` dice quién lo puso: el usuario,
// una regla del diccionario, o el código (`inyectado`).
export interface Filtro {
  columna: string
  operador: string
  valores: string[]
  origen: 'pregunta' | 'default' | 'obligatoria' | 'inyectado'
  justificacion: string
}

export interface Medida {
  agregacion: string
  columna: string
  alias: string
}

// Una extracción simple contra la base (SQLite). El agente puede pedir varias y después
// cruzarlas en Python.
export interface ConsultaBase {
  nombre: string
  proposito: string
  medidas: Medida[]
  dimensiones: string[]
  filtros: Filtro[]
  inyectados: string[] // lo que agregó el código porque una regla lo obliga
  sql: string
  filas: number
  error: string | null
  resultado: Resultado | null // lo que devolvió esta consulta, para verlo junto al SQL
}

// Una consulta de reconocimiento que se corrió ANTES de armar el plan, para
// despejar una duda sobre los datos ("¿cuál es el último mes cargado?").
export interface Sondeo {
  nombre: string
  proposito: string // qué duda resuelve
  sql: string
  inyectados: string[]
  error: string | null
  resultado: Resultado | null
}

// Un número que entró a la fórmula. `verificado` lo pone el backend después de
// comprobar que ese valor existe de verdad en alguna extracción: le pedimos al
// modelo que explique de dónde sale una cifra, y la forma natural de fallar es
// citar una redondeada o inventada.
export interface Componente {
  nombre: string
  valor: number
  unidad: 'dolares' | 'porcentaje' | 'cantidad' | 'dias'
  de_donde: string
  verificado: boolean
}

// De dónde sale el número, contado para alguien de negocio. Va junto a la
// respuesta, no escondido en el panel: una cifra sola no se puede aceptar.
export interface Derivacion {
  formula: string
  componentes: Componente[]
  supuestos: string[]
  // Qué mirar después. Nunca llevan números: una sugerencia propone, no afirma.
  sugerencias: string[]
}

// Un filtro que terminó en el WHERE, listo para mostrar. Lo arma el backend
// desde el SQL ejecutado, no lo redacta el modelo.
export interface FiltroAplicado {
  columna: string
  operador: string
  valores: string
  justificacion: string
  automatico: boolean // lo inyectó el código, no salió de la pregunta
  // Vacío si el filtro está en todas las consultas. Si no, a cuál pertenece:
  // en un porcentaje el numerador y el denominador se diferencian justo en uno,
  // y verlos mezclados se lee como contradicción.
  alcance: string
}

// Algo digno de mención que apareció en un paso del razonamiento.
export interface Novedad {
  paso: string
  nivel: 'info' | 'aviso' | 'error'
  regla: string
  detalle: string
}

export interface Hallazgo {
  gravedad: 'error' | 'aviso'
  paso: string
  que_falta: string
  correccion: string
}

export interface Revision {
  aprobado?: boolean
  hallazgos?: Hallazgo[]
}

// El razonamiento completo detrás de una respuesta. Sirve para auditar que el
// agente eligió bien las columnas, el período, la métrica y las reglas.
export interface Analisis {
  interpretacion: string
  periodo: string
  rama: string
  metrica: string
  metrica_es_aditiva: boolean
  // Parte y todo. Solo tienen contenido cuando la pregunta pide un porcentaje,
  // que es justo donde el agente se equivocaba de denominador.
  pide_participacion: boolean
  dimension_de_la_parte: string
  universo_denominador: string
  intencion?: 'datos' | 'conversacion'
  // Cómo se calculó, en lenguaje de negocio. Se muestra junto a la respuesta.
  derivacion?: Derivacion
  filtros_aplicados?: FiltroAplicado[]
  sondeos: Sondeo[] // lo que se le preguntó a la base antes de decidir
  consultas: ConsultaBase[]
  composicion: string // el código Python que cruzó las extracciones
  impreso: string
  reparaciones: number
  novedades: Novedad[]
  revision: Revision // revisión de las decisiones, antes de ejecutar
  revision_resultado: Revision // revisión del resultado, después de ejecutar
  reintentos: number
  error: string
  respuesta: string
}

// Lo que devolvió la base (SQLite) al ejecutar el SQL que armó el sistema.
export interface Resultado {
  sql: string
  columnas: string[]
  filas: (string | number | null)[][]
  total_filas: number
  truncado: boolean
  error: string | null
}

export interface Mensaje {
  id: string
  autor: 'usuario' | 'agente'
  texto: string
  grafico?: string // HTML de un gráfico simple, se muestra dentro del propio mensaje
  artefacto?: Artefacto // HTML compuesto (dashboard/reporte), se muestra en el panel
  analisis?: Analisis // el razonamiento, se despliega bajo la respuesta
  resultado?: Resultado | null // las filas que devolvió la base
  mostrarTabla?: boolean // false cuando el número ya va dentro del texto
}

// Consumo acumulado de tokens y dólares. Lo calcula el backend
// (backend/uso_tokens.py); el frontend solo lo muestra.
export interface ResumenUso {
  consultas: number
  respuestas: number
  tokens_entrada: number
  tokens_salida: number
  tokens_cache_lectura: number
  tokens_cache_escritura: number
  costo_entrada: number
  costo_salida: number
  costo_total: number
  precio_conocido: boolean // false si algún modelo no está en la tabla de precios
  modelos: string[]
}

export interface Conversacion {
  id: string // el thread_id que usa el backend
  titulo: string
  mensajes: Mensaje[]
  uso?: ResumenUso
  turnos?: number // cuántas preguntas tiene, según el historial
  ultimo?: string // cuándo fue la última, para ordenar y mostrar
  // Las conversaciones se listan sin sus mensajes y se traen al abrirlas: una
  // lista de cincuenta con todo el razonamiento adentro sería enorme y casi
  // todo se descartaría sin mirarse.
  cargada?: boolean
}

export interface Respuesta {
  texto: string
  grafico?: string
  artefacto?: Artefacto
  analisis?: Analisis
  resultado?: Resultado | null
  mostrar_tabla?: boolean
  turno_id?: number | null // fila en agente_turnos, para auditar después
  acumulado?: ResumenUso // gasto de esta conversación hasta ahora
  total_sesion?: ResumenUso // gasto de todas las conversaciones
}
