/**
 * Cliente de la API.
 *
 * Lo único que hace de más es traducir los códigos de error a algo que la
 * pantalla pueda decirle a la persona. El servidor manda `clave` justamente
 * para eso: "saldo" no se le cuenta igual a un cajero que "permiso", y un
 * mensaje genérico lo deja sin saber qué hacer.
 */

export type Desenlace = "aprobada" | "rechazada" | "sin_resolver";

export class ErrorAPI extends Error {
  constructor(
    mensaje: string,
    readonly http: number,
    readonly clave: string = "",
  ) {
    super(mensaje);
  }

  /** Qué puede hacer la persona al respecto. */
  get consejo(): string {
    switch (this.clave) {
      case "saldo":
        return "Pide saldo a tu cliente antes de volver a intentar.";
      case "permiso":
        return "Si crees que sí deberías poder, habla con quien te dio de alta.";
      case "segundo_factor":
        return "Abre tu aplicación de códigos y captura el de seis dígitos.";
      case "tope_diario":
        return "Ya llegaste a tu tope del día. Se avisó al Super Admin.";
      case "limite":
        return "Baja el límite de otra caja antes de subir el de ésta.";
      case "sin_ruta":
        return "Ese producto no tiene proveedor que lo surta en este momento.";
      case "motivo":
        return "Elige un motivo del catálogo.";
      case "cambiar_contrasena":
        return "Elige una contraseña nueva para continuar.";
      case "contrasena":
        return "Usa al menos 12 caracteres, que no sean solo números ni una palabra común.";
      case "proveedor":
        return "La venta no se hizo y tu saldo no se tocó. Intenta de nuevo en unos minutos.";
      case "datos":
        return "Revisa los datos capturados.";
      case "csrf":
        return (
          "El servidor no reconoce el origen de esta pantalla. Revisa " +
          "DJANGO_CSRF_ORIGINS en el .env del servidor."
        );
      default:
        return "";
    }
  }
}

function galleta(nombre: string): string {
  const parte = document.cookie.split("; ").find((c) => c.startsWith(nombre + "="));
  return parte ? decodeURIComponent(parte.split("=")[1]) : "";
}

async function pedir<T>(ruta: string, opciones: RequestInit = {}): Promise<T> {
  const respuesta = await fetch("/api" + ruta, {
    ...opciones,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": galleta("csrftoken"),
      ...(opciones.headers || {}),
    },
  });

  if (respuesta.status === 204) return undefined as T;

  let cuerpo: any = null;
  try {
    cuerpo = await respuesta.json();
  } catch {
    cuerpo = null;
  }

  if (!respuesta.ok) {
    /*
     * Tres fuentes posibles para el mensaje, en orden:
     *  - `error`, que es lo que manda nuestra API,
     *  - `detail`, que es lo que manda Django REST Framework cuando el
     *    error ocurre antes de llegar a nuestras vistas (CSRF, parseo),
     *  - y si no hay cuerpo JSON, al menos el código HTTP.
     *
     * Antes aquí solo se miraba `error`, y cualquier fallo de la segunda
     * clase salía como "algo salió mal y no sabemos qué". Ese mensaje le
     * costó una tarde a alguien: lo que fallaba era el `.env`, y la
     * pantalla no tenía forma de decirlo.
     */
    const detalle = typeof cuerpo?.detail === "string" ? cuerpo.detail : "";
    throw new ErrorAPI(
      cuerpo?.error || detalle || `El servidor respondió ${respuesta.status}`,
      respuesta.status,
      cuerpo?.clave || (detalle.includes("CSRF") ? "csrf" : ""),
    );
  }
  return cuerpo as T;
}

const json = (cuerpo: unknown) => ({ method: "POST", body: JSON.stringify(cuerpo) });

// --- tipos que devuelve el servidor ---------------------------------------
export interface Perfil {
  uuid: string;
  nombre: string;
  correo: string;
  rol: string;
  rol_nombre: string;
  entidad: string;
  totp_listo: boolean;
  /** Si es true, la API niega todo salvo cambiar la contraseña y salir. */
  debe_cambiar_contrasena: boolean;
  capacidades: Record<string, boolean>;
}

/** Lo que muestra la pantalla de Exposición del Super Admin. */
export interface SaldoEmida {
  saldo_emida: string | null;
  moneda: string;
  consultado: string | null;
  consultado_por: string;
  ultimo_error: string;
  ultimo_intento: string | null;
  asignado_red: string;
  diferencia: string | null;
}

/** Los tres niveles que se dan de alta desde la pantalla de la red. */
export type TipoRed = "administrador" | "cliente" | "punto_venta";

export interface UsuarioRed {
  uuid: string;
  correo: string;
  nombre: string;
  debe_cambiar_contrasena: boolean;
}

export interface FilaRed {
  tipo: TipoRed;
  uuid: string;
  nombre: string;
  estado: string;
  padre: string | null;
  motivo_rechazo?: string;
  responsable?: string;
  saldo?: string;
  limite?: string | null;
  modo_bolsa?: string;
  usuario: UsuarioRed | null;
}

/** Respuesta de un alta. `credencial_temporal` existe solo en esta respuesta. */
export interface Alta {
  tipo: TipoRed;
  uuid: string;
  nombre: string;
  estado: string;
  usuario: UsuarioRed | null;
  credencial_temporal: string | null;
}

export interface Pendiente {
  tipo: "cliente" | "punto_venta";
  uuid: string;
  nombre: string;
  padre: string;
  creado: string;
  rfc?: string;
  margen_pct?: string;
  direccion?: string;
  limite?: string | null;
  usuario: UsuarioRed | null;
}

export interface Producto {
  sku: string;
  nombre: string;
  carrier: string;
  tipo: string;
  tipo_monto: "fijo" | "rango" | "recibo";
  monto_fijo: string | null;
  monto_min: string | null;
  monto_max: string | null;
  multiplo: string | null;
  etiqueta_monto: string;
  codigo_barras: string;
}

export interface Saldo {
  titular: string;
  saldo: string | null;
  reservado?: string;
  disponible?: string;
  limites_asignados?: string;
  retirable?: string;
  modo_bolsa?: string;
  mi_caja?: { nombre: string; limite: string | null; disponible: string | null };
}

export interface ResultadoVenta {
  folio: string;
  invoice_no: number;
  desenlace: Desenlace;
  monto: string;
  detalle: string;
  autorizacion: string;
}

export interface PuntoVenta {
  uuid: string;
  nombre: string;
  direccion: string;
  estado: string;
  cliente: string;
  modo_bolsa: string;
  limite: string | null;
}

export interface ClienteFila {
  uuid: string;
  nombre: string;
  rfc: string;
  administrador: string;
  margen_pct: string;
  modo_bolsa: string;
  estado: string;
  saldo: string;
  reservado: string;
  limites_asignados: string;
  retirable: string;
  puntos_venta: number;
}

// --- llamadas --------------------------------------------------------------
export const api = {
  entrar: (correo: string, password: string, codigo_totp?: string) =>
    pedir<Perfil>("/auth/entrar", json({ correo, password, codigo_totp })),

  salir: () => pedir<void>("/auth/salir", { method: "POST" }),

  yo: () => pedir<Perfil>("/yo"),

  cambiarContrasena: (actual: string, nueva: string) =>
    pedir<Perfil>("/auth/cambiar", json({ actual, nueva })),

  red: (tipo: TipoRed) => pedir<FilaRed[]>(`/red?tipo=${tipo}`),

  altaAdministrador: (datos: { nombre: string; responsable: string; correo: string }) =>
    pedir<Alta>("/administradores/alta", json(datos)),

  altaCliente: (datos: {
    administrador?: string; nombre: string; rfc: string; margen_pct: string; correo: string;
  }) => pedir<Alta>("/clientes/alta", json(datos)),

  altaPuntoVenta: (datos: {
    cliente?: string; nombre: string; direccion: string; correo: string; limite?: string;
  }) => pedir<Alta>("/puntos-venta/alta", json(datos)),

  fondearAdministrador: (administrador: string, monto: string) =>
    pedir<{ folio: string; saldo: string }>("/administradores/fondeo", json({ administrador, monto })),

  fondearCliente: (cliente: string, monto: string) =>
    pedir<{ folio: string; saldo: string }>("/clientes/fondeo", json({ cliente, monto })),

  aprobaciones: () => pedir<Pendiente[]>("/aprobaciones"),

  aprobar: (tipo: string, uuid: string) =>
    pedir<{ estado: string }>("/aprobaciones/aprobar", json({ tipo, uuid })),

  rechazar: (tipo: string, uuid: string, motivo: string) =>
    pedir<{ estado: string }>("/aprobaciones/rechazar", json({ tipo, uuid, motivo })),

  /** Saldo de EPS en Emida. GET no llama a Emida; POST consulta y guarda. */
  saldoEmida: () => pedir<SaldoEmida>("/proveedor/saldo"),
  consultarSaldoEmida: () => pedir<SaldoEmida>("/proveedor/saldo", { method: "POST" }),

  restablecer: (usuario: string) =>
    pedir<{ usuario: UsuarioRed; credencial_temporal: string }>(
      "/usuarios/restablecer", json({ usuario }),
    ),

  catalogo: (tipo?: string) =>
    pedir<Producto[]>("/catalogo" + (tipo ? `?tipo=${tipo}` : "")),

  porCodigo: (codigo: string) =>
    pedir<Producto>(`/catalogo/codigo/${encodeURIComponent(codigo)}`),

  saldo: () => pedir<Saldo>("/saldo"),

  vender: (sku: string, cuenta: string, monto?: string) =>
    pedir<ResultadoVenta>("/venta", json({ sku, cuenta, monto })),

  puntosVenta: () => pedir<PuntoVenta[]>("/puntos-venta"),

  asignarLimite: (punto_venta: string, monto: string) =>
    pedir<{ punto_venta: string; limite: string; suma_limites: string; retirable: string }>(
      "/puntos-venta/limite",
      json({ punto_venta, monto }),
    ),

  clientes: () => pedir<ClienteFila[]>("/clientes"),

  traspasar: (cliente: string, monto: string, codigo_totp?: string) =>
    pedir<{ folio: string; cliente: string; saldo_cliente: string }>(
      "/traspasos",
      json({ cliente, monto, codigo_totp }),
    ),

  retirar: (
    cliente: string, monto: string, motivo: string,
    tipo: "devolucion" | "ajuste", nota = "",
  ) =>
    pedir<{ folio: string; cliente: string; saldo_cliente: string; retirable: string }>(
      "/retiros",
      json({ cliente, monto, motivo, tipo, nota }),
    ),
};

/** Dinero, como se lee en México. */
export function pesos(valor: string | number | null | undefined): string {
  if (valor === null || valor === undefined) return "—";
  const n = typeof valor === "string" ? Number(valor) : valor;
  if (Number.isNaN(n)) return "—";
  return n.toLocaleString("es-MX", {
    style: "currency",
    currency: "MXN",
    minimumFractionDigits: 2,
  });
}
