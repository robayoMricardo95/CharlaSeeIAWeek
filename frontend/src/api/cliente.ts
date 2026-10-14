// La única puerta al backend. El resto de la app no sabe que hay HTTP detrás.
//
// Antes este archivo tenía conversaciones inventadas en el código y todo lo
// real se perdía al recargar la página. Ahora la lista y los
// mensajes salen del historial en SQLite, que es donde `backend/registro.py` guarda cada turno
// con su razonamiento completo.

import type { Conversacion, Mensaje, Respuesta, ResumenUso } from './tipos'

const URL_API = 'http://localhost:8000'

interface ConversacionCruda {
  thread_id: string
  titulo: string | null
  turnos: number
  ultimo: string | null
}

interface TurnoCrudo {
  id: number
  pregunta: string
  respuesta: string | null
  analisis: Conversacion['mensajes'][number]['analisis'] | null
  resultado: Conversacion['mensajes'][number]['resultado']
  mostrar_tabla: boolean
}

/** Las conversaciones guardadas, sin sus mensajes: esos se traen al abrirlas. */
export async function listarConversaciones(): Promise<Conversacion[]> {
  const respuesta = await fetch(`${URL_API}/api/conversaciones`)
  if (!respuesta.ok) throw new Error(`El backend respondió ${respuesta.status}`)
  const datos = await respuesta.json()
  return (datos.conversaciones as ConversacionCruda[]).map((fila) => ({
    id: fila.thread_id,
    titulo: fila.titulo?.trim() || 'Conversación sin título',
    mensajes: [],
    turnos: fila.turnos,
    ultimo: fila.ultimo ?? undefined,
    cargada: false,
  }))
}

/** Los mensajes de una conversación, reconstruidos desde el historial. */
export async function abrirConversacion(conversacionId: string): Promise<Mensaje[]> {
  const respuesta = await fetch(
    `${URL_API}/api/conversaciones/${encodeURIComponent(conversacionId)}`,
  )
  if (!respuesta.ok) throw new Error(`El backend respondió ${respuesta.status}`)
  const datos = await respuesta.json()

  // Cada turno guardado son dos mensajes en pantalla: lo que preguntó la persona
  // y lo que contestó el agente, con su razonamiento entero.
  return (datos.turnos as TurnoCrudo[]).flatMap((turno) => [
    { id: `t${turno.id}-p`, autor: 'usuario' as const, texto: turno.pregunta },
    {
      id: `t${turno.id}-r`,
      autor: 'agente' as const,
      texto: turno.respuesta ?? '',
      analisis: turno.analisis ?? undefined,
      resultado: turno.resultado,
      mostrarTabla: turno.mostrar_tabla,
    },
  ])
}

export async function enviarMensaje(
  texto: string,
  conversacionId: string,
): Promise<Respuesta> {
  const respuesta = await fetch(`${URL_API}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ mensaje: texto, thread_id: conversacionId }),
  })
  const datos = await respuesta.json()
  return {
    texto: datos.texto,
    analisis: datos.analisis,
    resultado: datos.resultado,
    mostrar_tabla: datos.mostrar_tabla,
    turno_id: datos.turno_id,
    acumulado: datos.acumulado,
    total_sesion: datos.total_sesion,
  }
}

/** Borra una conversación del historial y de la memoria del agente.
 *
 * Devuelve el gasto total actualizado: lo que costó esa conversación deja de
 * contar, y el medidor tiene que reflejarlo sin esperar a la próxima pregunta.
 */
export async function borrarConversacion(
  conversacionId: string,
): Promise<{ total_sesion?: ResumenUso }> {
  const respuesta = await fetch(
    `${URL_API}/api/conversaciones/${encodeURIComponent(conversacionId)}`,
    { method: 'DELETE' },
  )
  if (!respuesta.ok) throw new Error(`El backend respondió ${respuesta.status}`)
  return respuesta.json()
}

/** El gasto acumulado, leído del historial en SQLite.
 *
 * Existe porque el medidor se ponía en cero solo: el contador vivía en la RAM
 * del backend, así que un reinicio lo borraba, y una conversación abierta desde
 * el historial mostraba $0,00 aunque hubiera costado un dólar. Ahora se pide al
 * abrir la página y al cambiar de conversación.
 */
export async function consultarUso(
  conversacionId?: string,
): Promise<{ acumulado?: ResumenUso; total_sesion?: ResumenUso }> {
  const ruta = conversacionId
    ? `/api/uso/${encodeURIComponent(conversacionId)}`
    : '/api/uso'
  const respuesta = await fetch(`${URL_API}${ruta}`)
  if (!respuesta.ok) throw new Error(`El backend respondió ${respuesta.status}`)
  return respuesta.json()
}
