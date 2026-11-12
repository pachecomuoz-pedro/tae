/**
 * Venta de tiempo aire.
 *
 * Dos decisiones que no son de estilo:
 *
 * 1. **El número se captura dos veces.** Una recarga a un número equivocado
 *    no se puede revertir: el saldo ya está en un teléfono ajeno. Pedirlo
 *    dos veces cuesta tres segundos al cajero y evita la única clase de
 *    error que nadie puede deshacer.
 *
 * 3. **Si la caja no puede vender, se dice antes.** El servidor manda el
 *    motivo junto con el saldo (sin límite asignado, caja suspendida,
 *    saldo retenido). Dejar capturar producto y número para rechazar al
 *    final es lo que hizo pensar "tengo saldo y no me deja".
 *
 * 2. **Los tres desenlaces se ven distintos.** `sin_resolver` no se pinta
 *    como aprobada ni como rechazada. Si se viera aprobada, el cajero cobra
 *    tiempo aire que quizá no salió; si se viera rechazada, vuelve a vender
 *    y el cliente paga dos veces.
 */
import { useEffect, useRef, useState } from "react";

import { ErrorAPI, Producto, ResultadoVenta, Saldo, api, pesos } from "./api";
import { Aviso, Campo, Tarjeta } from "./piezas";

type Paso = "captura" | "confirma" | "enviando" | "cerrada";

const soloDigitos = (s: string) => s.replace(/\D/g, "").slice(0, 10);

export function Venta({ saldo, alCambiarSaldo }: {
  saldo: Saldo | null;
  alCambiarSaldo: () => void;
}) {
  const [productos, setProductos] = useState<Producto[]>([]);
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

  const montoFinal = elegido?.tipo_monto === "fijo" ? elegido.monto_fijo ?? "" : monto;
  const numeroCompleto = numero.length === 10;
  const coinciden = numeroCompleto && numero === confirmacion;
  // Sin saldo todavía (cargando) no se bloquea: el servidor valida igual.
  const bloqueo = saldo?.venta && !saldo.venta.puede ? saldo.venta.motivo : "";
  const cajaDisponible = saldo?.mi_caja?.disponible ? Number(saldo.mi_caja.disponible) : null;
  const noAlcanza = cajaDisponible !== null && Number(montoFinal) > cajaDisponible;
  const listo = Boolean(elegido) && coinciden && Number(montoFinal) > 0 && !bloqueo && !noAlcanza;

  // Agrupados por compañía: tres botones de $50 sin decir de quién son es
  // la forma más fácil de vender el producto equivocado.
  const grupos = productos.reduce<Record<string, Producto[]>>((g, p) => {
    (g[p.carrier] ||= []).push(p);
    return g;
  }, {});

  function reiniciar() {
    setElegido(null);
    setNumero("");
    setConfirmacion("");
    setMonto("");
    setResultado(null);
    setError(null);
    setPaso("captura");
    campoNumero.current?.focus();
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

  if (paso === "cerrada" && resultado) {
    return <Cierre resultado={resultado} alSeguir={reiniciar} />;
  }

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

        <Tarjeta titulo="Paso 1 · Producto">
          <div className="space-y-4">
            {Object.entries(grupos).map(([carrier, lista]) => (
              <div key={carrier}>
                <p className="mb-2 font-display text-sm font-bold text-tinta">{carrier}</p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {lista.map((p) => {
                    const activo = elegido?.sku === p.sku;
                    return (
                      <button
                        key={p.sku}
                        type="button"
                        onClick={() => { setElegido(p); setMonto(""); }}
                        data-sku={p.sku}
                        title={p.nombre}
                        className={
                          "rounded-xl border p-3 text-left transition " +
                          (activo
                            ? "border-2 border-primario bg-elegido"
                            : "border-borde bg-white hover:border-campo")
                        }
                      >
                        <span className="block font-display text-base font-bold">
                          {p.etiqueta_monto}
                        </span>
                        <span className="mt-0.5 block truncate text-xs text-apagado">{p.nombre}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
            {productos.length === 0 && (
              <p className="text-sm text-apagado">
                No hay productos disponibles ahora mismo.
              </p>
            )}
          </div>
        </Tarjeta>

        <Tarjeta titulo="Paso 2 · Número de teléfono">
          <div className="grid gap-4 sm:grid-cols-2">
            <Campo
              etiqueta="Número"
              refCampo={campoNumero}
              valor={numero}
              alCambiar={(v) => setNumero(soloDigitos(v))}
              marcador="10 dígitos"
              prueba="numero"
            />
            <Campo
              etiqueta="Confírmalo"
              valor={confirmacion}
              alCambiar={(v) => setConfirmacion(soloDigitos(v))}
              marcador="otra vez"
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

          {elegido?.tipo_monto === "rango" && (
            <div className="mt-4 max-w-[220px]">
              <Campo
                etiqueta={`Monto · de ${pesos(elegido.monto_min)} a ${pesos(elegido.monto_max)}`}
                valor={monto}
                alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))}
                marcador="0.00"
                prueba="monto"
              />
            </div>
          )}
        </Tarjeta>
      </div>

      <aside className="space-y-4">
        <div className="rounded-2xl border border-borde bg-white p-5">
          <h3 className="font-display text-base font-bold">Resumen</h3>
          <dl className="mt-4 space-y-2 text-sm">
            <Renglon termino="Producto" valor={elegido?.nombre ?? "—"} />
            <Renglon termino="Número" valor={numeroCompleto ? numero : "—"} />
          </dl>
          <div className="mt-4 rounded-xl bg-elegido p-4">
            <p className="text-[11px] font-bold uppercase tracking-wide">Se cobra</p>
            <p className="cifras mt-1 font-display text-3xl font-bold text-primario">
              {montoFinal ? pesos(montoFinal) : "—"}
            </p>
          </div>

          {noAlcanza && !bloqueo && (
            <p className="mt-4 rounded-xl bg-ambar-fondo p-3 text-xs font-semibold text-ambar-texto"
               data-prueba="no-alcanza">
              Esta caja tiene {pesos(cajaDisponible)} disponibles: no alcanza para esta recarga.
            </p>
          )}
          {error && <Aviso error={error} />}

          <button
            type="button"
            disabled={!listo || paso === "enviando"}
            onClick={enviar}
            data-prueba="vender"
            className={
              "mt-5 w-full rounded-xl py-4 font-display text-base font-bold transition " +
              (listo && paso !== "enviando"
                ? "bg-primario text-white hover:bg-[#085089]"
                : "cursor-not-allowed bg-[#D3D9E1] text-[#7A8699]")
            }
          >
            {paso === "enviando" ? "Enviando…" : "Vender"}
          </button>
          {paso === "enviando" && (
            <p className="mt-2 text-center text-xs text-apagado">
              No cierres esta pantalla. Si tarda, estamos confirmando con el
              proveedor.
            </p>
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

/**
 * El cierre. Tres caras distintas para tres cosas distintas.
 */
function Cierre({ resultado, alSeguir }: {
  resultado: ResultadoVenta;
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
      cuerpo: "No se cobró nada. Puedes volver a intentar.",
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

        <div className="mt-5 rounded-xl border border-dashed border-campo bg-white/70 p-4">
          <p className="text-[11px] font-bold uppercase tracking-wide text-apagado">
            Folio
          </p>
          <p className="font-display text-2xl font-bold tracking-wide" data-prueba="folio">
            {resultado.folio}
          </p>
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

      <button
        type="button"
        onClick={alSeguir}
        data-prueba="otra-venta"
        className="mt-4 w-full rounded-xl bg-marino py-4 font-display text-base font-bold text-white"
      >
        Otra venta
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

