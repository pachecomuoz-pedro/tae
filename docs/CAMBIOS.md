# Cambios de noviembre de 2026

Resumen de lo que cambió respecto a la versión anterior, para quien ya conocía el código. El porqué de cada cosa está en `DECISIONES.md`.

## Correcciones

- **Login fallido con el proveedor** (`proveedor/venta.py`). Antes la excepción `NoAutenticado` subía como error 500 y dejaba la reserva viva para siempre. Ahora, si falla antes del envío, se libera y la API responde 503 con clave `proveedor`. Si falla durante las consultas, la reserva se queda viva (decisión 10). Lo mismo aplica en `reintentar_sin_resolver`.
- **El Super Admin no podía entrar.** Su segundo factor era obligatorio y no había forma de activarlo. Ahora existe el comando `preparar_super_admin`.
- **Rutas fijas a otra máquina** en `sembrar_demo.py` y `pruebas_e2e.py`. Ahora son relativas al proyecto.
- **`confirmar` del restablecimiento por correo** no validaba la contraseña nueva. Ahora pasa por los validadores de Django.

## Nuevo

- **Contraseña temporal y cambio obligatorio** (`identidad/contrasenas.py`, `api/permisos_http.py`, decisión 15). Incluye el campo `Usuario.debe_cambiar_contrasena` y el endpoint `auth/cambiar`.
- **El restablecimiento por el superior** entrega una contraseña temporal en lugar de una liga.
- **Estado inicial según quién crea**, rechazo con motivo y estado `rechazado` para clientes y cajas (decisión 16).
- **`BitacoraAdministracion`** (decisión 17).
- **Endpoints de la red** en `api/vistas_red.py`: altas, `red`, `aprobaciones` y `usuarios/restablecer`.
- **Frontend** (`web/src/Red.tsx`): cambio obligatorio de contraseña, Usuarios de la red con alta y restablecimiento, y Aprobaciones con contador en el menú.
- **`PROVEEDOR_MODO=simulado`**, con candado para producción (decisión 18).
- **`PROVEEDOR_PREFIJO_OPERACIONES`**, por si axexo confirma que las operaciones viven bajo `/integratorapi`.
- **Producción:** ajustes de seguridad en `settings.py`, gunicorn y la carpeta `deploy/` (Compose, nginx, respaldo y cron).
- **Docstrings** en toda función y clase pública, y referencias a decisiones en los módulos clave.

## Migraciones

- `core/0002_rechazo_con_motivo`
- `identidad/0003_cambio_obligatorio_y_bitacora_administracion`

Son aditivas: no borran ni cambian datos existentes. Los usuarios ya existentes quedan con `debe_cambiar_contrasena = False`.

## Pruebas

Antes había 176; tras la primera entrega, 215.


---

## Segunda entrega: conexión con Emida UAT

### Nuevo

- **Saldo de EPS en Emida, solo para el Super Admin** (decisión 23). Pantalla *Exposición* con botón "Consultar saldo en Emida". Cada consulta se guarda en `ConsultaSaldoProveedor` y se muestra junto al total asignado en la red. Endpoint `proveedor/saldo`.
- **Asignar saldo a un administrador desde la pantalla** (Usuarios de la red, solo Super Admin). Endpoint `administradores/fondeo`. Antes solo existía en código.
- **Catálogo diario desde Emida** (decisión 24): `sincronizar_catalogo` (productos y product-flow) y `vincular_producto`. Tabla nueva `ProductoDelProveedor` y campo `ProductoProveedor.pausada_por_proveedor`.
- **Contenedor `tareas`** (decisión 25): catálogo diario y reintentos cada 5 minutos, en Windows y en Linux.
- **`probar_proveedor`**: diagnóstico de login, carriers, productos, product-flow, saldo y lookup, más la prueba de prefijos y la evidencia en JSON.
- **`exportar_evidencia`**: JSON por venta y `reporte_emida.csv` en formato de conciliación de Emida.
- **Cliente del proveedor:** `productos_ext`, `product_flow` y `Merchant-Id` en el saldo. Las respuestas en lista (catálogos) ya no rompen la interpretación.
- **`docs/PRUEBAS_UAT.md`**: guía para Windows de la certificación de tiempo aire, caso por caso.

### Migraciones

- `catalogo/0002_oferta_del_proveedor_y_pausa`
- `proveedor/0002_consulta_saldo_proveedor`

Son aditivas.

### Variables nuevas del `.env`

`TAE_INQUILINO`, `TAE_HORA_CATALOGO` y `TAE_REINTENTOS_MINUTOS`.

### Pruebas

Ahora hay 234 y todas pasan.


---

## Tercera entrega: instalación de primera vez

- **`manage.py instalar`**: crea el inquilino y cuatro usuarios (Super Admin, administrador, cliente y caja) sin saldo ni catálogo de demo; contraseña de `TAE_CLAVE_INICIAL` o aleatoria, con cambio obligatorio; activa el segundo factor del Super Admin; y trae de Emida el catálogo real (publicado en la caja) y el saldo real de EPS. Es idempotente.
- **El Super Admin asigna saldo a toda la red**: a clientes de forma directa (`clientes/fondeo`, sin margen ni pasar por el administrador) y límites de cajas, además de administradores. Botones en Usuarios de la red.
- `sincronizacion.vincular_todo`: publica el catálogo completo, solo durante la instalación.
- Corrección: el QR del segundo factor se deformaba al imprimirse en la terminal.
- Variables nuevas en `.env.example`: `TAE_CLAVE_INICIAL`, `TAE_NOMBRE_INQUILINO` y los correos y nombres de los cuatro usuarios iniciales.
- `sembrar_demo.py` queda solo para `pruebas_e2e.py`.
- Pruebas: 242, todas pasan.


---

## Quinta entrega: todo en contenedores

- **Renombre:** el servicio de Django pasa de `web` a **`api`**; **`web`** ahora es React. Todos los comandos son `docker compose exec api …`.
- **`web/Dockerfile`** con dos etapas: `desarrollo` (Vite con recarga en vivo, sondeo para Windows) y `produccion` (Nginx con el build, que reenvía `/api` a `api`).
- **`docker-compose.yml`** (desarrollo): db, cache, api, tareas y web. Ya no hace falta instalar Node.js en Windows.
- **`deploy/docker-compose.prod.yml`**: los mismos cinco servicios. Solo `web` publica puerto (`127.0.0.1:8080`) y los estáticos de Django van en un volumen compartido.
- **`deploy/nginx-tae.conf`**: el Nginx del servidor solo pone HTTPS y reenvía a `web`.
- **`DESPLIEGUE.md`**: sin Node.js ni copia manual del build; React se compila en su contenedor.
- **`.env.example`**: `DJANGO_ALLOWED_HOSTS` incluye `api`, porque el proxy de Vite llama a Django con ese nombre.
