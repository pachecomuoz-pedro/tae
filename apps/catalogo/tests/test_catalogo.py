"""
Catálogo: ruteo, montos y código de barras.

Lo que se prueba aquí no es que un producto se guarde, es que el catálogo
cumpla las dos promesas que justifican su diseño: que cambiar de proveedor
no toque la venta, y que el código de barras siga sirviendo cuando cambie
el precio.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.catalogo import servicios
from apps.catalogo.models import (
    Carrier,
    ProductoLogico,
    ProductoProveedor,
    Proveedor,
    TipoMonto,
    TipoProducto,
)
from apps.core.models import Inquilino

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.inq = Inquilino.objects.create(clave="scorpion", nombre="Scorpion")
        self.emida = Proveedor.objects.create(
            inquilino=self.inq, clave="emida", nombre="Emida"
        )
        self.telcel = Carrier.objects.create(
            inquilino=self.inq, clave="telcel", nombre="Telcel",
            tipo=TipoProducto.TIEMPO_AIRE,
        )
        self.cien = ProductoLogico.objects.create(
            inquilino=self.inq, carrier=self.telcel, sku="TEL-TAE-100",
            nombre="Telcel Tiempo Aire $100", tipo=TipoProducto.TIEMPO_AIRE,
            tipo_monto=TipoMonto.FIJO, monto_fijo=D("100"),
        )
        self.ruta = ProductoProveedor.objects.create(
            producto=self.cien, proveedor=self.emida, product_id="TEL100"
        )


class CambiarDeProveedor(Base):
    """La promesa principal de las dos capas."""

    def test_la_ruta_cambia_y_el_producto_no(self):
        segundo = Proveedor.objects.create(
            inquilino=self.inq, clave="otro", nombre="Segundo proveedor"
        )
        ProductoProveedor.objects.create(
            producto=self.cien, proveedor=segundo, product_id="OTRO-TELCEL-100",
            prioridad=5,
        )

        # El sku, el nombre y el monto que ve la caja son los mismos.
        self.assertEqual(self.cien.sku, "TEL-TAE-100")
        self.assertEqual(servicios.validar_monto(self.cien), D("100.0000"))
        # Pero ahora se surte por el otro, que tiene prioridad más baja.
        self.assertEqual(servicios.ruta_para(self.cien).proveedor, segundo)

    def test_gana_la_prioridad_mas_baja(self):
        segundo = Proveedor.objects.create(
            inquilino=self.inq, clave="otro", nombre="Segundo"
        )
        ProductoProveedor.objects.create(
            producto=self.cien, proveedor=segundo, product_id="X", prioridad=50
        )
        self.assertEqual(servicios.ruta_para(self.cien).proveedor, self.emida)

    def test_una_ruta_apagada_no_se_usa(self):
        segundo = Proveedor.objects.create(
            inquilino=self.inq, clave="otro", nombre="Segundo"
        )
        ProductoProveedor.objects.create(
            producto=self.cien, proveedor=segundo, product_id="X", prioridad=1
        )
        ProductoProveedor.objects.filter(proveedor=segundo).update(activo=False)

        self.assertEqual(servicios.ruta_para(self.cien).proveedor, self.emida)

    def test_un_proveedor_apagado_arrastra_sus_rutas(self):
        self.emida.activo = False
        self.emida.save()

        with self.assertRaises(servicios.SinRuta):
            servicios.ruta_para(self.cien)

    def test_un_producto_sin_ruta_no_se_vende_aunque_este_activo(self):
        """Un botón en la pantalla no sirve si nadie surte el producto."""
        self.ruta.delete()

        with self.assertRaises(servicios.SinRuta):
            servicios.ruta_para(self.cien)
        self.assertNotIn(self.cien, servicios.vendibles(self.inq))

    def test_las_de_respaldo_vienen_en_orden(self):
        segundo = Proveedor.objects.create(
            inquilino=self.inq, clave="dos", nombre="Dos"
        )
        tercero = Proveedor.objects.create(
            inquilino=self.inq, clave="tres", nombre="Tres"
        )
        ProductoProveedor.objects.create(
            producto=self.cien, proveedor=tercero, product_id="C", prioridad=30
        )
        ProductoProveedor.objects.create(
            producto=self.cien, proveedor=segundo, product_id="B", prioridad=20
        )

        respaldo = servicios.rutas_de_respaldo(self.cien)
        self.assertEqual([r.proveedor for r in respaldo], [segundo, tercero])


class Bajas(Base):
    def test_un_producto_dado_de_baja_no_se_vende(self):
        self.cien.activo = False
        self.cien.save()

        with self.assertRaises(servicios.ProductoNoDisponible):
            servicios.validar_monto(self.cien)

    def test_bajar_un_carrier_baja_todos_sus_productos(self):
        self.telcel.activo = False
        self.telcel.save()
        self.cien.refresh_from_db()

        with self.assertRaises(servicios.ProductoNoDisponible):
            servicios.validar_monto(self.cien)
        self.assertEqual(servicios.vendibles(self.inq).count(), 0)


class Montos(Base):
    def test_la_denominacion_fija_ignora_lo_que_teclee_el_cajero(self):
        """Eligió 'Telcel $100', no tecleó cien."""
        self.assertEqual(servicios.validar_monto(self.cien), D("100.0000"))
        self.assertEqual(servicios.validar_monto(self.cien, D("999")), D("100.0000"))

    def test_el_rango_respeta_minimo_y_maximo(self):
        abierto = ProductoLogico.objects.create(
            inquilino=self.inq, carrier=self.telcel, sku="TEL-TAE-ABIERTO",
            nombre="Telcel recarga abierta", tipo=TipoProducto.TIEMPO_AIRE,
            tipo_monto=TipoMonto.RANGO, monto_min=D("10"), monto_max=D("500"),
        )
        ProductoProveedor.objects.create(
            producto=abierto, proveedor=self.emida, product_id="TELOPEN"
        )

        self.assertEqual(servicios.validar_monto(abierto, D("250")), D("250.0000"))
        with self.assertRaises(servicios.MontoInvalido):
            servicios.validar_monto(abierto, D("5"))
        with self.assertRaises(servicios.MontoInvalido):
            servicios.validar_monto(abierto, D("501"))
        with self.assertRaises(servicios.MontoInvalido):
            servicios.validar_monto(abierto)  # sin capturar nada

    def test_el_multiplo_se_respeta(self):
        abierto = ProductoLogico.objects.create(
            inquilino=self.inq, carrier=self.telcel, sku="TEL-DIEZ",
            nombre="Telcel de diez en diez", tipo=TipoProducto.TIEMPO_AIRE,
            tipo_monto=TipoMonto.RANGO, monto_min=D("10"), monto_max=D("500"),
            multiplo=D("10"),
        )
        self.assertEqual(servicios.validar_monto(abierto, D("120")), D("120.0000"))
        with self.assertRaises(servicios.MontoInvalido):
            servicios.validar_monto(abierto, D("125"))

    def test_el_servicio_cobra_lo_que_diga_el_recibo(self):
        cfe = Carrier.objects.create(
            inquilino=self.inq, clave="cfe", nombre="CFE", tipo=TipoProducto.SERVICIO
        )
        luz = ProductoLogico.objects.create(
            inquilino=self.inq, carrier=cfe, sku="CFE-LUZ",
            nombre="CFE Luz", tipo=TipoProducto.SERVICIO, tipo_monto=TipoMonto.RECIBO,
        )

        self.assertEqual(servicios.validar_monto(luz, D("743.50")), D("743.5000"))
        with self.assertRaises(servicios.MontoInvalido):
            servicios.validar_monto(luz)  # falta consultar el recibo

    def test_un_producto_mal_dado_de_alta_no_pasa_la_validacion(self):
        """Lo que rompería la venta meses después se detiene en el alta."""
        malo = ProductoLogico(
            inquilino=self.inq, carrier=self.telcel, sku="MAL",
            nombre="Rango sin rango", tipo=TipoProducto.TIEMPO_AIRE,
            tipo_monto=TipoMonto.RANGO,
        )
        with self.assertRaises(ValidationError):
            malo.full_clean()

        confuso = ProductoLogico(
            inquilino=self.inq, carrier=self.telcel, sku="CONFUSO",
            nombre="Fijo con rango", tipo=TipoProducto.TIEMPO_AIRE,
            tipo_monto=TipoMonto.FIJO, monto_fijo=D("100"), monto_min=D("10"),
        )
        with self.assertRaises(ValidationError):
            confuso.full_clean()


class CodigoDeBarras(Base):
    def test_el_codigo_es_el_sku_y_no_lleva_el_precio(self):
        self.assertEqual(self.cien.codigo_barras, "TEL-TAE-100")
        self.assertNotIn("100.00", self.cien.codigo_barras)

    def test_escanear_encuentra_el_producto(self):
        encontrado = servicios.por_codigo_barras(self.inq, "tel-tae-100")
        self.assertEqual(encontrado, self.cien)

    def test_la_etiqueta_sigue_sirviendo_si_cambia_el_precio(self):
        """
        Esta es la razón de que el precio no viva en el código. Si viviera,
        subir un producto obligaría a reimprimir cada etiqueta, y una vieja
        olvidada en un estante vendería al precio anterior para siempre.
        """
        codigo = self.cien.codigo_barras

        self.cien.monto_fijo = D("150")
        self.cien.save()

        encontrado = servicios.por_codigo_barras(self.inq, codigo)
        self.assertEqual(encontrado, self.cien)
        self.assertEqual(servicios.validar_monto(encontrado), D("150.0000"))

    def test_un_codigo_desconocido_lo_dice(self):
        with self.assertRaises(servicios.ProductoNoDisponible):
            servicios.por_codigo_barras(self.inq, "NO-EXISTE")

    def test_no_se_puede_repetir_un_sku(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            ProductoLogico.objects.create(
                inquilino=self.inq, carrier=self.telcel, sku="TEL-TAE-100",
                nombre="Duplicado", tipo=TipoProducto.TIEMPO_AIRE,
                tipo_monto=TipoMonto.FIJO, monto_fijo=D("100"),
            )


class LoQueLaCajaOfrece(Base):
    def test_vendibles_filtra_por_tipo(self):
        cfe = Carrier.objects.create(
            inquilino=self.inq, clave="cfe", nombre="CFE", tipo=TipoProducto.SERVICIO
        )
        luz = ProductoLogico.objects.create(
            inquilino=self.inq, carrier=cfe, sku="CFE-LUZ", nombre="CFE Luz",
            tipo=TipoProducto.SERVICIO, tipo_monto=TipoMonto.RECIBO,
        )
        ProductoProveedor.objects.create(
            producto=luz, proveedor=self.emida, product_id="CFE"
        )

        self.assertEqual(servicios.vendibles(self.inq).count(), 2)
        self.assertEqual(
            list(servicios.vendibles(self.inq, TipoProducto.SERVICIO)), [luz]
        )
