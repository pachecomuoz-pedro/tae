/**
 * Abonos y retiros del Super Admin (decisión 27).
 *
 * Aquí el dinero entra a la red desde EPS o vuelve a EPS. No es el
 * traspaso del administrador: ese descuenta de la bolsa del administrador,
 * y por eso el Super Admin no podía abonarle a un cliente desde ahí.
 *
 * Tres cosas que la pantalla tiene que dejar ver sin explicación:
 *
 * 1. **Disponible y Saldo red, juntos.** El disponible es lo que se puede
 *    retirar hoy; el saldo red es lo que ya bajó por la cadena. La
 *    diferencia entre los dos es justo lo que una retención resuelve.
 * 2. **Qué va a pasar antes de apretar.** Si el retiro excede el
 *    disponible, la vista previa dice cuánto se retira y cuánto se retiene.
 * 3. **Las retenciones vivas**, con su folio de origen, para que nadie
 *    tenga que adivinar por qué una red dejó de vender.
 */
import { useEffect, useState } from "react";

import { ErrorAPI, FilaRed, Retencion, api, pesos } from "./api";
import { Aviso, Campo, Tarjeta } from "./piezas";

type Nivel = "administrador" | "cliente";

const MOTIVOS = [
  ["fondeo_excedente", "Fondeo de más (corrección)"],
  ["captura", "Error de captura en el monto"],
  ["cliente_equivocado", "Abono a quien no era"],
  ["suspension", "Recuperación de saldo tras suspensión"],
  ["conciliacion", "Ajuste por conciliación con el proveedor"],
  ["otro", "Otro"],
] as const;

export function SaldoRed({ alCambiar }: { alCambiar: () => void }) {
  const [nivel, setNivel] = useState<Nivel>("administrador");
  const [filas, setFilas] = useState<FilaRed[]>([]);
  const [elegido, setElegido] = useState<FilaRed | null>(null);
  const [retenciones, setRetenciones] = useState<Retencion[]>([]);
  const [buscar, setBuscar] = useState("");

  const cargar = (conservar?: string) => {
    api.red(nivel).then((f) => {
      setFilas(f);
      if (conservar) setElegido(f.find((x) => x.uuid === conservar) ?? null);
    }).catch(() => setFilas([]));
    api.retenciones().then(setRetenciones).catch(() => setRetenciones([]));
  };
  useEffect(() => { setElegido(null); cargar(); }, [nivel]);

  function hecho() {
    cargar(elegido?.uuid);
    alCambiar();
  }

  const q = buscar.trim().toLowerCase();
  const visibles = filas.filter((f) => q === "" || f.nombre.toLowerCase().includes(q));

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_420px]">
      <div className="space-y-5">
        <Tarjeta titulo="Red">
          <div className="mb-3 flex flex-wrap gap-2">
            {(["administrador", "cliente"] as const).map((n) => (
              <button key={n} type="button" onClick={() => setNivel(n)} data-nivel={n}
                      className={"rounded-xl border px-4 py-2 text-sm font-semibold " +
                        (nivel === n ? "border-primario bg-primario text-white"
                                     : "border-borde bg-white text-tinta-dos")}>
                {n === "administrador" ? "Administradores" : "Clientes"}
              </button>
            ))}
            <input value={buscar} onChange={(e) => setBuscar(e.target.value)}
                   placeholder="Buscar…" aria-label="Buscar"
                   className="min-w-[160px] flex-1 rounded-xl border border-borde px-3 py-2 text-sm outline-none focus:border-primario" />
          </div>

          <div className="grid grid-cols-[1fr_auto_auto] gap-x-5 px-3 pb-2 text-[11px] font-bold uppercase tracking-wide text-apagado">
            <span>{nivel === "administrador" ? "Administrador" : "Cliente"}</span>
            <span className="text-right">Disponible</span>
            <span className="w-28 text-right">Saldo red</span>
          </div>
          <div className="space-y-2">
            {visibles.map((f) => {
              const activo = elegido?.uuid === f.uuid;
              const retenido = Number(f.retenido ?? 0);
              return (
                <button key={f.uuid} type="button" onClick={() => setElegido(f)} data-fila={f.nombre}
                        className={"grid w-full grid-cols-[1fr_auto_auto] items-center gap-x-5 rounded-xl border p-3 text-left transition " +
                          (activo ? "border-2 border-primario bg-elegido"
                                  : "border-borde bg-white hover:border-campo")}>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-bold">{f.nombre}</span>
                    <span className="block text-xs text-apagado">
                      {f.padre ? `de ${f.padre} · ` : ""}{f.estado}
                      {retenido > 0 && (
                        <span className="ml-1 rounded-full bg-ambar-fondo px-2 py-0.5 font-bold text-ambar-texto">
                          {pesos(retenido)} retenidos
                        </span>
                      )}
                    </span>
                  </span>
                  <span className="cifras text-right font-display text-sm font-bold text-primario">
                    {pesos(f.disponible)}
                  </span>
                  <span className="cifras w-28 text-right font-display text-sm font-bold">
                    {pesos(f.saldo_red)}
                  </span>
                </button>
              );
            })}
            {visibles.length === 0 && <p className="text-sm text-apagado">Sin resultados.</p>}
          </div>
        </Tarjeta>

        <Retenciones filas={retenciones} alCambiar={hecho} />
      </div>

      <aside>
        {elegido ? (
          <Panel key={elegido.uuid} fila={elegido} nivel={nivel} alHecho={hecho} />
        ) : (
          <div className="rounded-2xl border border-borde bg-white p-5 text-sm text-apagado">
            Elige un {nivel === "administrador" ? "administrador" : "cliente"} para abonarle o
            retirarle saldo.
          </div>
        )}
      </aside>
    </div>
  );
}

function Panel({ fila, nivel, alHecho }: { fila: FilaRed; nivel: Nivel; alHecho: () => void }) {
  const [modo, setModo] = useState<"abonar" | "retirar">("abonar");
  const opera = ["activo", "aprobado"].includes(fila.estado);

  return (
    <div className="rounded-2xl border border-borde bg-white p-5">
      <h3 className="font-display text-base font-bold">{fila.nombre}</h3>
      <dl className="mt-3 grid grid-cols-2 gap-3">
        <Dato t="Disponible" v={pesos(fila.disponible)} acento />
        <Dato t="Saldo red" v={pesos(fila.saldo_red)} />
      </dl>
      {nivel === "cliente" && (
        <p className="mt-2 text-xs text-apagado">
          El disponible de un cliente no incluye lo asignado a sus cajas ni lo apartado
          en ventas en vuelo.
        </p>
      )}

      <div className="mt-4 grid grid-cols-2 gap-2">
        {(["abonar", "retirar"] as const).map((m) => (
          <button key={m} type="button" onClick={() => setModo(m)} data-modo={m}
                  className={"rounded-xl border p-2.5 text-sm font-bold " +
                    (modo === m ? "border-2 border-primario bg-elegido" : "border-borde")}>
            {m === "abonar" ? "Abonar desde EPS" : "Retirar a EPS"}
          </button>
        ))}
      </div>

      {modo === "abonar" ? (
        opera || nivel === "administrador" ? (
          <Abonar fila={fila} nivel={nivel} alHecho={alHecho} />
        ) : (
          <p className="mt-4 rounded-xl bg-suave p-3 text-sm text-tinta-dos">
            {fila.nombre} está {fila.estado}: no puede recibir saldo.
          </p>
        )
      ) : (
        <Retirar fila={fila} nivel={nivel} alHecho={alHecho} />
      )}
    </div>
  );
}

function Abonar({ fila, nivel, alHecho }: { fila: FilaRed; nivel: Nivel; alHecho: () => void }) {
  const [monto, setMonto] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [hecho, setHecho] = useState("");
  const [enviando, setEnviando] = useState(false);
  const retenido = Number(fila.retenido ?? 0);
  const n = Number(monto || 0);

  async function enviar() {
    setEnviando(true);
    setError(null);
    setHecho("");
    try {
      const r = nivel === "administrador"
        ? await api.fondearAdministrador(fila.uuid, monto)
        : await api.fondearCliente(fila.uuid, monto);
      const liq = r.liquidaciones.length
        ? ` Cubrió la retención con ${r.liquidaciones.join(", ")}.` : "";
      setHecho(`Folio ${r.folio}.${liq}`);
      setMonto("");
      alHecho();
    } catch (e) {
      setError(e as ErrorAPI);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="mt-4 space-y-3">
      <Campo etiqueta="Monto a abonar" valor={monto} marcador="0.00" prueba="monto-abono"
             alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))} />
      {retenido > 0 && n > 0 && (
        <p className="rounded-xl bg-ambar-fondo p-3 text-xs text-ambar-texto">
          Tiene {pesos(retenido)} retenidos. Este abono los cubre primero: le quedan
          disponibles {pesos(Math.max(n - retenido, 0))} de este abono.
        </p>
      )}
      <p className="rounded-xl bg-elegido p-3 text-xs">
        Sale de la cuenta de fondeo de EPS{nivel === "cliente" ? ", sin margen y sin pasar por su administrador" : ""}.
        No toca el saldo de EPS en Emida. Queda registrado con tu nombre.
      </p>
      {error && <Aviso error={error} />}
      {hecho && <p className="rounded-xl bg-exito-fondo p-3 text-sm font-semibold text-exito-oscuro">{hecho}</p>}
      <button type="button" onClick={enviar} disabled={n <= 0 || enviando} data-prueba="abonar"
              className="w-full rounded-xl bg-primario py-3.5 font-display font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
        {enviando ? "Abonando…" : "Abonar"}
      </button>
    </div>
  );
}

function Retirar({ fila, nivel, alHecho }: { fila: FilaRed; nivel: Nivel; alHecho: () => void }) {
  const [monto, setMonto] = useState("");
  const [motivo, setMotivo] = useState("fondeo_excedente");
  const [nota, setNota] = useState("");
  const [folio, setFolio] = useState("");
  const [retener, setRetener] = useState(true);
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [hecho, setHecho] = useState("");
  const [enviando, setEnviando] = useState(false);

  const disponible = Number(fila.disponible ?? 0);
  const pedido = Number(monto || 0);
  const directo = Math.min(pedido, disponible);
  const faltante = Math.max(pedido - disponible, 0);
  const notaFalta = motivo === "otro" && nota.trim().length <= 3;
  const listo = pedido > 0 && !notaFalta && (faltante === 0 || retener);

  async function enviar() {
    setEnviando(true);
    setError(null);
    setHecho("");
    try {
      const r = await api.retirarAEPS({
        tipo: nivel, uuid: fila.uuid, monto, motivo, nota,
        retener_faltante: retener, folio_origen: folio || undefined,
      });
      const partes = [];
      if (r.folio) partes.push(`Se retiraron ${pesos(r.retirado)} (folio ${r.folio}).`);
      if (r.retencion) partes.push(`Quedan ${pesos(r.retencion.pendiente)} retenidos en su red.`);
      setHecho(partes.join(" "));
      setMonto("");
      setNota("");
      alHecho();
    } catch (e) {
      setError(e as ErrorAPI);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="mt-4 space-y-3">
      <Campo etiqueta="Monto a retirar" valor={monto} marcador="0.00" prueba="monto-retiro-eps"
             alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))} />
      <label className="block">
        <span className="mb-1.5 block text-xs font-bold text-tinta-dos">Motivo · obligatorio</span>
        <select value={motivo} onChange={(e) => setMotivo(e.target.value)}
                className="w-full rounded-xl border border-campo bg-white px-3 py-3 text-sm outline-none focus:border-primario">
          {MOTIVOS.map(([c, t]) => <option key={c} value={c}>{t}</option>)}
        </select>
      </label>
      <Campo etiqueta="Folio del fondeo que se corrige · opcional" valor={folio} texto
             marcador="CMP-2026-000123" alCambiar={(v) => setFolio(v.toUpperCase())} />
      <Campo etiqueta={motivo === "otro" ? 'Nota · obligatoria para "Otro"' : "Nota · opcional"}
             valor={nota} alCambiar={setNota} texto malo={notaFalta}
             marcador="Queda en la bitácora junto al folio" />

      <label className="flex items-start gap-2.5 rounded-xl border border-borde p-3 text-sm">
        <input type="checkbox" checked={retener} onChange={(e) => setRetener(e.target.checked)}
               className="mt-0.5 h-4 w-4 accent-primario" data-prueba="retener" />
        <span>
          <span className="block font-bold">Si no alcanza, retener el resto en su red</span>
          <span className="block text-xs text-apagado">
            Su red sigue vendiendo hasta llegar a lo retenido y ahí se detiene. El siguiente
            abono lo cubre.
          </span>
        </span>
      </label>

      {pedido > 0 && (
        <div className="rounded-xl bg-suave p-3 text-xs" data-prueba="vista-previa">
          <p className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-apagado">
            Qué va a pasar
          </p>
          <Linea t="Se retira ahora" v={pesos(directo)} />
          {faltante > 0 && retener &&
           faltante + Number(fila.retenido ?? 0) >= Number(fila.saldo_red ?? 0) && (
            <p className="mt-1 font-semibold text-alerta-oscuro" data-prueba="bloqueo-inmediato">
              Lo retenido alcanza todo su saldo red: su red deja de vender de inmediato hasta
              que reciba un abono.
            </p>
          )}
          {faltante > 0 && (retener
            ? <Linea t="Queda retenido en su red" v={pesos(faltante)} ambar />
            : <p className="mt-1 font-semibold text-alerta-oscuro">
                Excede el disponible en {pesos(faltante)}. Marca la retención o baja el monto.
              </p>)}
        </div>
      )}

      {error && <Aviso error={error} />}
      {hecho && <p className="rounded-xl bg-exito-fondo p-3 text-sm font-semibold text-exito-oscuro">{hecho}</p>}
      <button type="button" onClick={enviar} disabled={!listo || enviando} data-prueba="retirar-eps"
              className="w-full rounded-xl bg-ambar py-3.5 font-display font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
        {enviando ? "Aplicando…" : "Retirar"}
      </button>
    </div>
  );
}

function Retenciones({ filas, alCambiar }: { filas: Retencion[]; alCambiar: () => void }) {
  const [cancelando, setCancelando] = useState("");
  const [nota, setNota] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);

  async function cancelar(r: Retencion) {
    setError(null);
    try {
      await api.cancelarRetencion(r.uuid, nota);
      setCancelando("");
      setNota("");
      alCambiar();
    } catch (e) {
      setError(e as ErrorAPI);
    }
  }

  return (
    <Tarjeta titulo={`Retenciones activas · ${filas.length}`}>
      {filas.length === 0 ? (
        <p className="text-sm text-apagado">Ninguna red tiene saldo retenido.</p>
      ) : (
        <div className="space-y-2">
          {filas.map((r) => (
            <div key={r.uuid} className="rounded-xl border border-ambar-borde bg-ambar-fondo/40 p-3"
                 data-retencion={r.titular}>
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-bold">{r.titular}</p>
                  <p className="text-xs text-apagado">
                    {r.tipo === "administrador" ? "Administrador" : "Cliente"} · {r.motivo}
                    {r.folio_origen && ` · corrige ${r.folio_origen}`} · por {r.autorizado_por}
                  </p>
                  {r.nota && <p className="mt-1 text-xs text-tinta-dos">{r.nota}</p>}
                </div>
                <div className="text-right">
                  <p className="cifras font-display text-base font-bold text-ambar-texto">
                    {pesos(r.pendiente)}
                  </p>
                  <p className="text-[11px] text-apagado">de {pesos(r.monto)} · red {pesos(r.saldo_red)}</p>
                </div>
              </div>
              {cancelando === r.uuid ? (
                <div className="mt-3 flex flex-wrap items-end gap-2">
                  <div className="min-w-[220px] flex-1">
                    <Campo etiqueta="Por qué se perdona" valor={nota} alCambiar={setNota} texto
                           marcador="Queda en la bitácora" />
                  </div>
                  <button type="button" onClick={() => { setCancelando(""); setNota(""); }}
                          className="rounded-xl border border-campo bg-white px-4 py-3 text-sm font-bold">
                    Volver
                  </button>
                  <button type="button" disabled={nota.trim().length <= 3} onClick={() => cancelar(r)}
                          className="rounded-xl bg-alerta-oscuro px-4 py-3 text-sm font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
                    Cancelar retención
                  </button>
                </div>
              ) : (
                <button type="button" onClick={() => setCancelando(r.uuid)}
                        className="mt-2 text-xs font-bold text-alerta-oscuro underline underline-offset-2">
                  Cancelar retención…
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {error && <Aviso error={error} />}
    </Tarjeta>
  );
}

function Dato({ t, v, acento }: { t: string; v: string; acento?: boolean }) {
  return (
    <div className="rounded-xl bg-suave p-3">
      <dt className="text-[11px] font-bold uppercase tracking-wide text-apagado">{t}</dt>
      <dd className={"cifras mt-0.5 font-display text-lg font-bold " + (acento ? "text-primario" : "")}>{v}</dd>
    </div>
  );
}

function Linea({ t, v, ambar }: { t: string; v: string; ambar?: boolean }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-apagado">{t}</span>
      <span className={"cifras font-bold " + (ambar ? "text-ambar-texto" : "")}>{v}</span>
    </div>
  );
}
