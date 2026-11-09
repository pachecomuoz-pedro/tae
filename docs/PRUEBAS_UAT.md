# Pruebas y certificación de tiempo aire contra Emida UAT (Windows)

Guía para preparar el ambiente, conectar con el sandbox de Emida y ejecutar los casos de **tiempo aire** de la matriz de certificación (Anexo B), generando la evidencia que pide el Plan de certificación.

- Todos los comandos son para **PowerShell**, en la carpeta del proyecto, con Docker Desktop abierto.
- Los comandos de `docker compose exec` corren **dentro** del contenedor, que es Linux. Por eso las rutas que se les pasan usan `/` (`evidencias/TA-20261110`). La carpeta aparece igual en el Explorador de Windows, dentro del proyecto.
- Los IDs de caso (TX-T-04, TX-LK-07…) son los de la hoja "Txns Tiempo Aire" del Anexo B.

---

## 0. Antes de cada sesión

1. **Pide a axexo la programación del emulador** para los casos TX-LK-07 a TX-LK-10: respuesta a los 25, 38 y 55 segundos, y sin respuesta. Anota qué número o monto activa cada escenario.
2. **Comprueba que la IP de este equipo es la que autorizó axexo:**
   ```powershell
   curl.exe -4 ifconfig.me
   docker compose exec web python -c "import requests; print(requests.get('https://ifconfig.me/ip', timeout=10).text)"
   ```
   Las dos deben coincidir con la IP autorizada. Si no coinciden, desactiva la VPN o el proxy, o pide a axexo que autorice la que salió.
3. **Comprueba que el puerto responde:**
   ```powershell
   Test-NetConnection gw-uat.axexo.com -Port 10002
   ```
   Debe decir `TcpTestSucceeded : True`.

## 1. Configurar el `.env` para UAT

Si no existe, créalo desde la plantilla con `Copy-Item .env.example .env` y ábrelo con `notepad .env`. Para UAT:

```ini
DJANGO_DEBUG=true
PROVEEDOR_MODO=real

# Solo el host, sin rutas. El login vive en /api/v1/auth/login.
PROVEEDOR_BASE_URL=https://gw-uat.axexo.com:10002
# Vacío para empezar; el paso 4 dice si hace falta /integratorapi.
PROVEEDOR_PREFIJO_OPERACIONES=

# De la hoja de credenciales de axexo
PROVEEDOR_USUARIO=
PROVEEDOR_CONTRASENA=
PROVEEDOR_SITE_ID=          # el "Terminal ID"
PROVEEDOR_CLERK_ID=
PROVEEDOR_MERCHANT_ID=      # solo se usa en la consulta de saldo
PROVEEDOR_LANGUAGE=2

# Tiempos del protocolo de reintentos (los que pidió el proveedor)
PROVEEDOR_TIMEOUT_VENTA=30
PROVEEDOR_TIMEOUT_LOOKUP=15
PROVEEDOR_LOOKUP_DESDE_ENVIO=40
PROVEEDOR_ESPERA_ENTRE_LOOKUPS=10
PROVEEDOR_MAX_LOOKUPS=3
```

Las credenciales nunca se suben al repositorio ni se pegan en chats.

## 2. Levantar la plataforma

```powershell
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose ps
```

`docker compose ps` debe mostrar `db`, `cache`, `web` y `tareas` arriba. `tareas` es el programador: descarga los productos cada día y reintenta las ventas sin resolver cada 5 minutos.

Después de cualquier cambio al `.env`:

```powershell
docker compose up -d --force-recreate web tareas
```

## 3. Instalación de primera vez

Antes, comprueba la conexión con el paso 4 (diagnóstico). Cuando el login y las consultas respondan:

```powershell
docker compose exec web python manage.py instalar
```

Crea el Super Admin, un administrador, un cliente y una caja; muestra **una vez** sus contraseñas; pide escanear el QR del segundo factor del Super Admin; y trae de Emida el **catálogo real** (ya publicado en la caja) y el **saldo real de EPS**. Anota las contraseñas: cada usuario debe cambiarla al entrar. Si prefieres una contraseña conocida para los cuatro, pon `TAE_CLAVE_INICIAL` en el `.env` antes de correrlo.

El frontend se abre con:

```powershell
cd web
npm install
npm run dev
```

Entra a http://localhost:5173.

## 4. Diagnóstico de la conexión

```powershell
docker compose exec web python manage.py probar_proveedor --probar-prefijos --evidencia evidencias/TA-AAAAMMDD/01-diagnostico
```

Cómo leer el resultado:

| Resultado | Qué hacer |
|---|---|
| Login "timed out" | La IP no está autorizada. Vuelve al paso 0 |
| Login HTTP 401 | Revisa `PROVEEDOR_USUARIO` y `PROVEEDOR_CONTRASENA` |
| Prefijo `ninguno` responde 200 | Deja `PROVEEDOR_PREFIJO_OPERACIONES` vacío |
| Solo `/integratorapi` responde 200 | Pon `PROVEEDOR_PREFIJO_OPERACIONES=/integratorapi` y recrea `web` y `tareas` |
| Saldo: "no se encontró el saldo en la respuesta" | Emida responde, pero con otro nombre de campo. La respuesta completa queda en la evidencia; agrega el nombre en `CAMPOS_SALDO` de `apps/proveedor/saldo.py` |

Con esto ya tienes la evidencia de los casos de consulta para la interfaz: **TR-CR-01** (carriers), **TR-PR-01** (productos), **TR-EX-01** (productos extendidos) y **TR-FL-01** (product-flow). Cada llamada queda en un JSON con request, response, código HTTP y fecha y hora, sin el token ni la contraseña.

## 5. Revisar el catálogo

La instalación ya publicó todo lo que Emida lista con monto fijo. Para revisarlo:

```powershell
docker compose exec web python manage.py vincular_producto --listar
```

TX-T-05 pide cinco ventas de **varios productos**: comprueba que haya al menos cinco de tiempo aire de carriers distintos. Si falta alguno (por ejemplo, uno de monto abierto), vincúlalo a mano:

```powershell
docker compose exec web python manage.py vincular_producto --product-id 2001 --sku TEL-TAE-100 --nombre 'Telcel Tiempo Aire $100' --carrier Telcel --monto 100
```

Con comillas simples, el `$` no se interpreta en PowerShell.

## 6. Asignar saldo para vender

Los usuarios ya existen (los creó `instalar`). Falta el saldo, que asigna el Super Admin a mano:

1. Entra como **Super Admin** y cambia la contraseña. En **Exposición** verás el saldo real de EPS en Emida que trajo la instalación: es tu punto de comparación para TX-T-05.
2. En **Usuarios de la red → Clientes → Asignar saldo**, asígnale saldo al cliente. Si prefieres que fluya por el administrador, asígnaselo a él en la pestaña Administradores y que él lo traspase.
3. En **Puntos de venta → Límite**, fija el límite de la caja.
4. Entra como **la caja** y cambia su contraseña. Ya puede vender.

## 7. Ejecutar los casos de tiempo aire

Anota la hora y el folio de cada venta: con eso se arma la evidencia en el paso 8.

| Caso | Cómo se ejecuta | Qué debe pasar en la plataforma |
|---|---|---|
| **TX-T-04** Topup exitoso | Una venta desde la caja | Pantalla "aprobada" con autorización; el límite de la caja baja |
| **TX-T-05** Cinco exitosas de varios productos | Cinco ventas con productos distintos | Las cinco aprobadas. En Exposición, el saldo de Emida bajó la suma de las cinco |
| **TX-T-01 a 03** Validaciones de Invoice-No, accountId y amount | No se pueden provocar desde la plataforma, porque ella misma lo impide: siempre manda Invoice-No y la pantalla no deja vender sin número ni con un monto distinto al del producto | Se documentan como "prevenido por el integrador". Si axexo pide ver la respuesta de Emida, se prueban con su Swagger |
| **TX-LK-01** Invoice-No duplicado | La plataforma nunca repite un Invoice-No: cada intento usa el siguiente | Evidencia: la secuencia de Invoice-No en `reporte_emida.csv` |
| **TX-LK-02** Lookup de una existente | `docker compose exec web python manage.py probar_proveedor --lookup <invoice> --evidencia evidencias/TA-AAAAMMDD/TX-LK-02` | `responseCode=0` y el mensaje original |
| **TX-LK-03** Lookup de una inexistente | `... probar_proveedor --lookup 99999 --evidencia ...` | Respuesta documentada (404), sin cobro |
| **TX-LK-04** invoiceNo no numérico | `... probar_proveedor --lookup ABC --evidencia ...` | HTTP 400 |
| **TX-LK-05** Balance insuficiente | Una venta cuando axexo deja el merchant sin fondos | "Rechazada"; el saldo de la caja no se toca |
| **TX-LK-06** Monto inválido | Vincula un producto de prueba con `--monto` distinto al de Emida y véndelo | "Rechazada"; el saldo no se toca |
| **TX-LK-07 a 09** Respuesta a los 25, 38 y 55 s | Venta con el número o monto programado por axexo | La pantalla espera, la plataforma consulta a los 40 s y cada 10 s, y la venta termina "aprobada". En la evidencia se ven el envío y cada consulta |
| **TX-LK-10** Sin respuesta | Venta con el escenario "no responde" | Tras la última consulta, la venta queda **"sin resolver"** (202) con el saldo apartado, nunca liberado. El contenedor `tareas` la vuelve a consultar cada 5 minutos |

## 8. Exportar la evidencia

```powershell
docker compose exec web python manage.py exportar_evidencia --desde AAAA-MM-DD --carpeta evidencias/TA-AAAAMMDD/ventas
```

Genera:

- **Un JSON por venta** (`VTA-…json`) con el request (encabezados y cuerpo, sin token), cada respuesta (envío y consultas) con su código HTTP y su fecha y hora, y el desenlace. Llena el campo `caso_matriz` con el ID del caso.
- **`reporte_emida.csv`**, en el mismo formato que el archivo de conciliación de Emida, con ventas exitosas y no exitosas. Sirve para el **reporte diario de la Fase 2** (cuántas transacciones, cuáles y por cuánto) y para reportar diferencias de conciliación.

Organiza la carpeta como pide el Plan (sección 9) y súbela al drive junto con la matriz llena:

```
Mex\Tk-<ticket>\Scorpion\AAAAMMDD\TA\
```

## 9. Si algo falla

| Síntoma | Qué revisar |
|---|---|
| La venta responde 503 "el proveedor no está disponible" | Falló el login con Emida antes de enviar: la venta no salió y el saldo no se tocó. Corre `probar_proveedor` |
| La venta se queda en "sin resolver" | Es lo correcto si Emida no contestó. `docker compose exec web python manage.py reintentar_pendientes` la vuelve a consultar sin esperar a `tareas` |
| El catálogo de la caja quedó vacío | Corre `vincular_producto --listar`: si los productos dicen "—", no están vinculados. Si Emida dejó de listarlos, la sincronización pausó sus rutas |
| Ver qué pasó | `docker compose logs --tail=100 web` y `docker compose logs --tail=50 tareas` |
