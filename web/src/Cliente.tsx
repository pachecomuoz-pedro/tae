/**
 * Pantallas del cliente: sus puntos de venta y el reparto de su bolsa.
 *
 * La pieza que vale es la barra de reparto. La regla "la suma de los
 * límites no puede exceder mi bolsa" es fácil de enunciar y difícil de
 * sentir cuando son cinco cajas; dibujada, se entiende de un vistazo por
 * qué no alcanza. Y cuando no alcanza, el servidor rechaza de todos modos:
 * la barra evita el viaje, no sustituye la validación.
 */
import { useEffect, useState } from "react";

import { ErrorAPI, PuntoVenta, Saldo, api, pesos } from "./api";
import { Aviso, Campo, Tarjeta } from "./piezas";

export function PuntosVenta({ saldo, alCambiar }: {
  saldo: Saldo | null;
  alCambiar: () => void;
}) {
  const [cajas, setCajas] = useState<PuntoVenta[]>([]);
  const [elegida, setElegida] = useState<PuntoVenta | null>(null);
  const [monto, setMonto] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [listo, setListo] = useState("");
  const [modo, setModo] = useState<"asignar" | "retirar">("asignar");
  const [motivo, setMotivo] = useState("");

  const cargar = () => api.puntosVenta().then(setCajas).catch(() => setCajas([]));

  /**
   * El servidor manda los montos con cuatro decimales porque así los guarda
   * el ledger. Un cajero no teclea cuatro decimales: al precargar el campo
   * se recortan a dos.
   */
  const paraCapturar = (v: string | null) =>
    v === null ? "" : String(Number(v).toFixed(2)).replace(/\.00$/, "");
  useEffect(() => { cargar(); }, []);

  const bolsa = Number(saldo?.saldo ?? 0);
  const asignadoAOtras = cajas
    .filter((c) => c.uuid !== elegida?.uuid)
    .reduce((t, c) => t + Number(c.limite ?? 0), 0);
  const tope = bolsa - asignadoAOtras;
  const propuesto = Number(monto || 0);
  const excede = propuesto > tope;
  const libre = Math.max(bolsa - asignadoAOtras - propuesto, 0);
  const compartida = saldo?.modo_bolsa === "compartida";
  // Bajar el límite es un retiro: el servidor exige motivo, y aquí se pide
  // antes de enviar para no rechazar a ciegas.
  const actual = Number(elegida?.limite ?? 0);
  const baja = elegida !== null && monto !== "" && propuesto < actual;

  async function asignar() {
    if (!elegida) return;
    setError(null);
    setListo("");
    try {
      const r = await api.asignarLimite(elegida.uuid, monto || "0", baja ? motivo : "");
      setListo(`${r.punto_venta} quedó en ${pesos(r.limite)}`);
      setMonto("");
      setElegida(null);
      await cargar();
      alCambiar();
    } catch (e) {
      setError(e as ErrorAPI);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <Tarjeta titulo="Mis puntos de venta">
        <div className="space-y-2">
          {cajas.map((c) => {
            const activa = elegida?.uuid === c.uuid;
            return (
              <button
                key={c.uuid}
                type="button"
                data-caja={c.nombre}
                onClick={() => { setElegida(c); setMonto(paraCapturar(c.limite)); }}
                disabled={compartida}
                className={
                  "flex w-full items-center gap-3 rounded-xl border p-3 text-left transition " +
                  (activa ? "border-2 border-primario bg-elegido" : "border-borde bg-white") +
                  (compartida ? " cursor-default opacity-80" : " hover:border-campo")
                }
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-bold">{c.nombre}</span>
                  <span className="block text-xs text-apagado">
                    {c.direccion || "Sin dirección"} · {c.estado}
                  </span>
                </span>
                <span className="shrink-0 text-right">
                  <span className="block text-[11px] text-apagado">
                    {compartida ? "Origen" : "Bolsa asignada"}
                  </span>
                  <span className="cifras block font-display text-sm font-bold">
                    {compartida ? "Usa mi bolsa" : pesos(c.limite)}
                  </span>
                </span>
              </button>
            );
          })}
          {cajas.length === 0 && (
            <p className="text-sm text-apagado">Todavía no tienes cajas dadas de alta.</p>
          )}
        </div>

        {compartida && (
          <p className="mt-4 rounded-xl bg-suave p-3 text-xs text-apagado">
            Operas en bolsa compartida: todas tus cajas debitan directo de tu
            bolsa, sin límite individual.
          </p>
        )}
      </Tarjeta>

      {!compartida && (
        <aside className="space-y-4">
          <div className="rounded-2xl border border-borde bg-white p-5">
            <h3 className="font-display text-base font-bold">Asignar saldo</h3>

            {!elegida && (
              <p className="mt-3 text-sm text-apagado">
                Elige una caja de la lista para repartirle saldo.
              </p>
            )}

            {elegida && (
              <div className="mt-3 grid grid-cols-2 gap-2">
                {(["asignar", "retirar"] as const).map((m) => (
                  <button key={m} type="button" data-modo-caja={m}
                          onClick={() => { setModo(m); setError(null); setListo(""); }}
                          className={"rounded-xl border p-2.5 text-sm font-bold " +
                            (modo === m ? "border-2 border-primario bg-elegido" : "border-borde")}>
                    {m === "asignar" ? "Asignar" : "Retirar"}
                  </button>
                ))}
              </div>
            )}

            {elegida && modo === "retirar" && (
              <RetirarDeCaja caja={elegida}
                             alHecho={(t) => { setListo(t); setElegida(null); cargar(); alCambiar(); }} />
            )}

            {elegida && modo === "asignar" && (
              <>
                <p className="mt-3 text-sm font-semibold">{elegida.nombre}</p>
                <dl className="mt-3 space-y-1.5 text-sm">
                  <Renglon t="Asignado a las otras" v={pesos(asignadoAOtras)} />
                  <Renglon t="Tope para ésta" v={pesos(tope)} acento />
                </dl>

                <div className="mt-4">
                  <Campo
                    etiqueta="Monto"
                    valor={monto}
                    alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))}
                    marcador="0.00"
                    prueba="monto-limite"
                    malo={excede}
                  />
                </div>

                {/* El reparto, dibujado. */}
                <div className="mt-4">
                  <p className="text-[11px] font-bold uppercase tracking-wide text-apagado">
                    Reparto de mi bolsa
                  </p>
                  <div className="mt-2 flex h-6 overflow-hidden rounded-lg bg-suave">
                    <span
                      className="block bg-marino/70"
                      style={{ width: pct(asignadoAOtras, bolsa, propuesto) }}
                    />
                    <span
                      className={"block " + (excede ? "bg-alerta" : "bg-primario")}
                      style={{ width: pct(propuesto, bolsa, propuesto, asignadoAOtras) }}
                    />
                    <span className="block bg-borde" style={{ width: pct(libre, bolsa, propuesto, asignadoAOtras) }} />
                  </div>
                  <p className="mt-2 text-xs text-apagado">
                    Otras {pesos(asignadoAOtras)} · ésta {pesos(propuesto)} · sin
                    asignar {pesos(libre)}
                  </p>
                </div>

                {excede && (
                  <p className="mt-3 rounded-xl bg-alerta-fondo p-3 text-xs font-semibold text-alerta-oscuro">
                    Rebasa el tope en {pesos(propuesto - tope)}. La suma de las
                    bolsas no puede exceder la mía.
                  </p>
                )}

                {baja && (
                  <div className="mt-3 rounded-xl bg-ambar-fondo p-3">
                    <p className="mb-2 text-xs font-semibold text-ambar-texto">
                      Bajas el límite de {pesos(actual)} a {pesos(propuesto)}: es un retiro de{" "}
                      {pesos(actual - propuesto)} y lleva motivo.
                    </p>
                    <SelectorMotivo valor={motivo} alCambiar={setMotivo} />
                  </div>
                )}

                {error && <Aviso error={error} />}

                <button
                  type="button"
                  onClick={asignar}
                  disabled={excede || propuesto < 0 || monto === "" || (baja && !motivo)}
                  data-prueba="asignar"
                  className={
                    "mt-4 w-full rounded-xl py-3.5 font-display font-bold transition " +
                    (!excede && monto !== "" && !(baja && !motivo)
                      ? "bg-primario text-white"
                      : "cursor-not-allowed bg-[#D3D9E1] text-[#7A8699]")
                  }
                >
                  Asignar saldo
                </button>
                <p className="mt-2 text-center text-[11px] text-apagado">
                  Asignar no mueve dinero: reserva un tope de gasto para esa caja.
                </p>
              </>
            )}

            {listo && (
              <p className="mt-4 rounded-xl bg-exito-fondo p-3 text-sm font-semibold text-exito-oscuro" data-prueba="asignado">
                {listo}
              </p>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}

const MOTIVOS_CAJA = [
  ["captura", "Error de captura en el monto"],
  ["cliente_equivocado", "Se asignó a la caja equivocada"],
  ["suspension", "Recuperación de saldo de la caja"],
  ["otro", "Otro"],
] as const;

function SelectorMotivo({ valor, alCambiar }: { valor: string; alCambiar: (v: string) => void }) {
  return (
    <select value={valor} onChange={(e) => alCambiar(e.target.value)} data-prueba="motivo-caja"
            className="w-full rounded-xl border border-campo bg-white px-3 py-2.5 text-sm outline-none focus:border-primario">
      <option value="">Elige el motivo…</option>
      {MOTIVOS_CAJA.map(([c, t]) => <option key={c} value={c}>{t}</option>)}
    </select>
  );
}

/**
 * Retirar saldo de una caja: baja su límite en el monto. El dinero nunca
 * salió de la bolsa del cliente (decisión 7), así que vuelve a quedar libre
 * para otra caja. No se puede retirar lo que la caja tiene en ventas en
 * vuelo; el servidor lo verifica con la fila bloqueada.
 */
function RetirarDeCaja({ caja, alHecho }: { caja: PuntoVenta; alHecho: (t: string) => void }) {
  const [monto, setMonto] = useState("");
  const [motivo, setMotivo] = useState("");
  const [nota, setNota] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [enviando, setEnviando] = useState(false);
  const limite = Number(caja.limite ?? 0);
  const pedido = Number(monto || 0);
  const excede = pedido > limite;
  const listo = pedido > 0 && !excede && motivo !== "" && (motivo !== "otro" || nota.trim().length > 3);

  async function retirar() {
    if (enviando) return;
    setEnviando(true);
    setError(null);
    try {
      const r = await api.retirarDeCaja(caja.uuid, monto, motivo, nota);
      alHecho(`Se retiraron ${pesos(pedido)} de ${r.punto_venta}; su límite quedó en ${pesos(r.limite)}.`);
    } catch (e) {
      setError(e as ErrorAPI);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="mt-4 space-y-3">
      <dl className="space-y-1.5 text-sm">
        <Renglon t={`Saldo asignado a ${caja.nombre}`} v={pesos(limite)} acento />
      </dl>
      <Campo etiqueta="Monto a retirar" valor={monto} marcador="0.00" prueba="monto-retiro-caja"
             alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))} malo={excede} />
      {excede && (
        <p className="rounded-xl bg-alerta-fondo p-3 text-xs font-semibold text-alerta-oscuro">
          La caja solo tiene {pesos(limite)} asignados.
        </p>
      )}
      <label className="block">
        <span className="mb-1.5 block text-xs font-bold text-tinta-dos">Motivo · obligatorio</span>
        <SelectorMotivo valor={motivo} alCambiar={setMotivo} />
      </label>
      <Campo etiqueta={motivo === "otro" ? 'Nota · obligatoria para "Otro"' : "Nota · opcional"}
             valor={nota} alCambiar={setNota} texto marcador="Queda en el historial de la caja" />
      {error && <Aviso error={error} />}
      <button type="button" onClick={retirar} disabled={!listo || enviando} data-prueba="retirar-caja"
              className="w-full rounded-xl bg-ambar py-3.5 font-display font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
        {enviando ? "Retirando…" : "Retirar de la caja"}
      </button>
      <p className="text-center text-[11px] text-apagado">
        El saldo regresa a tu bolsa, libre para otra caja. Queda en el historial con tu nombre.
      </p>
    </div>
  );
}

/** Ancho del segmento. El denominador crece si lo propuesto se pasa, para que el exceso se vea. */
function pct(valor: number, bolsa: number, propuesto: number, otras = 0): string {
  const denom = Math.max(bolsa, otras + propuesto, 1);
  return `${Math.max((valor / denom) * 100, 0)}%`;
}

function Renglon({ t, v, acento }: { t: string; v: string; acento?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-apagado">{t}</dt>
      <dd className={"cifras font-semibold " + (acento ? "text-primario" : "")}>{v}</dd>
    </div>
  );
}
