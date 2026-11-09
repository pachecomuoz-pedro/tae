/** Paleta Scorpion, la misma del prototipo. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        rail: { DEFAULT: "#13243D", activo: "#1F4580", borde: "#2A4E8A",
                texto: "#C3D2E6", icono: "#7E9BC4", tenue: "#9FB3D1" },
        acento: "#F08A1C",
        primario: "#0A6CB4",
        marino: "#17386E",
        tinta: { DEFAULT: "#13243D", dos: "#35455C" },
        apagado: { DEFAULT: "#5A6C85", dos: "#94A3B8" },
        borde: "#DDE3EC",
        campo: "#C3CDDB",
        lienzo: "#F2F4F7",
        suave: "#EFF2F6",
        elegido: "#E8F1FA",
        exito: { DEFAULT: "#15803D", fondo: "#ECF6EF", borde: "#BFE0C9", oscuro: "#14532D" },
        ambar: { DEFAULT: "#B35C0A", fondo: "#FEF4E8", borde: "#F7D4A6", texto: "#8A4408" },
        alerta: { DEFAULT: "#C21B12", fondo: "#FDECEA", oscuro: "#8A1711" },
      },
      fontFamily: {
        display: ["'Space Grotesk'", "sans-serif"],
        sans: ["Manrope", "sans-serif"],
      },
    },
  },
  plugins: [],
};
