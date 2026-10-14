import type { Analisis, Componente, FiltroAplicado } from '../api/tipos'

interface Props {
  analisis: Analisis
}

// Vive DENTRO del panel de razonamiento, no debajo de la respuesta.
//
// Estuvo siempre visible bajo la respuesta y se sacó por una razón concreta:
// en una pregunta simple —un solo número, una sola consulta— la tarjeta no tiene
// fórmula ni componentes, y lo único que muestra son los filtros, que no hacen
// más que repetir la pregunta ("Año es 2009, Mes es 2"). Eso no genera confianza,
// genera ruido.
//
// El detalle sigue estando entero, a un clic. Lo que cambió es que ya no se
// impone en preguntas donde no aporta nada.
export function ComoSeCalculo({ analisis }: Props) {
  const derivacion = analisis.derivacion
  const filtros = analisis.filtros_aplicados ?? []
  const componentes = derivacion?.componentes ?? []
  const supuestos = derivacion?.supuestos ?? []

  if (!derivacion && filtros.length === 0) return null
  if (componentes.length === 0 && filtros.length === 0 && supuestos.length === 0) return null

  // El backend ya los ordena: primero los comunes a todas las consultas, que
  // describen el universo; después los que distinguen a cada una.
  const comunes = filtros.filter((f) => !f.alcance)
  const especificos = filtros.filter((f) => f.alcance)
  const automaticos = filtros.filter((f) => f.automatico)

  return (
    // Sin marco propio: vive dentro del panel, que ya tiene el suyo. Una tarjeta
    // adentro de otra tarjeta se lee como dos cosas distintas.
    <section className="text-xs">
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-gray-400">
        Cómo se calculó
      </div>

      {derivacion?.formula && (
        <p className="mb-3 rounded-lg bg-white px-3 py-2 text-sm text-gray-800">
          {derivacion.formula}
        </p>
      )}

      {componentes.length > 0 && (
        <ul className="mb-3 space-y-2">
          {componentes.map((componente) => (
            <FilaComponente key={componente.nombre} componente={componente} />
          ))}
        </ul>
      )}

      {filtros.length > 0 && (
        <div className="mb-3">
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-gray-400">
            Sobre qué datos
          </div>
          <ul className="space-y-1">
            {comunes.map((filtro) => (
              <FilaFiltro key={clave(filtro)} filtro={filtro} />
            ))}
          </ul>
          {especificos.length > 0 && (
            <ul className="mt-2 space-y-1 border-t border-gray-200 pt-2">
              {especificos.map((filtro) => (
                <FilaFiltro key={clave(filtro)} filtro={filtro} />
              ))}
            </ul>
          )}
          {automaticos.length > 0 && (
            <p className="mt-1.5 text-[11px] text-gray-400">
              Los marcados con ● los agrega el sistema porque el diccionario de negocio los
              exige, aunque no se pidan.
            </p>
          )}
        </div>
      )}

      {supuestos.length > 0 && (
        <div>
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-gray-400">
            Supuestos
          </div>
          <ul className="space-y-1 text-gray-600">
            {supuestos.map((supuesto) => (
              <li key={supuesto}>· {supuesto}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

// Las sugerencias van FUERA de la tarjeta del cálculo y con otro tono, porque son
// otra cosa: la tarjeta afirma con números verificados, esto solo propone. Nunca
// llevan cifras — esa regla la impone el backend, no el estilo de este archivo.
export function Sugerencias({
  sugerencias,
  onPreguntar,
}: {
  sugerencias: string[]
  onPreguntar?: (texto: string) => void
}) {
  if (!sugerencias?.length) return null

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <span className="text-[11px] text-gray-400">Para profundizar:</span>
      {sugerencias.map((sugerencia) =>
        onPreguntar ? (
          <button
            key={sugerencia}
            onClick={() => onPreguntar(sugerencia)}
            className="rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-600 transition hover:border-acento hover:text-acento"
          >
            {sugerencia}
          </button>
        ) : (
          <span
            key={sugerencia}
            className="rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-500"
          >
            {sugerencia}
          </span>
        ),
      )}
    </div>
  )
}

function FilaComponente({ componente }: { componente: Componente }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 border-l-2 border-gray-200 pl-3">
      <span className="text-gray-700">{componente.nombre}</span>
      <span className="font-mono font-medium text-gray-900">
        {formatear(componente.valor, componente.unidad)}
      </span>
      {/* El backend comprueba que el valor exista en alguna extracción real. Si
          no pudo confirmarlo se avisa en vez de borrarlo: un número sin
          confirmar y dicho es honesto; uno que desaparece esconde el problema. */}
      {!componente.verificado && (
        <span
          className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700"
          title="Este valor no coincide con ninguno de los que devolvió la base"
        >
          sin verificar
        </span>
      )}
      {componente.de_donde && (
        <span className="block w-full text-gray-500">{componente.de_donde}</span>
      )}
    </li>
  )
}

function FilaFiltro({ filtro }: { filtro: FiltroAplicado }) {
  return (
    <li className="flex gap-2">
      <span
        className={filtro.automatico ? 'text-acento' : 'text-gray-300'}
        title={filtro.automatico ? 'Lo aplica el sistema' : 'Salió de su pregunta'}
      >
        {filtro.automatico ? '●' : '○'}
      </span>
      <span className="min-w-0">
        <span className="text-gray-700">
          {legible(filtro.columna)} {simbolo(filtro.operador)} <strong>{filtro.valores}</strong>
        </span>
        {filtro.alcance && (
          <span className="ml-1.5 text-gray-400">— solo para {filtro.alcance}</span>
        )}
        {filtro.automatico && filtro.justificacion && (
          <span className="block text-gray-400">{filtro.justificacion}</span>
        )}
      </span>
    </li>
  )
}

function clave(filtro: FiltroAplicado): string {
  return `${filtro.columna}-${filtro.operador}-${filtro.valores}`
}

// Los nombres de columna son de la base (tabla `sabana`); quien lee esto no
// tiene por qué conocerlos.
const NOMBRES: Record<string, string> = {
  anio: 'Año',
  mes: 'Mes',
  ambito: 'Ámbito',
  monto_pagado: 'Monto pagado',
  monto_total: 'Costo completo',
  deducible: 'Deducible',
  coaseguro: 'Coaseguro',
  monto_pagado_tercero: 'Pagado por otro seguro',
  sexo: 'Sexo',
  raza: 'Raza',
  edad: 'Edad',
  banda_edad: 'Banda de edad',
  estado: 'Estado',
  codigo_condado: 'Condado',
  grupo_enfermedad: 'Grupo de enfermedad',
  diagnostico_principal: 'Diagnóstico principal',
  categoria: 'Categoría de diagnóstico',
  procedimiento_principal: 'Procedimiento principal',
  medico_tratante: 'Médico tratante',
  id_prestador: 'Prestador',
  id_afiliado: 'Afiliado',
  grupo_drg: 'Grupo DRG',
  dias_estancia: 'Días de estancia',
}

function legible(columna: string): string {
  return NOMBRES[columna] ?? columna
}

function simbolo(operador: string): string {
  if (operador === '=' || operador === 'IN') return 'es'
  if (operador === '!=' || operador === 'NOT IN') return 'no es'
  if (operador === 'BETWEEN') return 'entre'
  if (operador === '>=') return 'desde'
  if (operador === '<=') return 'hasta'
  return operador
}

function formatear(valor: number, unidad: Componente['unidad']): string {
  if (unidad === 'porcentaje') {
    // El backend puede mandarlo como fracción (0,2609) o ya en escala (26,09).
    const escala = Math.abs(valor) <= 1 ? valor * 100 : valor
    return `${escala.toLocaleString('es-EC', { maximumFractionDigits: 2 })} %`
  }
  if (unidad === 'dolares') {
    return valor.toLocaleString('es-EC', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 2,
    })
  }
  return valor.toLocaleString('es-EC', { maximumFractionDigits: 2 })
}

