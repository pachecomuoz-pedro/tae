# Guía de despliegue — Plataforma TAE

De un servidor vacío a producción con HTTPS, conexión a axexo, tareas programadas y respaldos. Está pensada para una sola persona y se sigue en orden. Los archivos que se mencionan ya están en la carpeta `deploy/` del repositorio.

> Los comandos son para **Ubuntu 24.04** y se corren en el servidor, por SSH. Donde dice `tae.tudominio.mx`, pon tu dominio.

## Arquitectura

```
Internet ──HTTPS──▶ nginx (en el servidor)
                     ├── /         → frontend React compilado (/var/www/tae)
                     ├── /api/     → Django + gunicorn  (contenedor web, 127.0.0.1:8000)
                     └── /static/  → archivos estáticos de Django
                                       │
                       contenedores ───┼── db     MySQL 8   (sin puerto público)
                                       └── cache  Redis 7   (sin puerto público)

Contenedor web ──HTTPS──▶ axexo / Emida   (requiere la IP del servidor autorizada)
contenedor tareas ──▶ catálogo diario (04:00) · reintentos cada 5 min
cron del servidor ──▶ respaldo diario
```

## Paso 0. Antes de empezar

| Necesitas | Detalle |
|---|---|
| Un servidor | 2 vCPU, 4 GB de RAM, 40 GB de disco, Ubuntu 24.04 |
| **IP pública fija** | Es la que axexo tiene que autorizar |
| Dominio | Un registro DNS `A` que apunte a esa IP |
| Credenciales de **producción** de axexo | Distintas a las de UAT; llegan después de certificar |
| Un teléfono con app de códigos | Google Authenticator, Microsoft Authenticator o similar, para el Super Admin |

Antes de producción, la plataforma debe haber pasado la certificación de tiempo aire en UAT: ver `docs/PRUEBAS_UAT.md`.

**Haz esto primero, porque es lo que más tarda:** envía a axexo la IP del servidor para que la autoricen. Pregunta también si las operaciones (topup y lookup) viven bajo `/integratorapi`; la respuesta define `PROVEEDOR_PREFIJO_OPERACIONES`.

## Paso 1. Preparar el servidor

```bash
sudo apt update && sudo apt upgrade -y
sudo adduser tae && sudo usermod -aG sudo tae

# Firewall: solo SSH, HTTP y HTTPS
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443
sudo ufw enable
```

De aquí en adelante, entra como `tae`.

## Paso 2. Instalar Docker, nginx, Certbot y Node.js

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker tae          # sal y vuelve a entrar por SSH

sudo apt install -y nginx certbot python3-certbot-nginx
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

Verifica que `docker compose version`, `nginx -v` y `node -v` respondan sin error.

## Paso 3. Descargar el código

```bash
sudo mkdir -p /opt/tae && sudo chown tae:tae /opt/tae
cd /opt/tae
git clone <URL-del-repositorio> .
```

## Paso 4. El `.env` de producción

```bash
cp .env.example .env && nano .env && chmod 600 .env
```

Cambia como mínimo:

```ini
DJANGO_SECRET_KEY=        # python3 -c "import secrets; print(secrets.token_urlsafe(64))"
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=tae.tudominio.mx
DJANGO_CSRF_ORIGINS=https://tae.tudominio.mx

DB_PASSWORD=              # larga, distinta a la de desarrollo
DB_ROOT_PASSWORD=         # otra distinta

PROVEEDOR_MODO=real
PROVEEDOR_BASE_URL=       # solo el host de producción, sin rutas
PROVEEDOR_PREFIJO_OPERACIONES=   # vacío o /integratorapi, según axexo
PROVEEDOR_USUARIO=
PROVEEDOR_CONTRASENA=
PROVEEDOR_SITE_ID=
PROVEEDOR_CLERK_ID=
PROVEEDOR_MERCHANT_ID=

TAE_URL_BASE=https://tae.tudominio.mx
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=  EMAIL_HOST_USER=  EMAIL_HOST_PASSWORD=   # para la recuperación por correo
```

La aplicación **se niega a arrancar** si dejas la `SECRET_KEY` de desarrollo o `PROVEEDOR_MODO=simulado` con `DEBUG=false`. Es a propósito.

## Paso 5. Levantar el backend

Desde ahora, `PROD` abrevia el comando de Compose de producción:

```bash
cd /opt/tae
PROD="docker compose -f deploy/docker-compose.prod.yml --env-file .env"

$PROD up -d --build
$PROD exec web python manage.py migrate
$PROD exec web python manage.py collectstatic --noinput
$PROD exec web python manage.py check --deploy
```

`check --deploy` debe salir sin errores graves. Prueba también `curl -I http://127.0.0.1:8000/api/yo`: 401 o 403 es lo correcto; 500 no.

## Paso 6. Instalación de primera vez

Primero comprueba la conexión con Emida (paso 10, primer comando). Después:

```bash
$PROD exec web python manage.py instalar
```

Crea el inquilino y los cuatro usuarios iniciales con los correos del `.env` (`TAE_SUPERADMIN_CORREO`, `TAE_ADMIN_CORREO`, `TAE_CLIENTE_CORREO`, `TAE_CAJA_CORREO`). Muestra **una sola vez** sus contraseñas, que deben cambiarse al entrar, y pide escanear el QR del segundo factor del Super Admin. Luego trae de Emida el catálogo real y el saldo real de EPS. Los saldos de la red quedan en cero: los asigna el Super Admin desde la pantalla.

Si el código del QR falla, actívalo después con:

```bash
$PROD exec web python manage.py preparar_super_admin --correo <correo-del-super-admin> --solo-totp
```

## Paso 7. Compilar el frontend

```bash
cd /opt/tae/web
npm ci && npm run build
sudo mkdir -p /var/www/tae && sudo rsync -a --delete dist/ /var/www/tae/
```

## Paso 8. nginx

```bash
sudo cp /opt/tae/deploy/nginx-tae.conf /etc/nginx/sites-available/tae
sudo sed -i 's/tae.tudominio.mx/TU-DOMINIO-REAL/' /etc/nginx/sites-available/tae
sudo ln -s /etc/nginx/sites-available/tae /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## Paso 9. HTTPS

```bash
sudo certbot --nginx -d tae.tudominio.mx
sudo certbot renew --dry-run          # confirma que la renovación automática funciona
```

Cuando el HTTPS lleve unas semanas estable, sube `DJANGO_HSTS_SEGUNDOS` a `31536000` y recrea `web`.

## Paso 10. Verificar el proveedor

Primero, que el contenedor llegue a axexo:

```bash
$PROD exec web python -c "import os,requests; print(requests.get(os.environ['PROVEEDOR_BASE_URL'], timeout=15).status_code)"
```

Cualquier código HTTP significa que hay conexión. Un **timeout** significa que axexo aún no autoriza la IP (paso 0).

Después haz **una venta real del monto mínimo** a un número propio y confirma tres cosas: que llegó la recarga, que el saldo se descontó una sola vez y que aparece con folio y autorización. Si la venta responde 404 del proveedor, ajusta `PROVEEDOR_PREFIJO_OPERACIONES` y recrea `web`.

## Paso 11. Tareas programadas

Las tareas de la plataforma las corre el contenedor **`tareas`**, que ya se levantó en el paso 5 junto con `web` (decisión 25):

- **Catálogo diario** desde Emida (`sincronizar_catalogo`), a las `TAE_HORA_CATALOGO` (04:00 por omisión).
- **Reintentos de ventas sin resolver** (`reintentar_pendientes`), cada `TAE_REINTENTOS_MINUTOS` (5). **No es opcional:** sin ellos, una venta sin resolver se queda así para siempre, con el saldo del tendero apartado.

Comprueba que esté arriba y trabajando:

```bash
$PROD ps tareas
$PROD logs --tail=30 tareas
```

En el crontab del servidor solo va el respaldo:

```bash
mkdir -p /opt/tae/respaldos
crontab -e        # pega el contenido de /opt/tae/deploy/crontab.ejemplo
```

Ese archivo trae comentadas las líneas equivalentes al contenedor `tareas`. Úsalas solo si apagas el contenedor: nunca los dos a la vez.

La instalación (paso 6) ya cargó el catálogo; desde ahí lo actualiza `tareas` cada día.

## Paso 12. Respaldos

`deploy/respaldo.sh` guarda un respaldo diario comprimido y conserva 14 días. Dos reglas:

1. **Copia los respaldos fuera del servidor**, a otro proveedor o a almacenamiento externo. Un respaldo en el mismo disco se pierde con el disco.
2. **Prueba una restauración** antes de abrir y después una vez al mes:

```bash
gunzip -c respaldos/tae_AAAA-MM-DD_HHMM.sql.gz | \
  $PROD exec -T db sh -c 'mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'
```

## Paso 13. Publicar una versión nueva

```bash
cd /opt/tae
./deploy/respaldo.sh                      # siempre antes de actualizar
git pull
$PROD up -d --build web tareas
$PROD exec web python manage.py migrate
$PROD exec web python manage.py collectstatic --noinput
cd web && npm ci && npm run build && sudo rsync -a --delete dist/ /var/www/tae/
```

Publica fuera del horario de más venta. Para regresar a la versión anterior, haz `git checkout <commit-anterior>` y repite; si una migración ya cambió la base, restaura el respaldo que acabas de hacer.

## Operación diaria

| Tarea | Comando |
|---|---|
| ¿Está todo arriba? | `$PROD ps` |
| Logs del backend | `$PROD logs --tail=100 web` |
| Ventas sin resolver, a mano | `$PROD exec web python manage.py reintentar_pendientes` |
| Conciliar el archivo del proveedor | `$PROD exec web python manage.py conciliar /ruta/archivo.csv` (con `--sin-resolver` primero) |
| Aplicar cambios del `.env` | `$PROD up -d --force-recreate web tareas` |
| Programador de tareas | `$PROD logs --tail=50 tareas` |
| Diagnóstico de Emida | `$PROD exec web python manage.py probar_proveedor` |
| Contraseña olvidada de un Super Admin | `$PROD exec web python manage.py changepassword <correo>` |

---

## Lista de verificación antes de abrir

Si algún punto falta, no se abre.

**Proveedor**
- [ ] Certificación con axexo aprobada
- [ ] Credenciales de **producción** en el `.env` y `PROVEEDOR_MODO=real`
- [ ] Prefijo de operaciones confirmado con axexo
- [ ] Venta real del monto mínimo verificada de punta a punta
- [ ] Contenedor `tareas` arriba (catálogo diario y reintentos)
- [ ] Catálogo sincronizado y productos vinculados

**Seguridad**
- [ ] `DJANGO_DEBUG=false` y `SECRET_KEY` nueva
- [ ] Contraseñas de base de datos nuevas (`DB_PASSWORD` y `DB_ROOT_PASSWORD`)
- [ ] HTTPS activo; HTTP redirige a HTTPS
- [ ] MySQL y Redis sin puertos públicos (`sudo ss -tlnp` no muestra 3306 ni 6379 en `0.0.0.0`)
- [ ] **Límite de intentos de entrada implementado** (pendiente en el código)
- [ ] `instalar` corrido; los cuatro usuarios cambiaron su contraseña inicial
- [ ] Super Admin con segundo factor activo
- [ ] `.env` con permisos `600` y fuera de git

**Continuidad**
- [ ] Respaldo diario funcionando, con copia fuera del servidor
- [ ] Una restauración de prueba completada
- [ ] Renovación de certificados probada
