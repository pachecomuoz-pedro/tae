/** Piezas compartidas entre pantallas. Nada de lógica de negocio aquí. */
import { ErrorAPI } from "./api";

export function Tarjeta({ titulo, children }: {
  titulo: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-borde bg-white p-5">
      <h2 className="text-[11px] font-bold uppercase tracking-wide text-apagado">
        {titulo}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function Campo({ etiqueta, valor, alCambiar, marcador, prueba, malo, texto, refCampo }: {
  etiqueta: string;
  valor: string;
  alCambiar: (v: string) => void;
  marcador?: string;
  prueba?: string;
  malo?: boolean;
  texto?: boolean;
  refCampo?: React.RefObject<HTMLInputElement>;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-bold text-tinta-dos">{etiqueta}</span>
      <input
        ref={refCampo}
        value={valor}
        onChange={(e) => alCambiar(e.target.value)}
        placeholder={marcador}
        data-prueba={prueba}
        inputMode={texto ? "text" : "numeric"}
        className={
          "w-full rounded-xl border px-3.5 py-3 outline-none " +
          (texto ? "text-sm " : "cifras font-display text-lg font-bold ") +
          (malo ? "border-alerta text-alerta" : "border-campo focus:border-primario")
        }
      />
    </label>
  );
}

export function Aviso({ error }: { error: ErrorAPI }) {
  return (
    <div className="mt-4 rounded-xl bg-alerta-fondo p-3" data-prueba="error">
      <p className="text-sm font-semibold text-alerta-oscuro">{error.message}</p>
      {error.consejo && (
        <p className="mt-1 text-xs text-alerta-oscuro/80">{error.consejo}</p>
      )}
    </div>
  );
}
