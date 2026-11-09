import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
  server: {
    // En desarrollo el front corre en 5173 y la API en 8000. El proxy los
    // deja en el mismo origen para que la cookie de sesión funcione igual
    // que en producción, donde Nginx sirve los dos desde un solo dominio.
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});
