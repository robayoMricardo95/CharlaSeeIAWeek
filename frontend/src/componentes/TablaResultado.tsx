import type { Resultado } from '../api/tipos'

interface Props {
  resultado: Resultado
}

// Los montos vienen como número crudo de la base. Con separador de miles se leen;
// sin él, "245512790" no dice nada de un vistazo.
function formatearCelda(valor: string | number | null): string {
  if (valor === null) return '—'
  if (typeof valor !== 'number') return valor
  if (Number.isInteger(valor)) return valor.toLocaleString('es-EC')
  return valor.toLocaleString('es-EC', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function TablaResultado({ resultado }: Props) {
  if (resultado.error) {
    return (
      <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
        La consulta no se pudo ejecutar contra la base.
      </div>
    )
  }

  if (resultado.total_filas === 0) {
    return (
      <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
        La consulta corrió bien pero no devolvió ninguna fila.
      </div>
    )
  }

  return (
    <div className="mt-3">
      {/* La tabla scrollea sola: con muchas columnas no debe empujar la página */}
      <div className="max-h-96 overflow-auto rounded-lg border border-gray-200">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-gray-50">
            <tr>
              {resultado.columnas.map((columna) => (
                <th
                  key={columna}
                  className="border-b border-gray-200 px-3 py-2 text-left font-medium text-gray-500"
                >
                  {columna}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {resultado.filas.map((fila, indice) => (
              // Las filas no tienen id propio y el orden lo fija la consulta,
              // así que el índice sirve como clave.
              <tr key={indice} className="odd:bg-white even:bg-gray-50/50">
                {fila.map((celda, columna) => (
                  <td
                    key={resultado.columnas[columna]}
                    className="whitespace-nowrap border-b border-gray-100 px-3 py-1.5 font-mono tabular-nums text-gray-700"
                  >
                    {formatearCelda(celda)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-1.5 text-[11px] text-gray-400">
        {resultado.total_filas.toLocaleString('es-EC')} fila(s)
        {resultado.truncado && ` · se muestran las primeras ${resultado.filas.length}`}
      </div>
    </div>
  )
}

export default TablaResultado
