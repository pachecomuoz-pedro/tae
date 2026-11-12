/**
 * Pantallas del administrador: sus clientes, el traspaso y el retiro.
 *
 * Dos cosas que la pantalla tiene que dejar ver sin que nadie las explique:
 *
 * 1. **En el traspaso, los dos montos.** El cliente paga uno y recibe otro,
 *    porque el margen se aplica desde la compra. Enseñar solo uno hace que
 *    alguien capture mal y luego haya que corregirlo con un ajuste.
 *
 * 2. **En el retiro, de dónde sale el dinero.** El saldo de un cliente no
 *    es lo retirable: hay dinero comprometido en los límites de sus cajas y
 *    en ventas sin cerrar. Sin el desglose, el administrador intenta
 *    retirar y el servidor le dice que no, sin que entienda por qué.
 */
import { useEffect, useState } from "react";

import { ClienteFila, ErrorAPI, api, nuevaLlave, pesos } from "./api";
import { Aviso, Campo, Tarjeta } from "./piezas";

const MOTIVOS = [
  ["captura", "Error de captura en el monto"],
  ["cliente_equivocado", "Traspaso a cliente equivocado"],
  ["solicitada", "Devolución solicitada por el cliente"],
  ["conciliacion", "Ajuste por conciliación con el proveedor"],
  ["suspension", "Recuperación de saldo tras suspensión"],
  ["otro", "Otro"],
] as const;

export function Clientes({ modo, alCambiar }: {
  modo: "traspaso" | "retiro";
  alCambiar: () => void;
}) {
  const [filas, setFilas] = useState<ClienteFila[]>([]);
  const [elegido, setElegido] = useState<ClienteFila | null>(null);

  const cargar = () => api.clientes().then(setFilas).catch(() => setFilas([]));
  useEffect(() => { cargar(); }, []);

  function refrescar() {
    cargar();
    alCambiar();
    if (elegido) {
      api.clientes().then((cs) =>
        setElegido(cs.find((c) => c.uuid === elegido.uuid) ?? null),
      );
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_400px]">
      <Tarjeta titulo="Mis clientes">
        <div className="space-y-2">
          {filas.map((c) => {
            const activo = elegido?.uuid === c.uuid;
            return (
              <button
                key={c.uuid}
                type="button"
                data-cliente={c.nombre}
                onClick={() => setElegido(c)}
                className={
                  "flex w-full items-center gap-3 rounded-xl border p-3 text-left transition " +
                  (activo
                    ? "border-2 border-primario bg-elegido"
                    : "border-borde bg-white hover:border-campo")
                }
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-bold">{c.nombre}</span>
                  <span className="block text-xs text-apagado">
                    Margen {c.margen_pct}% · {c.puntos_venta} PDV · {c.estado}
                  </span>
                </span>
                <span className="shrink-0 text-right">
                  <span className="block text-[11px] text-apagado">Bolsa</span>
                  <span className="cifras block font-display text-sm font-bold">
                    {pesos(c.saldo)}
                  </span>
                </span>
              </button>
            );
          })}
          {filas.length === 0 && (
            <p className="text-sm text-apagado">No hay clientes bajo tu alcance.</p>
          )}
        </div>
      </Tarjeta>

      <aside>
        {!elegido && (
          <div className="rounded-2xl border border-borde bg-white p-5 text-sm text-apagado">
            Elige un cliente de la lista.
          </div>
        )}
        {elegido && modo === "traspaso" && (
          <Traspaso cliente={elegido} alHecho={refrescar} />
        )}
        {elegido && modo === "retiro" && (
          <Retiro cliente={elegido} alHecho={refrescar} />
        )}
      </aside>
    </div>
  );
}

function Traspaso({ cliente, alHecho }: { cliente: ClienteFila; alHecho: () => void }) {
  const [monto, setMonto] = useState("");
  const [codigo, setCodigo] = useState("");
  const [pideCodigo, setPideCodigo] = useState(false);
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [folio, setFolio] = useState("");
  // Una llave por intención: si pide el segundo factor y se reenvía, es la
  // misma operación y conserva la llave; un monto nuevo es otra intención.
  const [llave, setLlave] = useState(nuevaLlave);
  const [enviando, setEnviando] = useState(false);

  const paga = Number(monto || 0);
  const margen = Number(cliente.margen_pct || 0);
  const bonificacion = (paga * margen) / 100;
  const acredita = paga + bonificacion;

  async function enviar() {
    setError(null);
    setFolio("");
    if (enviando) return;
    setEnviando(true);
    try {
      const r = await api.traspasar(cliente.uuid, monto, codigo || undefined, llave);
      setFolio(r.repetida ? `${r.folio} (ya se había aplicado)` : r.folio);
      setMonto("");
      setCodigo("");
      setPideCodigo(false);
      setLlave(nuevaLlave());
      alHecho();
    } catch (e) {
      const fallo = e as ErrorAPI;
      if (fallo.clave === "segundo_factor") setPideCodigo(true);
      setError(fallo);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="rounded-2xl border border-borde bg-white p-5">
      <h3 className="font-display text-base font-bold">Traspaso a {cliente.nombre}</h3>

      <div className="mt-4">
        <Campo
          etiqueta="Monto que paga el cliente"
          valor={monto}
          alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))}
          marcador="0.00"
          prueba="monto-traspaso"
        />
      </div>

      <dl className="mt-4 space-y-1.5 text-sm">
        <Renglon t="Paga" v={pesos(paga)} />
        <Renglon t={`Margen ${cliente.margen_pct}%`} v={`+ ${pesos(bonificacion)}`} verde />
      </dl>

      <div className="mt-3 rounded-xl bg-elegido p-4">
        <p className="text-[11px] font-bold uppercase tracking-wide">
          Se acredita a su bolsa
        </p>
        <p className="cifras mt-1 font-display text-3xl font-bold text-primario">
          {pesos(acredita)}
        </p>
      </div>

      {pideCodigo && (
        <div className="mt-4">
          <Campo
            etiqueta="Tu código de seis dígitos"
            valor={codigo}
            alCambiar={(v) => setCodigo(v.replace(/\D/g, "").slice(0, 6))}
            marcador="000000"
            prueba="codigo-traspaso"
          />
        </div>
      )}

      {error && <Aviso error={error} />}
      {folio && (
        <p className="mt-4 rounded-xl bg-exito-fondo p-3 text-sm font-semibold text-exito-oscuro" data-prueba="folio-traspaso">
          Aplicado · {folio}
        </p>
      )}

      <button
        type="button"
        onClick={enviar}
        disabled={paga <= 0 || enviando}
        data-prueba="traspasar"
        className={
          "mt-4 w-full rounded-xl py-3.5 font-display font-bold " +
          (paga > 0 ? "bg-primario text-white" : "cursor-not-allowed bg-[#D3D9E1] text-[#7A8699]")
        }
      >
        Autorizar traspaso
      </button>
    </div>
  );
}

function Retiro({ cliente, alHecho }: { cliente: ClienteFila; alHecho: () => void }) {
  const [tipo, setTipo] = useState<"devolucion" | "ajuste">("devolucion");
  const [monto, setMonto] = useState("");
  const [motivo, setMotivo] = useState("");
  const [nota, setNota] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [folio, setFolio] = useState("");
  const [llave, setLlave] = useState(nuevaLlave);
  const [enviando, setEnviando] = useState(false);

  const retirable = Number(cliente.retirable || 0);
  const pedido = Number(monto || 0);
  const excede = pedido > retirable;
  const notaObligatoria = motivo === "otro";
  const listo =
    pedido > 0 && !excede && motivo !== "" && (!notaObligatoria || nota.trim().length > 3);

  async function enviar() {
    setError(null);
    setFolio("");
    if (enviando) return;
    setEnviando(true);
    try {
      const r = await api.retirar(cliente.uuid, monto, motivo, tipo, nota, llave);
      setFolio(r.repetida ? `${r.folio} (ya se había aplicado)` : r.folio);
      setMonto("");
      setNota("");
      setLlave(nuevaLlave());
      alHecho();
    } catch (e) {
      setError(e as ErrorAPI);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="rounded-2xl border border-borde bg-white p-5">
      <h3 className="font-display text-base font-bold">Retiro de {cliente.nombre}</h3>

      {/* De dónde sale: el saldo no es lo retirable. */}
      <div className="mt-4 rounded-xl bg-suave p-3">
        <p className="text-[11px] font-bold uppercase tracking-wide text-apagado">
          De dónde sale
        </p>
        <dl className="mt-2 space-y-1 text-xs">
          <Renglon t="Bolsa actual" v={pesos(cliente.saldo)} />
          <Renglon t="− Topes de cajas" v={pesos(cliente.limites_asignados)} ambar />
          <Renglon t="− Reservas activas" v={pesos(cliente.reservado)} ambar />
        </dl>
        <div className="mt-2 border-t border-borde pt-2">
          <Renglon t="Retirable" v={pesos(cliente.retirable)} acento negrita />
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2">
        {(["devolucion", "ajuste"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTipo(t)}
            data-tipo={t}
            className={
              "rounded-xl border p-3 text-left text-xs transition " +
              (tipo === t ? "border-2 border-primario bg-elegido" : "border-borde")
            }
          >
            <span className="block font-display text-sm font-bold">
              {t === "devolucion" ? "Devolución" : "Ajuste"}
            </span>
            <span className="mt-1 block text-apagado">
              {t === "devolucion"
                ? "Revierte compra y margen en proporción"
                : "Diferencia exacta, sin recalcular margen"}
            </span>
          </button>
        ))}
      </div>

      <div className="mt-4 space-y-3">
        <Campo
          etiqueta="Monto a retirar"
          valor={monto}
          alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))}
          marcador="0.00"
          prueba="monto-retiro"
          malo={excede}
        />
        <label className="block">
          <span className="mb-1.5 block text-xs font-bold text-tinta-dos">
            Motivo · obligatorio
          </span>
          <select
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
            data-prueba="motivo"
            className="w-full rounded-xl border border-campo px-3 py-3 text-sm outline-none focus:border-primario"
          >
            <option value="">Selecciona un motivo…</option>
            {MOTIVOS.map(([clave, texto]) => (
              <option key={clave} value={clave}>{texto}</option>
            ))}
          </select>
        </label>
        <Campo
          etiqueta={notaObligatoria ? 'Nota · obligatoria para "Otro"' : "Nota · opcional"}
          valor={nota}
          alCambiar={setNota}
          marcador="Queda en la bitácora junto al folio"
          prueba="nota"
          malo={notaObligatoria && nota.trim().length <= 3}
          texto
        />
      </div>

      {excede && (
        <p className="mt-3 rounded-xl bg-alerta-fondo p-3 text-xs font-semibold text-alerta-oscuro">
          Excede lo retirable en {pesos(pedido - retirable)}. Baja los topes de
          sus cajas antes de retirar.
        </p>
      )}

      {error && <Aviso error={error} />}
      {folio && (
        <p className="mt-4 rounded-xl bg-exito-fondo p-3 text-sm font-semibold text-exito-oscuro" data-prueba="folio-retiro">
          Aplicado · {folio}
        </p>
      )}

      <button
        type="button"
        onClick={enviar}
        disabled={!listo || enviando}
        data-prueba="retirar"
        className={
          "mt-4 w-full rounded-xl py-3.5 font-display font-bold " +
          (listo ? "bg-ambar text-white" : "cursor-not-allowed bg-[#D3D9E1] text-[#7A8699]")
        }
      >
        {tipo === "devolucion" ? "Autorizar devolución" : "Aplicar ajuste"}
      </button>
    </div>
  );
}

function Renglon({ t, v, acento, verde, ambar, negrita }: {
  t: string; v: string; acento?: boolean; verde?: boolean; ambar?: boolean; negrita?: boolean;
}) {
  const color = acento ? "text-primario" : verde ? "text-exito" : ambar ? "text-ambar-texto" : "";
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-apagado">{t}</dt>
      <dd className={`cifras ${negrita ? "font-bold" : "font-semibold"} ${color}`}>{v}</dd>
    </div>
  );
}
