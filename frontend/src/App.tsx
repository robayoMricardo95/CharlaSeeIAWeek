import { useEffect, useState } from 'react'
import BarraLateral from './componentes/BarraLateral'
import PanelCentral from './componentes/PanelCentral'
import PanelArtefactos from './componentes/PanelArtefactos'
import {
  abrirConversacion,
  borrarConversacion,
  consultarUso,
  enviarMensaje,
  listarConversaciones,
} from './api/cliente'
import type { Artefacto, Conversacion, Mensaje, ResumenUso } from './api/tipos'

function App() {
  const [conversaciones, setConversaciones] = useState<Conversacion[]>([])
  const [conversacionActivaId, setConversacionActivaId] = useState('')
  const [esperandoRespuesta, setEsperandoRespuesta] = useState(false)
  const [artefactoAbierto, setArtefactoAbierto] = useState<Artefacto | null>(null)
  // Gasto de todas las conversaciones juntas. Lo calcula el backend y viene
  // en cada respuesta, así no hay que sumarlo acá.
  const [usoSesion, setUsoSesion] = useState<ResumenUso | undefined>()
  const [cargandoMensajes, setCargandoMensajes] = useState(false)
  const [errorCarga, setErrorCarga] = useState('')

  // La lista sale del historial en SQLite: lo que se preguntó la semana pasada
  // sigue estando después de recargar la página o de reiniciar el backend.
  // El gasto total al abrir la página. Sin esto el medidor arranca en $0,00 y
  // solo se enciende cuando llega la primera respuesta.
  useEffect(() => {
    consultarUso()
      .then((uso) => uso.total_sesion && setUsoSesion(uso.total_sesion))
      .catch(() => {})
  }, [])

  useEffect(() => {
    listarConversaciones()
      .then((datos) => {
        setConversaciones(datos)
        if (datos[0]) seleccionar(datos[0].id, datos)
      })
      .catch(() => setErrorCarga('No pude leer el historial. ¿Está corriendo el backend?'))
  }, [])

  // Los mensajes se traen al abrir la conversación, no al listarla: cincuenta
  // conversaciones con todo su razonamiento adentro serían megabytes que casi
  // nadie va a mirar.
  async function seleccionar(conversacionId: string, lista = conversaciones) {
    setConversacionActivaId(conversacionId)
    const elegida = lista.find((c) => c.id === conversacionId)
    if (!elegida || elegida.cargada) return

    setCargandoMensajes(true)
    try {
      const [mensajes, uso] = await Promise.all([
        abrirConversacion(conversacionId),
        // El acumulado de ESTA conversación, que vive en el historial y no en la
        // memoria del navegador.
        consultarUso(conversacionId).catch(
          (): { acumulado?: ResumenUso; total_sesion?: ResumenUso } => ({}),
        ),
      ])
      setConversaciones((prev) =>
        prev.map((c) =>
          c.id === conversacionId
            ? { ...c, mensajes, cargada: true, uso: uso.acumulado ?? c.uso }
            : c,
        ),
      )
      if (uso.total_sesion) setUsoSesion(uso.total_sesion)
    } catch {
      setErrorCarga('No pude abrir esa conversación.')
    } finally {
      setCargandoMensajes(false)
    }
  }

  // Devuelve el id de la conversación creada para que la barra lateral
  // sepa a cuál ponerle el campo de texto para pedir el nombre.
  function crearConversacion(): string {
    const nueva: Conversacion = {
      id: crypto.randomUUID(),
      titulo: 'Nueva conversación',
      mensajes: [],
      cargada: true,
    }
    setConversaciones((prev) => [nueva, ...prev])
    setConversacionActivaId(nueva.id)
    return nueva.id
  }

  function renombrarConversacion(conversacionId: string, titulo: string) {
    setConversaciones((prev) =>
      prev.map((conversacion) =>
        conversacion.id === conversacionId ? { ...conversacion, titulo } : conversacion,
      ),
    )
  }

  // Primero el backend y después la pantalla: si el borrado falla, la
  // conversación sigue a la vista en vez de desaparecer y volver al recargar.
  async function eliminarConversacion(conversacionId: string) {
    try {
      const { total_sesion } = await borrarConversacion(conversacionId)
      if (total_sesion) setUsoSesion(total_sesion)
    } catch {
      setErrorCarga('No pude borrar esa conversación. ¿Está corriendo el backend?')
      return
    }

    const restantes = conversaciones.filter((c) => c.id !== conversacionId)
    setConversaciones(restantes)
    if (conversacionId === conversacionActivaId) {
      setArtefactoAbierto(null)
      if (restantes[0]) {
        void seleccionar(restantes[0].id, restantes)
      } else {
        setConversacionActivaId('')
      }
    }
  }

  function agregarMensaje(conversacionId: string, mensaje: Mensaje) {
    setConversaciones((prev) =>
      prev.map((conversacion) =>
        conversacion.id === conversacionId
          ? { ...conversacion, mensajes: [...conversacion.mensajes, mensaje] }
          : conversacion,
      ),
    )
  }

  function registrarUso(conversacionId: string, uso: ResumenUso) {
    setConversaciones((prev) =>
      prev.map((conversacion) =>
        conversacion.id === conversacionId ? { ...conversacion, uso } : conversacion,
      ),
    )
  }

  async function manejarEnvio(texto: string) {
    const activa = conversaciones.find((c) => c.id === conversacionActivaId)
    if (activa && activa.mensajes.length === 0) {
      // El backend titula el hilo con su primera pregunta; se hace lo mismo acá
      // para que no cambie de nombre al recargar la página.
      renombrarConversacion(conversacionActivaId, texto.slice(0, 60))
    }

    agregarMensaje(conversacionActivaId, {
      id: crypto.randomUUID(),
      autor: 'usuario',
      texto,
    })

    setEsperandoRespuesta(true)
    try {
      const respuesta = await enviarMensaje(texto, conversacionActivaId)
      agregarMensaje(conversacionActivaId, {
        id: crypto.randomUUID(),
        autor: 'agente',
        texto: respuesta.texto,
        artefacto: respuesta.artefacto,
        analisis: respuesta.analisis,
        resultado: respuesta.resultado,
        mostrarTabla: respuesta.mostrar_tabla,
      })
      if (respuesta.artefacto) {
        setArtefactoAbierto(respuesta.artefacto)
      }
      if (respuesta.acumulado) {
        registrarUso(conversacionActivaId, respuesta.acumulado)
      }
      if (respuesta.total_sesion) {
        setUsoSesion(respuesta.total_sesion)
      }
    } catch {
      agregarMensaje(conversacionActivaId, {
        id: crypto.randomUUID(),
        autor: 'agente',
        texto: 'No pude conectarme con el servidor. ¿Está corriendo el backend?',
      })
    } finally {
      setEsperandoRespuesta(false)
    }
  }

  return (
    <div className="relative flex h-screen bg-white">
      {errorCarga && (
        <div className="absolute inset-x-0 top-0 z-10 bg-red-50 px-4 py-2 text-center text-sm text-red-700">
          {errorCarga}
          <button onClick={() => setErrorCarga('')} className="ml-3 underline">
            cerrar
          </button>
        </div>
      )}
      <BarraLateral
        conversaciones={conversaciones}
        conversacionActivaId={conversacionActivaId}
        onSeleccionar={(id) => void seleccionar(id)}
        onNuevaConversacion={crearConversacion}
        onRenombrar={renombrarConversacion}
        onBorrar={(id) => void eliminarConversacion(id)}
      />
      <PanelCentral
        conversaciones={conversaciones}
        conversacionActivaId={conversacionActivaId}
        onEnviarMensaje={manejarEnvio}
        esperandoRespuesta={esperandoRespuesta || cargandoMensajes}
        onAbrirArtefacto={setArtefactoAbierto}
        usoSesion={usoSesion}
      />
      {artefactoAbierto && (
        <PanelArtefactos
          artefacto={artefactoAbierto}
          onCerrar={() => setArtefactoAbierto(null)}
        />
      )}
    </div>
  )
}

export default App
