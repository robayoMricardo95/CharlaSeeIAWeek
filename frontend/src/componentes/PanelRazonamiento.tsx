import { useState } from 'react'
import type { Analisis, ConsultaBase, Filtro, Resultado, Revision, Sondeo } from '../api/tipos'
import { ComoSeCalculo } from './ComoSeCalculo'
import TablaResultado from './TablaResultado'

interface Props {
  analisis: Analisis
  resultado?: Resultado | null // el resultado final, después del cruce en Python
}

type Pestana = 'razonamiento' | 'consulta'

// Va arriba de la respuesta, plegado, con dos pestañas a la misma altura:
//
// - Razonamiento: POR QUÉ salió ese número — qué se entendió, qué reglas se
//   aplicaron, qué filtros puso el modelo y cuáles garantizó el código.
// - Consulta y resultado: QUÉ se ejecutó — el SQL de cada extracción con las
//   filas que devolvió, y el Python que las cruzó.
//
// Están separadas porque se usan en momentos distintos: la primera cuando
// desconfiás del criterio, la segunda cuando querés llevarte la consulta.
function PanelRazonamiento({ analisis, resultado }: Props) {
  const [abierta, setAbierta] = useState<Pestana | null>(null)

  const observaciones = [
    ...(analisis.revision?.hallazgos ?? []),
    ...(analisis.revision_resultado?.hallazgos ?? []),
  ]
  const inyectados = analisis.consultas.reduce((total, c) => total + c.inyectados.length, 0)
  // Las respuestas viejas no traen `sondeos`: se guardan en el historial y se
  // vuelven a pintar cuando se recarga la conversación.
  const sondeos = analisis.sondeos ?? []
  const grave =
    analisis.error !== '' ||
    observaciones.some((h) => h.gravedad === 'error') ||
    analisis.novedades.some((n) => n.nivel === 'error')

  function alternar(pestana: Pestana) {
    setAbierta((actual) => (actual === pestana ? null : pestana))
  }

  return (
    <div className="mb-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <Solapa
          activa={abierta === 'razonamiento'}
          onClick={() => alternar('razonamiento')}
          titulo="Razonamiento"
        >
          <Separador />
          {analisis.consultas.length} consulta(s)
          {inyectados > 0 && (
            <>
              <Separador />
              <span className="text-acento">{inyectados} por regla</span>
            </>
          )}
          {(analisis.novedades.length > 0 || observaciones.length > 0) && (
            <>
              <Separador />
              <span className={grave ? 'text-red-600' : 'text-amber-600'}>
                {analisis.novedades.length + observaciones.length} novedad(es)
              </span>
            </>
          )}
        </Solapa>

        <Solapa
          activa={abierta === 'consulta'}
          onClick={() => alternar('consulta')}
          titulo="Consulta y resultado"
        >
          {sondeos.length > 0 && (
            <>
              <Separador />
              <span className="text-acento">
                {sondeos.length} sondeo{sondeos.length > 1 ? 's' : ''}
              </span>
            </>
          )}
          {analisis.composicion && (
            <>
              <Separador />
              <span>+ cruce en Python</span>
            </>
          )}
        </Solapa>
      </div>

      {abierta === 'razonamiento' && <PestanaRazonamiento analisis={analisis} />}
      {abierta === 'consulta' && (
        <PestanaConsulta analisis={analisis} resultado={resultado} />
      )}
    </div>
  )
}

function Solapa({
  activa,
  onClick,
  titulo,
  children,
}: {
  activa: boolean
  onClick: () => void
  titulo: string
  children?: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`flex flex-wrap items-center gap-2 transition ${
        activa ? 'text-acento' : 'text-gray-400 hover:text-acento'
      }`}
    >
      <span className={`transition ${activa ? 'rotate-90' : ''}`}>▸</span>
      {titulo}
      {children}
    </button>
  )
}

function PestanaRazonamiento({ analisis }: { analisis: Analisis }) {
  return (
    <Marco>
      <Seccion titulo="Qué entendió">
        <p className="text-gray-700">{analisis.interpretacion}</p>
      </Seccion>

      {/* El detalle del cálculo. Estuvo siempre visible bajo la respuesta, pero
          en preguntas simples no tenía fórmula y solo repetía los
          filtros de la propia pregunta, así que ensuciaba más de lo que aportaba.
          Acá sigue entero, a un clic, para quien quiera auditarlo. */}
      <ComoSeCalculo analisis={analisis} />

      <div className="grid gap-4 sm:grid-cols-3">
        <Seccion titulo="Período">
          <p className="text-gray-700">{analisis.periodo}</p>
        </Seccion>
        <Seccion titulo="Métrica">
          <p className="text-gray-700">
            {analisis.metrica}
            {!analisis.metrica_es_aditiva && (
              <span className="ml-1.5 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700">
                no aditiva
              </span>
            )}
          </p>
        </Seccion>
        <Seccion titulo="Rama">
          <p className="text-gray-700">{analisis.rama}</p>
        </Seccion>
      </div>

      {analisis.pide_participacion && (
        <Seccion titulo="Parte y todo">
          <div className="rounded-lg border border-acento/30 bg-acento/5 p-3">
            <Linea
              etiqueta="La parte"
              valor={analisis.dimension_de_la_parte || '(no se declaró)'}
            />
            <Linea etiqueta="El todo" valor={analisis.universo_denominador || '(no se declaró)'} />
            <p className="mt-2 text-gray-500">
              El porcentaje es la parte sobre el todo. Si el todo no es el universo que pediste,
              el número está mal aunque las consultas sean correctas.
            </p>
          </div>
        </Seccion>
      )}

      {analisis.novedades.length > 0 && (
        <Seccion titulo="Novedades del razonamiento">
          <ul className="space-y-1.5">
            {analisis.novedades.map((novedad) => (
              <li key={`${novedad.paso}-${novedad.detalle}`} className="flex gap-2">
                <Etiqueta texto={novedad.paso} />
                <span className={color(novedad.nivel)}>
                  <span className="font-medium">{novedad.regla}</span> — {novedad.detalle}
                </span>
              </li>
            ))}
          </ul>
        </Seccion>
      )}

      {analisis.consultas.map((consulta) => (
        <BloqueDecisiones key={consulta.nombre} consulta={consulta} />
      ))}

      <BloqueRevision titulo="Revisión de las decisiones" revision={analisis.revision} />
      <BloqueRevision titulo="Revisión del resultado" revision={analisis.revision_resultado} />

      {analisis.error && (
        <Seccion titulo="Error">
          <p className="rounded-lg bg-red-50 px-3 py-2 text-red-700">{analisis.error}</p>
        </Seccion>
      )}
    </Marco>
  )
}

function PestanaConsulta({
  analisis,
  resultado,
}: {
  analisis: Analisis
  resultado?: Resultado | null
}) {
  const conSql = analisis.consultas.filter((consulta) => consulta.sql)
  const sondeos = analisis.sondeos ?? []

  if (conSql.length === 0 && sondeos.length === 0) {
    return (
      <Marco>
        <p className="text-gray-500">
          No se ejecutó ninguna consulta: la pregunta se resolvió sin tocar la base.
        </p>
      </Marco>
    )
  }

  return (
    <Marco>
      {sondeos.length > 0 && (
        <div>
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-gray-400">
            Sondeo previo
          </div>
          <p className="mb-2 text-[11px] text-gray-500">
            Antes de armar el plan, el agente le preguntó esto a la base. Son consultas de
            reconocimiento: no aportan al número final, resuelven un dato que no podía suponer.
          </p>
          <div className="space-y-3">
            {sondeos.map((sondeo) => (
              <BloqueSondeo key={sondeo.nombre} sondeo={sondeo} />
            ))}
          </div>
        </div>
      )}

      {conSql.map((consulta) => (
        <div key={consulta.nombre}>
          <div className="mb-1.5 flex flex-wrap items-baseline gap-2">
            <span className="font-mono text-[11px] font-medium text-acento">
              {consulta.nombre}
            </span>
            <span className="text-gray-500">{consulta.proposito}</span>
            <span className="ml-auto text-[11px] text-gray-400">
              {consulta.error ? 'falló' : `${consulta.filas.toLocaleString('es-EC')} fila(s)`}
            </span>
          </div>
          <Codigo texto={consulta.sql} />
          {consulta.error ? (
            <p className="mt-1.5 rounded-lg bg-red-50 px-3 py-2 text-red-700">{consulta.error}</p>
          ) : (
            consulta.resultado && <TablaResultado resultado={consulta.resultado} />
          )}
        </div>
      ))}

      {analisis.composicion && (
        <div>
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-gray-400">
            Cruce en Python
            {analisis.reparaciones > 0 && ` (reparado ${analisis.reparaciones}×)`}
          </div>
          <p className="mb-1.5 text-[11px] text-gray-500">
            Corre en un contenedor aislado, sin red y sin acceso a la base. Opera sobre los
            resultados de arriba, no sobre los 656 mil reclamos de la base.
          </p>
          <Codigo texto={analisis.composicion} />
          {analisis.impreso && (
            <pre className="mt-1.5 overflow-x-auto rounded-lg bg-gray-100 p-2 text-[11px] text-gray-600">
              {analisis.impreso}
            </pre>
          )}
          {resultado && (
            <div className="mt-2">
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-gray-400">
                Resultado final
              </div>
              <TablaResultado resultado={resultado} />
            </div>
          )}
        </div>
      )}
    </Marco>
  )
}

function BloqueSondeo({ sondeo }: { sondeo: Sondeo }) {
  return (
    <div className="border-l-2 border-acento/40 pl-3">
      <div className="mb-1.5 flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-[11px] font-medium text-acento">{sondeo.nombre}</span>
        <span className="text-gray-500">{sondeo.proposito}</span>
      </div>
      {sondeo.inyectados?.length > 0 && (
        <ul className="mb-1.5 space-y-0.5">
          {sondeo.inyectados.map((nota) => (
            <li key={nota} className="text-acento">
              ● {nota}
            </li>
          ))}
        </ul>
      )}
      {sondeo.sql && <Codigo texto={sondeo.sql} />}
      {sondeo.error ? (
        <p className="mt-1.5 rounded-lg bg-red-50 px-3 py-2 text-red-700">{sondeo.error}</p>
      ) : (
        sondeo.resultado && <TablaResultado resultado={sondeo.resultado} />
      )}
    </div>
  )
}

// Los filtros y las medidas: el "por qué", sin el SQL (eso vive en la otra pestaña).
function BloqueDecisiones({ consulta }: { consulta: ConsultaBase }) {
  const medidas = consulta.medidas
    .map((m) => `${m.agregacion}(${m.columna}) → ${m.alias}`)
    .join(', ')

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-[11px] font-medium text-acento">{consulta.nombre}</span>
        <span className="text-gray-500">{consulta.proposito}</span>
      </div>

      <Linea etiqueta="Mide" valor={medidas || '(listado sin agregar)'} />
      <Linea etiqueta="Agrupa por" valor={consulta.dimensiones.join(', ') || '(nada)'} />

      <div className="mt-2 text-[10px] font-medium uppercase tracking-wider text-gray-400">
        Filtros
      </div>
      <ul className="space-y-1">
        {consulta.filtros.map((filtro) => (
          <FilaFiltro key={`${filtro.columna}-${filtro.operador}`} filtro={filtro} />
        ))}
      </ul>
    </div>
  )
}

function FilaFiltro({ filtro }: { filtro: Filtro }) {
  const porRegla = filtro.origen === 'inyectado' || filtro.origen === 'obligatoria'
  return (
    <li className="flex gap-2">
      <span
        className={porRegla ? 'text-acento' : 'text-gray-300'}
        title={porRegla ? 'Lo garantiza el código' : 'Lo decidió el modelo'}
      >
        {porRegla ? '●' : '○'}
      </span>
      <span className="min-w-0">
        <span className="font-mono text-gray-800">
          {filtro.columna} {filtro.operador} {filtro.valores.join(', ')}
        </span>
        {porRegla && (
          <span className="ml-1.5 rounded bg-acento/10 px-1.5 py-0.5 text-[10px] text-acento">
            regla
          </span>
        )}
        <span className="block text-gray-400">{filtro.justificacion}</span>
      </span>
    </li>
  )
}

function BloqueRevision({ titulo, revision }: { titulo: string; revision: Revision }) {
  const hallazgos = revision?.hallazgos ?? []
  if (revision?.aprobado === undefined) return null

  return (
    <Seccion titulo={titulo}>
      {hallazgos.length === 0 ? (
        <p className="text-gray-500">Sin observaciones.</p>
      ) : (
        <ul className="space-y-1.5">
          {hallazgos.map((hallazgo) => (
            <li key={hallazgo.que_falta} className="flex gap-2">
              <Etiqueta texto={hallazgo.paso} />
              <span className={hallazgo.gravedad === 'error' ? 'text-red-700' : 'text-amber-700'}>
                {hallazgo.que_falta}
                <span className="block text-gray-500">→ {hallazgo.correccion}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Seccion>
  )
}

function Marco({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-2 space-y-4 rounded-xl border border-gray-200 bg-gray-50/60 p-4 text-xs">
      {children}
    </div>
  )
}

function Codigo({ texto }: { texto: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg bg-gray-900 p-3 font-mono text-[11px] leading-relaxed text-gray-100">
      {texto}
    </pre>
  )
}

function Linea({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return (
    <div className="flex gap-2">
      <span className="shrink-0 text-gray-400">{etiqueta}:</span>
      <span className="font-mono text-gray-700">{valor}</span>
    </div>
  )
}

function Etiqueta({ texto }: { texto: string }) {
  return (
    <span className="shrink-0 rounded bg-gray-200 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-gray-500">
      {texto}
    </span>
  )
}

function Separador() {
  return <span className="text-gray-300">·</span>
}

function color(nivel: string): string {
  if (nivel === 'error') return 'text-red-700'
  if (nivel === 'aviso') return 'text-amber-700'
  return 'text-gray-700'
}

function Seccion({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-gray-400">
        {titulo}
      </div>
      {children}
    </div>
  )
}

export default PanelRazonamiento
