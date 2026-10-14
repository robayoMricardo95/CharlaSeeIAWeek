// Indicador de que el agente está trabajando. Va al final del hilo, en el
// lugar donde después va a aparecer la respuesta, no debajo del input. Sin
// burbuja, igual que los mensajes del agente.
function Pensando() {
  return (
    <div className="flex items-center gap-2 text-sm text-gray-400">
      <span className="flex gap-1">
        {[0, 150, 300].map((retraso) => (
          <span
            key={retraso}
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-acento"
            style={{ animationDelay: `${retraso}ms` }}
          />
        ))}
      </span>
      Pensando...
    </div>
  )
}

export default Pensando
