# Registro de decisiones — Plataforma TAE

Este documento explica **por qué** el sistema es como es. Cada decisión tiene contexto, qué se decidió, por qué y qué consecuencias trae para quien mantiene el código. Los comentarios del código apuntan aquí con "decisión N".

**Origen.** Las decisiones de negocio son del responsable del proyecto (Cesar). Las razones de las decisiones técnicas se redactaron junto con el código y se verificaron contra él y contra sus pruebas. Si una razón no refleja el motivo real, corrígela aquí. Cambiar una decisión obliga a revisar las pruebas que la vigilan.

## Índice

| # | Decisión | Tipo |
|---|---|---|
| 1 | Stack Open Source y sencillo | Técnica |
| 2 | Web primero, app móvil después, misma API | Alcance |
| 3 | Vista → acción → servicio: el permiso siempre en medio | Técnica |
| 4 | Cuatro roles cerrados y un inquilino desde el primer día | Negocio |
| 5 | Ledger de partida doble y solo inserción | Dinero |
| 6 | Reservar no es gastar | Dinero |
| 7 | El límite de una caja no mueve dinero, y se reserva desde el alta | Dinero |
| 8 | El margen se aplica al comprar saldo | Dinero |
| 9 | Bloqueos en orden ascendente de llave primaria | Técnica / dinero |
| 10 | El silencio no es un rechazo: protocolo de consulta | Proveedor / dinero |
| 11 | Un solo criterio de éxito del proveedor | Proveedor |
| 12 | Catálogo en dos capas | Producto |
| 13 | La conciliación solo completa lo que ya estaba empezado | Dinero |
| 14 | Segundo factor proporcional al daño | Seguridad |
| 15 | Contraseña temporal, cambio obligatorio y dos caminos de recuperación | Seguridad |
| 16 | El estado inicial depende de quién crea | Negocio |
| 17 | Tres bitácoras separadas | Seguridad / auditoría |
| 18 | Proveedor simulado solo en desarrollo | Técnica |
| 19 | Un solo Site-Id ante el proveedor; plataforma dedicada a Scorpion | Integración |
| 20 | Reglas y tiempos en variables de entorno | Técnica |
| 21 | Un diseño de referencia y un menú por rol | Producto |
| 22 | Fuera del MVP | Alcance |
| 23 | El saldo de Emida no es el saldo de la red | Dinero |
| 24 | El catálogo se sincroniza a diario, pero una persona decide qué se vende | Producto / proveedor |
| 25 | Las tareas programadas viven en un contenedor, no en el crontab | Técnica |
| 26 | Suspensión en cada nivel y acceso por persona | Negocio / seguridad |
| 27 | Retiro a EPS y retención de red para fondeos de más | Dinero |
| 28 | Retiros a prueba de balas | Dinero / técnica |
| 29 | Transacciones y movimientos por jerarquía | Producto / seguridad |
| 30 | Margen dinámico en administrador y cliente; la caja conserva su límite | Dinero |

---

## 1. Stack Open Source y sencillo

**Contexto.** Lo implementa y mantiene una persona, en poco tiempo, sin tecnologías sofisticadas.

**Decisión.** Python con Django y Django REST Framework, MySQL 8 con InnoDB, Redis, React con Vite y Tailwind, todo en Docker Compose. Todo es Open Source.

**Por qué.** Django trae autenticación, ORM, migraciones y validación: menos código propio en un sistema que mueve dinero. InnoDB da transacciones y bloqueo de fila, de los que depende el motor de saldo. Son tecnologías comunes, con documentación abundante y fáciles de relevar.

**Consecuencias.** InnoDB no es opcional: MyISAM bloquea la tabla entera y las pruebas de concurrencia no significan nada sobre SQLite. Redis y Celery están instalados para tareas en segundo plano, pero el código todavía no los usa: las tareas periódicas corren por cron (`deploy/crontab.ejemplo`).

## 2. Web primero, app móvil después, misma API

**Decisión.** Primero la web, con diseño adaptable a celular. La app vendrá después, sobre la misma API.

**Por qué.** Con una sola persona, dos clientes en paralelo duplican pruebas y errores. La web valida el negocio completo y la certificación con el proveedor antes de invertir en la app.

**Consecuencias.** El frontend no lleva lógica de negocio que la app tendría que repetir.

## 3. Vista → acción → servicio: el permiso siempre en medio

**Contexto.** En un sistema con roles, el error más caro es una pantalla o un endpoint que se salta el permiso.

**Decisión.** Tres capas con responsabilidades separadas:
- `apps/api/vistas*.py` traducen JSON a argumentos y excepciones a códigos HTTP. Nada más.
- `apps/identidad/acciones.py` verifica el permiso (`permisos.py`), ejecuta y deja registro.
- `apps/saldo/services.py` hace la contabilidad y no sabe quién es el usuario.

**Por qué.** Un permiso escrito en dos lugares termina diciendo dos cosas distintas. Con las reglas en un solo archivo, `permisos.py` se puede leer de un tirón y comparar contra el documento de alcance.

**Consecuencias.** Ninguna vista llama a `services` directamente, y hay pruebas (`LaAPINoEsPuertaTrasera`) que fallan si alguien lo hace. Lo que la pantalla oculta es comodidad: el servidor vuelve a verificar todo. Tampoco hay `/admin` de Django, porque se saltaría permisos y bitácora.

## 4. Cuatro roles cerrados y un inquilino desde el primer día

**Decisión.** Super Admin → Administrador → Cliente → Punto de venta. No hay roles a la medida. Cada usuario cuelga de exactamente una entidad, y la base de datos lo garantiza con un `CheckConstraint`. Todas las tablas llevan `inquilino`, aunque hoy solo exista Scorpion.

**Por qué.** Cuatro roles fijos se pueden probar exhaustivamente: hay una prueba por combinación prohibida. Agregar `inquilino` después habría obligado a reescribir cada consulta de saldo.

**Consecuencias.** El cliente no vende: siempre vende una caja. Un cajero solo vende en la caja a la que está asignado.

## 5. Ledger de partida doble y solo inserción

**Decisión.** Todo movimiento de dinero se registra como asientos que suman cero, y nada se actualiza ni se borra. `_asentar` comprueba la partida doble **antes** de escribir.

**Por qué.** La auditoría tiene que poder ver el error, no solo la corrección. Con solo inserción, una corrección agrega asientos contrarios bajo un folio nuevo y el movimiento equivocado sigue visible.

**Consecuencias.** `PartidaDescuadrada` nunca debería ocurrir: si ocurre, es un error de programación. Revisar `_asentar`, `reservar` y `capturar` exige ojos humanos antes de construir encima.

## 6. Reservar no es gastar

**Decisión.** Una venta aparta el dinero, lo manda al proveedor y lo captura solo si el proveedor aprobó.

**Por qué.** Emida no tiene reversa. Si se debitara primero, cada falla obligaría a devolver algo que nunca salió, y ahí es donde se pierden pesos en el camino.

**Consecuencias.** Una venta valida primero lo barato (permiso, monto y ruta) y reserva al final: una venta que iba a fallar no debe haber tocado el saldo. Hay una prueba que falla si se invierte el orden.

## 7. El límite de una caja no mueve dinero, y se reserva desde el alta

**Decisión.** Con bolsa asignada, cada caja tiene un **límite**: un techo de gasto sobre la bolsa del cliente. El dinero no se mueve. La suma de los límites nunca supera la bolsa del cliente. El límite inicial de una caja se fija desde su alta, aunque quede pendiente, y si la caja se rechaza vuelve a cero.

**Por qué.** Si el dinero se moviera entre bolsas, la regla de la suma no haría falta, pero cada reparto generaría asientos. Reservar desde el alta evita que el cliente prometa el mismo dinero a dos cajas mientras una espera aprobación.

**Consecuencias.** Lo retirable de un cliente no es su saldo: le restan los límites asignados y las ventas en vuelo. Por eso la API devuelve `retirable` aparte.

## 8. El margen se aplica al comprar saldo

**Decisión.** Si un cliente con 4% de margen paga 1,000, su bolsa recibe 1,040. Los 40 salen de la cuenta de **bonificación de margen**, no del administrador.

**Por qué.** Así cada venta descuenta exactamente su valor de cara. Si el margen saliera del administrador, su bolsa se vaciaría más rápido de lo que la fondean y el número dejaría de significar algo.

**Consecuencias.** En el traspaso, la pantalla muestra los dos montos: lo que el cliente paga y lo que recibe. Cambiar el margen solo afecta compras futuras.

## 9. Bloqueos en orden ascendente de llave primaria

**Decisión.** Toda operación de saldo corre en una transacción con `select_for_update()`, y cuando bloquea varias bolsas lo hace en orden ascendente de llave primaria.

**Por qué.** El bloqueo de fila evita que dos ventas simultáneas gasten el mismo saldo. El orden fijo evita interbloqueos cuando dos operaciones tocan las mismas dos bolsas.

**Consecuencias.** No es cosmético y no se cambia. `test_concurrencia.py` lo comprueba con hilos reales contra InnoDB.

## 10. El silencio no es un rechazo: protocolo de consulta

**Contexto.** Una venta puede fallar antes de salir, salir y no recibir respuesta, o recibir un rechazo. El segundo caso es el peligroso: la recarga pudo aplicarse aunque no lo sepamos.

**Decisión.**
- Rechazo explícito del proveedor: se libera.
- Falla de login **antes** del envío: la venta no salió, se libera y la API responde 503 ("intenta de nuevo").
- Sin respuesta: se consulta por Invoice-No a los **40 segundos contados desde el envío**, luego cada 10 segundos, tres veces. Un 404 en la consulta significa que la venta nunca entró y se libera.
- Si nada responde, la venta queda **SIN_RESOLVER con la reserva viva**. La resuelve `reintentar_pendientes` (cron) o la conciliación.

**Por qué.** Liberar ante el silencio significa que el tendero vuelve a vender, el proveedor cobra las dos y nadie se entera hasta el día siguiente. Una reserva atorada molesta a un tendero; un doble cobro le cuesta a alguien. Los tiempos se cuentan desde el envío porque así lo pidió el proveedor: si se contaran desde el timeout, bajar el timeout adelantaría la consulta.

**Consecuencias.** La pantalla muestra tres desenlaces distintos (201 aprobada, 200 rechazada, 202 sin resolver) y nunca traduce `sin_resolver` a otro. Un login caído durante las consultas no libera nada. `reintentar_pendientes` tiene que estar en cron. El timeout de gunicorn (90 s) es mayor que la venta más lenta posible (unos 75 s).

## 11. Un solo criterio de éxito del proveedor

**Decisión.** Una operación se aprueba si `responseCode` es `0` o `00`, en tiempo aire, en pago de servicios y en la consulta. Si viene `h2hResultCode`, también debe ser cero; si no viene, no se exige.

**Por qué.** Lo confirmó el proveedor por correo (29 de octubre de 2026). La matriz de certificación sugería criterios distintos por método; se llegó a implementar así y las pruebas se reescribieron cuando llegó la respuesta, porque documentaban una suposición y no un hecho.

## 12. Catálogo en dos capas

**Decisión.** Arriba, lo que ve la caja ("Telcel Tiempo Aire $100", con su SKU propio). Abajo, a qué producto de qué proveedor se manda (`product_id`). El código de barras identifica la fila del catálogo, nunca el precio.

**Por qué.** Cuando entre un segundo proveedor o Emida cambie un identificador, se toca la capa de abajo y la pantalla de venta no se entera. Si el precio viviera en el código de barras, una etiqueta vieja vendería al precio anterior para siempre.

## 13. La conciliación solo completa lo que ya estaba empezado

**Decisión.** Con el archivo del proveedor, la conciliación captura o libera las reservas que ya existían, porque eso completa una operación empezada. Cualquier otra diferencia, como un cobro de más, queda marcada para que una persona decida, con motivo y autor.

**Por qué.** Corregir dinero que ya estaba quieto sin autor es dinero moviéndose sin responsable. Una reserva tampoco se libera por vieja: el tiempo no es evidencia.

**Consecuencias.** El cruce va por Invoice-No dentro de la ventana de fechas del archivo, porque el contador da la vuelta en 99,999.

## 14. Segundo factor proporcional al daño

**Decisión.**
- **Super Admin:** TOTP obligatorio al entrar, porque está arriba de toda la cadena.
- **Administrador:** solo al autorizar un traspaso sobre `TAE_TRASPASO_UMBRAL_2FA`, más un tope diario.
- **Clientes y cajeros:** nunca.

El segundo factor se activa desde la terminal con `preparar_super_admin`. Reiniciárselo a otra persona (porque perdió el teléfono) le toca a un Super Admin: la regla existe en `acciones.reiniciar_totp`, pero todavía no tiene pantalla, así que hoy se hace con el mismo comando y `--solo-totp`.

**Por qué.** Si al administrador se le piden seis dígitos cada mañana, termina dejando la sesión abierta, que es peor. A los cajeros la fricción los expulsa, y su riesgo está acotado a su bolsa. El tope diario es lo que de verdad da tiempo para suspender y revertir.

**Consecuencias.** Todavía no hay pantalla para activar ni reiniciar el segundo factor: se hace en el servidor con `preparar_super_admin --correo ... --solo-totp`. Sin eso, un administrador no puede traspasar por encima del umbral.

## 15. Contraseña temporal, cambio obligatorio y dos caminos de recuperación

**Contexto.** Cada nivel da de alta al de abajo, y muchos cajeros no tienen un correo que revisen.

**Decisión.**
- **Nadie elige ni conoce la contraseña definitiva de otro.** El alta genera una contraseña temporal de 12 caracteres, sin caracteres ambiguos, que se muestra **una sola vez**.
- **Cambio obligatorio:** con una temporal, la API solo permite cambiarla, ver el perfil y salir.
- **Recuperación por el superior** (para todos, y única vía del cajero): genera una temporal nueva. La anterior deja de servir, las sesiones abiertas se cierran y las ligas de correo vivas se invalidan.
- **Recuperación por correo** (todos menos el cajero): liga de un solo uso, vigente una hora. De la liga solo se guarda su huella.
- **Restablecer la contraseña nunca toca el segundo factor.**
- Para la **segunda etapa**: límite de intentos de entrada (obligatorio antes de abrir producción a usuarios desconocidos) y PIN corto para cajeros.

**Por qué.** Si quien da de alta conociera la contraseña definitiva, cualquier operación de esa cuenta tendría dos posibles autores. La temporal se dicta por teléfono; una liga no. Si restablecer la contraseña reiniciara el segundo factor, robar un buzón de correo bastaría para entrar. Nadie se restablece a sí mismo por el camino del superior: si se pudiera, una sesión olvidada en un equipo compartido se volvería una cuenta tomada.

**Consecuencias.** El correo es el usuario para entrar; un cajero puede usar uno de la tienda. La temporal nunca se escribe en logs ni en bitácoras, y su respuesta HTTP lleva `Cache-Control: no-store`. Quien perdió la contraseña y el teléfono necesita a dos personas para volver: es incómodo a propósito.

## 16. El estado inicial depende de quién crea

**Decisión.**
- Lo que crea **quien aprueba** nace aprobado: un cliente creado por su administrador, o una caja creada por su cliente o por su administrador.
- Lo que crea el **Super Admin** en clientes o cajas nace **pendiente**, y lo aprueba su administrador (o su cliente, si es una caja).
- Una cuenta pendiente o rechazada **no entra**.
- Rechazar exige motivo, solo aplica a lo pendiente y es reversible.

**Por qué.** Quien responde por un cliente o una caja es quien lo conoce. Pedir una segunda firma cuando quien crea es el mismo que aprobaría solo agrega espera sin agregar control: el control real está en el saldo que se le transfiere. Un cliente que ya operó tiene historia de dinero; por eso se suspende, no se rechaza.

## 17. Tres bitácoras separadas

**Decisión.**
- **Ledger** (`saldo`): quién movió dinero.
- **BitacoraAcceso:** quién entró, desde qué IP, y si falló, por qué.
- **BitacoraAdministracion:** quién cambió la red (altas, aprobaciones, rechazos con motivo, suspensiones, restablecimientos y reinicios del segundo factor).

**Por qué.** Cada una responde una pregunta distinta, y mezclarlas hace que ninguna se pueda leer. Lo que no se registró hoy no se puede reconstruir mañana.

**Consecuencias.** Las tres son de solo inserción. La fila de bitácora se escribe en la misma transacción que el cambio: si el cambio se revierte, la fila también. Guardan nombres además de llaves, porque una entidad puede renombrarse. Nunca guardan contraseñas.

## 18. Proveedor simulado solo en desarrollo

**Decisión.** `PROVEEDOR_MODO=simulado` hace que la venta hable con el emulador dentro del proceso. La aplicación **no arranca** si está en modo simulado con `DJANGO_DEBUG=false`.

**Por qué.** Permite desarrollar y probar el flujo completo sin red ni credenciales, y reproducir a voluntad el timeout, el duplicado y la respuesta ambigua, que nunca ocurren cuando uno quiere probarlos. El candado existe porque copiar un `.env` de desarrollo al servidor produciría ventas que nunca salen.

## 19. Un solo Site-Id ante el proveedor; plataforma dedicada a Scorpion

**Decisión.** Ante Emida existe un solo Site-Id, el de EPS. Toda la jerarquía es nuestra y el proveedor no la ve. La plataforma es dedicada: Scorpion es el inquilino, y la columna `inquilino` deja abierta la puerta a varios clientes de EPS.

**Consecuencias.** La conciliación cruza el archivo del proveedor con nuestras ventas por Invoice-No, no por caja. El código no asume nombres de inquilino: los comandos reciben `--inquilino`.

## 20. Reglas y tiempos en variables de entorno

**Decisión.** Topes, umbrales, vigencias y tiempos del protocolo se leen del `.env`.

**Por qué.** Emida exige que los parámetros del protocolo se puedan ajustar sin desplegar, y los umbrales de autorización los define Scorpion y van a cambiar.

**Consecuencias.** Un cambio al `.env` requiere recrear el contenedor. El `.env` nunca se sube al repositorio; el repositorio solo lleva `.env.example`.

## 21. Un diseño de referencia y un menú por rol

**Decisión.** El diseño de referencia es el canvas *Punto de Venta TAE — Mockup navegable* (https://claude.ai/artifact/6VnnsooJFC8tyyXXqGhRGt). La paleta y las tipografías (Space Grotesk y Manrope) viven en `web/tailwind.config.js`. Cada rol tiene un solo menú, definido en `pantallasDe` de `web/src/App.tsx`.

**Por qué.** Un usuario que encuentra el mismo menú en el mismo lugar aprende la herramienta una vez. Para quien mantiene el código, un menú por rol es un solo lugar que cambiar.

**Consecuencias.** Una pantalla nueva se diseña primero en el canvas. El menú solo muestra opciones que ya funcionan; las del canvas que faltan (Tablero, Transacciones, Conciliación) se agregan cuando exista su pantalla.

## 22. Fuera del MVP

| Tema | Decisión |
|---|---|
| Stable Coins | Fuera del MVP |
| KYC | No se requiere en esta etapa |
| Vigencia del saldo asignado | No hay: el saldo no caduca |
| Facturación | Etapa futura |
| Pago de servicios con comisión | El cliente HTTP existe; falta reflejar la comisión en el ledger |
| App móvil | Después de la web (decisión 2) |

## 23. El saldo de Emida no es el saldo de la red

**Contexto.** Hay dos dineros distintos: el que EPS tiene depositado en Emida, del que se descuenta cada venta aprobada, y el que circula en la red de administradores, clientes y cajas.

**Decisión.**
- El saldo de Emida (`GET /api/v1/account/balance`) **solo lo ve el Super Admin**, en la pantalla Exposición. Se consulta con un botón y **cada consulta se guarda** en `ConsultaSaldoProveedor`, con la respuesta cruda.
- Los saldos de la red **los asigna el Super Admin a mano** desde "Usuarios de la red → Asignar saldo", a cualquiera: a un administrador o a un cliente se le suma saldo desde la cuenta de fondeo de EPS (al cliente sin margen y sin pasar por su administrador), y a una caja se le fija su límite. Nada de eso se descuenta ni se suma al saldo de Emida.
- La instalación (`manage.py instalar`) consulta y guarda el saldo real de Emida, pero deja la red en cero.
- La pantalla pone lado a lado el saldo de Emida, el total asignado en la red y la diferencia.

**Por qué.** Mostrarle a un administrador o a un tendero el saldo de Emida solo invitaría a confundirlo con el suyo. Guardar cada consulta permite abrir el tablero sin llamar al proveedor, conservar la historia del float y revisar qué contestó Emida si el número no se pudo leer. La diferencia es la señal que importa: si la red tiene asignado más de lo que hay en Emida, en algún momento Emida empezará a rechazar ventas por fondos insuficientes.

**Consecuencias.**
- No se bloquean ventas por saldo bajo en Emida: Emida las rechaza, y la plataforma ya maneja ese rechazo sin cobrar.
- No tenemos el contrato exacto de la respuesta del saldo. `leer_saldo` busca los nombres de campo más probables y, si no encuentra un número, guarda la consulta sin saldo en lugar de inventarlo. Si Emida usa otro nombre, se agrega en `CAMPOS_SALDO`.
- `Merchant-Id` solo se manda en esta consulta.

## 24. El catálogo se sincroniza a diario, pero una persona decide qué se vende

**Contexto.** Las Reglas Generales de Emida piden descargar y actualizar a diario los productos (GetProductList y ProductFlowInfoService).

**Decisión.**
- `sincronizar_catalogo` copia lo que Emida lista para la terminal en `ProductoDelProveedor`, con su product-flow.
- **No crea productos en la pantalla del cajero.** Eso lo hace una persona con `vincular_producto` (decisión 12).
- Si Emida deja de listar un `product_id`, su ruta se **pausa** (`pausada_por_proveedor`), y se reanuda sola cuando vuelve. El campo `activo` de la ruta es la decisión de una persona y la sincronización nunca lo toca.
- Si Emida responde con una lista vacía, **no se cambia nada**.

- **Excepción, la instalación:** `manage.py instalar` publica de una vez todo lo que Emida lista con monto fijo, para que una tienda nueva arranque con el catálogo real completo. A partir de ahí rige la regla anterior.

**Por qué.** Que lo que Emida ofrece entre solo a la pantalla significaría nombres, precios y orden que nadie revisó. Separar "pausada por el proveedor" de "apagada por una persona" evita que la sincronización reviva un producto que alguien quitó a propósito. El candado de la lista vacía existe porque una falla momentánea de Emida pausaría todas las ventas.

## 25. Las tareas programadas viven en un contenedor, no en el crontab

**Decisión.** El contenedor `tareas` corre `manage.py programador`: sincroniza el catálogo una vez al día (`TAE_HORA_CATALOGO`, 04:00 por omisión) y reintenta las ventas sin resolver cada `TAE_REINTENTOS_MINUTOS` (5). El crontab del servidor solo lleva el respaldo de la base.

**Por qué.** Las pruebas contra UAT se hacen en Windows, que no tiene crontab. Un contenedor funciona igual en Windows y en el servidor Linux, y se levanta y se detiene con el resto de la plataforma. Si el contenedor se reinicia después de la hora del catálogo y hoy no se ha sincronizado, sincroniza al arrancar.

**Consecuencias.** Nunca deben correr al mismo tiempo el contenedor y las líneas equivalentes del crontab: las tareas correrían doble. `deploy/crontab.ejemplo` trae esas líneas comentadas, como alternativa.

## 26. Suspensión en cada nivel y acceso por persona

**Contexto.** Solo se podía suspender a un cliente. No había forma de detener a un administrador, de apagar una caja sola, ni de quitarle la entrada a un cajero que dejó de trabajar sin bloquear su caja para los demás.

**Decisión.**
- **Administrador:** nuevo campo `estado` (activo o suspendido), que solo cambia el Super Admin. La cascada **se deriva** en `Cliente.puede_operar` y no se escribe en cada cliente ni en cada caja.
- **Cliente:** sin cambios. Su cascada sí escribe `estado_previo` en las cajas.
- **Caja:** se suspende sola. Queda con `estado_previo` vacío, que es la marca de "suspendida a mano": reactivar a su cliente no la revive. No se reactiva si su cliente está suspendido.
- **Persona:** `is_active` en el usuario. Sigue el mismo camino que el restablecimiento de contraseña: cada nivel sobre los de abajo y nadie sobre sí mismo. Al intentar entrar con la contraseña correcta se le dice el motivo real.
- Nada se borra: el ledger y las bitácoras apuntan a esas entidades.

**Por qué.** Derivar la cascada del administrador evita reescribir cientos de filas al suspender y, sobre todo, al reactivar: cada cliente vuelve a su estado exacto, incluido uno que ya estaba suspendido a mano. Separar a la persona de la entidad permite dar de baja a un cajero sin detener la venta.

**Consecuencias.** `Cliente.puede_operar` ahora consulta a su administrador; las consultas calientes usan `select_related("cliente__administrador")`. El mensaje de por qué no opera vive en `Cliente.motivo_no_opera`. Endpoints: `red/suspension` y `usuarios/acceso`.

## 27. Retiro a EPS y retención de red para fondeos de más

**Contexto.** Se fondean 110 a un administrador y debían ser 106. Para cuando se detecta, el administrador ya traspasó los 110 a sus clientes y estos los asignaron a sus cajas. Retirar los 4 obligaba a elegir de qué cliente o caja quitarlos.

**Decisión.**
- **Retiro a EPS** (`services.retirar_a_eps`): el Super Admin regresa saldo de un administrador o un cliente a la cuenta de fondeo, con motivo y folio `RET-`. Solo toma lo disponible: nunca lo apartado por ventas en vuelo, y en un cliente tampoco lo asignado a sus cajas.
- **Corrección con retención** (`services.corregir_fondeo`): retira lo disponible y el faltante se convierte en una `Retencion` sobre el administrador (o el cliente). Cada venta de esa rama pasa solo si el saldo de red, después de la venta, queda igual o arriba de lo retenido. Al llegar ahí, la rama deja de vender.
- **Liquidación:** el siguiente fondeo del titular cubre primero la retención (y en un cliente también un traspaso de su administrador). La liquidación sale con su propio folio y apunta a la retención (`Operacion.retencion`). El Super Admin puede cancelarla con nota.
- **Dos saldos a la vista:** *Disponible* (lo que el titular mueve hoy) y *Saldo red* (lo que queda por vender en su rama).

**Por qué.** La red termina vendiendo exactamente lo que debió recibir, sin castigar a nadie antes de tiempo y sin que el Super Admin tenga que escoger una caja. Las bolsas nunca quedan en negativo y la corrección queda visible en el ledger, que sigue siendo de solo inserción (decisión 5).

**Consecuencias.**
- **Candados:** toda venta, fondeo y traspaso bloquea primero las retenciones activas de la rama y después las bolsas (decisión 9). Las ventas de una rama con retención se ponen en fila; las ramas sin retención no pagan nada, porque la consulta no encuentra filas que bloquear.
- Un retiro directo puede dejar una rama por debajo de su retención; en ese caso la rama se detiene hasta el siguiente fondeo, que es el comportamiento buscado.
- Endpoints: `retiros/eps`, `retenciones` y `retenciones/cancelar`. Pantalla: "Abonos y retiros" del Super Admin.
- Corrección relacionada: `disponible_para_retirar` restaba dos veces las ventas en vuelo de cajas con límite (el límite ya las incluye).

## 28. Retiros a prueba de balas

**Contexto.** Una revisión de todos los caminos que sacan dinero encontró cuatro huecos: `devolver` y `ajustar` verificaban el saldo antes de bloquear la bolsa; la base aceptaba saldos negativos; un doble clic podía aplicar un retiro dos veces; y la devolución revertía el margen con el porcentaje actual, no con el otorgado.

**Decisión.**
- **Bloquear antes de leer.** Toda operación de dinero bloquea, al inicio y en orden de llave (decisión 9), todas las bolsas que va a tocar, incluidas las cuentas de EPS. Después verifica y después asienta.
- **La base como última defensa.** `bolsa_red_nunca_negativa`: una bolsa de administrador o cliente no puede tener saldo negativo ni más apartado que saldo. `limite_caja_nunca_negativo`: lo mismo para el límite de una caja. Las cuentas de sistema de EPS quedan fuera porque van en negativo por diseño.
- **Idempotencia.** La pantalla manda `Idempotency-Key` con una llave por intención. `Operacion.clave_idempotencia` y `Retencion.clave_idempotencia` son únicas por inquilino: una petición repetida, aunque llegue al mismo tiempo que la original, no mueve dinero y recibe el folio original.
- **El margen con que se otorgó.** La devolución usa la proporción real pagado/acreditado de los traspasos del cliente (promedio ponderado). Con `operacion_origen` usa la de ese traspaso. El retiro a EPS de un administrador con margen regresa la bonificación a su cuenta en la misma proporción.
- **Lo que es de quién.** A un administrador solo se le devuelve lo que él traspasó neto. Lo que EPS abonó directo al cliente se retira hacia EPS.
- **Bolsas desde el alta.** Toda bolsa se crea con su dueño (`saldo/senales.py`). Crearlas bajo carga en la primera operación provocaba bloqueos mutuos en MySQL.
- **Retirar de una caja** es bajar su límite y exige motivo. Cada cambio queda en `MovimientoLimite`.

**Consecuencias.** `capturar` suelta el apartado antes de cobrar, para no violar la restricción en el instante intermedio. Las pruebas `test_blindaje_retiros.py` atacan cada hueco con hilos simultáneos reales contra MySQL.

## 29. Transacciones y movimientos por jerarquía

**Decisión.** `vistas_reportes.py` define el alcance una sola vez por rol (`_alcance_ventas`, `_alcance_bolsas`, `_alcance_limites`) antes de aplicar cualquier filtro de la pantalla. Cada nivel ve lo suyo y lo de abajo:
- **Caja:** sus ventas y los cambios a su límite. Las columnas `saldo_despues` y `administrador` le llegan en `null`.
- **Cliente:** las ventas de sus cajas, los asientos de su bolsa y los cambios de límite de sus cajas.
- **Administrador:** lo de sus clientes y las cajas de estos, más su propia bolsa.
- **Super Admin:** todo el inquilino.

Un concepto como "Traspaso de Scorpion Centro" dice de dónde vino el dinero, nunca cuánto le queda a quien lo envió. Exportar usa exactamente el mismo alcance y los mismos filtros.

**Consecuencias.** Se guardan además `carrier_control_no`, `response_message` y `pin` de cada venta. El PIN solo lo ve el alcance de esa venta y nunca va a los logs.

## 30. Margen dinámico en administrador y cliente; la caja conserva su límite

**Decisión.** El margen es configurable de 0 % a 100 % con dos decimales: en el alta y después (`red/margen`). El del administrador lo pone el Super Admin y se aplica al fondearlo; el del cliente lo ponen el Super Admin o su administrador y se aplica al traspasarle. Cambiarlo no toca lo ya otorgado (decisión 28).

La caja **no** tiene margen: sigue con límite sobre la bolsa de su cliente (decisión 7), porque no compra saldo y no hay una compra sobre la cual bonificar.

## Cómo agregar una decisión

Copia este formato al final, agrégala al índice y referencia su número en el código que la aplica:

```
## N. Título corto

**Contexto.** Qué situación obligó a decidir.
**Decisión.** Qué se eligió.
**Por qué.** Razones y alternativas descartadas.
**Consecuencias.** Qué implica para el código y para quien lo mantenga.
```
