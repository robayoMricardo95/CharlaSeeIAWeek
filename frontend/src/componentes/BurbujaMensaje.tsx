import type { Artefacto, Mensaje } from '../api/tipos'
import { Sugerencias } from './ComoSeCalculo'
import PanelRazonamiento from './PanelRazonamiento'
import TablaResultado from './TablaResultado'

interface Props {
  mensaje: Mensaje
  onAbrirArtefacto: (artefacto: Artefacto) => void
  // Al tocar una sugerencia se manda como pregunta nueva. Es opcional para que
  // una conversación vieja del historial se pueda pintar sin poder responder.
  onPreguntar?: (texto: string) => void
}

// Estilo ChatGPT: lo que escribe el usuario es una burbuja compacta pegada a
// la derecha; la respuesta del agente no tiene burbuja y usa todo el ancho de
// la columna, que es donde puede haber tablas, gráficos o reportes largos.
function BurbujaMensaje({ mensaje, onAbrirArtefacto, onPreguntar }: Props) {
  const esUsuario = mensaje.autor === 'usuario'
  const { grafico, artefacto, analisis, resultado, mostrarTabla } = mensaje

  if (esUsuario) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl bg-gray-100 px-4 py-2.5 text-sm leading-relaxed text-gray-800">
          {mensaje.texto}
        </div>
      </div>
    )
  }

  return (
    <div className="w-full">
      {analisis && <PanelRazonamiento analisis={analisis} resultado={resultado} />}
      <RespuestaAgente mensaje={mensaje} />
      {/* Nivel 3: propone, no afirma. Va abajo de todo y sin números. */}
      <Sugerencias
        sugerencias={analisis?.derivacion?.sugerencias ?? []}
        onPreguntar={onPreguntar}
      />
      {/* Con pocas filas el número ya está adentro del texto: la tabla sobra */}
      {resultado && mostrarTabla !== false && <TablaResultado resultado={resultado} />}
      {grafico && (
        // HTML propio (no de terceros); sandbox="allow-scripts" sin
        // allow-same-origin igual lo aísla del resto de la página.
        <iframe
          sandbox="allow-scripts"
          srcDoc={grafico}
          title="Gráfico"
          className="mt-3 h-64 w-full rounded-md border border-gray-200 bg-white"
        />
      )}
      {artefacto && (
        <button
          onClick={() => onAbrirArtefacto(artefacto)}
          className="mt-3 block text-sm font-medium text-acento hover:underline"
        >
          Ver artefacto
        </button>
      )}
    </div>
  )
}

// El backend redacta en HTML y lo sanea antes de mandarlo (`backend/formato.py`):
// deja pasar una lista corta de etiquetas y borra TODOS los atributos, así que no
// puede traer `onerror`, `href="javascript:"` ni estilos. Los mensajes viejos y
// los del cliente simulado son texto plano y se muestran tal cual.
function RespuestaAgente({ mensaje }: { mensaje: Mensaje }) {
  const esHtml = mensaje.texto.trimStart().startsWith('<')

  if (!esHtml) {
    return (
      <div className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800">
        {mensaje.texto}
      </div>
    )
  }

  return (
    <div
      className="respuesta text-sm leading-relaxed text-gray-800"
      dangerouslySetInnerHTML={{ __html: mensaje.texto }}
    />
  )
}

export default BurbujaMensaje
