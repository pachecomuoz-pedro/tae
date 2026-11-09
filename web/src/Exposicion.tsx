/**
 * Exposición: el saldo de EPS en Emida frente a lo asignado en la red.
 *
 * Solo la ve el Super Admin. Son dos números que NO son el mismo
 * (decisión 23): Emida es el dinero real de EPS con el proveedor; la red es
 * lo que el Super Admin asignó a mano a administradores y clientes. La
 * pantalla los pone lado a lado para que la diferencia salte a la vista.
 *
 * Abrir la pantalla no llama a Emida: muestra la última consulta guardada.
 * El botón consulta, guarda en la base y refresca.
 */
import { useEffect, useState } from "react";

import { ErrorAPI, SaldoEmida, api, pesos } from "./api";
import { Aviso, Tarjeta } from "./piezas";

function cuando(iso: string | null) {
  if (!iso) return "nunca";
  return new Date(iso).toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

export function Exposicion() {
  const [datos, setDatos] = useState<SaldoEmida | null>(null);
  const [consultando, setConsultando] = useState(false);
  const [error, setError] = useState<ErrorAPI | null>(null);

  useEffect(() => {
    api.saldoEmida().then(setDatos).catch((e) => setError(e as ErrorAPI));
  }, []);

  async function consultar() {
    setConsultando(true);
    setError(null);
    try {
      setDatos(await api.consultarSaldoEmida());
    } catch (e) {
      setError(e as ErrorAPI);
    } finally {
      setConsultando(false);
    }
  }

  const diferencia = datos?.diferencia != null ? Number(datos.diferencia) : null;
  const enRiesgo = diferencia != null && diferencia < 0;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <Tarjeta titulo="Saldo de EPS en Emida">
          <p className="cifras font-display text-3xl font-bold text-primario" data-prueba="saldo-emida">
            {datos?.saldo_emida != null ? pesos(datos.saldo_emida) : "Sin consultar"}
          </p>
          <p className="mt-1 text-xs text-apagado">
            Última consulta: {cuando(datos?.consultado ?? null)}
            {datos?.consultado_por && ` · ${datos.consultado_por}`}
          </p>
          <button type="button" onClick={consultar} disabled={consultando}
                  data-prueba="consultar-saldo"
                  className="mt-4 w-full rounded-xl bg-primario py-3 text-sm font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
            {consultando ? "Consultando a Emida…" : "Consultar saldo en Emida"}
          </button>
        </Tarjeta>

        <Tarjeta titulo="Asignado en la red">
          <p className="cifras font-display text-3xl font-bold">
            {datos ? pesos(datos.asignado_red) : "—"}
          </p>
          <p className="mt-1 text-xs text-apagado">
            Suma de las bolsas de administradores y clientes. Lo asignas tú a mano;
            no sale de Emida.
          </p>
        </Tarjeta>

        <Tarjeta titulo="Emida menos lo asignado">
          <p className={"cifras font-display text-3xl font-bold " +
            (enRiesgo ? "text-alerta-oscuro" : "text-exito-oscuro")}>
            {diferencia != null ? pesos(String(diferencia)) : "—"}
          </p>
          <p className="mt-1 text-xs text-apagado">
            {enRiesgo
              ? "La red tiene asignado más de lo que hay en Emida: si se vende todo, Emida empezará a rechazar ventas por fondos insuficientes."
              : "Positivo: lo que hay en Emida cubre todo lo asignado en la red."}
          </p>
        </Tarjeta>
      </div>

      {datos?.ultimo_error && (
        <p className="rounded-xl bg-ambar-fondo p-3 text-sm">
          El último intento ({cuando(datos.ultimo_intento)}) no obtuvo saldo: {datos.ultimo_error}
          {datos.saldo_emida != null && " Se muestra el último saldo bueno."}
        </p>
      )}
      {error && <Aviso error={error} />}
    </div>
  );
}
