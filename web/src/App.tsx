/**
 * Cascarón de la aplicación: sesión, rail y la pantalla que toque.
 *
 * El perfil que devuelve el servidor trae capacidades, y con eso se decide
 * qué pintar. No es el permiso: el permiso se vuelve a verificar en el
 * servidor cuando se aprieta el botón. Aquí solo se evita enseñar un botón
 * que de todos modos iba a dar 403.
 */
import { useCallback, useEffect, useState } from "react";

import { Clientes } from "./Admin";
import { ErrorAPI, Perfil, Saldo, api, pesos } from "./api";
import { PuntosVenta } from "./Cliente";
import { Exposicion } from "./Exposicion";
import { Aprobaciones, CambiarContrasena, Red } from "./Red";
import { Venta } from "./Venta";

/**
 * Qué pantallas ve cada rol.
 *
 * Esto decide qué pintar, no qué se permite: el servidor vuelve a verificar
 * cada acción. Si alguien edita este arreglo en su navegador, lo único que
 * consigue es ver botones que le van a devolver 403.
 */
type Pantalla = { clave: string; titulo: string };

function pantallasDe(perfil: Perfil): Pantalla[] {
  // Un menú por rol, igual en todas sus pantallas (decisión 21). Los
  // nombres siguen el canvas de diseño; solo aparecen las opciones que ya
  // existen en el backend.
  if (perfil.capacidades.vender) return [{ clave: "venta", titulo: "Venta de tiempo aire" }];
  if (perfil.rol === "cliente") {
    return [
      { clave: "cajas", titulo: "Mis puntos de venta" },
      { clave: "aprobaciones", titulo: "Aprobaciones" },
      { clave: "red", titulo: "Cajeros" },
    ];
  }
  if (perfil.rol === "super_admin") {
    return [
      { clave: "exposicion", titulo: "Exposición" },
      { clave: "red", titulo: "Usuarios de la red" },
      { clave: "aprobaciones", titulo: "Aprobaciones" },
      { clave: "traspaso", titulo: "Traspaso de saldo" },
      { clave: "retiro", titulo: "Retiro de saldo" },
    ];
  }
  return [
    { clave: "red", titulo: "Usuarios de la red" },
    { clave: "aprobaciones", titulo: "Aprobaciones" },
    { clave: "traspaso", titulo: "Traspaso de saldo" },
    { clave: "retiro", titulo: "Retiro de saldo" },
  ];
}

export function App() {
  const [perfil, setPerfil] = useState<Perfil | null>(null);
  const [saldo, setSaldo] = useState<Saldo | null>(null);
  const [cargando, setCargando] = useState(true);
  const [pantalla, setPantalla] = useState("");
  const [pendientes, setPendientes] = useState(0);

  /**
   * Refresca lo que se ve en el encabezado y en el menú: el saldo y el
   * contador de Aprobaciones. Si el perfil debe cambiar contraseña la API
   * negaría las dos llamadas, así que ni se intentan.
   */
  const refrescarSaldo = useCallback(() => {
    api.saldo().then(setSaldo).catch(() => setSaldo(null));
    api.aprobaciones().then((l) => setPendientes(l.length)).catch(() => setPendientes(0));
  }, []);

  useEffect(() => {
    api
      .yo()
      .then((p) => {
        setPerfil(p);
        setPantalla(pantallasDe(p)[0]?.clave ?? "");
        if (!p.debe_cambiar_contrasena) refrescarSaldo();
      })
      .catch(() => setPerfil(null))
      .finally(() => setCargando(false));
  }, [refrescarSaldo]);

  if (cargando) {
    return (
      <div className="grid h-full place-items-center text-apagado">Cargando…</div>
    );
  }

  if (!perfil) {
    return (
      <Entrar
        alEntrar={(p) => {
          setPerfil(p);
          setPantalla(pantallasDe(p)[0]?.clave ?? "");
          if (!p.debe_cambiar_contrasena) refrescarSaldo();
        }}
      />
    );
  }

  // Entró con contraseña temporal: es lo único que puede hacer (decisión 15).
  if (perfil.debe_cambiar_contrasena) {
    return (
      <CambiarContrasena
        perfil={perfil}
        alTerminar={(p) => { setPerfil(p); refrescarSaldo(); }}
        alSalir={async () => {
          await api.salir().catch(() => undefined);
          setPerfil(null);
        }}
      />
    );
  }

  return (
    <div className="flex h-full flex-col md:flex-row">
      <Rail
        perfil={perfil}
        pantallas={pantallasDe(perfil)}
        pendientes={pendientes}
        actual={pantalla}
        alElegir={setPantalla}
        alSalir={async () => {
          await api.salir().catch(() => undefined);
          setPerfil(null);
          setSaldo(null);
        }}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-borde bg-white px-5 py-4">
          <div>
            <p className="text-[11px] uppercase tracking-wide text-apagado">
              {perfil.entidad || perfil.rol_nombre}
            </p>
            <h1 className="font-display text-lg font-bold">
              {pantallasDe(perfil).find((p) => p.clave === pantalla)?.titulo ?? "Tablero"}
            </h1>
          </div>
          {saldo?.saldo && (
            <div className="text-right">
              <p className="text-[11px] uppercase tracking-wide text-apagado">
                {saldo.titular}
              </p>
              <p className="cifras font-display text-2xl font-bold text-primario">
                {pesos(saldo.disponible ?? saldo.saldo)}
              </p>
            </div>
          )}
        </header>

        <main className="flex-1 overflow-y-auto p-5">
          {pantalla === "venta" && (
            <Venta saldo={saldo} alCambiarSaldo={refrescarSaldo} />
          )}
          {pantalla === "cajas" && (
            <PuntosVenta saldo={saldo} alCambiar={refrescarSaldo} />
          )}
          {pantalla === "traspaso" && (
            <Clientes modo="traspaso" alCambiar={refrescarSaldo} />
          )}
          {pantalla === "retiro" && (
            <Clientes modo="retiro" alCambiar={refrescarSaldo} />
          )}
          {pantalla === "exposicion" && <Exposicion />}
          {pantalla === "red" && <Red perfil={perfil} alCambiar={refrescarSaldo} />}
          {pantalla === "aprobaciones" && <Aprobaciones alCambiar={refrescarSaldo} />}
        </main>
      </div>
    </div>
  );
}

function Rail({ perfil, pantallas, pendientes, actual, alElegir, alSalir }: {
  perfil: Perfil;
  pantallas: Pantalla[];
  pendientes: number;
  actual: string;
  alElegir: (clave: string) => void;
  alSalir: () => void;
}) {
  return (
    <nav className="flex shrink-0 flex-row items-center gap-3 bg-rail px-4 py-3 md:w-56 md:flex-col md:items-stretch md:py-6">
      <div className="flex items-center gap-2.5">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-acento font-display text-sm font-bold text-rail">
          S
        </span>
        <span className="font-display text-lg font-bold text-white">
          EPS <span className="text-acento">TAE</span>
        </span>
      </div>

      {pantallas.length > 1 && (
        <div className="flex gap-1 md:mt-6 md:flex-col">
          {pantallas.map((p) => (
            <button
              key={p.clave}
              type="button"
              onClick={() => alElegir(p.clave)}
              data-pantalla={p.clave}
              className={
                "rounded-lg px-3 py-2.5 text-left text-sm transition " +
                (actual === p.clave
                  ? "bg-rail-activo font-semibold text-white"
                  : "text-rail-texto hover:bg-rail-activo/50")
              }
            >
              <span className="flex items-center gap-2">
                <span className="flex-1">{p.titulo}</span>
                {p.clave === "aprobaciones" && pendientes > 0 && (
                  <span className="min-w-5 rounded-full bg-acento px-1.5 text-center text-[11px] font-bold text-rail">
                    {pendientes}
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="ml-auto md:ml-0 md:mt-auto md:border-t md:border-rail-borde md:pt-4">
        <p className="hidden text-[11px] uppercase tracking-wide text-rail-tenue md:block">
          Sesión
        </p>
        <p className="text-sm font-semibold text-white">{perfil.nombre}</p>
        <p className="hidden text-xs text-rail-tenue md:block">{perfil.rol_nombre}</p>
        <button
          type="button"
          onClick={alSalir}
          data-prueba="salir"
          className="mt-2 text-xs text-rail-texto underline underline-offset-2"
        >
          Salir
        </button>
      </div>
    </nav>
  );
}

function Entrar({ alEntrar }: { alEntrar: (p: Perfil) => void }) {
  const [correo, setCorreo] = useState("");
  const [password, setPassword] = useState("");
  const [codigo, setCodigo] = useState("");
  const [pideCodigo, setPideCodigo] = useState(false);
  const [error, setError] = useState<string>("");
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setError("");
    try {
      alEntrar(await api.entrar(correo, password, codigo || undefined));
    } catch (err) {
      const fallo = err as ErrorAPI;
      if (fallo.clave === "segundo_factor") {
        setPideCodigo(true);
        setError(fallo.message);
      } else {
        setError(fallo.message);
      }
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="grid h-full place-items-center bg-rail px-5">
      <form
        onSubmit={enviar}
        className="w-full max-w-sm rounded-2xl bg-white p-7"
        data-prueba="entrar"
      >
        <p className="font-display text-xl font-bold">
          EPS <span className="text-acento">TAE</span>
        </p>
        <p className="mt-1 text-sm text-apagado">Entra para vender.</p>

        <label className="mt-6 block">
          <span className="mb-1.5 block text-xs font-bold text-tinta-dos">Correo</span>
          <input
            value={correo}
            onChange={(e) => setCorreo(e.target.value)}
            type="email"
            autoComplete="username"
            data-prueba="correo"
            className="w-full rounded-xl border border-campo px-3.5 py-3 outline-none focus:border-primario"
          />
        </label>

        <label className="mt-4 block">
          <span className="mb-1.5 block text-xs font-bold text-tinta-dos">Contraseña</span>
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            autoComplete="current-password"
            data-prueba="password"
            className="w-full rounded-xl border border-campo px-3.5 py-3 outline-none focus:border-primario"
          />
        </label>

        {pideCodigo && (
          <label className="mt-4 block">
            <span className="mb-1.5 block text-xs font-bold text-tinta-dos">
              Código de seis dígitos
            </span>
            <input
              value={codigo}
              onChange={(e) => setCodigo(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric"
              data-prueba="codigo"
              className="cifras w-full rounded-xl border border-campo px-3.5 py-3 font-display text-lg font-bold outline-none focus:border-primario"
            />
          </label>
        )}

        {error && (
          <p className="mt-4 rounded-xl bg-alerta-fondo p-3 text-sm font-semibold text-alerta-oscuro" data-prueba="error-entrar">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={enviando}
          className="mt-6 w-full rounded-xl bg-primario py-3.5 font-display font-bold text-white disabled:bg-[#D3D9E1] disabled:text-[#7A8699]"
        >
          {enviando ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}
