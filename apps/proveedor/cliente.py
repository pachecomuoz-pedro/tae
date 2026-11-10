"""
Cliente REST del proveedor (axexo / Integrator API v1).

Tres cosas de este archivo salieron de leer la matriz de certificación y no
de suponer:

1. **El criterio de éxito no es el mismo para todos los métodos.** Tiempo
   aire aprueba con `responseCode == '0'`. Pago de servicios aprueba con
   `responseCode == '00'` y `h2hResultCode == '0'`. La matriz además se
   contradice entre el pago y su propio lookup, así que esto vive en un
   diccionario por método, está marcado como pendiente de confirmar, y no
   como una constante global. Una constante global aquí significa leer una
   venta aprobada como rechazada, o al revés.

2. **El pago de servicios cobra comisión** y hay que consultarla antes con
   `user-fee`. El cliente paga monto más comisión.

3. **Los tiempos del reintento son datos, no código.** La certificación
   programa el emulador a 25, 38 y 55 segundos; nuestros tiempos tienen que
   poder moverse sin desplegar.

Decisiones: 11 criterio de éxito, 19 un solo Site-Id, 20 tiempos en el .env.
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal

import requests
from django.conf import settings

log = logging.getLogger("tae.proveedor")


class ErrorProveedor(Exception):
    """Algo salió mal y sabemos qué."""


class SinRespuesta(ErrorProveedor):
    """
    No contestó a tiempo. No dice que la venta falló: dice que no sabemos.
    La diferencia es el centro de todo el diseño.
    """


class RechazoProveedor(ErrorProveedor):
    """El proveedor dijo que no (HTTP 4xx). La venta no ocurrió: liberar es seguro."""
    def __init__(self, mensaje, *, http=None, codigo=None, cuerpo=None):
        super().__init__(mensaje)
        self.http = http
        self.codigo = codigo
        self.cuerpo = cuerpo or {}


class NoAutenticado(ErrorProveedor):
    """No se pudo obtener token. Antes del envío significa que la venta no salió."""


# ---------------------------------------------------------------------------
# Criterio de éxito
# ---------------------------------------------------------------------------
# CONFIRMADO POR EL PROVEEDOR (correo del 29 oct 2026):
#   "El criterio de éxito por método siempre será el '00' y/o '0'. En una
#    transacción, sea TAE o PS, siempre se responderá como exitosa con esos
#    valores. Al momento de realizar el lookup, deberá responder con el mismo
#    comportamiento."
#
# O sea: un solo criterio para todo, y el lookup se lee igual que la venta.
# Queda más simple de lo que la matriz sugería, donde tiempo aire y pago de
# servicios parecían pedir cosas distintas.
APROBADOS = {"0", "00"}

# `h2hResultCode` no lo mencionaron. La matriz sí lo exige en PS-13, así que
# se aplica la lectura prudente: si el proveedor lo manda, tiene que venir en
# cero; si no lo manda, no se inventa un requisito que ellos no pidieron.
METODOS_CONOCIDOS = {"topup", "bill_payment", "lookup"}


def aprobada(metodo, cuerpo):
    """
    True solo si el proveedor confirmó. La ausencia de confirmación no es una
    confirmación: un método desconocido o un cuerpo sin `responseCode` nunca
    aprueban.
    """
    if metodo not in METODOS_CONOCIDOS:
        return False

    codigo = cuerpo.get("responseCode")
    if codigo is None or str(codigo).strip() not in APROBADOS:
        return False

    h2h = cuerpo.get("h2hResultCode")
    if h2h is not None and str(h2h).strip() not in APROBADOS:
        return False
    return True


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
@dataclass
class Config:
    """
    Lo que cambia entre certificación y producción, y lo que el proveedor
    puede pedirnos que ajustemos durante la certificación.
    """

    base_url: str
    usuario: str = ""
    contrasena: str = ""
    site_id: str = ""
    clerk_id: str = ""
    merchant_id: str = ""
    language: str = "2"  # 1 inglés, 2 español. El patrón del API es ^(1|2)$

    # Prefijo de las operaciones (topup, lookup, etc.), separado del login.
    # La hoja de axexo da el login en la raíz del host
    # (`/api/v1/auth/login`) y el "End Point" bajo `/integratorapi`. Si las
    # operaciones viven ahí, se pone PROVEEDOR_PREFIJO_OPERACIONES=/integratorapi
    # sin tocar código. Vacío = mismo nivel que el login. CONFIRMAR con axexo.
    prefijo_operaciones: str = ""
    # Prefijo del login. Vacío = /api/v1/auth/login en la raíz del host. Si
    # /integratorapi solo acepta tokens de su propio login, va /integratorapi.
    # diagnostico_emida.py dice cuál funciona.
    prefijo_login: str = ""
    # Nombre del campo del usuario en el cuerpo del login ("email" o "username").
    campo_usuario: str = "email"

    # Tiempos del protocolo, indicados por el proveedor:
    #   "El llamado del lookup se debe realizar a los 40 segundos una vez
    #    enviado el PinDistSale, y luego se debe realizar cada 10 segundos."
    #
    # `lookup_desde_envio` se cuenta desde que SALIÓ la venta, no desde que
    # se nos acabó la paciencia. Si se contara desde el timeout, bajar el
    # timeout adelantaría la consulta y dejaría de cumplir lo que pidieron.
    timeout_venta: float = 30.0
    timeout_lookup: float = 15.0
    lookup_desde_envio: float = 40.0
    espera_entre_lookups: float = 10.0
    max_lookups: int = 3

    @classmethod
    def desde_settings(cls):
        c = settings.TAE_PROVEEDOR
        return cls(**{k: v for k, v in c.items() if k in cls.__dataclass_fields__})


def desenvolver(datos):
    """
    Quita el sobre en el que Emida envuelve sus respuestas.

    Visto en UAT (7 de noviembre de 2026): Emida responde
    `{"success": ..., "data": {...}, "message": ..., "code": ...}`. Los
    campos que nos importan (token, responseCode, la lista de productos, el
    saldo) vienen dentro de `data`. Si la respuesta no trae ese sobre, se
    devuelve tal cual: así funciona con ambos formatos.
    """
    if isinstance(datos, dict) and "data" in datos and ("success" in datos or "code" in datos):
        interior = datos["data"]
        if isinstance(interior, (dict, list)):
            return interior
    return datos


@dataclass
class Respuesta:
    """Lo que devolvió el proveedor, ya interpretado."""

    metodo: str
    http: int
    cuerpo: dict
    aprobada: bool
    transaction_id: str = ""
    control_no: str = ""
    response_code: str = ""
    h2h_result_code: str = ""
    mensaje: str = ""
    crudo: str = field(default="", repr=False)
    # El JSON tal como llegó. En las consultas de información (productos,
    # carriers, product-flow) suele ser una lista, no un objeto, y `cuerpo`
    # quedaría vacío. Quien lee catálogos usa `datos`.
    datos: object = field(default=None, repr=False)

    @classmethod
    def desde(cls, metodo, http, datos, crudo=""):
        datos = desenvolver(datos)
        cuerpo = datos if isinstance(datos, dict) else {}
        return cls(
            metodo=metodo,
            http=http,
            cuerpo=cuerpo,
            datos=datos,
            aprobada=aprobada(metodo, cuerpo),
            transaction_id=str(cuerpo.get("transactionId") or ""),
            control_no=str(cuerpo.get("controlNo") or ""),
            response_code=str(cuerpo.get("responseCode") or ""),
            h2h_result_code=str(cuerpo.get("h2hResultCode") or ""),
            mensaje=str(cuerpo.get("responseMessage") or cuerpo.get("detail") or ""),
            crudo=crudo,
        )


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
class ClienteProveedor:
    """
    Un cliente por proceso. Guarda el token y lo renueva solo.

    El token se renueva bajo candado para que veinte ventas simultáneas al
    expirar no disparen veinte logins. Sin el candado, el primer segundo
    después del vencimiento se convierte en una tormenta contra el
    proveedor justo cuando menos conviene.
    """

    def __init__(self, config=None, sesion=None):
        self.config = config or Config.desde_settings()
        self.sesion = sesion or requests.Session()
        self._token = ""
        self._token_vence = 0.0
        self._candado = threading.Lock()

    # -- autenticación ----------------------------------------------------
    def _autenticar(self):
        url = f"{self.config.base_url}{self.config.prefijo_login}/api/v1/auth/login"
        try:
            h = {
                "Authorization": "Bearer ",
                "APP": "INTEGRATOR_API",
            }
            r = self.sesion.post(
                url,
                headers=h,
                json={self.config.campo_usuario or "email": self.config.usuario,
                      "password": self.config.contrasena},
                timeout=self.config.timeout_lookup,
            )
        except requests.RequestException as e:
            raise NoAutenticado(f"No se pudo pedir el token: {e}") from e

        if r.status_code != 200:
            log.warning("login rechazado: HTTP %s %s", r.status_code, r.text[:300])
            raise NoAutenticado(f"Login rechazado con HTTP {r.status_code}")

        try:
            cuerpo = desenvolver(r.json())
        except ValueError as e:
            raise NoAutenticado("El login respondió algo que no es JSON") from e
        if not isinstance(cuerpo, dict):
            raise NoAutenticado("El login respondió sin token")
        token = cuerpo.get("token") or cuerpo.get("accessToken") or cuerpo.get("access_token")
        if not token:
            raise NoAutenticado("El login respondió sin token")

        # Si el proveedor no dice cuánto dura, se asume corto y se renueva
        # antes. Suponer de más es quedarse con un token muerto a media venta.
        vigencia = float(cuerpo.get("expiresIn") or 900)
        self._token = token
        self._token_vence = time.monotonic() + vigencia - 60
        return token

    def token(self):
        with self._candado:
            if not self._token or time.monotonic() >= self._token_vence:
                return self._autenticar()
            return self._token

    def invalidar_token(self):
        """Tras un 401: el siguiente intento pide uno nuevo."""
        with self._candado:
            self._token = ""
            self._token_vence = 0.0

    # -- cabeceras --------------------------------------------------------
    def _cabeceras(self, *, invoice_no=None, product_id=None):
        cab = {
            "Authorization": f"Bearer {self.token()}",
            "Site-Id": str(self.config.site_id),
            "Clerk-Id": str(self.config.clerk_id),
            "Language": str(self.config.language),
            "Content-Type": "application/json",
        }
        if invoice_no is not None:
            cab["Invoice-No"] = str(invoice_no)
        if product_id is not None:
            cab["Product-Id"] = str(product_id)
        return cab

    # -- llamada base -----------------------------------------------------
    def _llamar(self, metodo_http, ruta, *, metodo, cabeceras, cuerpo=None, timeout=None):
        url = f"{self.config.base_url}{self.config.prefijo_operaciones}{ruta}"
        try:
            r = self.sesion.request(
                metodo_http, url, headers=cabeceras, json=cuerpo,
                timeout=timeout or self.config.timeout_venta,
            )
        except requests.Timeout as e:
            # El caso que define el diseño: no sabemos si pasó o no.
            raise SinRespuesta(f"{metodo} no respondió en {timeout}s") from e
        except requests.RequestException as e:
            raise SinRespuesta(f"{metodo} falló la conexión: {e}") from e

        try:
            datos = r.json()
            print(f"{metodo} HTTP {r.status_code} OK", "datos", r)
        except ValueError:
            datos = {}

        # Una línea por llamada, sin cuerpo: el cuerpo de una venta de pines
        # trae el PIN y no debe quedar en los logs.
        log.info("%s %s %s → HTTP %s", metodo, metodo_http, url, r.status_code)

        if r.status_code == 401:
            self.invalidar_token()
            raise RechazoProveedor(
                "El proveedor rechazó el token", http=401, cuerpo=datos
            )
        if r.status_code >= 400:
            # 400 validación, 404 no existe, 422 regla de negocio. Todos son
            # rechazos definitivos: la venta NO ocurrió y el saldo se libera.
            if not isinstance(datos, dict):
                datos = {"detail": str(datos)[:500]}
            # En un error sí se registra el cuerpo: trae el motivo y no trae PINs.
            log.warning("%s rechazado HTTP %s: %s", metodo, r.status_code, str(datos)[:500])
            interior = desenvolver(datos)
            interior = interior if isinstance(interior, dict) else {}
            raise RechazoProveedor(
                datos.get("detail") or datos.get("title") or interior.get("message")
                or datos.get("message") or f"HTTP {r.status_code}",
                http=r.status_code,
                codigo=str(datos.get("responseCode") or interior.get("responseCode") or ""),
                cuerpo=datos,
            )

        print(f"{metodo} HTTP {r.status_code} OK", "datos", datos)
        return Respuesta.desde(metodo, r.status_code, datos, crudo=r.text[:4000])

    # -- métodos del API --------------------------------------------------
    def topup(self, *, invoice_no, product_id, account_id, amount):
        """Venta de tiempo aire. PinDistSale en el nombre viejo."""
        return self._llamar(
            "POST", "/api/v1/transaction/topup",
            metodo="topup",
            cabeceras=self._cabeceras(invoice_no=invoice_no, product_id=product_id),
            cuerpo={"accountId": str(account_id), "amount": str(Decimal(amount))},
        )

    def user_fee(self, product_id):
        """
        Comisión del producto. Es prerrequisito del pago de servicios: hay
        que saber cuánto se le suma al monto antes de cobrarlo.
        """
        return self._llamar(
            "GET", f"/api/v1/product/{product_id}/user-fee",
            metodo="user_fee",
            cabeceras=self._cabeceras(),
            timeout=self.config.timeout_lookup,
        )

    def bill_payment(self, *, invoice_no, product_id, account_number, amount, fee):
        """
        Pago de servicio con comisión aplicada. El cliente paga monto más
        comisión y la certificación compara lo enviado contra lo respondido.
        """
        return self._llamar(
            "POST", "/api/v1/bill-payment/user-fee",
            metodo="bill_payment",
            cabeceras=self._cabeceras(invoice_no=invoice_no, product_id=product_id),
            cuerpo={
                "accountNumber": str(account_number),
                "amount": str(Decimal(amount)),
                "userFee": str(Decimal(fee)),
            },
        )

    def lookup(self, invoice_no):
        """
        La consulta que resuelve un timeout.

        Un 404 aquí no es un error nuestro: significa que el proveedor no
        tiene esa transacción, es decir que la venta nunca entró. Quien
        llama lo trata como rechazo y libera el saldo.
        """
        return self._llamar(
            "GET", f"/api/v1/transaction/lookup/invoice-no/{invoice_no}",
            metodo="lookup",
            cabeceras=self._cabeceras(),
            timeout=self.config.timeout_lookup,
        )

    def saldo_merchant(self):
        """
        Saldo del merchant de EPS en Emida (`GET /api/v1/merchants/account/balance`).

        Lo ve solo el Super Admin. NO tiene relación con los saldos de la
        red, que el Super Admin asigna a mano (decisión 23). Agrega
        `Merchant-Id` si está en el .env: el método clásico de Emida
        (GetMerchantBalance) se consulta por merchant.
        """
        cabeceras = self._cabeceras()
        if self.config.merchant_id:
            cabeceras["Merchant-Id"] = str(self.config.merchant_id)
        return self._llamar(
            "GET", "/api/v1/merchants/account/balance", metodo="saldo_merchant",
            cabeceras=cabeceras, timeout=self.config.timeout_lookup,
        )

    # -- consultas de información (GetCarrierList, GetProductList, ...) ---
    # Ninguna mueve dinero. Se usan para armar y actualizar el catálogo y
    # para el diagnóstico. Rutas del Anexo B, hoja "End Points".
    def carriers(self):
        """GetCarrierList: carriers disponibles en el terminal."""
        return self._llamar(
            "GET", "/api/v1/terminal/carriers", metodo="carriers",
            cabeceras=self._cabeceras(), timeout=self.config.timeout_lookup,
        )

    def productos(self):
        """GetProductList: productos asignados al terminal (rate plan)."""
        return self._llamar(
            "GET", "/api/v1/terminal/products/ext", metodo="productos",
            cabeceras=self._cabeceras(), timeout=self.config.timeout_lookup,
        )

    def productos_ext(self):
        """GetProductListExt: igual que productos, con productCategory."""
        return self._llamar(
            "GET", "/api/v1/terminal/products/ext", metodo="productos_ext",
            cabeceras=self._cabeceras(), timeout=self.config.timeout_lookup,
        )

    def product_flow(self, product_id=None):
        """
        ProductFlowInfoService: textos, referencias y tooltips por producto,
        en el idioma de `Language`. Las Reglas Generales de Emida piden
        descargarlo a diario; lo hace `sincronizar_catalogo`.
        """
        return self._llamar(
            "GET", "/api/v1/terminal/product-flow", metodo="product_flow",
            cabeceras=self._cabeceras(product_id=product_id),
            timeout=self.config.timeout_lookup,
        )
