import type { Artefacto } from '../api/tipos'

interface Props {
  artefacto: Artefacto
  onCerrar: () => void
}

function PanelArtefactos({ artefacto, onCerrar }: Props) {
  return (
    <aside className="flex w-96 shrink-0 flex-col border-l border-gray-200 bg-gray-50">
      <div className="flex items-center justify-between border-b border-gray-200 p-3">
        <span className="text-sm font-medium text-gray-700">Artefacto</span>
        <button
          onClick={onCerrar}
          className="text-gray-400 hover:text-gray-600"
          aria-label="Cerrar panel de artefactos"
        >
          ✕
        </button>
      </div>
      <div className="flex-1 overflow-auto p-3">
        {artefacto.tipo === 'html' ? (
          // HTML propio (no de terceros); sandbox="allow-scripts" sin
          // allow-same-origin igual lo aísla del resto de la página.
          <iframe
            sandbox="allow-scripts"
            srcDoc={artefacto.contenido}
            title="Artefacto generado por el agente"
            className="h-full w-full rounded-md border border-gray-200 bg-white"
          />
        ) : (
          <img
            src={artefacto.contenido}
            alt="Artefacto generado por el agente"
            className="w-full rounded-md"
          />
        )}
      </div>
    </aside>
  )
}

export default PanelArtefactos
