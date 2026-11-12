"""La compañía de cada producto, aunque Emida no la mande en un campo propio."""
from decimal import Decimal

from django.test import TestCase

from apps.catalogo.models import Carrier, ProductoLogico, TipoMonto, TipoProducto
from apps.catalogo.sincronizacion import inferir_carrier, reasignar_carriers
from apps.core.models import Inquilino


class InferirCarrier(TestCase):
    def test_lo_deduce_del_nombre(self):
        self.assertEqual(inferir_carrier("PRUEBA Recarga Movistar $10"), "Movistar")
        self.assertEqual(inferir_carrier("Recarga AT&T $100"), "AT&T")
        self.assertEqual(inferir_carrier("Paquete Unefón 100"), "Unefon")

    def test_no_adivina_si_no_hay_marca(self):
        self.assertEqual(inferir_carrier("Recarga 50"), "")

    def test_no_confunde_una_marca_dentro_de_otra_palabra(self):
        self.assertEqual(inferir_carrier("Baiterita 10"), "")


class ReasignarCarriers(TestCase):
    def setUp(self):
        self.inquilino = Inquilino.objects.create(clave="scorpion", nombre="Scorpion")
        self.otros = Carrier.objects.create(
            inquilino=self.inquilino, clave="otros", nombre="Otros",
            tipo=TipoProducto.TIEMPO_AIRE,
        )

    def producto(self, sku, nombre, carrier=None):
        return ProductoLogico.objects.create(
            inquilino=self.inquilino, carrier=carrier or self.otros, sku=sku, nombre=nombre,
            tipo=TipoProducto.TIEMPO_AIRE, tipo_monto=TipoMonto.FIJO,
            monto_fijo=Decimal("10"),
        )

    def test_mueve_los_de_otros_a_su_compania(self):
        self.producto("EMD-1", "PRUEBA Recarga Movistar $10")
        self.producto("EMD-2", "PRUEBA Recarga Telcel $20")
        self.producto("EMD-3", "Recarga $30")  # sin marca: se queda

        movidos = reasignar_carriers(self.inquilino)

        self.assertEqual(sorted(movidos), [("EMD-1", "Movistar"), ("EMD-2", "Telcel")])
        self.assertEqual(ProductoLogico.objects.get(sku="EMD-1").carrier.nombre, "Movistar")
        self.assertEqual(ProductoLogico.objects.get(sku="EMD-3").carrier.clave, "otros")

    def test_no_toca_lo_que_ya_se_acomodo_a_mano(self):
        bait = Carrier.objects.create(
            inquilino=self.inquilino, clave="bait", nombre="Bait", tipo=TipoProducto.TIEMPO_AIRE,
        )
        self.producto("EMD-4", "Recarga Movistar $10", carrier=bait)
        self.assertEqual(reasignar_carriers(self.inquilino), [])
