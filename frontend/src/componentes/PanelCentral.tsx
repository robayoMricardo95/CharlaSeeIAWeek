import { useEffect, useRef } from 'react'
import type { Artefacto, Conversacion, ResumenUso } from '../api/tipos'
import BurbujaMensaje from './BurbujaMensaje'
import EntradaMensaje from './EntradaMensaje'
import MedidorCosto from './MedidorCosto'
import Pensando from './Pensando'

interface Props {
  conversaciones: Conversacion[]
  conversacionActivaId: string
  onEnviarMensaje: (texto: string) => void
  esperandoRespuesta: boolean
  onAbrirArtefacto: (artefacto: Artefacto) => void
  usoSesion?: ResumenUso
}

// El hilo y el input comparten el mismo ancho de columna centrada, así todo
// queda alineado y no se desperdicia la pantalla en monitores anchos.
const COLUMNA = 'mx-auto w-full max-w-3xl'

function PanelCentral({
  conversaciones,
  conversacionActivaId,
  onEnviarMensaje,
  esperandoRespuesta,
  onAbrirArtefacto,
  usoSesion,
}: Props) {
  const conversacionActiva = conversaciones.find(
    (conversacion) => conversacion.id === conversacionActivaId,
  )
  const contenedorRef = useRef<HTMLDivElement>(null)

  // Baja el scroll al último mensaje al cambiar de conversación, al llegar un
  // mensaje nuevo y al aparecer el "Pensando..."
  useEffect(() => {
    const contenedor = contenedorRef.current
    if (!contenedor) return
    contenedor.scrollTop = contenedor.scrollHeight
  }, [conversacionActivaId, conversacionActiva?.mensajes.length, esperandoRespuesta])

  return (
    <main className="flex min-w-0 flex-1 flex-col">
      <header className="border-b border-gray-200 px-6 py-3">
        <div className={`${COLUMNA} flex items-center justify-between gap-4`}>
          <h1 className="truncate text-sm font-medium text-gray-700">
            {conversacionActiva?.titulo ?? 'Sin conversación'}
          </h1>
          <MedidorCosto conversacion={conversacionActiva?.uso} sesion={usoSesion} />
        </div>
      </header>

      <div ref={contenedorRef} className="flex-1 overflow-y-auto p-6">
        <div className={`${COLUMNA} flex flex-col gap-6`}>
          {conversacionActiva?.mensajes.map((mensaje) => (
            <BurbujaMensaje
              key={mensaje.id}
              mensaje={mensaje}
              onAbrirArtefacto={onAbrirArtefacto}
              onPreguntar={onEnviarMensaje}
            />
          ))}
          {esperandoRespuesta && <Pensando />}
        </div>
      </div>

      <div className="border-t border-gray-200 p-4">
        <div className={COLUMNA}>
          <EntradaMensaje onEnviar={onEnviarMensaje} esperandoRespuesta={esperandoRespuesta} />
          {/* Siempre a la vista: es la condición de uso de los datos, no un detalle. */}
          <p className="mt-2 text-center text-xs text-gray-400">
            Datos públicos y sintéticos de Medicare (CMS DE-SynPUF, 2008–2009). Sirven para
            analizar y construir herramientas, no para sacar conclusiones sobre personas
            reales.
          </p>
        </div>
      </div>
    </main>
  )
}

export default PanelCentral
