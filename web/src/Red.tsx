/**
 * La red: cambio obligatorio de contraseña, usuarios de la red (altas y
 * restablecimiento) y la bandeja de aprobaciones.
 *
 * Diseño de referencia: el canvas "Punto de Venta TAE — Mockup navegable",
 * artboards "Super Admin — Usuarios de la red", "Administrador —
 * Aprobaciones" y "Cliente — Aprobaciones".
 *
 * Igual que el resto del frontend, aquí no se decide ningún permiso: se
 * pintan los botones según las capacidades del perfil y el servidor vuelve
 * a verificar todo al apretarlos.
 */
import { useEffect, useState } from "react";

import {
  Alta, ErrorAPI, FilaRed, Pendiente, Perfil, TipoRed, api, pesos,
} from "./api";
import { Aviso, Tarjeta } from "./piezas";

const NOMBRE_TIPO: Record<TipoRed, string> = {
  administrador: "Administrador",
  cliente: "Cliente",
  punto_venta: "Punto de venta",
};

const PLURAL_TIPO: Record<TipoRed, string> = {
  administrador: "Administradores",
  cliente: "Clientes",
  punto_venta: "Puntos de venta",
};

/** Colores de estado, los mismos del canvas. */
function Estado({ estado }: { estado: string }) {
  const estilos: Record<string, string> = {
    aprobado: "bg-exito-fondo text-exito-oscuro",
    activo: "bg-exito-fondo text-exito-oscuro",
    pendiente: "bg-ambar-fondo text-ambar-texto",
    suspendido: "bg-alerta-fondo text-alerta-oscuro",
    rechazado: "bg-suave text-tinta-dos",
  };
  return (
    <span className={"inline-block rounded-full px-2.5 py-1 text-xs font-bold capitalize " +
      (estilos[estado] ?? "bg-suave text-tinta-dos")}>
      {estado}
    </span>
  );
}

function CampoTexto({ etiqueta, valor, alCambiar, tipo = "text", marcador, ayuda }: {
  etiqueta: string;
  valor: string;
  alCambiar: (v: string) => void;
  tipo?: string;
  marcador?: string;
  ayuda?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-bold text-tinta-dos">{etiqueta}</span>
      <input
        type={tipo}
        value={valor}
        placeholder={marcador}
        onChange={(e) => alCambiar(e.target.value)}
        className="w-full rounded-xl border border-campo px-3.5 py-3 text-sm outline-none focus:border-primario"
      />
      {ayuda && <span className="mt-1 block text-xs text-apagado">{ayuda}</span>}
    </label>
  );
}

/**
 * La contraseña temporal, en la única pantalla donde existe.
 * No se guarda en el estado de la app más allá de este panel.
 */
function Credencial({ correo, temporal, alCerrar }: {
  correo: string;
  temporal: string;
  alCerrar: () => void;
}) {
  const [copiada, setCopiada] = useState(false);
  return (
    <div className="space-y-4" data-prueba="credencial">
      <div className="overflow-hidden rounded-xl border border-borde">
        <div className="flex border-b border-suave px-4 py-3">
          <span className="w-40 text-sm text-apagado">Usuario (correo)</span>
          <span className="text-sm font-bold">{correo}</span>
        </div>
        <div className="flex items-center bg-suave px-4 py-3">
          <span className="w-40 text-sm text-apagado">Contraseña temporal</span>
          <span className="flex-1 font-display text-xl font-bold tracking-widest text-primario"
                data-prueba="temporal">
            {temporal}
          </span>
          <button
            type="button"
            className="rounded-lg border border-campo bg-white px-3 py-1.5 text-xs font-bold"
            onClick={() => {
              navigator.clipboard?.writeText(temporal).then(() => setCopiada(true)).catch(() => undefined);
            }}
          >
            {copiada ? "Copiada" : "Copiar"}
          </button>
        </div>
      </div>
      <p className="rounded-xl bg-ambar-fondo p-3 text-xs text-tinta">
        Solo se muestra esta vez. Entrégala por un medio seguro; al entrar por primera
        vez el sistema pide cambiarla.
      </p>
      <div className="flex justify-end">
        <button type="button" onClick={alCerrar}
                className="rounded-xl bg-primario px-5 py-3 text-sm font-bold text-white">
          Listo
        </button>
      </div>
    </div>
  );
}

function Modal({ titulo, alCerrar, children }: {
  titulo: string;
  alCerrar: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-20 grid place-items-center bg-tinta/45 p-4" role="dialog"
         aria-modal="true" aria-label={titulo}>
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-borde px-6 py-4">
          <h2 className="font-display text-lg font-bold">{titulo}</h2>
          <button type="button" onClick={alCerrar} aria-label="Cerrar"
                  className="px-2 text-xl leading-none text-apagado">×</button>
        </div>
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cambio obligatorio de contraseña
// ---------------------------------------------------------------------------
/**
 * Lo único que ve quien entró con una contraseña temporal. La API le niega
 * todo lo demás (clave `cambiar_contrasena`), así que esta pantalla no es
 * una sugerencia: es la puerta.
 */
export function CambiarContrasena({ perfil, alTerminar, alSalir }: {
  perfil: Perfil;
  alTerminar: (p: Perfil) => void;
  alSalir: () => void;
}) {
  const [actual, setActual] = useState("");
  const [nueva, setNueva] = useState("");
  const [repetida, setRepetida] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [enviando, setEnviando] = useState(false);

  const noCoinciden = repetida !== "" && repetida !== nueva;
  const listo = actual !== "" && nueva.length >= 12 && nueva === repetida;

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (!listo) return;
    setEnviando(true);
    setError(null);
    try {
      alTerminar(await api.cambiarContrasena(actual, nueva));
    } catch (err) {
      setError(err as ErrorAPI);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="grid h-full place-items-center bg-rail px-5">
      <form onSubmit={enviar} className="w-full max-w-sm rounded-2xl bg-white p-7"
            data-prueba="cambiar-contrasena">
        <p className="font-display text-xl font-bold">
          EPS <span className="text-acento">TAE</span>
        </p>
        <h1 className="mt-4 font-display text-lg font-bold">Elige tu contraseña</h1>
        <p className="mt-1 text-sm text-apagado">
          {perfil.nombre}, entraste con una contraseña temporal. Antes de continuar
          pon una que solo tú conozcas.
        </p>

        <div className="mt-5 space-y-4">
          <CampoTexto etiqueta="Contraseña temporal" tipo="password"
                      valor={actual} alCambiar={setActual} />
          <CampoTexto etiqueta="Contraseña nueva" tipo="password" valor={nueva}
                      alCambiar={setNueva}
                      ayuda="Mínimo 12 caracteres. Que no sea solo números ni una palabra común." />
          <CampoTexto etiqueta="Repite la contraseña nueva" tipo="password"
                      valor={repetida} alCambiar={setRepetida} />
          {noCoinciden && (
            <p className="text-xs font-semibold text-alerta-oscuro">Las dos no coinciden.</p>
          )}
        </div>

        {error && <Aviso error={error} />}

        <button type="submit" disabled={!listo || enviando}
                className="mt-6 w-full rounded-xl bg-primario py-3.5 font-display font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
          {enviando ? "Guardando…" : "Guardar y continuar"}
        </button>
        <button type="button" onClick={alSalir}
                className="mt-3 w-full text-xs text-apagado underline underline-offset-2">
          Salir
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Usuarios de la red
// ---------------------------------------------------------------------------
/** Qué niveles ve cada rol: siempre los de abajo. */
function tiposDe(perfil: Perfil): TipoRed[] {
  if (perfil.rol === "super_admin") return ["administrador", "cliente", "punto_venta"];
  if (perfil.rol === "administrador") return ["cliente", "punto_venta"];
  return ["punto_venta"];
}

export function Red({ perfil, alCambiar }: { perfil: Perfil; alCambiar: () => void }) {
  const tipos = tiposDe(perfil);
  const [tipo, setTipo] = useState<TipoRed>(tipos[0]);
  const [filas, setFilas] = useState<FilaRed[]>([]);
  const [buscar, setBuscar] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [altaAbierta, setAltaAbierta] = useState(false);
  const [restablecer, setRestablecer] = useState<FilaRed | null>(null);
  const [fondear, setFondear] = useState<FilaRed | null>(null);
  const [suspender, setSuspender] = useState<FilaRed | null>(null);
  const [acceso, setAcceso] = useState<FilaRed | null>(null);

  const cargar = () => {
    setError(null);
    api.red(tipo).then(setFilas).catch((e) => { setFilas([]); setError(e as ErrorAPI); });
  };
  useEffect(cargar, [tipo]);

  const q = buscar.trim().toLowerCase();
  const visibles = filas.filter((f) =>
    q === "" || f.nombre.toLowerCase().includes(q) ||
    (f.usuario?.correo ?? "").toLowerCase().includes(q));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {tipos.length > 1 && tipos.map((t) => (
          <button key={t} type="button" onClick={() => setTipo(t)} data-tipo={t}
                  className={"rounded-xl border px-4 py-2.5 text-sm font-semibold " +
                    (tipo === t ? "border-primario bg-primario text-white"
                                : "border-borde bg-white text-tinta-dos")}>
            {PLURAL_TIPO[t]}
          </button>
        ))}
        <input value={buscar} onChange={(e) => setBuscar(e.target.value)}
               placeholder="Buscar por nombre o correo…" aria-label="Buscar"
               className="min-w-[200px] flex-1 rounded-xl border border-borde bg-white px-4 py-2.5 text-sm outline-none focus:border-primario" />
        <button type="button" onClick={() => setAltaAbierta(true)} data-prueba="nuevo"
                className="rounded-xl bg-primario px-4 py-2.5 text-sm font-bold text-white">
          Nuevo {NOMBRE_TIPO[tipo].toLowerCase()}
        </button>
      </div>

      {error && <Aviso error={error} />}

      <div className="overflow-x-auto rounded-2xl border border-borde bg-white">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-suave text-left text-[11px] uppercase tracking-wide text-apagado">
            <tr>
              <th className="px-4 py-3">{NOMBRE_TIPO[tipo]}</th>
              <th className="px-4 py-3">Depende de</th>
              <th className="px-4 py-3">Estado</th>
              {tipo === "punto_venta" ? (
                <th className="px-4 py-3 text-right">Límite</th>
              ) : (
                <>
                  <th className="px-4 py-3 text-right">Disponible</th>
                  <th className="px-4 py-3 text-right">Saldo red</th>
                </>
              )}
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {visibles.map((f) => {
              // Una cuenta pendiente o rechazada todavía no puede entrar:
              // restablecerla no tendría efecto.
              const puedeRestablecer = !!f.usuario && f.usuario.uuid !== perfil.uuid &&
                !["pendiente", "rechazado"].includes(f.estado);
              return (
                <tr key={f.uuid} className="border-t border-suave" data-fila={f.nombre}>
                  <td className="px-4 py-3">
                    <span className="block font-bold">{f.nombre}</span>
                    <span className="block text-xs text-apagado">
                      {f.usuario?.correo ?? "Sin usuario"}
                      {f.usuario?.debe_cambiar_contrasena && " · aún no cambia su contraseña"}
                    </span>
                    {f.usuario && !f.usuario.activo && (
                      <span className="mt-1 inline-block rounded-full bg-suave px-2 py-0.5 text-[11px] font-bold text-tinta-dos">
                        Acceso desactivado
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-tinta-dos">{f.padre ?? "EPS"}</td>
                  <td className="px-4 py-3">
                    <Estado estado={f.estado} />
                    {f.motivo_rechazo && (
                      <span className="mt-1 block text-xs text-apagado">{f.motivo_rechazo}</span>
                    )}
                    {f.tipo === "punto_venta" && f.puede_vender === false &&
                     ["activo"].includes(f.estado) && (
                      <span className="mt-1 block max-w-[220px] text-xs font-semibold text-alerta-oscuro"
                            data-prueba="no-vende">
                        No puede vender: {f.motivo_no_vende}
                      </span>
                    )}
                  </td>
                  {tipo === "punto_venta" ? (
                    <td className="cifras px-4 py-3 text-right font-display font-bold">
                      {f.modo_bolsa === "compartida" ? "Bolsa del cliente" : pesos(f.limite)}
                    </td>
                  ) : (
                    <>
                      <td className="cifras px-4 py-3 text-right font-display font-bold text-primario">
                        {pesos(f.disponible)}
                      </td>
                      <td className="cifras px-4 py-3 text-right font-display font-bold">
                        {pesos(f.saldo_red)}
                        {Number(f.retenido ?? 0) > 0 && (
                          <span className="mt-0.5 block text-[11px] font-bold text-ambar-texto">
                            {pesos(f.retenido)} retenidos
                          </span>
                        )}
                      </td>
                    </>
                  )}
                  <td className="space-x-2 whitespace-nowrap px-4 py-3 text-right">
                    {perfil.rol === "super_admin" && ["aprobado", "activo"].includes(f.estado) &&
                     !(f.tipo === "punto_venta" && f.modo_bolsa === "compartida") && (
                      <button type="button" onClick={() => setFondear(f)} data-prueba="fondear"
                              className="rounded-lg bg-primario px-3 py-1.5 text-xs font-bold text-white">
                        {f.tipo === "punto_venta" ? "Límite" : "Asignar saldo"}
                      </button>
                    )}
                    {puedeRestablecer && (
                      <button type="button" onClick={() => setRestablecer(f)}
                              data-prueba="restablecer"
                              className="rounded-lg border border-campo bg-white px-3 py-1.5 text-xs font-bold">
                        Restablecer contraseña
                      </button>
                    )}
                    {puedeSuspender(perfil, f) && (
                      <button type="button" onClick={() => setSuspender(f)} data-prueba="suspender"
                              className={"rounded-lg border px-3 py-1.5 text-xs font-bold " +
                                (f.estado === "suspendido"
                                  ? "border-exito-borde bg-exito-fondo text-exito-oscuro"
                                  : "border-[#F3C9C6] bg-white text-alerta-oscuro")}>
                        {f.estado === "suspendido" ? "Reactivar" : "Suspender"}
                      </button>
                    )}
                    {!!f.usuario && f.usuario.uuid !== perfil.uuid && perfil.capacidades.cambiar_acceso && (
                      <button type="button" onClick={() => setAcceso(f)} data-prueba="acceso"
                              className="rounded-lg border border-campo bg-white px-3 py-1.5 text-xs font-bold text-tinta-dos">
                        {f.usuario.activo ? "Desactivar acceso" : "Reactivar acceso"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {visibles.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10 text-center text-apagado">
                Sin resultados.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {altaAbierta && (
        <AltaModal tipo={tipo} perfil={perfil}
                   alCerrar={() => { setAltaAbierta(false); cargar(); alCambiar(); }} />
      )}
      {restablecer && (
        <RestablecerModal fila={restablecer} alCerrar={() => { setRestablecer(null); cargar(); }} />
      )}
      {fondear && (
        <FondeoModal fila={fondear} alCerrar={() => { setFondear(null); cargar(); alCambiar(); }} />
      )}
      {suspender && (
        <SuspensionModal fila={suspender}
                         alCerrar={() => { setSuspender(null); cargar(); alCambiar(); }} />
      )}
      {acceso && (
        <AccesoModal fila={acceso} alCerrar={() => { setAcceso(null); cargar(); }} />
      )}
    </div>
  );
}

function AltaModal({ tipo, perfil, alCerrar }: {
  tipo: TipoRed;
  perfil: Perfil;
  alCerrar: () => void;
}) {
  const [nombre, setNombre] = useState("");
  const [correo, setCorreo] = useState("");
  const [responsable, setResponsable] = useState("");
  const [rfc, setRfc] = useState("");
  const [margen, setMargen] = useState("4");
  const [direccion, setDireccion] = useState("");
  const [limite, setLimite] = useState("");
  const [padre, setPadre] = useState("");
  const [padres, setPadres] = useState<FilaRed[]>([]);
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [hecho, setHecho] = useState<Alta | null>(null);
  const [enviando, setEnviando] = useState(false);

  // De quién depende: el Super Admin elige administrador para un cliente;
  // quien no es cliente elige cliente para una caja. Los demás no eligen.
  const eligePadre = (tipo === "cliente" && perfil.rol === "super_admin") ||
    (tipo === "punto_venta" && perfil.rol !== "cliente");
  useEffect(() => {
    if (!eligePadre) return;
    const nivel: TipoRed = tipo === "cliente" ? "administrador" : "cliente";
    api.red(nivel).then((f) => {
      const vigentes = f.filter((x) => ["aprobado", "activo"].includes(x.estado));
      setPadres(vigentes);
      setPadre(vigentes[0]?.uuid ?? "");
    }).catch(() => setPadres([]));
  }, [tipo, eligePadre]);

  const nace = perfil.rol === "super_admin" && tipo !== "administrador"
    ? "Quedará pendiente hasta que lo apruebe su superior."
    : "Quedará activo al guardarlo.";
  const listo = nombre.trim().length > 2 && /\S+@\S+\.\S+/.test(correo) && (!eligePadre || padre);

  async function crear() {
    setEnviando(true);
    setError(null);
    try {
      if (tipo === "administrador") {
        setHecho(await api.altaAdministrador({ nombre, responsable, correo }));
      } else if (tipo === "cliente") {
        setHecho(await api.altaCliente({
          administrador: eligePadre ? padre : undefined, nombre, rfc, margen_pct: margen, correo,
        }));
      } else {
        setHecho(await api.altaPuntoVenta({
          cliente: eligePadre ? padre : undefined, nombre, direccion, correo,
          limite: limite || undefined,
        }));
      }
    } catch (e) {
      setError(e as ErrorAPI);
    } finally {
      setEnviando(false);
    }
  }

  if (hecho) {
    return (
      <Modal titulo={`${NOMBRE_TIPO[tipo]} creado`} alCerrar={alCerrar}>
        <p className="mb-4 text-sm">
          <b>{hecho.nombre}</b> quedó {hecho.estado === "pendiente" ? "pendiente de aprobación" : "activo"}.
        </p>
        {hecho.advertencia && (
          <p className="mb-4 rounded-xl bg-ambar-fondo p-3 text-sm text-ambar-texto" data-prueba="advertencia">
            {hecho.advertencia}
          </p>
        )}
        {hecho.credencial_temporal && hecho.usuario ? (
          <Credencial correo={hecho.usuario.correo} temporal={hecho.credencial_temporal}
                      alCerrar={alCerrar} />
        ) : (
          <button type="button" onClick={alCerrar}
                  className="rounded-xl bg-primario px-5 py-3 text-sm font-bold text-white">Listo</button>
        )}
      </Modal>
    );
  }

  return (
    <Modal titulo={`Nuevo ${NOMBRE_TIPO[tipo].toLowerCase()}`} alCerrar={alCerrar}>
      <div className="space-y-4">
        {eligePadre && (
          <label className="block">
            <span className="mb-1.5 block text-xs font-bold text-tinta-dos">
              {tipo === "cliente" ? "Administrador al que pertenece" : "Cliente al que pertenece"}
            </span>
            <select value={padre} onChange={(e) => setPadre(e.target.value)}
                    className="w-full rounded-xl border border-campo bg-white px-3.5 py-3 text-sm">
              {padres.map((p) => <option key={p.uuid} value={p.uuid}>{p.nombre}</option>)}
            </select>
          </label>
        )}
        <CampoTexto etiqueta={tipo === "punto_venta" ? "Nombre del punto de venta" : "Nombre comercial"}
                    valor={nombre} alCambiar={setNombre}
                    marcador={tipo === "punto_venta" ? "Caja 3" : "Abarrotes El Sol"} />
        {tipo === "administrador" && (
          <CampoTexto etiqueta="Responsable" valor={responsable} alCambiar={setResponsable} />
        )}
        {tipo === "cliente" && (
          <div className="flex gap-3">
            <div className="flex-1">
              <CampoTexto etiqueta="RFC" valor={rfc} alCambiar={(v) => setRfc(v.toUpperCase())} />
            </div>
            <label className="block w-32">
              <span className="mb-1.5 block text-xs font-bold text-tinta-dos">Margen</span>
              <select value={margen} onChange={(e) => setMargen(e.target.value)}
                      className="w-full rounded-xl border border-campo bg-white px-3.5 py-3 text-sm">
                {["0", "3", "4", "5"].map((m) => <option key={m} value={m}>{m}%</option>)}
              </select>
            </label>
          </div>
        )}
        {tipo === "punto_venta" && (
          <>
            <CampoTexto etiqueta="Dirección" valor={direccion} alCambiar={setDireccion} />
            <CampoTexto etiqueta="Límite inicial (opcional)" valor={limite}
                        alCambiar={(v) => setLimite(v.replace(/[^0-9.]/g, ""))}
                        ayuda="Solo si el cliente opera en bolsa por punto de venta. Se reserva desde ahora." />
          </>
        )}
        <CampoTexto etiqueta="Correo (con él entra a la plataforma)" tipo="email"
                    valor={correo} alCambiar={setCorreo}
                    ayuda="Para un cajero sin correo propio, uno de la tienda sirve: no lo usa para recuperar." />
        <p className="rounded-xl bg-elegido p-3 text-xs">
          {nace} Al guardar verás su contraseña temporal.
        </p>
        {error && <Aviso error={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={alCerrar}
                  className="rounded-xl border border-campo px-5 py-3 text-sm font-bold">Cancelar</button>
          <button type="button" onClick={crear} disabled={!listo || enviando} data-prueba="crear"
                  className="rounded-xl bg-primario px-5 py-3 text-sm font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
            {enviando ? "Guardando…" : "Crear"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function RestablecerModal({ fila, alCerrar }: { fila: FilaRed; alCerrar: () => void }) {
  const [temporal, setTemporal] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);

  async function generar() {
    if (!fila.usuario) return;
    setError(null);
    try {
      setTemporal((await api.restablecer(fila.usuario.uuid)).credencial_temporal);
    } catch (e) {
      setError(e as ErrorAPI);
    }
  }

  return (
    <Modal titulo="Restablecer contraseña" alCerrar={alCerrar}>
      {temporal ? (
        <Credencial correo={fila.usuario?.correo ?? ""} temporal={temporal} alCerrar={alCerrar} />
      ) : (
        <div className="space-y-4">
          <p className="text-sm">
            Se generará una contraseña temporal nueva para <b>{fila.nombre}</b>
            {" "}({fila.usuario?.correo}).
          </p>
          <p className="rounded-xl bg-ambar-fondo p-3 text-xs">
            La contraseña actual deja de funcionar y se cierran sus sesiones abiertas.
            Si está vendiendo en este momento, tendrá que volver a entrar.
          </p>
          {error && <Aviso error={error} />}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={alCerrar}
                    className="rounded-xl border border-campo px-5 py-3 text-sm font-bold">Cancelar</button>
            <button type="button" onClick={generar} data-prueba="confirmar-restablecer"
                    className="rounded-xl bg-primario px-5 py-3 text-sm font-bold text-white">
              Restablecer
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/**
 * El Super Admin asigna saldo a mano a cualquiera de la red (decisión 23).
 * No sale de Emida ni se descuenta del saldo de EPS en Emida.
 *
 * - Administrador y cliente: se SUMA a su bolsa, desde la cuenta de fondeo
 *   de EPS. Al cliente, sin margen y sin pasar por su administrador.
 * - Caja: no tiene dinero propio; se FIJA su límite sobre la bolsa de su
 *   cliente (decisión 7). Por eso el texto dice "límite" y no "saldo".
 */
function FondeoModal({ fila, alCerrar }: { fila: FilaRed; alCerrar: () => void }) {
  const esCaja = fila.tipo === "punto_venta";
  const [monto, setMonto] = useState(esCaja && fila.limite ? String(Number(fila.limite)) : "");
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [hecho, setHecho] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function asignar() {
    setEnviando(true);
    setError(null);
    try {
      if (fila.tipo === "administrador") {
        const r = await api.fondearAdministrador(fila.uuid, monto);
        setHecho(`Folio ${r.folio}. Saldo nuevo: ${pesos(r.saldo)}.`);
      } else if (fila.tipo === "cliente") {
        const r = await api.fondearCliente(fila.uuid, monto);
        setHecho(`Folio ${r.folio}. Saldo nuevo: ${pesos(r.saldo)}.`);
      } else {
        const r = await api.asignarLimite(fila.uuid, monto);
        setHecho(`Límite de la caja: ${pesos(r.limite)}.`);
      }
    } catch (e) {
      setError(e as ErrorAPI);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <Modal titulo={`${esCaja ? "Límite de" : "Asignar saldo a"} ${fila.nombre}`} alCerrar={alCerrar}>
      {hecho ? (
        <div className="space-y-4">
          <p className="text-sm">{hecho}</p>
          <div className="flex justify-end">
            <button type="button" onClick={alCerrar}
                    className="rounded-xl bg-primario px-5 py-3 text-sm font-bold text-white">Listo</button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-tinta-dos">
            {esCaja
              ? <>Límite actual: <b className="cifras">{pesos(fila.limite)}</b>. La caja vende sobre la bolsa de {fila.padre}.</>
              : <>Saldo actual: <b className="cifras">{pesos(fila.saldo)}</b>. Lo que captures se suma.</>}
          </p>
          <CampoTexto etiqueta={esCaja ? "Límite nuevo" : "Monto a asignar"} valor={monto}
                      alCambiar={(v) => setMonto(v.replace(/[^0-9.]/g, ""))} marcador="0.00" />
          <p className="rounded-xl bg-elegido p-3 text-xs">
            {esCaja
              ? "El límite no mueve dinero: reserva parte de la bolsa del cliente para esta caja."
              : "Este saldo lo asignas tú: no sale de Emida ni se descuenta del saldo de EPS en Emida. Queda registrado con tu nombre."}
          </p>
          {error && <Aviso error={error} />}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={alCerrar}
                    className="rounded-xl border border-campo px-5 py-3 text-sm font-bold">Cancelar</button>
            <button type="button" onClick={asignar}
                    disabled={!(esCaja ? Number(monto) >= 0 && monto !== "" : Number(monto) > 0) || enviando}
                    className="rounded-xl bg-primario px-5 py-3 text-sm font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
              {enviando ? "Guardando…" : esCaja ? "Fijar límite" : "Asignar"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/**
 * Quién puede suspender qué. El servidor lo vuelve a verificar; esto solo
 * evita pintar un botón que va a dar 403. Cada nivel sobre los de abajo.
 */
function puedeSuspender(perfil: Perfil, f: FilaRed): boolean {
  if (!["activo", "aprobado", "suspendido"].includes(f.estado)) return false;
  if (perfil.rol === "super_admin") return true;
  if (perfil.rol === "administrador") return f.tipo !== "administrador";
  if (perfil.rol === "cliente") return f.tipo === "punto_venta";
  return false;
}

const EFECTO_SUSPENSION: Record<TipoRed, string> = {
  administrador:
    "Deja de traspasar, sus clientes no reciben saldo y ninguna caja de su red vende. " +
    "No cambia el estado de sus clientes: al reactivarlo, cada uno queda como estaba.",
  cliente:
    "Deja de recibir saldo y todas sus cajas dejan de vender. Al reactivarlo, cada caja " +
    "vuelve al estado que tenía; una caja suspendida a mano sigue suspendida.",
  punto_venta:
    "Solo esta caja deja de vender. Sus cajeros pueden entrar, pero no vender.",
};

function SuspensionModal({ fila, alCerrar }: { fila: FilaRed; alCerrar: () => void }) {
  const reactivar = fila.estado === "suspendido";
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function aplicar() {
    setEnviando(true);
    setError(null);
    try {
      await api.suspension(fila.tipo, fila.uuid, reactivar);
      alCerrar();
    } catch (e) {
      setError(e as ErrorAPI);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <Modal titulo={`${reactivar ? "Reactivar" : "Suspender"} a ${fila.nombre}`} alCerrar={alCerrar}>
      <div className="space-y-4">
        <p className="text-sm">
          {reactivar
            ? `${fila.nombre} vuelve a operar. Las ventas y saldos que tenía siguen igual.`
            : EFECTO_SUSPENSION[fila.tipo]}
        </p>
        {!reactivar && (
          <p className="rounded-xl bg-ambar-fondo p-3 text-xs">
            Las ventas que ya salieron hacia el proveedor terminan solas en los siguientes
            segundos. El saldo no se mueve: si hay que recuperarlo, se retira aparte.
          </p>
        )}
        {error && <Aviso error={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={alCerrar}
                  className="rounded-xl border border-campo px-5 py-3 text-sm font-bold">Cancelar</button>
          <button type="button" onClick={aplicar} disabled={enviando} data-prueba="confirmar-suspension"
                  className={"rounded-xl px-5 py-3 text-sm font-bold text-white disabled:opacity-60 " +
                    (reactivar ? "bg-exito-oscuro" : "bg-alerta-oscuro")}>
            {enviando ? "Aplicando…" : reactivar ? "Reactivar" : "Suspender"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function AccesoModal({ fila, alCerrar }: { fila: FilaRed; alCerrar: () => void }) {
  const usuario = fila.usuario!;
  const activar = !usuario.activo;
  const [error, setError] = useState<ErrorAPI | null>(null);

  async function aplicar() {
    setError(null);
    try {
      await api.acceso(usuario.uuid, activar);
      alCerrar();
    } catch (e) {
      setError(e as ErrorAPI);
    }
  }

  return (
    <Modal titulo={`${activar ? "Reactivar" : "Desactivar"} acceso`} alCerrar={alCerrar}>
      <div className="space-y-4">
        <p className="text-sm">
          {activar
            ? <><b>{usuario.nombre}</b> ({usuario.correo}) podrá volver a entrar con su contraseña.</>
            : <><b>{usuario.nombre}</b> ({usuario.correo}) deja de poder entrar y se cierran sus
                sesiones abiertas. {fila.nombre} no cambia de estado.</>}
        </p>
        {error && <Aviso error={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={alCerrar}
                  className="rounded-xl border border-campo px-5 py-3 text-sm font-bold">Cancelar</button>
          <button type="button" onClick={aplicar} data-prueba="confirmar-acceso"
                  className="rounded-xl bg-primario px-5 py-3 text-sm font-bold text-white">
            {activar ? "Reactivar acceso" : "Desactivar acceso"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Aprobaciones
// ---------------------------------------------------------------------------
const MOTIVOS = [
  "Datos incompletos o incorrectos",
  "Ya existe en la red",
  "No pertenece a mi red",
  "Otro",
];

export function Aprobaciones({ alCambiar }: { alCambiar: () => void }) {
  const [pendientes, setPendientes] = useState<Pendiente[]>([]);
  const [rechazando, setRechazando] = useState<string>("");
  const [motivo, setMotivo] = useState("");
  const [error, setError] = useState<ErrorAPI | null>(null);
  const [aviso, setAviso] = useState("");

  const cargar = () => api.aprobaciones().then(setPendientes).catch((e) => setError(e as ErrorAPI));
  useEffect(() => { cargar(); }, []);

  async function resolver(p: Pendiente, aprobar: boolean) {
    setError(null);
    try {
      if (aprobar) await api.aprobar(p.tipo, p.uuid);
      else await api.rechazar(p.tipo, p.uuid, motivo);
      setAviso(`${p.nombre}: ${aprobar ? "aprobado" : "rechazado"}.`);
      setRechazando("");
      setMotivo("");
      await cargar();
      alCambiar();
    } catch (e) {
      setError(e as ErrorAPI);
    }
  }

  return (
    <Tarjeta titulo={`Pendientes · ${pendientes.length}`}>
      <p className="mb-4 text-sm text-tinta-dos">
        Altas que hizo el Super Admin en tu red. Nada de esto puede entrar ni operar hasta
        que lo apruebes. Rechazar es reversible.
      </p>
      {aviso && <p className="mb-3 rounded-xl bg-exito-fondo p-3 text-sm font-semibold text-exito-oscuro">{aviso}</p>}
      {error && <Aviso error={error} />}

      <div className="space-y-3">
        {pendientes.map((p) => (
          <div key={p.uuid} className="rounded-xl border border-borde p-4" data-pendiente={p.nombre}>
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="font-bold">{p.nombre}</p>
                <p className="text-xs text-apagado">
                  {p.tipo === "cliente" ? "Cliente" : "Punto de venta"} · de {p.padre}
                  {p.usuario && ` · ${p.usuario.correo}`}
                </p>
                {p.tipo === "cliente" && (
                  <p className="mt-1 text-xs text-tinta-dos">RFC {p.rfc || "—"} · margen {p.margen_pct}%</p>
                )}
                {p.tipo === "punto_venta" && p.limite && Number(p.limite) > 0 && (
                  <p className="mt-1 text-xs text-tinta-dos">
                    Bolsa propia de {pesos(p.limite)}, reservada desde el alta. Si la rechazas, se libera.
                  </p>
                )}
              </div>
              {rechazando !== p.uuid && (
                <div className="flex gap-2">
                  <button type="button" onClick={() => setRechazando(p.uuid)}
                          className="rounded-xl border border-campo px-4 py-2 text-sm font-bold text-alerta-oscuro">
                    Rechazar
                  </button>
                  <button type="button" onClick={() => resolver(p, true)} data-prueba="aprobar"
                          className="rounded-xl bg-exito-oscuro px-4 py-2 text-sm font-bold text-white">
                    Aprobar
                  </button>
                </div>
              )}
            </div>
            {rechazando === p.uuid && (
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <label className="block min-w-[220px] flex-1">
                  <span className="mb-1.5 block text-xs font-bold text-tinta-dos">Motivo del rechazo</span>
                  <select value={motivo} onChange={(e) => setMotivo(e.target.value)}
                          className="w-full rounded-xl border border-campo bg-white px-3.5 py-2.5 text-sm">
                    <option value="">Elige un motivo</option>
                    {MOTIVOS.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </label>
                <button type="button" onClick={() => { setRechazando(""); setMotivo(""); }}
                        className="rounded-xl border border-campo px-4 py-2.5 text-sm font-bold">Cancelar</button>
                <button type="button" disabled={!motivo} onClick={() => resolver(p, false)}
                        className="rounded-xl bg-alerta-oscuro px-4 py-2.5 text-sm font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]">
                  Confirmar rechazo
                </button>
              </div>
            )}
          </div>
        ))}
        {pendientes.length === 0 && (
          <p className="py-8 text-center text-sm text-apagado">
            Todo al día: no hay solicitudes esperando tu aprobación.
          </p>
        )}
      </div>
    </Tarjeta>
  );
}
