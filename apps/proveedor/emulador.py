"""
Emulador del proveedor.

No es un extra para las pruebas: es lo que permite reproducir a voluntad el
timeout, el duplicado y la respuesta ambigua, que son exactamente los casos
que nunca ocurren cuando uno quiere probarlos y siempre ocurren en
producción un viernes.

La certificación programa su propio emulador para responder a los 25, 38 o
55 segundos, o para no responder. Este hace lo mismo sin esperar de verdad,
para que la batería corra en segundos en lugar de en minutos.
"""
import itertools
from decimal import Decimal

import requests

from .cliente import APROBADOS  # noqa: F401  (el emulador responde con estos códigos)


class Emulador:
    """
    Sustituye a `requests.Session` dentro del cliente.

    `responde_en` es el tiempo que tardaría el proveedor. Si es mayor que el
    timeout de la llamada, la llamada truena con Timeout igual que en la
    vida real, y la transacción queda registrada aquí dentro como si el
    proveedor la hubiera procesado. Esa combinación —el cliente no se entera,
    el proveedor sí la hizo— es el escenario que cuesta dinero.
    """

    def __init__(self, *, responde_en=0.2, nunca_responde=False,
                 aprobar=True, response_code=None, rechazar_http=None):
        self.responde_en = responde_en
        self.nunca_responde = nunca_responde
        self.aprobar = aprobar
        self.response_code = response_code
        self.rechazar_http = rechazar_http

        self.transacciones = {}   # invoice_no -> cuerpo de respuesta
        # Lo que contestan las consultas de información. Las pruebas los
        # cambian para simular un saldo distinto o un producto que desaparece.
        self.saldo = "250000.00"
        # Mismos product_id que siembra sembrar_demo.py, para que en modo
        # simulado la sincronización diaria no pause el catálogo de demo.
        self.productos_terminal = [
            {"productId": f"{clave}{monto}", "description": f"{nombre} Tiempo Aire ${monto}",
             "carrierName": nombre, "amount": monto}
            for nombre, clave in (("Telcel", "TEL"), ("AT&T", "ATT"), ("Movistar", "MOV"))
            for monto in (50, 100, 200)
        ]
        self.llamadas = []        # bitácora de todo lo que se pidió
        self._ids = itertools.count(991204877)
        # Reloj simulado en segundos desde que salió el envío. Lo avanza el
        # cliente con sus esperas, igual que pasaría el tiempo de verdad.
        self.reloj = 0.0
        self.disponible_en = None

    def avanzar(self, segundos):
        """Lo que la prueba llama en lugar de dormir."""
        self.reloj += float(segundos or 0)

    # -- utilidades para las pruebas -------------------------------------
    @property
    def topups(self):
        return [c for c in self.llamadas if c["ruta"].endswith("/transaction/topup")]

    @property
    def lookups(self):
        return [c for c in self.llamadas if "/lookup/invoice-no/" in c["ruta"]]

    def cobros(self):
        """Cuántas veces se descontó balance de verdad. Debe ser 0 o 1."""
        return len([t for t in self.transacciones.values() if t.get("_cobrada")])

    # -- interfaz de requests.Session ------------------------------------
    def post(self, url, json=None, timeout=None, **kw):
        return self.request("POST", url, headers=kw.get("headers"), json=json, timeout=timeout)

    def request(self, metodo, url, headers=None, json=None, timeout=None, **kw):
        headers = headers or {}
        ruta = url.split("://", 1)[-1].split("/", 1)[-1]
        ruta = "/" + ruta
        self.llamadas.append(
            {"metodo": metodo, "ruta": ruta, "headers": dict(headers), "cuerpo": json}
        )

        if ruta.endswith("/api/v1/auth/login"):
            return _Resp(200, {"token": "jwt-de-prueba", "expiresIn": 900})

        if ruta.endswith("/transaction/topup"):
            return self._topup(headers, json, timeout)

        if "/lookup/invoice-no/" in ruta:
            return self._lookup(ruta.rsplit("/", 1)[-1], timeout)

        # Consultas de información. Formatos supuestos (no tenemos el
        # contrato exacto); lo que importa probar es nuestro manejo.
        if ruta.endswith("/account/balance"):
            return _Resp(200, {"availableBalance": self.saldo, "currency": "MXN"})
        if ruta.endswith("/terminal/products") or ruta.endswith("/terminal/products/ext"):
            return _Resp(200, list(self.productos_terminal))
        if ruta.endswith("/terminal/product-flow"):
            return _Resp(200, [
                {"productId": p["productId"],
                 "references": [{"language": "2", "label": "Número a 10 dígitos"}]}
                for p in self.productos_terminal
            ])
        if ruta.endswith("/terminal/carriers"):
            nombres = sorted({p["carrierName"] for p in self.productos_terminal})
            return _Resp(200, [{"id": i + 1, "name": n} for i, n in enumerate(nombres)])

        return _Resp(200, {})

    # -- métodos emulados -------------------------------------------------
    def _topup(self, headers, cuerpo, timeout):
        invoice = str(headers.get("Invoice-No") or "")

        if self.rechazar_http:
            return _Resp(
                self.rechazar_http,
                {"title": "Rechazado", "detail": "El emulador rechaza a propósito"},
            )

        # Invoice-No repetido: el proveedor ya la tiene. Nunca doble débito.
        if invoice in self.transacciones:
            previa = dict(self.transacciones[invoice])
            previa["_duplicada"] = True
            return _Resp(200, previa)

        cuerpo_respuesta = self._armar(invoice, cuerpo)
        # La transacción se registra SIEMPRE, conteste o no. Así es en la
        # realidad: que el cliente no escuche no significa que no pasó.
        self.transacciones[invoice] = cuerpo_respuesta
        self.disponible_en = None if self.nunca_responde else self.responde_en

        if self.nunca_responde or (timeout is not None and self.responde_en > timeout):
            # El cliente se cansa en su timeout; el proveedor sigue trabajando.
            self.reloj += float(timeout or 0)
            raise requests.Timeout(f"el emulador tarda {self.responde_en}s")

        self.reloj += self.responde_en
        return _Resp(200, cuerpo_respuesta)

    def _lookup(self, invoice, timeout):
        if self.nunca_responde:
            raise requests.Timeout("el emulador no responde ni el lookup")

        if invoice not in self.transacciones:
            return _Resp(404, {"title": "No encontrada", "detail": "Sin registro"})

        datos = dict(self.transacciones[invoice])

        # Si el proveedor todavía la está procesando, contesta sin folio del
        # carrier. Es la respuesta ambigua: ni sí ni no.
        if self.disponible_en is None or self.reloj < self.disponible_en:
            return _Resp(200, {"responseCode": "", "responseMessage": "En proceso"})

        return _Resp(200, datos)

    def _armar(self, invoice, cuerpo):
        aprobada = self.aprobar
        codigo = self.response_code or ("0" if aprobada else "51")
        tid = str(next(self._ids))
        datos = {
            "transactionId": tid,
            "responseCode": codigo,
            "responseMessage": (
                f"totalAmount {cuerpo.get('amount')} PhoneNumber {cuerpo.get('accountId')}"
            ),
            "_cobrada": aprobada,
        }
        if aprobada:
            datos["controlNo"] = str(8842019336 + int(invoice or 0))
        return datos


class _Resp:
    """Lo mínimo de una respuesta de requests que usa el cliente."""

    def __init__(self, status_code, datos):
        self.status_code = status_code
        self._datos = datos
        self.text = str(datos)

    def json(self):
        return self._datos


def emulador_de_certificacion(escenario):
    """
    Los cuatro escenarios de reintento de la matriz, por nombre.

    TX-LK-07  el proveedor tarda 25 s y la consulta a los 30 lo encuentra
    TX-LK-08  tarda 38 s, la primera consulta llega antes y sale ambigua
    TX-LK-09  tarda 55 s, hacen falta varias consultas
    TX-LK-10  no responde nunca: la venta queda sin resolver
    """
    mapa = {
        "TX-LK-07": Emulador(responde_en=25),
        "TX-LK-08": Emulador(responde_en=38),
        "TX-LK-09": Emulador(responde_en=55),
        "TX-LK-10": Emulador(nunca_responde=True),
    }
    if escenario not in mapa:
        raise KeyError(f"{escenario} no está en la matriz")
    return mapa[escenario]
