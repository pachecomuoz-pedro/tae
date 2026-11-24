"""La familia agrupa los productos de un carrier en la pantalla de venta."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.catalogo.servicios import familia_de


def p(nombre):
    return SimpleNamespace(nombre=nombre)


class FamiliaDelProducto(SimpleTestCase):
    def test_tiempo_aire_es_lo_que_no_es_paquete_ni_internet(self):
        self.assertEqual(familia_de(p("Telcel Tiempo Aire $100")), "Tiempo aire")
        self.assertEqual(familia_de(p("Recarga Movistar $50")), "Tiempo aire")

    def test_paquetes(self):
        self.assertEqual(familia_de(p("Telcel Paquete Amigo Sin Límite 200")), "Paquetes")
        self.assertEqual(familia_de(p("AT&T Paquete $150")), "Paquetes")

    def test_internet_gana_sobre_paquete(self):
        self.assertEqual(familia_de(p("Telcel Internet Amigo $50")), "Internet")
        self.assertEqual(familia_de(p("Paquete de datos Bait 100")), "Internet")

    def test_acentos_y_mayusculas_no_importan(self):
        self.assertEqual(familia_de(p("PAQUETE SIN LIMITE")), "Paquetes")

    def test_sin_nombre(self):
        self.assertEqual(familia_de(p(None)), "Tiempo aire")

    def test_recarga_amigo_de_telcel_es_tiempo_aire(self):
        self.assertEqual(familia_de(p("Telcel Recarga Amigo $100")), "Tiempo aire")
