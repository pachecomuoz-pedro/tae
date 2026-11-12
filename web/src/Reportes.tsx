/**
 * Transacciones y movimientos de saldo, por jerarquía.
 *
 * El servidor decide qué filas y qué columnas ve cada quien: una caja recibe
 * `saldo_despues` y `administrador` en null, y por eso esas columnas ni se
 * dibujan para ella. La pantalla no oculta datos que le llegaron: no le
 * llegan. Así un cajero curioso con las herramientas del navegador tampoco
 * ve el saldo de su cliente.
 *
 * Las dos tablas comparten filtros (fechas y búsqueda) y el botón Exportar,
 * que descarga un CSV con exactamente los mismos filtros.
 */
import { useEffect, useState } from "react";

import {
  ErrorAPI, Movimiento, Pagina, Perfil, Transaccion, api, pesos, urlExportar,
} from "./api";
import { Aviso } from "./piezas";
import { Modal } from "./Red";

const hoy = () => new Date().toISOString().slice(0, 10);
const haceDias = (n: number) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);

const fechaHora = (iso: string) =>
  new Date(iso).toLocaleString("es-MX", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });

const COLOR_ESTADO: Record<string, string> = {
  aprobada: "bg-exito-fondo text-exito-oscuro",
  rechazada: "bg-alerta-fondo text-alerta-oscuro",
  sin_resolver: "bg-ambar-fondo text-ambar-texto",
  pendiente: "bg-suave text-tinta-dos",
  duplicada: "bg-suave text-tinta-dos",
};

function useFiltros(extra: Record<string, string>) {
  const [filtros, setFiltros] = useState<Record<string, string>>({
    desde: haceDias(7), hasta: hoy(), q: "", ...extra,
  });
  const [pagina, setPagina] = useState(1);
  const poner = (k: string, v: string) => { setFiltros((f) => ({ ...f, [k]: v })); setPagina(1); };
  return { filtros, poner, pagina, setPagina };
}

function BarraFiltros({ filtros, poner, exportar, children }: {
  filtros: Record<string, string>;
  poner: (k: string, v: string) => void;
  exportar: string;
  children?: React.ReactNode;
}) {
  const campo = "rounded-xl border border-borde bg-white px-3 py-2 text-sm outline-none focus:border-primario";
  return (
    <div className="mb-4 flex flex-wrap items-end gap-2">
      <label className="text-xs font-bold text-tinta-dos">
        <span className="mb-1 block">Desde</span>
        <input type="date" value={filtros.desde} onChange={(e) => poner("desde", e.target.value)} className={campo} />
      </label>
      <label className="text-xs font-bold text-tinta-dos">
        <span className="mb-1 block">Hasta</span>
        <input type="date" value={filtros.hasta} onChange={(e) => poner("hasta", e.target.value)} className={campo} />
      </label>
      {children}
      <input value={filtros.q} onChange={(e) => poner("q", e.target.value)} aria-label="Buscar"
             placeholder="Buscar folio, teléfono, autorización, caja…"
             className={campo + " min-w-[220px] flex-1"} />
      <a href={exportar} data-prueba="exportar"
         className="rounded-xl bg-exito px-4 py-2 text-sm font-bold text-white hover:bg-exito-oscuro">
        Exportar
      </a>
    </div>
  );
}

function Paginador({ pagina, setPagina, hayMas, total, porPagina }: {
  pagina: number; setPagina: (n: number) => void; hayMas: boolean;
  total?: number; porPagina: number;
}) {
  const boton = "rounded-lg border border-borde bg-white px-3 py-1.5 text-sm font-semibold disabled:opacity-40";
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-apagado">
      <span>
        {total !== undefined
          ? `${total.toLocaleString("es-MX")} en total · página ${pagina} de ${Math.max(Math.ceil(total / porPagina), 1)}`
          : `Página ${pagina}`}
      </span>
      <div className="flex gap-2">
        <button type="button" className={boton} disabled={pagina <= 1} onClick={() => setPagina(pagina - 1)}>Anterior</button>
        <button type="button" className={boton} disabled={!hayMas} onClick={() => setPagina(pagina + 1)}>Siguiente</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Transacciones
// ---------------------------------------------------------------------------
export function Transacciones({ perfil }: { perfil: Perfil }) {
  const { filtros, poner, pagina, setPagina } = useFiltros({ estado: "" });
  const [datos, setDatos] = useState<Pagina<Transaccion> | null>(null);
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [detalle, setDetalle] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      api.transacciones({ ...filtros, pagina: String(pagina) })
        .then((d) => { setDatos(d); setError(null); })
        .catch((e) => setError(e as ErrorAPI));
    }, 250);
    return () => clearTimeout(t);
  }, [filtros, pagina]);

  const esCaja = perfil.rol === "punto_venta";
  const veAdmin = perfil.rol === "super_admin" || perfil.rol === "administrador";
  const th = "px-3 py-3 text-left text-[11px] font-bold uppercase tracking-wide text-apagado";
  const td = "px-3 py-2.5 align-top";

  return (
    <div className="rounded-2xl border border-borde bg-white p-5">
      <BarraFiltros filtros={filtros} poner={poner}
                    exportar={urlExportar("/transacciones/exportar", filtros)}>
        <label className="text-xs font-bold text-tinta-dos">
          <span className="mb-1 block">Estado</span>
          <select value={filtros.estado} onChange={(e) => poner("estado", e.target.value)}
                  className="rounded-xl border border-borde bg-white px-3 py-2 text-sm">
            <option value="">Todas</option>
            <option value="aprobada">Aprobadas</option>
            <option value="rechazada">Rechazadas</option>
            <option value="sin_resolver">Sin resolver</option>
          </select>
        </label>
      </BarraFiltros>
      {error && <Aviso error={error} />}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-sm" data-prueba="tabla-transacciones">
          <thead className="border-b border-borde bg-suave">
            <tr>
              <th className={th}>Fecha</th>
              <th className={th}>Producto</th>
              <th className={th + " text-right"}>Monto</th>
              <th className={th}>Teléfono</th>
              <th className={th}>Estado</th>
              <th className={th}>Autorización</th>
              <th className={th}>Folio</th>
              {!esCaja && <th className={th}>Punto de venta</th>}
              {veAdmin && <th className={th}>Cliente</th>}
              {!esCaja && <th className={th + " text-right"}>Saldo</th>}
              <th className={th}></th>
            </tr>
          </thead>
          <tbody>
            {datos?.filas.map((t) => (
              <tr key={t.folio} className="border-b border-borde/70 hover:bg-suave/50">
                <td className={td + " whitespace-nowrap text-xs"}>{fechaHora(t.fecha)}</td>
                <td className={td}>
                  <span className="block font-semibold">{t.producto}</span>
                  <span className="block text-xs text-apagado">{t.compania}</span>
                </td>
                <td className={td + " cifras text-right font-display font-bold"}>{pesos(t.monto)}</td>
                <td className={td + " cifras"}>{t.telefono}</td>
                <td className={td}>
                  <span className={"rounded-full px-2.5 py-1 text-xs font-bold " + (COLOR_ESTADO[t.estado] ?? "")}>
                    {t.estado_texto}
                  </span>
                </td>
                <td className={td + " cifras text-xs"}>
                  {t.autorizacion_carrier && <span className="block">Carrier {t.autorizacion_carrier}</span>}
                  {t.autorizacion_emida && <span className="block text-apagado">Emida {t.autorizacion_emida}</span>}
                  {!t.autorizacion_carrier && !t.autorizacion_emida && <span className="text-apagado">—</span>}
                </td>
                <td className={td + " cifras text-xs"}>{t.folio}</td>
                {!esCaja && <td className={td}>{t.punto_venta}</td>}
                {veAdmin && <td className={td}>{t.cliente}</td>}
                {!esCaja && (
                  <td className={td + " cifras text-right"}>{t.saldo_despues ? pesos(t.saldo_despues) : "—"}</td>
                )}
                <td className={td}>
                  <button type="button" onClick={() => setDetalle(t.folio)} aria-label="Ver detalle"
                          data-prueba="ver-detalle" title="Ver detalle"
                          className="rounded-lg border border-borde px-2 py-1 text-apagado hover:border-primario hover:text-primario">
                    <Ojo />
                  </button>
                </td>
              </tr>
            ))}
            {datos && datos.filas.length === 0 && (
              <tr><td colSpan={11} className="px-4 py-10 text-center text-apagado">
                Sin transacciones en ese periodo.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      {datos && (
        <Paginador pagina={pagina} setPagina={setPagina} total={datos.total}
                   porPagina={datos.por_pagina}
                   hayMas={pagina * datos.por_pagina < (datos.total ?? 0)} />
      )}
      {detalle && <DetalleTransaccion folio={detalle} alCerrar={() => setDetalle(null)} />}
    </div>
  );
}

function DetalleTransaccion({ folio, alCerrar }: { folio: string; alCerrar: () => void }) {
  const [t, setT] = useState<Transaccion | null>(null);
  const [error, setError] = useState<ErrorAPI | null>(null);
  useEffect(() => { api.transaccion(folio).then(setT).catch((e) => setError(e as ErrorAPI)); }, [folio]);

  const Dato = ({ e, v }: { e: string; v?: string | number | null }) =>
    v === null || v === undefined || v === "" ? null : (
      <div className="rounded-xl bg-suave px-3 py-2">
        <dt className="text-[11px] font-bold uppercase tracking-wide text-apagado">{e}</dt>
        <dd className="cifras mt-0.5 break-words text-sm font-semibold">{v}</dd>
      </div>
    );

  return (
    <Modal titulo={`Transacción ${folio}`} alCerrar={alCerrar}>
      {error && <Aviso error={error} />}
      {!t && !error && <p className="text-sm text-apagado">Cargando…</p>}
      {t && (
        <div className="space-y-3" data-prueba="detalle-transaccion">
          <div className="flex items-center justify-between">
            <span className={"rounded-full px-3 py-1 text-sm font-bold " + (COLOR_ESTADO[t.estado] ?? "")}>
              {t.estado_texto}
            </span>
            <span className="cifras font-display text-xl font-bold">{pesos(t.monto)}</span>
          </div>
          <dl className="grid grid-cols-2 gap-2">
            <Dato e="Fecha" v={fechaHora(t.fecha)} />
            <Dato e="Producto" v={`${t.producto} · ${t.compania}`} />
            <Dato e="Teléfono" v={t.telefono} />
            <Dato e="Punto de venta" v={t.punto_venta} />
            <Dato e="Cliente" v={t.cliente} />
            <Dato e="Administrador" v={t.administrador} />
            <Dato e="Autorización carrier (CarrierControlNo)" v={t.autorizacion_carrier} />
            <Dato e="Autorización Emida (ControlNo)" v={t.autorizacion_emida} />
            <Dato e="Transacción Emida" v={t.transaccion_emida} />
            <Dato e="Folio interno" v={t.folio} />
            <Dato e="ResponseCode" v={t.response_code} />
            <Dato e="H2H Result Code" v={t.h2h_result_code} />
            <Dato e="Invoice-No" v={t.invoice_no} />
            <Dato e="Saldo del cliente después" v={t.saldo_despues ? pesos(t.saldo_despues) : null} />
            <Dato e="PIN" v={t.pin} />
          </dl>
          {t.mensaje && (
            <div>
              <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-apagado">
                Mensaje del proveedor (ResponseMessage)
              </p>
              <p className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-xl bg-suave p-3 text-sm">
                {t.mensaje}
              </p>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function Ojo() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" /><circle cx="12" cy="12" r="3" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Movimientos de saldo
// ---------------------------------------------------------------------------
export function Movimientos({ perfil }: { perfil: Perfil }) {
  const { filtros, poner, pagina, setPagina } = useFiltros({ tipo: "" });
  const [datos, setDatos] = useState<Pagina<Movimiento> | null>(null);
  const [error, setError] = useState<ErrorAPI | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      api.movimientos({ ...filtros, pagina: String(pagina) })
        .then((d) => { setDatos(d); setError(null); })
        .catch((e) => setError(e as ErrorAPI));
    }, 250);
    return () => clearTimeout(t);
  }, [filtros, pagina]);

  const esCaja = perfil.rol === "punto_venta";
  const th = "px-3 py-3 text-left text-[11px] font-bold uppercase tracking-wide text-apagado";
  const td = "px-3 py-2.5 align-top";

  return (
    <div className="rounded-2xl border border-borde bg-white p-5">
      <BarraFiltros filtros={filtros} poner={poner}
                    exportar={urlExportar("/movimientos/exportar", filtros)}>
        {!esCaja && (
          <label className="text-xs font-bold text-tinta-dos">
            <span className="mb-1 block">Tipo</span>
            <select value={filtros.tipo} onChange={(e) => poner("tipo", e.target.value)}
                    className="rounded-xl border border-borde bg-white px-3 py-2 text-sm">
              <option value="">Todos</option>
              <option value="CMP">Fondeos</option>
              <option value="TRS">Traspasos</option>
              <option value="DEV">Devoluciones</option>
              <option value="RET">Retiros a EPS</option>
              <option value="AJU">Ajustes</option>
              <option value="limite">Cajas (asignaciones y retiros)</option>
            </select>
          </label>
        )}
      </BarraFiltros>
      {error && <Aviso error={error} />}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-sm" data-prueba="tabla-movimientos">
          <thead className="border-b border-borde bg-suave">
            <tr>
              <th className={th}>Fecha</th>
              <th className={th}>Concepto</th>
              <th className={th}>Afectado</th>
              <th className={th}>Autorizó</th>
              <th className={th + " text-right"}>Saldo anterior</th>
              <th className={th + " text-right"}>Movimiento</th>
              <th className={th + " text-right"}>Saldo actual</th>
              <th className={th}>Folio</th>
            </tr>
          </thead>
          <tbody>
            {datos?.filas.map((m, i) => (
              <tr key={`${m.fecha}-${m.folio}-${m.afectado}-${i}`} className="border-b border-borde/70">
                <td className={td + " whitespace-nowrap text-xs"}>{fechaHora(m.fecha)}</td>
                <td className={td}>
                  <span className="block font-semibold">{m.concepto}</span>
                  {(m.motivo || m.nota) && (
                    <span className="block text-xs text-apagado">{[m.motivo, m.nota].filter(Boolean).join(" · ")}</span>
                  )}
                </td>
                <td className={td}>
                  <span className="block">{m.afectado}</span>
                  <span className="block text-xs text-apagado">{m.nivel}</span>
                </td>
                <td className={td + " text-xs"}>{m.autorizado_por || "—"}</td>
                <td className={td + " cifras text-right"}>{pesos(m.saldo_anterior)}</td>
                <td className={td + " cifras text-right font-display font-bold " +
                  (m.movimiento === "abono" ? "text-exito" : "text-alerta-oscuro")}>
                  {m.movimiento === "abono" ? "+" : "−"}{pesos(Math.abs(Number(m.monto)))}
                </td>
                <td className={td + " cifras text-right font-semibold"}>{pesos(m.saldo_actual)}</td>
                <td className={td + " cifras text-xs"}>{m.folio || "—"}</td>
              </tr>
            ))}
            {datos && datos.filas.length === 0 && (
              <tr><td colSpan={8} className="px-4 py-10 text-center text-apagado">
                Sin movimientos en ese periodo.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      {datos && (
        <Paginador pagina={pagina} setPagina={setPagina} hayMas={!!datos.hay_mas}
                   porPagina={datos.por_pagina} />
      )}
      {esCaja && (
        <p className="mt-3 text-xs text-apagado">
          Aquí ves cada vez que tu cliente te asignó o te retiró saldo.
        </p>
      )}
    </div>
  );
}
