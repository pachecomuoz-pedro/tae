import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/*
 * En desarrollo el front corre en 5173 y la API en 8000. El proxy los deja
 * en el mismo origen para que la cookie de sesión funcione igual que en
 * producción, donde Nginx sirve los dos desde un solo dominio.
 *
 * VITE_API_DESTINO: a dónde manda el proxy las llamadas a /api.
 *   - Con Docker (docker-compose.yml): http://api:8000, el contenedor de Django.
 *   - Fuera de Docker (npm run dev en tu equipo): http://127.0.0.1:8000.
 * VITE_POLLING=1: en Windows con Docker, los cambios de archivos no siempre
 * llegan al contenedor; el sondeo hace que la recarga en vivo funcione.
 */
const destino = process.env.VITE_API_DESTINO || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
  server: {
    host: true, // escucha también fuera del contenedor
    port: 5173,
    watch: { usePolling: process.env.VITE_POLLING === "1", interval: 300 },
    proxy: { "/api": { target: destino, changeOrigin: true } },
  },
});
