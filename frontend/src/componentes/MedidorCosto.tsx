import { useEffect, useRef, useState } from 'react'
import type { ResumenUso } from '../api/tipos'

interface Props {
  conversacion?: ResumenUso
  sesion?: ResumenUso
}

const RESUMEN_VACIO: ResumenUso = {
  consultas: 0,
  respuestas: 0,
  tokens_entrada: 0,
  tokens_salida: 0,
  tokens_cache_lectura: 0,
  tokens_cache_escritura: 0,
  costo_entrada: 0,
  costo_salida: 0,
  costo_total: 0,
  precio_conocido: true,
  modelos: [],
}

// Los costos por consulta son centavos de centavo: con 2 decimales se verían
// todos como "$0.00". Cuanto más chico el número, más decimales mostramos.
function formatearDinero(valor: number): string {
  if (valor === 0) return '$0.00'
  if (valor >= 1) return `$${valor.toFixed(2)}`
  if (valor >= 0.01) return `$${valor.toFixed(4)}`
  if (valor < 0.000001) return '<$0.000001'
  return `$${valor.toFixed(6)}`
}

function formatearTokens(valor: number): string {
  return valor.toLocaleString('es-EC')
}

function MedidorCosto({ conversacion, sesion }: Props) {
  const [abierto, setAbierto] = useState(false)
  const [verSesion, setVerSesion] = useState(false)
  const contenedorRef = useRef<HTMLDivElement>(null)

  // Cerrar el detalle al hacer clic afuera o con Escape, como cualquier menú.
  useEffect(() => {
    if (!abierto) return

    function alClicAfuera(evento: MouseEvent) {
      if (!contenedorRef.current?.contains(evento.target as Node)) {
        setAbierto(false)
      }
    }
    function alPresionarEscape(evento: KeyboardEvent) {
      if (evento.key === 'Escape') setAbierto(false)
    }

    document.addEventListener('mousedown', alClicAfuera)
    document.addEventListener('keydown', alPresionarEscape)
    return () => {
      document.removeEventListener('mousedown', alClicAfuera)
      document.removeEventListener('keydown', alPresionarEscape)
    }
  }, [abierto])

  const uso = (verSesion ? sesion : conversacion) ?? RESUMEN_VACIO
  const enPastilla = conversacion ?? RESUMEN_VACIO

  // Proporción del costo que se fue en entrada, para la barra del detalle.
  const porcentajeEntrada =
    uso.costo_total > 0 ? (uso.costo_entrada / uso.costo_total) * 100 : 50

  return (
    <div ref={contenedorRef} className="relative">
      <button
        onClick={() => setAbierto((valor) => !valor)}
        title="Ver detalle de tokens y costo"
        className="flex items-center gap-2.5 rounded-full border border-gray-200 bg-white py-1.5 pl-3 pr-2.5 shadow-sm transition hover:border-acento/40 hover:shadow"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-acento" />
        <span className="text-[10px] font-medium uppercase tracking-wider text-gray-400">
          Costo
        </span>
        <span className="font-mono text-sm font-semibold tabular-nums text-acento">
          {formatearDinero(enPastilla.costo_total)}
        </span>
        <span className="hidden text-[11px] text-gray-400 sm:inline">
          {formatearTokens(enPastilla.tokens_entrada + enPastilla.tokens_salida)} tk
        </span>
        <span className={`text-[10px] text-gray-400 transition ${abierto ? 'rotate-180' : ''}`}>
          ▾
        </span>
      </button>

      {abierto && (
        <div className="absolute right-0 z-20 mt-2 w-80 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl">
          <div className="flex gap-1 border-b border-gray-100 p-1">
            <button
              onClick={() => setVerSesion(false)}
              className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                verSesion ? 'text-gray-500 hover:bg-gray-50' : 'bg-acento/10 text-acento'
              }`}
            >
              Esta conversación
            </button>
            <button
              onClick={() => setVerSesion(true)}
              className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                verSesion ? 'bg-acento/10 text-acento' : 'text-gray-500 hover:bg-gray-50'
              }`}
            >
              Total de la sesión
            </button>
          </div>

          {/* El costo total es el dato que manda: va grande y solo. */}
          <div className="px-4 pb-4 pt-5 text-center">
            <div className="text-[10px] font-medium uppercase tracking-wider text-gray-400">
              Costo acumulado
            </div>
            <div className="mt-1 font-mono text-3xl font-semibold tabular-nums text-acento">
              {formatearDinero(uso.costo_total)}
            </div>
          </div>

          <div className="px-4">
            <div className="flex h-1.5 overflow-hidden rounded-full bg-gray-100">
              <div className="bg-acento" style={{ width: `${porcentajeEntrada}%` }} />
              <div className="flex-1 bg-acento/35" />
            </div>
            <div className="mt-2 grid grid-cols-2 gap-3">
              <Bloque
                etiqueta="Entrada"
                dinero={formatearDinero(uso.costo_entrada)}
                detalle={`${formatearTokens(uso.tokens_entrada)} tokens`}
                punto="bg-acento"
              />
              <Bloque
                etiqueta="Salida"
                dinero={formatearDinero(uso.costo_salida)}
                detalle={`${formatearTokens(uso.tokens_salida)} tokens`}
                punto="bg-acento/35"
              />
            </div>
          </div>

          <div className="mt-4 grid grid-cols-3 divide-x divide-gray-100 border-t border-gray-100 bg-gray-50/60 text-center">
            <Contador etiqueta="Consultas" valor={formatearTokens(uso.consultas)} />
            <Contador etiqueta="Respuestas" valor={formatearTokens(uso.respuestas)} />
            <Contador
              etiqueta="Tokens"
              valor={formatearTokens(uso.tokens_entrada + uso.tokens_salida)}
            />
          </div>

          {uso.tokens_cache_lectura > 0 && (
            <div className="border-t border-gray-100 px-4 py-2 text-[11px] text-gray-500">
              {formatearTokens(uso.tokens_cache_lectura)} tokens leídos de caché (más baratos)
            </div>
          )}

          <div className="border-t border-gray-100 px-4 py-2.5 text-[11px] text-gray-400">
            {uso.modelos.length > 0 ? uso.modelos.join(' · ') : 'Todavía sin consultas'}
            {!uso.precio_conocido && (
              <div className="mt-1 text-amber-600">
                Falta el precio de algún modelo en la tabla: el costo está subestimado.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function Bloque({
  etiqueta,
  dinero,
  detalle,
  punto,
}: {
  etiqueta: string
  dinero: string
  detalle: string
  punto: string
}) {
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${punto}`} />
        <span className="text-[10px] font-medium uppercase tracking-wider text-gray-400">
          {etiqueta}
        </span>
      </div>
      <div className="font-mono text-sm font-semibold tabular-nums text-gray-800">{dinero}</div>
      <div className="text-[11px] text-gray-400">{detalle}</div>
    </div>
  )
}

function Contador({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return (
    <div className="py-2.5">
      <div className="font-mono text-sm font-semibold tabular-nums text-gray-800">{valor}</div>
      <div className="text-[10px] uppercase tracking-wider text-gray-400">{etiqueta}</div>
    </div>
  )
}

export default MedidorCosto
