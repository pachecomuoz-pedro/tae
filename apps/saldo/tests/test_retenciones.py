"""
Retiro a EPS, retención de red y suspensión por nivel (decisión 27).

El caso que dio origen a todo esto está escrito tal cual en
`CasoDeLosCientoDiez`: se fondearon 110 a un administrador, debían ser 106,
y los 110 ya habían bajado hasta la caja.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import (
    Administrador,
    Cliente,
    EstadoCliente,
    EstadoPuntoVenta,
    ModoBolsa,
    PuntoVenta,
)
from apps.saldo import services
from apps.saldo.models import EstadoRetencion, Motivo, Retencion, TipoOperacion

from .factories import escenario

D = Decimal
FONDEO_EXCEDENTE = Motivo.FONDEO_EXCEDENTE


def vender(pdv, monto):
    """Una venta aprobada por el proveedor: reservar y capturar."""
    return services.capturar(services.reservar(pdv, D(monto)))


def red_de_ciento_diez():
    """
    Administrador fondeado con 110, todo traspasado a su cliente (sin
    margen) y todo asignado a la única caja.
    """
    inquilino, admin, cliente, pdv = escenario(margen="0.00", fondeo="110.00")
    services.traspasar_a_cliente(admin, cliente, D("110"))
    services.asignar_limite(pdv, D("110"))
    return inquilino, admin, cliente, pdv


class RetiroAEPS(TestCase):
    def test_retira_del_administrador_hacia_la_cuenta_de_fondeo(self):
        inquilino, admin, _, _ = escenario(fondeo="110.00")

        operacion = services.retirar_a_eps(admin, D("4"), motivo=FONDEO_EXCEDENTE)

        self.assertEqual(services.bolsa_de(admin).saldo, D("106.0000"))
        self.assertTrue(operacion.folio.startswith("RET-"))
        self.assertEqual(operacion.tipo, TipoOperacion.RETIRO)
        self.assertEqual(services.cuadra(inquilino), (True, []))

    def test_no_retira_mas_de_lo_disponible(self):
        _, admin, _, _ = escenario(fondeo="110.00")
        with self.assertRaises(services.SaldoInsuficiente):
            services.retirar_a_eps(admin, D("111"), motivo=FONDEO_EXCEDENTE)

    def test_en_un_cliente_no_toca_lo_asignado_a_sus_cajas(self):
        _, admin, cliente, pdv = escenario(margen="0.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("700"))

        with self.assertRaises(services.SaldoInsuficiente):
            services.retirar_a_eps(cliente, D("301"), motivo=FONDEO_EXCEDENTE)
        services.retirar_a_eps(cliente, D("300"), motivo=FONDEO_EXCEDENTE)
        self.assertEqual(services.bolsa_de(cliente).saldo, D("700.0000"))

    def test_exige_motivo(self):
        _, admin, _, _ = escenario(fondeo="110.00")
        with self.assertRaises(services.MotivoRequerido):
            services.retirar_a_eps(admin, D("4"), motivo="")


class CorreccionSinRetencion(TestCase):
    def test_si_el_dinero_sigue_arriba_se_retira_directo(self):
        _, admin, _, _ = escenario(fondeo="110.00")

        retirado, operacion, retencion = services.corregir_fondeo(admin, D("4"))

        self.assertEqual(retirado, D("4.0000"))
        self.assertIsNotNone(operacion)
        self.assertIsNone(retencion)
        self.assertEqual(services.bolsa_de(admin).saldo, D("106.0000"))


class CasoDeLosCientoDiez(TestCase):
    """El ejemplo de la conversación, paso por paso."""

    def test_el_faltante_se_vuelve_retencion(self):
        _, admin, _, _ = red_de_ciento_diez()

        retirado, operacion, retencion = services.corregir_fondeo(admin, D("4"))

        self.assertEqual(retirado, D("0"))
        self.assertIsNone(operacion)  # no había nada que retirar arriba
        self.assertEqual(retencion.pendiente, D("4.0000"))
        self.assertEqual(services.retenido(admin), D("4.0000"))

    def test_la_red_vende_hasta_dejar_lo_retenido_y_ahi_se_detiene(self):
        _, admin, _, pdv = red_de_ciento_diez()
        services.corregir_fondeo(admin, D("4"))

        vender(pdv, "100")   # quedan 10
        vender(pdv, "6")     # quedan 4: justo lo retenido
        self.assertEqual(services.saldo_red(admin), D("4.0000"))

        with self.assertRaises(services.RedRetenida):
            vender(pdv, "1")

    def test_una_venta_que_cruzaria_el_piso_se_rechaza_aunque_haya_saldo(self):
        _, admin, _, pdv = red_de_ciento_diez()
        services.corregir_fondeo(admin, D("4"))
        vender(pdv, "100")   # quedan 10

        with self.assertRaises(services.RedRetenida):
            vender(pdv, "10")  # dejaría 0 y hay 4 retenidos
        vender(pdv, "6")       # esta sí: deja exactamente 4

    def test_el_siguiente_fondeo_cubre_la_retencion_y_libera_la_red(self):
        inquilino, admin, _, pdv = red_de_ciento_diez()
        _, _, retencion = services.corregir_fondeo(admin, D("4"))
        vender(pdv, "106")

        operacion = services.fondear_administrador(admin, D("50"))

        retencion.refresh_from_db()
        self.assertEqual(retencion.estado, EstadoRetencion.LIQUIDADA)
        self.assertEqual(retencion.pendiente, D("0.0000"))
        self.assertEqual(len(operacion.liquidaciones), 1)
        self.assertEqual(operacion.liquidaciones[0].retencion_id, retencion.pk)
        self.assertEqual(services.bolsa_de(admin).saldo, D("46.0000"))
        # Los 4 que estaban detenidos en la caja ya se pueden vender.
        vender(pdv, "4")
        self.assertEqual(services.cuadra(inquilino), (True, []))

    def test_un_fondeo_menor_liquida_solo_una_parte(self):
        _, admin, _, pdv = red_de_ciento_diez()
        _, _, retencion = services.corregir_fondeo(admin, D("4"))

        services.fondear_administrador(admin, D("1"))

        retencion.refresh_from_db()
        self.assertEqual(retencion.estado, EstadoRetencion.ACTIVA)
        self.assertEqual(retencion.pendiente, D("3.0000"))

    def test_si_la_red_ya_vendio_de_mas_se_bloquea_de_inmediato(self):
        _, admin, _, pdv = red_de_ciento_diez()
        vender(pdv, "108")  # solo quedan 2 cuando se detecta el error
        services.corregir_fondeo(admin, D("4"))

        with self.assertRaises(services.RedRetenida):
            vender(pdv, "1")
        estado = services.estado_de_venta(pdv)
        self.assertFalse(estado["puede"])
        self.assertIn("retenido", estado["motivo"])

    def test_cancelarla_devuelve_la_venta_a_la_normalidad(self):
        _, admin, _, pdv = red_de_ciento_diez()
        _, _, retencion = services.corregir_fondeo(admin, D("4"))
        vender(pdv, "106")

        services.cancelar_retencion(retencion, nota="Se negoció con el administrador")

        vender(pdv, "4")
        retencion.refresh_from_db()
        self.assertEqual(retencion.estado, EstadoRetencion.CANCELADA)

    def test_cancelar_exige_nota(self):
        _, admin, _, _ = red_de_ciento_diez()
        _, _, retencion = services.corregir_fondeo(admin, D("4"))
        with self.assertRaises(services.MotivoRequerido):
            services.cancelar_retencion(retencion, nota="  ")


class RetencionDeCliente(TestCase):
    def test_un_fondeo_directo_de_mas_a_un_cliente(self):
        inquilino, admin, cliente, pdv = escenario(margen="0.00", fondeo="1.00")
        services.fondear_cliente(cliente, D("110"))
        services.asignar_limite(pdv, D("110"))

        _, _, retencion = services.corregir_fondeo(cliente, D("4"))
        self.assertEqual(retencion.cliente_id, cliente.pk)

        vender(pdv, "106")
        with self.assertRaises(services.RedRetenida):
            vender(pdv, "1")

    def test_un_traspaso_de_su_administrador_tambien_la_liquida(self):
        inquilino, admin, cliente, pdv = escenario(margen="0.00", fondeo="1000.00")
        services.fondear_cliente(cliente, D("110"))
        services.asignar_limite(pdv, D("110"))
        _, _, retencion = services.corregir_fondeo(cliente, D("4"))

        operacion = services.traspasar_a_cliente(admin, cliente, D("10"))

        retencion.refresh_from_db()
        self.assertEqual(retencion.estado, EstadoRetencion.LIQUIDADA)
        self.assertEqual(len(operacion.liquidaciones), 1)
        self.assertEqual(services.cuadra(inquilino), (True, []))

    def test_la_retencion_de_un_cliente_no_frena_a_otro_cliente(self):
        _, admin, cliente, pdv = escenario(margen="0.00", fondeo="1000.00")
        otro = Cliente.objects.create(
            inquilino=cliente.inquilino, administrador=admin, nombre="Otro",
            modo_bolsa=ModoBolsa.COMPARTIDA, estado=EstadoCliente.APROBADO,
        )
        caja_otro = PuntoVenta.objects.create(
            inquilino=cliente.inquilino, cliente=otro, nombre="Caja",
            estado=EstadoPuntoVenta.ACTIVO,
        )
        services.fondear_cliente(cliente, D("10"))
        services.asignar_limite(pdv, D("10"))
        services.traspasar_a_cliente(admin, otro, D("50"))
        services.corregir_fondeo(cliente, D("10"))  # todo retenido

        vender(caja_otro, "50")  # el otro cliente vende completo


class SaldoDeRed(TestCase):
    def test_el_administrador_ve_su_bolsa_mas_la_de_sus_clientes(self):
        _, admin, cliente, pdv = escenario(margen="0.00", fondeo="110.00")
        services.traspasar_a_cliente(admin, cliente, D("60"))
        services.asignar_limite(pdv, D("60"))

        self.assertEqual(services.disponible_de(admin), D("50.0000"))
        self.assertEqual(services.saldo_red(admin), D("110.0000"))

        vender(pdv, "10")
        self.assertEqual(services.saldo_red(admin), D("100.0000"))
        self.assertEqual(services.saldo_red(cliente), D("50.0000"))
        # Lo asignado a la caja no es disponible del cliente.
        self.assertEqual(services.disponible_de(cliente), D("0.0000"))


class EstadoDeVenta(TestCase):
    def test_caja_sin_limite_en_bolsa_asignada(self):
        _, admin, cliente, pdv = escenario(margen="0.00", fondeo="100.00")
        services.traspasar_a_cliente(admin, cliente, D("100"))

        estado = services.estado_de_venta(pdv)

        self.assertFalse(estado["puede"])
        self.assertIn("no tiene saldo asignado", estado["motivo"])

    def test_en_bolsa_compartida_vende_de_la_bolsa_del_cliente(self):
        _, admin, cliente, pdv = escenario(
            margen="0.00", fondeo="100.00", modo=ModoBolsa.COMPARTIDA
        )
        services.traspasar_a_cliente(admin, cliente, D("100"))

        estado = services.estado_de_venta(pdv)

        self.assertTrue(estado["puede"])
        self.assertEqual(estado["disponible"], D("100.0000"))


class SuspensionPorNivel(TestCase):
    def test_suspender_al_administrador_detiene_a_toda_su_red(self):
        _, admin, cliente, pdv = red_de_ciento_diez()

        services.suspender_administrador(admin)
        pdv.refresh_from_db()

        with self.assertRaises(services.OperacionNoPermitida):
            vender(pdv, "10")
        self.assertFalse(services.estado_de_venta(pdv)["puede"])
        # Pero no se reescribió el estado de nadie de abajo.
        cliente.refresh_from_db()
        self.assertEqual(cliente.estado, EstadoCliente.APROBADO)
        self.assertEqual(pdv.estado, EstadoPuntoVenta.ACTIVO)

        services.reactivar_administrador(admin)
        vender(pdv, "10")

    def test_un_administrador_suspendido_no_traspasa(self):
        _, admin, cliente, _ = escenario(fondeo="100.00")
        services.suspender_administrador(admin)
        with self.assertRaises(services.OperacionNoPermitida):
            services.traspasar_a_cliente(Administrador.objects.get(pk=admin.pk), cliente, D("10"))

    def test_suspender_una_sola_caja(self):
        _, admin, cliente, pdv = red_de_ciento_diez()

        services.suspender_punto_venta(pdv)
        with self.assertRaises(services.OperacionNoPermitida):
            vender(pdv, "10")

        services.reactivar_punto_venta(pdv)
        vender(pdv, "10")

    def test_una_caja_suspendida_a_mano_no_revive_al_reactivar_al_cliente(self):
        _, admin, cliente, pdv = red_de_ciento_diez()
        services.suspender_punto_venta(pdv)
        services.suspender_cliente(cliente)
        services.reactivar_cliente(cliente)

        pdv.refresh_from_db()
        self.assertEqual(pdv.estado, EstadoPuntoVenta.SUSPENDIDO)

    def test_no_se_reactiva_una_caja_si_su_cliente_esta_suspendido(self):
        _, admin, cliente, pdv = red_de_ciento_diez()
        services.suspender_cliente(cliente)
        with self.assertRaises(services.OperacionNoPermitida):
            services.reactivar_punto_venta(pdv)


class RetiroConVentaEnVuelo(TestCase):
    def test_no_se_retira_lo_apartado_por_una_venta_sin_cerrar(self):
        _, admin, cliente, pdv = escenario(
            margen="0.00", fondeo="100.00", modo=ModoBolsa.COMPARTIDA
        )
        services.traspasar_a_cliente(admin, cliente, D("100"))
        services.reservar(pdv, D("30"))  # en vuelo

        with self.assertRaises(services.SaldoInsuficiente):
            services.retirar_a_eps(cliente, D("71"), motivo=FONDEO_EXCEDENTE)
        services.retirar_a_eps(cliente, D("70"), motivo=FONDEO_EXCEDENTE)

        retenciones = Retencion.objects.count()
        self.assertEqual(retenciones, 0)
