"""
Los endpoints del on-ramp, vistos desde fuera (HTTP).

Mismo propósito que `test_api.py`: que la API no sea una puerta trasera. Se
repiten aquí los candados (sesión, rol, validación de entrada) entrando por
HTTP, y se comprueba el contrato: 201 aprobada, 200 rechazada, 202
sin_resolver, la repetición idempotente, y que el teléfono nunca regrese.
"""
from decimal import Decimal

from django.test import Client

from apps.api.tests.test_api import BaseAPI
from apps.catalogo.models import (
    Carrier, ProductoLogico, ProductoProveedor, TipoMonto, TipoProducto,
)
from apps.identidad.tests.factories import CLAVE
from apps.proveedor.emulador import Emulador
from apps.api import proveedor as puente
from apps.api.tests.test_api import cliente_con

D = Decimal
TELEFONO = "525598236495"
VENTA = "/api/on-ramp/venta"
COTIZA = "/api/on-ramp/cotizacion"


class BaseOnRampAPI(BaseAPI):
    def setUp(self):
        super().setUp()
        carrier = Carrier.objects.create(
            inquilino=self.m.inquilino, clave="btc", nombre="BTC", tipo=TipoProducto.CRIPTO
        )
        producto = ProductoLogico.objects.create(
            inquilino=self.m.inquilino, carrier=carrier, sku="CRIPTO-BTC",
            nombre="Crypto $1 - $500", tipo=TipoProducto.CRIPTO,
            tipo_monto=TipoMonto.RANGO, monto_min=D("1"), monto_max=D("500"),
        )
        ProductoProveedor.objects.create(
            producto=producto, proveedor=self.m.proveedor, product_id="651"
        )
        self.caja = self.como("luis@laermita.mx")

    def usar(self, emulador):
        self.emulador = emulador
        puente.usar_fabrica(lambda: cliente_con(emulador))

    def vender(self, cuerpo=None, llave=None, cliente=None):
        cuerpo = {"sku": "CRIPTO-BTC", "amount": 100, "phoneNumber": TELEFONO} \
            if cuerpo is None else cuerpo
        extra = {"HTTP_IDEMPOTENCY_KEY": llave} if llave else {}
        return (cliente or self.caja).post(VENTA, cuerpo, content_type="application/json", **extra)


class Acceso(BaseOnRampAPI):
    def test_sin_sesion_no_pasa_ninguno(self):
        self.assertIn(Client().get(COTIZA, {"sku": "CRIPTO-BTC", "amount": 100}).status_code, (401, 403))
        r = Client().post(VENTA, {"sku": "CRIPTO-BTC", "amount": 100, "phoneNumber": TELEFONO},
                          content_type="application/json")
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(self.emulador.on_ramps, [])

    def test_un_rol_que_no_es_caja_no_puede_vender_ni_cotizar(self):
        for usuario in (self.m.usuario_cliente, self.m.usuario_admin):
            c = self.como(usuario.correo)
            self.assertEqual(
                c.get(COTIZA, {"sku": "CRIPTO-BTC", "amount": 100}).status_code, 403, usuario.correo)
            self.assertEqual(self.vender(cliente=c).status_code, 403, usuario.correo)
        self.assertEqual(self.emulador.on_ramps, [])


class Entrada(BaseOnRampAPI):
    def test_faltan_campos(self):
        base = {"sku": "CRIPTO-BTC", "amount": 100, "phoneNumber": TELEFONO}
        for campo, clave in (("sku", "producto"), ("amount", "monto"), ("phoneNumber", "telefono")):
            cuerpo = {k: v for k, v in base.items() if k != campo}
            r = self.vender(cuerpo)
            self.assertEqual(r.status_code, 400, campo)
            self.assertEqual(r.json()["clave"], clave)
        self.assertEqual(self.emulador.on_ramps, [])

    def test_telefono_de_11_digitos(self):
        r = self.vender({"sku": "CRIPTO-BTC", "amount": 100, "phoneNumber": "52559823649"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["clave"], "telefono")
        self.assertEqual(self.emulador.on_ramps, [])

    def test_monto_con_decimales_no_se_acepta(self):
        r = self.vender({"sku": "CRIPTO-BTC", "amount": 100.5, "phoneNumber": TELEFONO})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["clave"], "monto")
        self.assertEqual(self.emulador.on_ramps, [])

    def test_monto_fuera_del_rango_del_producto(self):
        r = self.vender({"sku": "CRIPTO-BTC", "amount": 501, "phoneNumber": TELEFONO})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.emulador.on_ramps, [])

    def test_cotizacion_sin_parametros(self):
        self.assertEqual(self.caja.get(COTIZA, {"amount": 100}).status_code, 400)
        r = self.caja.get(COTIZA, {"sku": "CRIPTO-BTC"})
        self.assertEqual((r.status_code, r.json()["clave"]), (400, "monto"))


class Cotizacion(BaseOnRampAPI):
    def test_devuelve_la_tasa_como_texto_y_no_mueve_saldo(self):
        antes = self.caja.get("/api/saldo").json()
        r = self.caja.get(COTIZA, {"sku": "CRIPTO-BTC", "amount": 100})
        self.assertEqual(r.status_code, 200, r.content)
        cuerpo = r.json()
        self.assertEqual(set(cuerpo), {"rate", "fee", "fiatAmountAfterFee", "originalFiatAmount"})
        self.assertTrue(all(isinstance(v, str) for v in cuerpo.values()))
        self.assertEqual(self.caja.get("/api/saldo").json(), antes)


class Venta(BaseOnRampAPI):
    def test_aprobada_es_201_y_trae_los_datos_del_cierre(self):
        r = self.vender()
        self.assertEqual(r.status_code, 201, r.content)
        c = r.json()
        self.assertEqual(c["desenlace"], "aprobada")
        self.assertEqual(c["target_asset"], "BTC")
        self.assertTrue(c["transaction_id"])
        self.assertTrue(c["folio"])
        self.assertEqual(D(c["monto"]), D("100"))
        self.assertNotIn("repetida", c)

    def test_el_telefono_nunca_regresa(self):
        r = self.vender()
        self.assertNotIn(TELEFONO, r.content.decode())

    def test_rechazada_es_200_y_libera(self):
        self.usar(Emulador(rechazar_http=422))
        r = self.vender()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["desenlace"], "rechazada")

    def test_sin_respuesta_es_202_y_no_se_libera(self):
        self.usar(Emulador(nunca_responde=True))
        r = self.vender()
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json()["desenlace"], "sin_resolver")
        # El dinero sigue apartado: no se vuelve a ofrecer como disponible.
        self.assertGreater(D(str(self.caja.get("/api/saldo").json().get("reservado", "1"))), D("0"))


class Idempotencia(BaseOnRampAPI):
    def test_repetir_la_misma_llave_no_cobra_dos_veces(self):
        primera = self.vender(llave="llave-1")
        segunda = self.vender(llave="llave-1")
        self.assertEqual(primera.status_code, 201)
        self.assertEqual(segunda.status_code, 200)
        self.assertTrue(segunda.json()["repetida"])
        self.assertEqual(segunda.json()["folio"], primera.json()["folio"])
        self.assertEqual(self.emulador.cobros(), 1)
        self.assertEqual(len(self.emulador.on_ramps), 1)

    def test_repetir_un_sin_resolver_sigue_siendo_202(self):
        self.usar(Emulador(nunca_responde=True))
        self.assertEqual(self.vender(llave="llave-2").status_code, 202)
        r = self.vender(llave="llave-2")
        self.assertEqual(r.status_code, 202, r.content)
        self.assertTrue(r.json()["repetida"])

    def test_llaves_distintas_son_ventas_distintas(self):
        self.assertEqual(self.vender(llave="a").status_code, 201)
        self.assertEqual(self.vender(llave="b").status_code, 201)
        self.assertEqual(self.emulador.cobros(), 2)
