# Frontend React (Vite). Dos etapas finales, según el uso:
#
#   desarrollo  → servidor de Vite con recarga en vivo (docker-compose.yml)
#   produccion  → archivos compilados servidos por Nginx, que además pasa
#                 /api al contenedor de Django (deploy/docker-compose.prod.yml)

# --- Dependencias (se reutilizan en ambas) ------------------------------
FROM node:20-alpine AS dependencias
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# --- Desarrollo ----------------------------------------------------------
FROM dependencias AS desarrollo
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

# --- Compilación ---------------------------------------------------------
FROM dependencias AS compilacion
COPY . .
RUN npm run build

# --- Producción ----------------------------------------------------------
FROM nginx:1.27-alpine AS produccion
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=compilacion /app/dist /usr/share/nginx/html
EXPOSE 80
