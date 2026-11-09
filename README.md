# EPS TAE — Plataforma de Tiempo Aire Electrónico

Plataforma web para vender tiempo aire electrónico (y después pago de servicios y pines) a través de una red de Administradores, Clientes y Puntos de venta. El MVP opera conectado al proveedor **axexo / Emida** (Integrator API).

> **Cómo leer este repo**
> - Este README dice **qué hay, cómo se corre y dónde está cada cosa**.
> - El **por qué** de cada decisión está en [`docs/DECISIONES.md`](docs/DECISIONES.md). Los comentarios del código apuntan ahí con "decisión N".
> - Las **pruebas contra Emida UAT y la certificación de tiempo aire**, desde Windows, están en [`docs/PRUEBAS_UAT.md`](docs/PRUEBAS_UAT.md).
> - La **puesta en producción** paso a paso está en [`docs/DESPLIEGUE.md`](docs/DESPLIEGUE.md).
> - El **diseño de referencia** de las pantallas es el canvas *Punto de Venta TAE — Mockup navegable*: https://claude.ai/artifact/6VnnsooJFC8tyyXXqGhRGt

---

## 1. Qué hace

| Rol | Qué hace |
|---|---|
| **Super Admin** (EPS) | Da de alta Administradores, Clientes y Puntos de venta; asigna saldo a mano a toda la red (a administradores y clientes su saldo, a cajas su límite); es el único que ve el saldo de EPS en Emida. |
| **Administrador** | Da de alta y aprueba a sus Clientes, aprueba sus cajas, traspasa y retira saldo. |
| **Cliente** (tendero o sucursal) | Da de alta y aprueba sus propias cajas y reparte su bolsa entre ellas. No vende. |
| **Punto de venta** (caja) | Vende tiempo aire. |

El dinero baja: **Super Admin → Administrador → Cliente → caja**. Cada nivel solo ve y toca lo que cuelga de él.

**El saldo de la red no es el saldo de Emida** (decisión 23). Lo que tiene cada administrador, cliente y caja lo asigna el Super Admin a mano. El saldo de EPS en Emida es otro dinero, y solo lo ve el Super Admin en la pantalla Exposición.

## 2. Estado actual

| Área | Estado |
|---|---|
| Ledger, reservas, traspasos, retiros, límites por caja | Hecho y probado |
| Venta con timeout, consulta y reintentos del proveedor | Hecho y probado contra el emulador |
| Conciliación con el archivo del proveedor | Hecho y probado |
| Alta por nivel, aprobación, rechazo, bitácora | Hecho y probado (backend y pantallas) |
| Contraseña temporal con cambio obligatorio | Hecho y probado (backend y pantalla) |
| Recuperación: por el superior y por correo | Hecho; el correo necesita configurar `EMAIL_*` |
| Proveedor simulado para desarrollo | Hecho (`PROVEEDOR_MODO=simulado`) |
| Saldo de Emida para el Super Admin (Exposición) | Hecho: botón de consulta, guardado en base de datos |
| El Super Admin asigna saldo a toda la red desde la pantalla | Hecho: administradores, clientes (directo, sin margen) y límites de cajas |
| Instalación de primera vez con datos reales (`instalar`) | Hecho |
| Catálogo diario desde Emida (productos y product-flow) | Hecho: contenedor `tareas`, a las 04:00 |
| Diagnóstico y evidencia para la certificación | Hecho: `probar_proveedor` y `exportar_evidencia` |
| **Conexión real con axexo UAT** | **En proceso**: autorización de IP; luego certificación de tiempo aire (`docs/PRUEBAS_UAT.md`) |
| Límite de intentos de entrada | **Pendiente, obligatorio antes de abrir producción** |
| Pantallas Tablero, Transacciones, Conciliación del canvas | Pendientes (el backend de conciliación existe) |
| Pago de servicios, pines y On Ramp | Fuera de esta certificación: hoy se certifica solo tiempo aire |
| Pago de servicios con comisión | Pendiente: el cliente HTTP existe, falta el asiento de la comisión |
| App móvil | Después de la web (decisión 2) |

## 3. Estructura

```
tae/
├── apps/
│   ├── core/          jerarquía: inquilino, administrador, cliente, punto de venta; programador de tareas
│   ├── saldo/         bolsas, ledger, reservas, folios: LO ÚNICO QUE MUEVE DINERO (services.py)
│   ├── identidad/     usuarios, permisos, acciones, contraseñas, segundo factor, bitácoras
│   │   ├── permisos.py      quién puede qué. Un archivo, todas las reglas
│   │   ├── acciones.py      lo que llaman las vistas: permiso → ejecuta → bitácora
│   │   ├── contrasenas.py   contraseña temporal y cambio obligatorio
│   │   └── restablecimiento.py   recuperación por correo y por el superior
│   ├── catalogo/      productos que ve la caja, su ruta al proveedor y la sincronización diaria
│   ├── proveedor/     cliente REST de axexo, cierre de venta, saldo de Emida, emulador
│   ├── api/           endpoints HTTP (vistas.py y vistas_red.py), una línea por ruta en urls.py
│   └── conciliacion/  archivo del proveedor, diferencias, corte; comandos de cron
├── config/            settings por variables de entorno
├── web/               frontend React + Vite + Tailwind
│   └── src/           App.tsx (menú por rol), Venta, Cliente, Admin, Red (altas y aprobaciones), Exposicion
├── deploy/            Compose de producción, nginx, respaldo y cron
├── docs/              DECISIONES.md, PRUEBAS_UAT.md, DESPLIEGUE.md y CAMBIOS.md
├── sembrar_demo.py    datos de demostración
└── pruebas_e2e.py     prueba de punta a punta con navegador
```

La regla que sostiene la arquitectura (decisión 3): **las vistas llaman a `identidad/acciones.py`, que verifica el permiso y luego llama a `saldo/services.py`**. Una vista que llame directo a `services` se salta las compuertas, y hay pruebas que lo detectan.

## 4. Correrlo en local (Windows)

Requisitos: Docker Desktop y Node.js 20 o superior. Los comandos son para **PowerShell**, en la carpeta del proyecto.

```powershell
# 1. Configuración. Para datos reales de Emida pon PROVEEDOR_MODO=real y las
#    credenciales (ver docs/PRUEBAS_UAT.md). Con "simulado" trae datos del emulador.
Copy-Item .env.example .env
notepad .env

# 2. Base de datos, Redis, Django y el programador de tareas
docker compose up -d --build
docker compose exec web python manage.py migrate

# 3. Instalación de primera vez: 4 usuarios, catálogo real y saldo de Emida
docker compose exec web python manage.py instalar

# 4. Frontend, en otra ventana de PowerShell
cd web
npm install
npm run dev        # http://localhost:5173
```

**`instalar`** hace en un solo paso lo que necesita una tienda nueva:

1. Crea cuatro usuarios: **Super Admin, administrador, cliente y caja**. Nada más: sin saldo y sin catálogo de demostración. Los correos y nombres salen del `.env` (`TAE_SUPERADMIN_CORREO`, `TAE_ADMIN_CORREO`, etc.).
2. Muestra **una sola vez** sus contraseñas: la de `TAE_CLAVE_INICIAL` si la definiste, o una aleatoria para cada uno. Todos deben cambiarla al entrar.
3. Pide escanear un QR para el segundo factor del Super Admin.
4. Llama a Emida: descarga el **catálogo real**, lo publica en la pantalla de la caja y guarda el **saldo real de EPS**.

Los saldos de la red empiezan en cero: el Super Admin los asigna en **Usuarios de la red → Asignar saldo**, a administradores, clientes y cajas. Se puede volver a correr sin riesgo: los usuarios que ya existen no se tocan. No corre solo al levantar Docker porque las contraseñas quedarían en los logs.

`sembrar_demo.py` sigue existiendo, pero solo para la prueba de punta a punta (`pruebas_e2e.py`): crea saldo y catálogo ficticios.

Después de cambiar el `.env` hay que recrear los contenedores; reiniciarlos desde Docker Desktop no siempre vuelve a leer el archivo:

```powershell
docker compose up -d --force-recreate web tareas
```

Para conectar con Emida UAT en lugar del simulador, sigue `docs/PRUEBAS_UAT.md`.

## 5. Configuración (`.env`)

La lista completa y comentada está en `.env.example`. Lo esencial:

| Variable | Para qué |
|---|---|
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` | Con `DEBUG=false` la app se niega a arrancar si la llave es la de desarrollo |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_ORIGINS` | Sin ellos: 400 al abrir, o 403 en el primer POST con sesión |
| `DB_*`, `DB_ROOT_PASSWORD`, `REDIS_URL` | Base de datos y Redis |
| `PROVEEDOR_MODO` | `simulado` en desarrollo, `real` en producción. **Simulado con `DEBUG=false` no arranca** |
| `PROVEEDOR_BASE_URL` | Solo el host: `https://gw-uat.axexo.com:10002`. Sin rutas |
| `PROVEEDOR_PREFIJO_OPERACIONES` | Vacío o `/integratorapi`, según confirme axexo dónde viven topup y lookup |
| `PROVEEDOR_USUARIO`, `_CONTRASENA`, `_SITE_ID`, `_CLERK_ID`, `_MERCHANT_ID` | Hoja de credenciales. Nunca en el repo. `Site-Id` es el Terminal ID; `Merchant-Id` solo va en la consulta de saldo |
| `PROVEEDOR_TIMEOUT_*`, `_LOOKUP_*`, `_MAX_LOOKUPS` | Tiempos del protocolo que indicó el proveedor (decisión 10) |
| `TAE_TRASPASO_UMBRAL_2FA`, `TAE_TRASPASO_TOPE_DIARIO`, `TAE_AJUSTE_FAVOR_TOPE` | Reglas de negocio ajustables sin desplegar |
| `EMAIL_*`, `TAE_URL_BASE` | Correo de recuperación. En local se imprime en la consola |
| `TAE_INQUILINO`, `TAE_HORA_CATALOGO`, `TAE_REINTENTOS_MINUTOS` | Programador de tareas: catálogo diario y reintentos de ventas |

## 6. Usuarios y contraseñas

Detalle y razones en las decisiones 14 y 15.

- **El correo es el usuario para entrar**, en todos los roles. Un cajero sin correo propio puede usar uno de la tienda (`caja3@laermita.mx`): el cajero no recupera su contraseña por correo, así que no necesita leerlo.
- **Nadie elige ni conoce la contraseña definitiva de otro.** Al dar de alta a alguien, la plataforma genera una contraseña temporal de 12 caracteres y la muestra **una sola vez** a quien hizo el alta.
- **Cambio obligatorio.** Quien entra con una temporal solo puede cambiarla o salir; la API le niega todo lo demás (403, clave `cambiar_contrasena`).
- **Recuperación por el superior.** Cada nivel restablece a los de abajo y recibe una temporal nueva. La anterior deja de servir, las sesiones abiertas se cierran y las ligas de correo pendientes se invalidan.

| Usuario | Lo restablece |
|---|---|
| Caja | Su cliente, su administrador o el Super Admin |
| Cliente | Su administrador o el Super Admin |
| Administrador | El Super Admin |
| Super Admin | Otro Super Admin, o `python manage.py changepassword <correo>` en el servidor |

- **Recuperación por correo** (Super Admin, administradores y clientes): liga de un solo uso, vigente una hora.
- **Segundo factor:** obligatorio para el Super Admin al entrar, y para un administrador al traspasar sobre el umbral. Se activa con `preparar_super_admin` (`--solo-totp` para un usuario existente). **Restablecer la contraseña nunca toca el segundo factor.**
- **Cuentas pendientes o rechazadas no entran**, aunque la contraseña sea correcta.

## 7. La API

Todo cuelga de `/api/`. Sesión por cookie, mismo dominio que el frontend. Lista completa en `apps/api/urls.py`.

| Ruta | Qué hace |
|---|---|
| `auth/entrar`, `auth/salir`, `yo` | Sesión y perfil (incluye `debe_cambiar_contrasena` y capacidades) |
| `auth/cambiar` | Cambiar la propia contraseña |
| `auth/restablecer`, `auth/restablecer/confirmar` | Recuperación por correo |
| `catalogo`, `catalogo/codigo/<código>` | Productos vendibles |
| `venta`, `saldo` | Vender y consultar saldo |
| `puntos-venta`, `puntos-venta/alta`, `puntos-venta/limite` | Cajas |
| `clientes`, `clientes/alta`, `clientes/suspension`, `traspasos`, `retiros` | Clientes y dinero |
| `administradores/alta`, `administradores/fondeo`, `clientes/fondeo`, `red`, `aprobaciones`, `aprobaciones/aprobar`, `aprobaciones/rechazar`, `usuarios/restablecer` | La red y el saldo que el Super Admin asigna |
| `proveedor/saldo` | Saldo de EPS en Emida, solo Super Admin. GET lee lo guardado; POST consulta a Emida y guarda |

Los códigos de error significan algo y la pantalla decide qué decir según cuál llegue: **403** no te toca · **402** no alcanza el saldo · **409** una regla de negocio lo impide · **401** con clave `segundo_factor` pide el código · **400** datos o motivo inválidos · **503** con clave `proveedor` la venta no salió, intenta de nuevo.

Una venta responde **201 aprobada, 200 rechazada o 202 sin resolver**. La pantalla nunca traduce `sin_resolver` a ninguno de los otros dos (decisión 10).

## 8. El proveedor

- **Un solo Site-Id ante Emida: el de EPS** (decisión 19). La red es nuestra; el proveedor no la ve.
- **Flujo de una venta:** validar permiso, monto y ruta → reservar → enviar con un Invoice-No único → capturar si aprobó, liberar si rechazó. Si no contesta, se consulta por Invoice-No a los 40 s del envío y luego cada 10 s; sin respuesta, queda **sin resolver** con la reserva viva.
- **Si falla el login con axexo antes de enviar**, la venta no salió: se libera la reserva y la API responde 503. Si falla durante las consultas, la reserva sigue viva, porque la venta pudo haber salido.
- **Modo simulado:** con `PROVEEDOR_MODO=simulado` todo el flujo corre contra el emulador dentro del proceso. Así se desarrolla sin red ni credenciales.

Rutas que usa la plataforma, tomadas del Anexo B (hoja "End Points") y de "Actual Methods for integrations":

| Método de Emida | Ruta | Para qué |
|---|---|---|
| Login | `POST /api/v1/auth/login` (sin prefijo) | Token |
| PinDistSale | `POST /api/v1/transaction/topup` | Venta de tiempo aire |
| LookUpTransactionByInvoiceNo | `GET /api/v1/transaction/lookup/invoice-no/{invoiceNo}` | Consulta tras un timeout |
| GetProductList / Ext | `GET /api/v1/terminal/products` y `/products/ext` | Catálogo diario |
| ProductFlowInfoService | `GET /api/v1/terminal/product-flow` | Textos de ayuda por producto, a diario |
| GetCarrierList | `GET /api/v1/terminal/carriers` | Diagnóstico |
| Account Balance | `GET /api/v1/account/balance` | Saldo de EPS (Super Admin) |

Todas, menos el login, llevan el prefijo `PROVEEDOR_PREFIJO_OPERACIONES` (vacío o `/integratorapi`). `probar_proveedor --probar-prefijos` dice cuál responde.

## 9. Pruebas

```bash
docker compose exec web python manage.py test apps
```

Son 242 pruebas. Corren contra MySQL o MariaDB, **nunca SQLite**: `test_concurrencia.py` usa hilos reales contra el bloqueo de fila de InnoDB, y SQLite serializa las escrituras, así que la prueba pasaría sin probar nada.

| Archivo | Qué vigila |
|---|---|
| `saldo/tests/test_reglas.py` | Una prueba por regla del documento de alcance |
| `saldo/tests/test_concurrencia.py` | Veinticinco cajas vendiendo a la vez contra saldo para diez: pasan exactamente diez |
| `identidad/tests/test_permisos.py` | Cada cosa que un rol NO debe poder hacer, llamada directo a la función |
| `identidad/tests/test_altas_y_contrasenas.py` | Estado inicial, rechazo, contraseña temporal, bitácora |
| `api/tests/test_api.py`, `test_red.py` | Que la API no sea puerta trasera |
| `proveedor/tests/test_cierre.py` | Los escenarios de la matriz de certificación, y el login caído |
| `proveedor/tests/test_saldo_y_catalogo.py` | Saldo de Emida solo para el Super Admin, sincronización diaria, programador, evidencia |
| `conciliacion/tests/` | Lo que la conciliación arregla sola y lo que no debe tocar |

Las pruebas tienen dientes, y conviene comprobarlo en lugar de creerlo. Algunos ejemplos:

- Quitando el `select_for_update` de `reservar`, las veinticinco ventas pasan y la bolsa queda en menos cien.
- Haciendo que la vista de venta llame directo a `services`, las pruebas de `LaAPINoEsPuertaTrasera` fallan con 201 donde esperaban 403.
- Liberando la reserva cuando se agotan las consultas, `test_tx_lk_10_no_responde_nunca_y_queda_sin_resolver` falla.

Prueba de punta a punta con navegador real: ver el encabezado de `pruebas_e2e.py`.

## 10. Comandos y tareas programadas

| Comando | Qué hace | Cuándo |
|---|---|---|
| `instalar` | Primera instalación: 4 usuarios, catálogo real y saldo de Emida (`--sin-emida`, `--sin-totp`) | Una vez por tienda |
| `preparar_super_admin` | Crea un Super Admin adicional o reactiva un segundo factor (`--solo-totp`) | Cuando haga falta |
| `probar_proveedor` | Diagnóstico de la conexión con Emida; `--evidencia` guarda request y response | Al configurar y en cada sesión de pruebas |
| `sincronizar_catalogo` | Descarga productos y product-flow de Emida | **Diario, solo** (contenedor `tareas`) |
| `vincular_producto` | Pone un producto de Emida en la pantalla de la caja (`--listar` para ver los disponibles) | Al armar el catálogo |
| `reintentar_pendientes` | Vuelve a consultar las ventas sin resolver | **Cada 5 min, solo** (contenedor `tareas`) |
| `conciliar` | Cruza el archivo del proveedor con nuestras ventas (`--sin-resolver` solo reporta) | Con cada archivo del proveedor |
| `exportar_evidencia` | JSON por venta y `reporte_emida.csv` en formato de conciliación de Emida | Certificación y reporte diario |

Todos se corren con `docker compose exec web python manage.py <comando>`.

**El contenedor `tareas` tiene que estar arriba.** Sin él, el catálogo no se actualiza y una venta sin resolver se queda así para siempre, con el saldo del tendero apartado. Hace las veces de crontab, porque Windows no tiene (decisión 25).

## 11. Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `Bad Request (400)` al abrir | El host no está en `DJANGO_ALLOWED_HOSTS` | Agregarlo y recrear `web` |
| Entra, pero el primer POST da 403 | Falta el origen en `DJANGO_CSRF_ORIGINS` | Agregarlo y recrear `web` |
| `No scheme supplied` al vender | `PROVEEDOR_BASE_URL` vacía | Llenarla con `https://host:puerto` |
| La venta responde 503, clave `proveedor` | No hubo login con axexo (la venta no salió) | `docker compose logs --tail=80 web`; revisar credenciales y red |
| `Connection to gw-uat.axexo.com timed out` | IP no autorizada por axexo, o salida por otra IP | Comparar `curl -4 ifconfig.me` con la IP autorizada |
| Las ventas responden 404 del proveedor | Las operaciones viven bajo `/integratorapi` | `PROVEEDOR_PREFIJO_OPERACIONES=/integratorapi` |
| El Super Admin no puede entrar | Falta activar su segundo factor | `preparar_super_admin --correo ... --solo-totp` |
| La app no arranca: "PROVEEDOR_MODO=simulado no se permite" | `.env` de desarrollo en producción | `PROVEEDOR_MODO=real` |
| Cambié el `.env` y no pasa nada | El reinicio no vuelve a leer el archivo | `docker compose up -d --force-recreate web` |
| `Access denied for user` en MySQL | El volumen se creó con otras credenciales | `docker compose down -v` (**borra la base local**) |
| No existe `/admin` | Decisión: toda alta pasa por la API, con permisos y bitácora | Usar la pantalla "Usuarios de la red" |
| Exposición: "no se encontró el saldo en la respuesta" | Emida usa otro nombre de campo | Revisar la respuesta guardada y agregar el campo en `CAMPOS_SALDO` (`apps/proveedor/saldo.py`) |
| El catálogo de la caja quedó vacío | Productos sin vincular, o Emida dejó de listarlos | `vincular_producto --listar` |

## 12. Convenciones de código

**Comentarios**
- En español, igual que los nombres del dominio (`bolsa`, `reserva`, `traspaso`).
- **Comentar el porqué, no el qué.** `# reserva para que el cliente no lo gaste dos veces` sirve; `# resta el monto` no.
- Toda regla de negocio apunta a su decisión: `# Ver docs/DECISIONES.md, decisión 7`.
- Cada módulo empieza con un docstring que dice qué hay ahí y qué NO debe ir ahí.
- Nada de código comentado "por si acaso": para eso está git.

**Dinero**
- Solo `apps/saldo/services.py` mueve dinero, y todo pasa por `_asentar`, que exige partida doble antes de escribir.
- Montos en `Decimal`, nunca `float`.
- Toda operación de saldo va en `transaction.atomic()` con `select_for_update()`, y los bloqueos en orden ascendente de llave primaria (decisión 9).
- El ledger es de solo inserción: nada se edita ni se borra; se corrige con asientos contrarios.

**Permisos y vistas**
- Las reglas de quién puede qué viven solo en `identidad/permisos.py`.
- Las vistas traducen JSON, llaman a `acciones` y traducen excepciones a HTTP en `manejar`. Nada más.
- Lo que la pantalla oculta es comodidad; lo que vale es lo que verifica el servidor.

**Pruebas**
- Una regla nueva entra con su prueba, y la prueba debe fallar si se quita la regla.
- Las pruebas de permisos prueban lo prohibido, no solo lo permitido.

**General**
- Ningún secreto en el código: todo viene del `.env`.
- Mensajes al usuario en español claro y con qué hacer, nunca con trazas técnicas.
