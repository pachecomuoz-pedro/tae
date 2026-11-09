"""
El catálogo visto desde la venta.

Aquí se comprueba el orden de las comprobaciones, que es lo que importa:
una venta que iba a fallar de todos modos no debe haber tocado el dinero.
"""
from decimal import Decimal

from django.test import TestCase

from apps.catalogo import servicios
from apps.catalogo.models import (
    ProductoLogico,
    ProductoProveedor,
    TipoMonto,
    TipoProducto,
)
from apps.identidad import acciones, permisos
from apps.identidad.tests.factories import Mundo
from apps.saldo import services
from apps.saldo.models import Reserva

D = Decimal


class VenderConCatalogo(TestCase):
    def setUp(self):
        self.m = Mundo()
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("1000"))
        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("500"))

    def test_la_venta_toma_el_monto_del_catalogo(self):
        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)

        self.assertEqual(reserva.monto, D("100.0000"))
        self.assertEqual(reserva.operacion.producto, self.m.producto)

    def test_el_cajero_no_puede_inventarse_el_monto_de_una_denominacion(self):
        """Eligió Telcel $100: que teclee 5 no cambia lo que cuesta."""
        reserva = acciones.vender(
            self.m.usuario_pdv, self.m.pdv, self.m.producto, D("5")
        )
        self.assertEqual(reserva.monto, D("100.0000"))

    def test_un_producto_sin_ruta_no_llega_a_tocar_el_saldo(self):
        ProductoProveedor.objects.filter(producto=self.m.producto).delete()
        antes = services.bolsa_de(self.m.cliente).reservado

        with self.assertRaises(servicios.SinRuta):
            acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)

        self.assertEqual(services.bolsa_de(self.m.cliente).reservado, antes)
        self.assertEqual(Reserva.objects.count(), 0)

    def test_un_monto_invalido_no_llega_a_tocar_el_saldo(self):
        abierto = ProductoLogico.objects.create(
            inquilino=self.m.inquilino, carrier=self.m.carrier, sku="TEL-ABIERTO",
            nombre="Telcel abierta", tipo=TipoProducto.TIEMPO_AIRE,
            tipo_monto=TipoMonto.RANGO, monto_min=D("10"), monto_max=D("500"),
        )
        ProductoProveedor.objects.create(
            producto=abierto, proveedor=self.m.proveedor, product_id="OPEN"
        )

        with self.assertRaises(servicios.MontoInvalido):
            acciones.vender(self.m.usuario_pdv, self.m.pdv, abierto, D("900"))

        self.assertEqual(Reserva.objects.count(), 0)

    def test_el_permiso_se_revisa_antes_que_el_catalogo(self):
        """
        Un cajero ajeno no debe enterarse siquiera de si el producto existe
        o tiene ruta. Primero el permiso, siempre.
        """
        ProductoProveedor.objects.filter(producto=self.m.producto).delete()

        with self.assertRaises(permisos.PermisoDenegado):
            acciones.vender(self.m.usuario_pdv, self.m.otra_caja, self.m.producto)

    def test_un_producto_dado_de_baja_no_se_vende(self):
        self.m.producto.activo = False
        self.m.producto.save()

        with self.assertRaises(servicios.ProductoNoDisponible):
            acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)
        self.assertEqual(Reserva.objects.count(), 0)

    def test_la_operacion_guarda_que_se_vendio(self):
        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)
        services.capturar(reserva)

        operacion = reserva.operacion
        operacion.refresh_from_db()
        self.assertEqual(operacion.producto.sku, "TEL-TAE-100")
        self.assertEqual(operacion.punto_venta, self.m.pdv)
        self.assertTrue(operacion.folio.startswith("VTA-"))

    def test_vender_por_codigo_de_barras(self):
        producto = servicios.por_codigo_barras(self.m.inquilino, "TEL-TAE-100")
        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, producto)

        self.assertEqual(reserva.monto, D("100.0000"))
