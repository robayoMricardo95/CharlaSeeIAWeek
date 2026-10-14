import { useState } from 'react'
import type { Conversacion } from '../api/tipos'

interface Props {
  conversaciones: Conversacion[]
  conversacionActivaId: string
  onSeleccionar: (id: string) => void
  onNuevaConversacion: () => string
  onRenombrar: (id: string, titulo: string) => void
  onBorrar: (id: string) => void
}

function BarraLateral({
  conversaciones,
  conversacionActivaId,
  onSeleccionar,
  onNuevaConversacion,
  onRenombrar,
  onBorrar,
}: Props) {
  const [colapsada, setColapsada] = useState(false)
  const [editandoId, setEditandoId] = useState<string | null>(null)

  function manejarNuevoChat() {
    const id = onNuevaConversacion()
    setEditandoId(id)
  }

  function confirmarNombre(id: string, valor: string) {
    const nombre = valor.trim()
    if (nombre) {
      onRenombrar(id, nombre)
    }
    setEditandoId(null)
  }

  // Se pide confirmación porque no hay vuelta atrás: se borra el historial
  // guardado y la memoria del agente para esa conversación.
  function confirmarBorrado(conversacion: Conversacion) {
    const seguro = window.confirm(
      `¿Borrar la conversación «${conversacion.titulo}»?\n\nSe elimina su historial y no se puede deshacer.`,
    )
    if (seguro) onBorrar(conversacion.id)
  }

  if (colapsada) {
    return (
      <aside className="flex w-12 shrink-0 justify-center border-r border-gray-200 bg-gray-50 p-3">
        <button
          onClick={() => setColapsada(false)}
          className="text-gray-400 hover:text-gray-600"
          aria-label="Expandir barra lateral"
        >
          »
        </button>
      </aside>
    )
  }

  return (
    <aside className="flex w-64 shrink-0 flex-col gap-3 border-r border-gray-200 bg-gray-50 p-3">
      <div className="flex items-center justify-between">
        <button
          onClick={manejarNuevoChat}
          className="rounded-md bg-acento px-3 py-2 text-sm text-white hover:opacity-90"
        >
          + Nuevo chat
        </button>
        <button
          onClick={() => setColapsada(true)}
          className="px-2 text-gray-400 hover:text-gray-600"
          aria-label="Colapsar barra lateral"
        >
          «
        </button>
      </div>

      <nav className="flex flex-col gap-1 overflow-y-auto">
        {conversaciones.map((conversacion) =>
          editandoId === conversacion.id ? (
            <input
              key={conversacion.id}
              autoFocus
              defaultValue={
                conversacion.titulo === 'Nueva conversación' ? '' : conversacion.titulo
              }
              placeholder="Nombre de la conversación"
              onBlur={(evento) => confirmarNombre(conversacion.id, evento.target.value)}
              onKeyDown={(evento) => {
                if (evento.key === 'Enter') evento.currentTarget.blur()
              }}
              className="rounded-md border border-acento bg-white px-3 py-2 text-sm text-gray-700 outline-none"
            />
          ) : (
            <div
              key={conversacion.id}
              className={`group flex items-center rounded-md ${
                conversacion.id === conversacionActivaId
                  ? 'bg-acento/10 text-acento'
                  : 'text-gray-700 hover:bg-gray-100'
              }`}
            >
              <button
                onClick={() => onSeleccionar(conversacion.id)}
                className="min-w-0 flex-1 truncate px-3 py-2 text-left text-sm"
              >
                {conversacion.titulo}
              </button>
              <button
                onClick={() => confirmarBorrado(conversacion)}
                className={`shrink-0 px-2 py-2 text-gray-400 hover:text-red-600 focus:opacity-100 ${
                  conversacion.id === conversacionActivaId
                    ? 'opacity-100'
                    : 'opacity-0 group-hover:opacity-100'
                }`}
                aria-label={`Borrar la conversación ${conversacion.titulo}`}
                title="Borrar conversación"
              >
                <svg
                  viewBox="0 0 24 24"
                  className="h-4 w-4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M3 6h18" />
                  <path d="M8 6V4h8v2" />
                  <path d="M19 6l-1 14H6L5 6" />
                  <path d="M10 11v6M14 11v6" />
                </svg>
              </button>
            </div>
          ),
        )}
      </nav>
    </aside>
  )
}

export default BarraLateral
