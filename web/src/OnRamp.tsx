/**
 * Venta de cripto (on-ramp): pesos → criptomoneda, contra un teléfono.
 *
 * Mismo esqueleto que `Venta.tsx` (producto → captura → resultado) con tres
 * diferencias que no son de estilo:
 *
 * 1. **Se cotiza antes de confirmar.** Al salir del campo del monto se
 *    consulta la tasa y se muestra lo que recibirá el cliente. La
 *    cotización es informativa (no aparta saldo) y solo vale para el monto
 *    y el producto con que se pidió: si cualquiera cambia, el botón vuelve
 *    a quedar apagado hasta cotizar de nuevo. Sin cotización no se vende.
 *
 * 2. **Los tres desenlaces se ven distintos**, igual que en tiempo aire.
 *    `sin_resolver` dice que NO se vuelva a vender: puede haber salido.
 *
 * 3. **La llave de idempotencia.** Una por intención de venta. Si el envío
 *    se repite por una falla de red, va la misma llave y el servidor
 *    contesta con el cierre original en lugar de cobrar dos veces. Si el
 *    servidor contestó con un error (la venta no salió), la siguiente
 *    pulsación es una venta nueva y estrena llave: reusar la anterior
 *    devolvería el rechazo viejo para siempre.
 */
import { useEffect, useRef, useState } from "react";

import {
  Cotizacion, ErrorAPI, Producto, ResultadoOnRamp, Saldo, api, nuevaLlave, pesos,
} from "./api";
import { Aviso, Campo, Tarjeta } from "./piezas";

type Paso = "captura" | "enviando" | "cerrada";

const LARGO_TELEFONO = 12;
const MONTO_ENTERO = /^[1-9][0-9]{0,8}$/;

const soloDigitos = (s: string, largo: number) => s.replace(/\D/g, "").slice(0, largo);

/** "100.0000" → "100". Vacío si no es un entero positivo (un producto mal dado de alta). */
function enteroDe(valor: string | null | undefined): string {
  const n = Number(valor);
  return Number.isInteger(n) && n > 0 ? String(n) : "";
}

/** Una cifra del proveedor sin ceros de más ni notación científica. */
function cifra(valor: string): string {
  const n = Number(valor);
  return Number.isFinite(n) ? n.toLocaleString("es-MX", { maximumFractionDigits: 8 }) : valor;
}

/** Una cotización vale para un producto y un monto concretos. */
interface CotizacionVigente {
  valor: Cotizacion;
  sku: string;
  monto: string;
}

export function OnRamp({ saldo, alCambiarSaldo }: {
  saldo: Saldo | null;
  alCambiarSaldo: () => void;
}) {
  const [productos, setProductos] = useState<Producto[]>([]);
  const [elegido, setElegido] = useState<Producto | null>(null);
  const [monto, setMonto] = useState("");
  const [telefono, setTelefono] = useState("");
  const [cotizacion, setCotizacion] = useState<CotizacionVigente | null>(null);
  const [cotizando, setCotizando] = useState(false);
  const [errorCotizacion, setErrorCotizacion] = useState<ErrorAPI | null>(null);
  const [paso, setPaso] = useState<Paso>("captura");
  const [resultado, setResultado] = useState<ResultadoOnRamp | null>(null);
  const [error, setError] = useState<ErrorAPI | null>(null);

  // Cada consulta lleva un turno: si el cajero cambia el monto mientras una
  // consulta viaja, la respuesta vieja no debe pisar a la nueva.
  const turno = useRef(0);
  const llave = useRef(nuevaLlave());

  // Al abrir: el catálogo cripto y el saldo de la caja (Requirement 7.8).
  useEffect(() => {
    api.catalogo("cripto").then(setProductos).catch(() => setProductos([]));
    alCambiarSaldo();
  }, []);

  const montoFinal = elegido?.tipo_monto === "fijo" ? enteroDe(elegido.monto_fijo) : monto;
  const montoEntero = MONTO_ENTERO.test(montoFinal);
  const fueraDeRango =
    elegido?.tipo_monto === "rango" && montoEntero &&
    (Number(montoFinal) < Number(elegido.monto_min) || Number(montoFinal) > Number(elegido.monto_max));

  // La cotización solo cuenta si es de lo que hay capturado AHORA.
  const tasa =
    cotizacion && cotizacion.sku === elegido?.sku && cotizacion.monto === montoFinal
      ? cotizacion.valor
      : null;

  const telefonoCompleto = telefono.length === LARGO_TELEFONO;
  const bloqueo = saldo?.venta && !saldo.venta.puede ? saldo.venta.motivo : "";
  const cajaDisponible = saldo?.mi_caja?.disponible ? Number(saldo.mi_caja.disponible) : null;
  const noAlcanza = cajaDisponible !== null && montoEntero && Number(montoFinal) > cajaDisponible;
  const listo =
    Boolean(elegido) && tasa !== null && telefonoCompleto && !fueraDeRango &&
    !bloqueo && !noAlcanza && !cotizando;

  // Agrupados por compañía y en orden alfabético (Requirement 7.1).
  const grupos = productos.reduce<Record<string, Producto[]>>((g, p) => {
    (g[p.carrier] ||= []).push(p);
    return g;
  }, {});
  const carriers = Object.keys(grupos).sort((a, b) => a.localeCompare(b, "es"));

  async function cotizar(producto: Producto | null, valor: string) {
    const mio = ++turno.current;
    setCotizacion(null);
    setErrorCotizacion(null);
    if (!producto || !MONTO_ENTERO.test(valor)) {
      setCotizando(false);
      return;
    }
    setCotizando(true);
    try {
      const c = await api.cotizarOnRamp(producto.sku, Number(valor));
      if (mio === turno.current) setCotizacion({ valor: c, sku: producto.sku, monto: valor });
    } catch (e) {
      if (mio === turno.current) {
        setErrorCotizacion(
          e instanceof ErrorAPI
            ? e
            : new ErrorAPI("No se pudo consultar la tasa. Revisa tu conexión.", 0, "proveedor"),
        );
      }
    } finally {
      if (mio === turno.current) setCotizando(false);
    }
  }

  function elegir(p: Producto) {
    setElegido(p);
    setMonto("");
    // Un producto de monto fijo ya trae su monto: se cotiza de inmediato.
    cotizar(p, p.tipo_monto === "fijo" ? enteroDe(p.monto_fijo) : "");
  }

  function reiniciar() {
    turno.current += 1;
    llave.current = nuevaLlave();
    setElegido(null);
    setMonto("");
    setTelefono("");
    setCotizacion(null);
    setCotizando(false);
    setErrorCotizacion(null);
    setResultado(null);
    setError(null);
    setPaso("captura");
  }

  async function enviar() {
    if (!elegido || !tasa) return;
    setPaso("enviando");
    setError(null);
    try {
      const r = await api.venderOnRamp(elegido.sku, Number(montoFinal), telefono, llave.current);
      setResultado(r);
      setPaso("cerrada");
    } catch (e) {
      if (e instanceof ErrorAPI) {
        setError(e);
        // El servidor contestó: la venta no salió o sigue en curso. Si sigue
        // en curso se conserva la llave; si no, la siguiente es una venta nueva.
        if (e.clave !== "en_proceso") llave.current = nuevaLlave();
      } else {
        // Ni siquiera sabemos si llegó. Se conserva la llave: reintentar no
        // cobra dos veces, devuelve lo que haya pasado.
        setError(new ErrorAPI(
          "No se pudo confirmar con el servidor. La venta pudo haberse aplicado: " +
          "revisa Transacciones antes de reintentar.", 0, "red",
        ));
      }
      setPaso("captura");
    } finally {
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
            {carriers.map((carrier) => (
              <div key={carrier}>
                <p className="mb-2 font-display text-sm font-bold text-tinta">{carrier}</p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {grupos[carrier].map((p) => {
                    const activo = elegido?.sku === p.sku;
                    return (
                      <button
                        key={p.sku}
                        type="button"
                        onClick={() => elegir(p)}
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
                No hay productos de cripto disponibles ahora mismo.
              </p>
            )}
          </div>
        </Tarjeta>

        <Tarjeta titulo="Paso 2 · Monto y teléfono">
          <div className="grid gap-4 sm:grid-cols-2">
            {elegido?.tipo_monto !== "fijo" && (
              <Campo
                etiqueta={
                  elegido?.tipo_monto === "rango"
                    ? `Monto en pesos · de ${pesos(elegido.monto_min)} a ${pesos(elegido.monto_max)}`
                    : "Monto en pesos"
                }
                valor={monto}
                alCambiar={(v) => setMonto(soloDigitos(v, 9))}
                alPerderFoco={() => cotizar(elegido, montoFinal)}
                marcador="Solo pesos enteros"
                prueba="monto"
                malo={Boolean(fueraDeRango)}
              />
            )}
            <Campo
              etiqueta="Teléfono del cliente"
              valor={telefono}
              alCambiar={(v) => setTelefono(soloDigitos(v, LARGO_TELEFONO))}
              marcador={`${LARGO_TELEFONO} dígitos`}
              prueba="telefono"
            />
          </div>
          {fueraDeRango && elegido && (
            <p className="mt-2 text-xs font-semibold text-alerta" data-prueba="fuera-de-rango">
              Este producto acepta de {pesos(elegido.monto_min)} a {pesos(elegido.monto_max)}.
            </p>
          )}
          {telefono.length > 0 && !telefonoCompleto && (
            <p className="mt-2 text-xs text-apagado" data-prueba="faltan-digitos">
              Faltan {LARGO_TELEFONO - telefono.length} dígitos.
            </p>
          )}
        </Tarjeta>

        {errorCotizacion && (
          <div data-prueba="error-cotizacion">
            <Aviso error={errorCotizacion} />
          </div>
        )}
      </div>

      <aside className="space-y-4">
        <div className="rounded-2xl border border-borde bg-white p-5">
          <h3 className="font-display text-base font-bold">Resumen</h3>
          <dl className="mt-4 space-y-2 text-sm">
            <Renglon termino="Producto" valor={elegido?.nombre ?? "—"} />
            <Renglon termino="Teléfono" valor={telefonoCompleto ? telefono : "—"} />
            <Renglon termino="Tasa" valor={tasa ? cifra(tasa.rate) : cotizando ? "Cotizando…" : "—"} />
            <Renglon termino="Comisión" valor={tasa ? `$${cifra(tasa.fee)}` : "—"} />
            <Renglon
              termino="Después de comisión"
              valor={tasa ? `$${cifra(tasa.fiatAmountAfterFee)}` : "—"}
            />
          </dl>
          <div className="mt-4 rounded-xl bg-elegido p-4">
            <p className="text-[11px] font-bold uppercase tracking-wide">Se cobra</p>
            <p className="cifras mt-1 font-display text-3xl font-bold text-primario">
              {montoEntero ? pesos(montoFinal) : "—"}
            </p>
          </div>

          {elegido && montoEntero && !tasa && !cotizando && !errorCotizacion && (
            <p className="mt-4 text-xs text-apagado" data-prueba="falta-cotizar">
              Sal del campo del monto para cotizar antes de confirmar.
            </p>
          )}
          {noAlcanza && !bloqueo && (
            <p className="mt-4 rounded-xl bg-ambar-fondo p-3 text-xs font-semibold text-ambar-texto"
               data-prueba="no-alcanza">
              Esta caja tiene {pesos(cajaDisponible)} disponibles: no alcanza para esta venta.
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
            {paso === "enviando" ? "Enviando…" : "Confirmar venta"}
          </button>
          {paso === "enviando" && (
            <p className="mt-2 text-center text-xs text-apagado" data-prueba="no-cierres">
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
            <p className="cifras mt-1 font-display text-2xl font-bold" data-prueba="saldo-caja">
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
  resultado: ResultadoOnRamp;
  alSeguir: () => void;
}) {
  const estilos = {
    aprobada: {
      caja: "border-exito-borde bg-exito-fondo",
      titulo: "text-exito-oscuro",
      encabezado: "Venta aplicada",
      cuerpo: "La criptomoneda ya se envió. Entrega el comprobante.",
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
        <p className="mt-2 text-sm leading-relaxed" data-prueba="mensaje-cierre">
          {estilos.cuerpo}
        </p>

        <div className="mt-5 rounded-xl border border-dashed border-campo bg-white/70 p-4">
          <p className="text-[11px] font-bold uppercase tracking-wide text-apagado">
            Folio
          </p>
          <p className="font-display text-2xl font-bold tracking-wide" data-prueba="folio">
            {resultado.folio}
          </p>

          {resultado.desenlace === "aprobada" && (
            <dl className="mt-3 space-y-1 text-sm">
              <Renglon termino="ID de transacción" valor={resultado.transaction_id || "—"} />
              <Renglon
                termino="Cripto enviada"
                valor={
                  resultado.target_amount
                    ? `${cifra(resultado.target_amount)} ${resultado.target_asset}`.trim()
                    : "—"
                }
              />
            </dl>
          )}
          {resultado.desenlace === "rechazada" && (
            <p className="mt-2 text-sm" data-prueba="motivo-rechazo">
              <span className="font-semibold">Motivo: </span>
              {resultado.mensaje || resultado.detalle || "El proveedor no dio un motivo"}
            </p>
          )}
          {resultado.desenlace === "sin_resolver" && resultado.transaction_id && (
            <p className="mt-2 text-xs text-apagado">
              ID de transacción · {resultado.transaction_id}
            </p>
          )}
          {resultado.repetida && resultado.aviso && (
            <p className="mt-2 text-xs text-apagado">{resultado.aviso}</p>
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
