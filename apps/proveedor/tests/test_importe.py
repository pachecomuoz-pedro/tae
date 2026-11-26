"""
El monto va a Emida con exactamente 2 decimales.

La base guarda 50.0000 y el API respondía 400 "amount must have at most 2
decimal places" a toda venta: pasó en el UAT y el emulador no lo detectaba.
"""
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.catalogo.servicios import MontoInvalido, exigir_centavos
from apps.proveedor.cliente import ClienteProveedor, Config
from apps.proveedor.emulador import Emulador


def cliente():
    emulador = Emulador(responde_en=0.1)
    cfg = Config(base_url="https://emulado.local", usuario="u", contrasena="x",
                 site_id="1", clerk_id="1")
    return ClienteProveedor(cfg, sesion=emulador), emulador


class ImporteAlProveedor(SimpleTestCase):
    def test_el_topup_manda_dos_decimales(self):
        c, emulador = cliente()
        c.topup(invoice_no="1", product_id="TEL50", account_id="9991234567",
                amount=Decimal("50.0000"))
        self.assertEqual(emulador.topups[-1]["cuerpo"]["amount"], "50.00")

    def test_formato_del_importe(self):
        self.assertEqual(ClienteProveedor._importe(Decimal("50.0000")), "50.00")
        self.assertEqual(ClienteProveedor._importe("100"), "100.00")
        self.assertEqual(ClienteProveedor._importe(Decimal("12.5")), "12.50")


class CentavosEnElCatalogo(SimpleTestCase):
    producto = SimpleNamespace(nombre="Servicio de prueba")

    def test_acepta_hasta_dos_decimales(self):
        for m in ["50", "50.5", "50.50", "50.5000", Decimal("50.0000")]:
            exigir_centavos(m, self.producto)

    def test_rechaza_centavos_de_mas(self):
        with self.assertRaises(MontoInvalido):
            exigir_centavos("50.005", self.producto)