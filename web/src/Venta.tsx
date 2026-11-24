/**
 * Venta de tiempo aire (v2: nueve botones de compañía).
 *
 * Diseño de referencia: canvas "Punto de Venta TAE — Mockup navegable",
 * pantalla "Tiempo aire — v2 (9 compañías)".
 *
 * Decisiones que no son de estilo:
 *
 * 1. **Nueve botones fijos.** Las 8 compañías con menor `carrier_orden`
 *    ocupan siempre el mismo lugar y la novena casilla agrupa al resto
 *    ("Otros", con buscador). El cajero memoriza la posición y vende sin
 *    leer. El orden lo decide una persona en el catálogo, no este archivo.
 *    Teclas 1 a 9 como atajo (la cuadrícula coincide con el teclado numérico).
 *
 * 2. **Compañía → familia → monto, sin listas desplegables.** Con el
 *    catálogo real de Emida una cuadrícula de todos los productos pasa de
 *    cien botones; agrupar mantiene la pantalla en una sola vista.
 *
 * 3. **El número se captura dos veces y se lee en voz alta antes de
 *    cobrar.** Una recarga a un número equivocado no se puede revertir.
 *
 * 4. **Si la caja no puede vender, se dice antes** (sin límite, suspendida,
 *    saldo retenido), no al final de la captura.
 *
 * 5. **Los tres desenlaces se ven distintos.** `sin_resolver` no se pinta
 *    como aprobada ni como rechazada: si se viera aprobada, el cajero cobra
 *    tiempo aire que quizá no salió; si se viera rechazada, vuelve a vender
 *    y el cliente paga dos veces.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { ErrorAPI, Producto, ResultadoVenta, Saldo, api, pesos } from "./api";
import { Aviso, Campo } from "./piezas";

type Paso = "captura" | "confirma" | "enviando" | "cerrada";

/** Cuántas compañías van como botón propio; la siguiente casilla es "Otros". */
const PRINCIPALES = 8;

const soloDigitos = (s: string) => s.replace(/\D/g, "").slice(0, 10);

const agrupar = (n: string) =>
  n.length === 10 ? `${n.slice(0, 2)} ${n.slice(2, 6)} ${n.slice(6)}` : n;

const sinAcentos = (s: string) =>
  s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** Iniciales para el mosaico de la compañía: "Virgin Mobile" → "VM". */
const iniciales = (n: string) => {
  const partes = n.replace(/[^A-Za-zÁÉÍÓÚÑáéíóúñ0-9& ]/g, "").split(/\s+/).filter(Boolean);
  if (partes.length >= 2) return (partes[0][0] + partes[1][0]).toUpperCase();
  return n.slice(0, 2).toUpperCase();
};

const COLORES = ["#0A6CB4", "#17386E", "#A8251C", "#B35C0A", "#0E5C98", "#2A5FA0"];

type Compania = { nombre: string; orden: number; familias: string[] };

export function Venta({ saldo, alCambiarSaldo }: {
  saldo: Saldo | null;
  alCambiarSaldo: () => void;
}) {
  const [productos, setProductos] = useState<Producto[]>([]);
  const [compania, setCompania] = useState<string | null>(null);
  const [otrosAbierto, setOtrosAbierto] = useState(false);
  const [busqueda, setBusqueda] = useState("");
  const [familia, setFamilia] = useState<string | null>(null);
  const [elegido, setElegido] = useState<Producto | null>(null);
  const [numero, setNumero] = useState("");
  const [confirmacion, setConfirmacion] = useState("");
  const [monto, setMonto] = useState("");
  const [paso, setPaso] = useState<Paso>("captura");
  const [resultado, setResultado] = useState<ResultadoVenta | null>(null);
  const [error, setError] = useState<ErrorAPI | null>(null);
  const campoNumero = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.catalogo("tae").then(setProductos).catch(() => setProductos([]));
  }, []);

  // Compañías en el orden del catálogo, con sus familias en orden de aparición.
  const companias = useMemo<Compania[]>(() => {
    const mapa = new Map<string, Compania>();
    for (const p of productos) {
      const c = mapa.get(p.carrier) ?? { nombre: p.carrier, orden: p.carrier_orden ?? 100, familias: [] };
      const fam = p.familia || "Tiempo aire";
      if (!c.familias.includes(fam)) c.familias.push(fam);
      mapa.set(p.carrier, c);
    }
    return [...mapa.values()].sort((a, b) => a.orden - b.orden || a.nombre.localeCompare(b.nombre));
  }, [productos]);

  // Si caben las nueve, no hace falta "Otros".
  const hayOtros = companias.length > PRINCIPALES + 1;
  const principales = hayOtros ? companias.slice(0, PRINCIPALES) : companias;
  const otras = hayOtros ? companias.slice(PRINCIPALES) : [];
  const esOtra = Boolean(compania) && otras.some((c) => c.nombre === compania);
  const actual = companias.find((c) => c.nombre === compania) ?? null;

  const color = (nombre: string) => {
    const i = companias.findIndex((c) => c.nombre === nombre);
    return i >= 0 && i < PRINCIPALES ? COLORES[i % COLORES.length] : "#5A6C85";
  };

  const delGrupo = productos.filter(
    (p) => p.carrier === compania && (p.familia || "Tiempo aire") === familia,
  );

  const montoFinal = elegido?.tipo_monto === "fijo" ? elegido.monto_fijo ?? "" : monto;
  const numeroCompleto = numero.length === 10;
  const coinciden = numeroCompleto && numero === confirmacion;
  const bloqueo = saldo?.venta && !saldo.venta.puede ? saldo.venta.motivo : "";
  const cajaDisponible = saldo?.mi_caja?.disponible ? Number(saldo.mi_caja.disponible) : null;
  const noAlcanza = cajaDisponible !== null && Number(montoFinal) > cajaDisponible;
  const listo = Boolean(elegido) && coinciden && Number(montoFinal) > 0 && !bloqueo && !noAlcanza;

  function elegirCompania(nombre: string) {
    const c = companias.find((x) => x.nombre === nombre);
    setCompania(nombre);
    setOtrosAbierto(false);
    setBusqueda("");
    // La primera familia (normalmente Tiempo aire) queda elegida: es la que más se vende.
    setFamilia(c ? c.familias[0] ?? null : null);
    setElegido(null);
    setMonto("");
    setError(null);
  }

  function abrirOtros() {
    setOtrosAbierto(true);
    setCompania(null);
    setFamilia(null);
    setElegido(null);
    setMonto("");
  }

  function reiniciar() {
    setCompania(null);
    setOtrosAbierto(false);
    setBusqueda("");
    setFamilia(null);
    setElegido(null);
    setNumero("");
    setConfirmacion("");
    setMonto("");
    setResultado(null);
    setError(null);
    setPaso("captura");
  }

  async function enviar() {
    if (!elegido) return;
    setPaso("enviando");
    setError(null);
    try {
      const r = await api.vender(elegido.sku, numero, montoFinal || undefined);
      setResultado(r);
      setPaso("cerrada");
      alCambiarSaldo();
    } catch (e) {
      setError(e as ErrorAPI);
      setPaso("captura");
      alCambiarSaldo();
    }
  }

  // Atajo: teclas 1 a 9 eligen la casilla. Nunca mientras se escribe en un campo.
  useEffect(() => {
    if (paso !== "captura") return;
    function alTeclear(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const n = Number(e.key);
      if (!Number.isInteger(n) || n < 1 || n > 9) return;
      if (n <= principales.length) {
        elegirCompania(principales[n - 1].nombre);
      } else if (n === 9 && hayOtros) {
        abrirOtros();
      }
    }
    window.addEventListener("keydown", alTeclear);
    return () => window.removeEventListener("keydown", alTeclear);
  });

  if (paso === "cerrada" && resultado) {
    return <Cierre resultado={resultado} numero={numero} alSeguir={reiniciar} />;
  }

  const filtradas = otras.filter(
    (c) => busqueda.trim() === "" || sinAcentos(c.nombre).includes(sinAcentos(busqueda.trim())),
  );
  const numFamilia = actual && actual.familias.length > 1 ? 3 : 2;

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <div className="space-y-5">
        {bloqueo && (
          <div className="rounded-2xl border border-[#F3C9C6] bg-alerta-fondo p-5" data-prueba="bloqueo">
            <p className="font-display text-base font-bold text-alerta-oscuro">
              Esta caja no puede vender ahora
            </p>
            <p className="mt-1 text-sm text-alerta-oscuro">{bloqueo}</p>
          </div>
        )}

        {/* 1 · Compañía */}
        <section>
          <div className="mb-2.5 flex items-baseline justify-between">
            <h2 className="text-[11px] font-bold uppercase tracking-wide text-apagado">1 · Compañía</h2>
            <span className="hidden text-xs text-apagado lg:inline">Atajo: teclas 1 a 9</span>
          </div>
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
            {principales.map((c, i) => (
              <BotonCompania
                key={c.nombre}
                nombre={c.nombre}
                detalle={c.familias.length > 1 ? `${c.familias.length} tipos de recarga` : c.familias[0]}
                tecla={String(i + 1)}
                color={color(c.nombre)}
                activo={compania === c.nombre}
                alElegir={() => elegirCompania(c.nombre)}
              />
            ))}
            {hayOtros && (
              <BotonCompania
                nombre={esOtra && compania ? compania : "Otros"}
                detalle={esOtra ? "Otras compañías" : `${otras.length} compañías más`}
                tecla="9"
                color="#5A6C85"
                mosaico="···"
                activo={otrosAbierto || esOtra}
                alElegir={abrirOtros}
              />
            )}
          </div>
          {productos.length === 0 && (
            <p className="mt-3 text-sm text-apagado">No hay productos disponibles ahora mismo.</p>
          )}
        </section>

        {otrosAbierto && !compania && (
          <section className="rounded-2xl border border-borde bg-white p-4" data-prueba="otros">
            <div className="flex items-center justify-between">
              <h3 className="font-display text-[15px] font-bold">Otras compañías</h3>
              <button type="button" onClick={() => setOtrosAbierto(false)}
                      className="p-1 text-sm font-bold text-primario">Cerrar</button>
            </div>
            <label className="mt-3 block">
              <span className="sr-only">Buscar compañía</span>
              <input
                autoFocus
                value={busqueda}
                onChange={(e) => setBusqueda(e.target.value)}
                placeholder="Escribe el nombre…"
                data-prueba="buscar-compania"
                className="w-full rounded-xl border border-campo px-3.5 py-3 text-sm outline-none focus:border-primario"
              />
            </label>
            <div className="mt-3 grid max-h-52 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3">
              {filtradas.map((c) => (
                <button key={c.nombre} type="button" onClick={() => elegirCompania(c.nombre)}
                        data-carrier={c.nombre}
                        className="flex items-center gap-2.5 rounded-xl border border-borde bg-white px-3 py-2 text-left hover:border-campo">
                  <Mosaico texto={iniciales(c.nombre)} color="#5A6C85" chico />
                  <span className="truncate text-sm font-semibold">{c.nombre}</span>
                </button>
              ))}
            </div>
            {filtradas.length === 0 && (
              <p className="mt-2 text-sm text-apagado">No hay compañías con ese nombre.</p>
            )}
          </section>
        )}

        {/* 2 · Tipo de recarga (solo si la compañía tiene más de uno) */}
        {actual && actual.familias.length > 1 && (
          <section>
            <h2 className="mb-2.5 text-[11px] font-bold uppercase tracking-wide text-apagado">2 · Tipo de recarga</h2>
            <div className="flex flex-wrap gap-2">
              {actual.familias.map((f) => (
                <button key={f} type="button" aria-pressed={familia === f}
                        onClick={() => { setFamilia(f); setElegido(null); setMonto(""); }}
                        data-familia={f}
                        className={
                          "rounded-full px-[18px] py-2.5 text-sm font-bold transition " +
                          (familia === f
                            ? "border-2 border-primario bg-primario text-white"
                            : "border border-campo bg-white text-tinta hover:border-primario")
                        }>
                  {f}
                </button>
              ))}
            </div>
          </section>
        )}

        {/* Monto: un botón por producto */}
        {actual && familia && (
          <section>
            <h2 className="mb-2.5 text-[11px] font-bold uppercase tracking-wide text-apagado">
              {numFamilia} · Monto
            </h2>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
              {delGrupo.map((p) => {
                const activo = elegido?.sku === p.sku;
                return (
                  <button
                    key={p.sku}
                    type="button"
                    onClick={() => { setElegido(p); setMonto(""); campoNumero.current?.focus(); }}
                    data-sku={p.sku}
                    title={p.nombre}
                    aria-pressed={activo}
                    className={
                      "min-h-[52px] rounded-xl px-2 font-display text-lg font-bold transition " +
                      (activo
                        ? "border-2 border-primario bg-elegido"
                        : "border border-borde bg-white hover:border-campo")
                    }
                  >
                    {p.tipo_monto === "fijo" ? p.etiqueta_monto.replace(/\.00$/, "") : "Otro monto"}
                  </button>
                );
              })}
            </div>
            {elegido?.tipo_monto === "rango" && (
              <div className="mt-4 max-w-[240px]">
                <Campo
                  etiqueta={`Monto · de ${pesos(elegido.monto_min)} a ${pesos(elegido.monto_max)}`}
                  valor={monto}
                  alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))}
                  marcador="0.00"
                  prueba="monto"
                />
              </div>
            )}
          </section>
        )}

        {!actual && !otrosAbierto && productos.length > 0 && (
          <div className="flex min-h-[140px] items-center justify-center rounded-2xl border border-dashed border-campo p-6 text-center">
            <p className="max-w-sm text-sm text-apagado">
              Elige la compañía. Aquí aparecen sus tipos de recarga y los montos.
            </p>
          </div>
        )}
      </div>

      <aside className="space-y-4">
        <div className="rounded-2xl border border-borde bg-white p-5">
          <div className="flex items-center justify-between">
            <h3 className="font-display text-base font-bold">Venta en curso</h3>
            <span className="text-xs font-semibold text-apagado">
              {paso === "confirma" || paso === "enviando" ? "Paso 2 de 2" : "Paso 1 de 2"}
            </span>
          </div>

          {paso === "captura" && (
            <>
              <dl className="mt-4 space-y-2 rounded-xl bg-lienzo p-3.5 text-sm">
                <Renglon termino="Compañía" valor={compania ?? "—"} />
                <Renglon termino="Producto" valor={elegido?.nombre ?? "—"} />
                <Renglon termino="Monto" valor={Number(montoFinal) > 0 ? pesos(montoFinal) : "—"} />
              </dl>

              <div className="mt-4 space-y-3">
                <Campo
                  etiqueta="Número telefónico"
                  refCampo={campoNumero}
                  valor={numero}
                  alCambiar={(v) => setNumero(soloDigitos(v))}
                  marcador="10 dígitos"
                  prueba="numero"
                />
                <Campo
                  etiqueta="Confirma el número"
                  valor={confirmacion}
                  alCambiar={(v) => setConfirmacion(soloDigitos(v))}
                  marcador="Escríbelo otra vez"
                  prueba="confirmacion"
                  malo={confirmacion.length > 0 && !numero.startsWith(confirmacion)}
                />
              </div>
              {confirmacion.length > 0 && !numero.startsWith(confirmacion) && (
                <p className="mt-2 text-xs font-semibold text-alerta">
                  Los números no coinciden. Una recarga a un número equivocado no
                  se puede revertir.
                </p>
              )}
              {coinciden && (
                <p className="mt-2 text-xs font-semibold text-exito">Los números coinciden</p>
              )}

              {noAlcanza && !bloqueo && (
                <p className="mt-4 rounded-xl bg-ambar-fondo p-3 text-xs font-semibold text-ambar-texto"
                   data-prueba="no-alcanza">
                  Esta caja tiene {pesos(cajaDisponible)} disponibles: no alcanza para esta recarga.
                </p>
              )}
              {error && <Aviso error={error} />}

              <button
                type="button"
                disabled={!listo}
                onClick={() => setPaso("confirma")}
                data-prueba="continuar"
                className={
                  "mt-5 w-full rounded-xl py-4 font-display text-base font-bold transition " +
                  (listo
                    ? "bg-primario text-white hover:bg-[#085089]"
                    : "cursor-not-allowed bg-[#D3D9E1] text-[#7A8699]")
                }
              >
                Continuar
              </button>
            </>
          )}

          {(paso === "confirma" || paso === "enviando") && elegido && (
            <div className="mt-4 space-y-4" data-prueba="confirma">
              <div className="flex gap-2.5 rounded-xl border border-ambar-borde bg-ambar-fondo p-3">
                <p className="text-[13px] leading-snug text-ambar-texto">
                  Lee el número en voz alta al cliente. Una vez cobrada, la recarga no se devuelve.
                </p>
              </div>
              <div className="text-center">
                <p className="text-[11px] uppercase tracking-wide text-apagado">Recarga a</p>
                <p className="cifras mt-1 font-display text-[32px] font-bold tracking-wide"
                   data-prueba="numero-confirmado">
                  {agrupar(numero)}
                </p>
                <p className="mt-1 text-sm text-tinta-dos">{elegido.nombre}</p>
              </div>
              <div className="rounded-xl bg-lienzo p-4 text-center">
                <p className="text-[11px] uppercase tracking-wide text-apagado">Cobra al cliente</p>
                <p className="cifras mt-1 font-display text-4xl font-bold text-primario">
                  {pesos(montoFinal)}
                </p>
              </div>

              {paso === "confirma" ? (
                <div className="flex gap-2.5">
                  <button type="button" onClick={() => setPaso("captura")}
                          className="flex-1 rounded-xl border border-campo bg-white py-4 font-display text-base font-bold">
                    Corregir
                  </button>
                  <button type="button" onClick={enviar} data-prueba="vender"
                          className="flex-[2] rounded-xl bg-primario py-4 font-display text-base font-bold text-white hover:bg-[#085089]">
                    Cobrar
                  </button>
                </div>
              ) : (
                <div className="text-center" data-prueba="enviando">
                  <p className="font-display text-base font-bold">Procesando la recarga…</p>
                  <p className="mt-2 text-xs leading-relaxed text-apagado">
                    No cierres esta pantalla ni repitas la venta. Puede tardar hasta
                    75 segundos mientras confirmamos con el proveedor.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        {saldo?.mi_caja && (
          <div className="rounded-2xl border border-borde bg-white p-5">
            <p className="text-[11px] font-bold uppercase tracking-wide text-apagado">
              {saldo.mi_caja.nombre}
            </p>
            <p className="cifras mt-1 font-display text-2xl font-bold">
              {pesos(saldo.mi_caja.disponible)}
            </p>
            <p className="mt-1 text-xs text-apagado">disponible para vender</p>
          </div>
        )}
      </aside>
    </div>
  );
}

function Mosaico({ texto, color, chico }: { texto: string; color: string; chico?: boolean }) {
  return (
    <span
      aria-hidden="true"
      style={{ background: color }}
      className={
        "flex flex-shrink-0 items-center justify-center rounded-[10px] font-display font-bold text-white " +
        (chico ? "h-8 w-8 text-xs" : "h-10 w-10 text-[13px]")
      }
    >
      {texto}
    </span>
  );
}

function BotonCompania({ nombre, detalle, tecla, color, activo, alElegir, mosaico }: {
  nombre: string;
  detalle: string;
  tecla: string;
  color: string;
  activo: boolean;
  alElegir: () => void;
  mosaico?: string;
}) {
  return (
    <button
      type="button"
      onClick={alElegir}
      aria-pressed={activo}
      aria-keyshortcuts={tecla}
      data-carrier={nombre}
      className={
        "flex min-h-[72px] w-full items-center gap-3 rounded-2xl px-3.5 py-3 text-left transition " +
        (activo
          ? "border-2 border-primario bg-elegido"
          : "border border-borde bg-white hover:border-campo")
      }
    >
      <Mosaico texto={mosaico ?? iniciales(nombre)} color={color} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] font-bold">{nombre}</span>
        <span className="block truncate text-xs text-apagado">{detalle}</span>
      </span>
      <span className="hidden h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-md border border-borde text-[11px] font-bold text-apagado lg:flex">
        {tecla}
      </span>
    </button>
  );
}

/**
 * El cierre. Tres caras distintas para tres cosas distintas.
 */
function Cierre({ resultado, numero, alSeguir }: {
  resultado: ResultadoVenta;
  numero: string;
  alSeguir: () => void;
}) {
  const estilos = {
    aprobada: {
      caja: "border-exito-borde bg-exito-fondo",
      titulo: "text-exito-oscuro",
      encabezado: "Venta aplicada",
      cuerpo: "El tiempo aire ya salió. Entrega el ticket.",
    },
    rechazada: {
      caja: "border-[#F3C9C6] bg-alerta-fondo",
      titulo: "text-alerta-oscuro",
      encabezado: "Venta rechazada",
      cuerpo: "No se cobró nada. Revisa el número con el cliente y vuelve a intentar.",
    },
    sin_resolver: {
      caja: "border-ambar-borde bg-ambar-fondo",
      titulo: "text-ambar-texto",
      encabezado: "Sin confirmar",
      cuerpo:
        "El proveedor no ha contestado. NO vuelvas a vender esta recarga: " +
        "puede haber salido. El saldo queda apartado y se resuelve solo en " +
        "los próximos minutos.",
    },
  }[resultado.desenlace];

  return (
    <div className="mx-auto max-w-xl" data-prueba="cierre" data-desenlace={resultado.desenlace}>
      <div className={"rounded-2xl border p-6 " + estilos.caja}>
        <h2 className={"font-display text-xl font-bold " + estilos.titulo}>
          {estilos.encabezado}
        </h2>
        <p className="mt-2 text-sm leading-relaxed">{estilos.cuerpo}</p>

        <div className="ticket mt-5 rounded-xl border border-dashed border-campo bg-white/70 p-4">
          <p className="text-[11px] font-bold uppercase tracking-wide text-apagado">
            Folio
          </p>
          <p className="font-display text-2xl font-bold tracking-wide" data-prueba="folio">
            {resultado.folio}
          </p>
          {numero && (
            <p className="cifras mt-2 text-sm">Número · {agrupar(numero)}</p>
          )}
          {resultado.desenlace === "rechazada" && (resultado.mensaje || resultado.codigo) && (
            <p className="mt-2 text-sm" data-prueba="motivo-rechazo">
              <span className="font-semibold">Motivo: </span>
              {resultado.mensaje || "El proveedor no dio un motivo"}
              {resultado.codigo && (
                <span className="ml-1 text-xs text-apagado">(código {resultado.codigo})</span>
              )}
            </p>
          )}
          {resultado.autorizacion && (
            <p className="mt-2 text-xs text-apagado">
              Autorización del carrier · {resultado.autorizacion}
            </p>
          )}
          <p className="cifras mt-2 text-sm font-semibold">{pesos(resultado.monto)}</p>
        </div>
      </div>

      {resultado.desenlace === "aprobada" && (
        <button
          type="button"
          onClick={() => window.print()}
          data-prueba="imprimir"
          className="mt-4 w-full rounded-xl border border-campo bg-white py-3.5 font-display text-base font-bold"
        >
          Imprimir ticket
        </button>
      )}
      <button
        type="button"
        onClick={alSeguir}
        data-prueba="otra-venta"
        className="mt-3 w-full rounded-xl bg-marino py-4 font-display text-base font-bold text-white"
      >
        Nueva venta
      </button>
    </div>
  );
}

function Renglon({ termino, valor }: { termino: string; valor: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-apagado">{termino}</dt>
      <dd className="cifras text-right font-semibold">{valor}</dd>
    </div>
  );
}
