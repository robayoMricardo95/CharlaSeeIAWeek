import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'

interface Props {
  onEnviar: (texto: string) => void
  esperandoRespuesta: boolean
}

function EntradaMensaje({ onEnviar, esperandoRespuesta }: Props) {
  const [texto, setTexto] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // El alto del textarea sigue al contenido (incluida la vuelta a una línea al enviar)
  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${textarea.scrollHeight}px`
  }, [texto])

  // El cursor tiene que quedarse acá siempre: al abrir la página, al mandar el
  // mensaje y al volver la respuesta. Antes el textarea se deshabilitaba
  // mientras se esperaba, y deshabilitar un elemento enfocado lo desenfoca:
  // había que volver a hacer clic para seguir escribiendo. Ahora nunca se
  // deshabilita — el envío se bloquea en manejarEnvio — y además se reenfoca
  // cuando llega la respuesta, por si algo más se robó el foco.
  useEffect(() => {
    if (!esperandoRespuesta) {
      textareaRef.current?.focus()
    }
  }, [esperandoRespuesta])

  function manejarEnvio() {
    const textoLimpio = texto.trim()
    if (!textoLimpio || esperandoRespuesta) return
    onEnviar(textoLimpio)
    setTexto('')
    textareaRef.current?.focus()
  }

  function manejarTecla(evento: KeyboardEvent<HTMLTextAreaElement>) {
    if (evento.key === 'Enter' && !evento.shiftKey) {
      evento.preventDefault()
      manejarEnvio()
    }
  }

  return (
    <textarea
      ref={textareaRef}
      autoFocus
      value={texto}
      onChange={(evento) => setTexto(evento.target.value)}
      onKeyDown={manejarTecla}
      aria-busy={esperandoRespuesta}
      placeholder={
        esperandoRespuesta
          ? 'Puede seguir escribiendo mientras responde...'
          : 'Escriba su pregunta sobre los datos...'
      }
      rows={1}
      className="block max-h-48 w-full resize-none overflow-y-auto rounded-xl border border-gray-300 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-acento"
    />
  )
}

export default EntradaMensaje
