"""
Las reglas de negocio, una prueba por regla.

Cada prueba corresponde a una decisión que quedó escrita en el documento de
alcance. Si alguna de estas falla, el documento y el código dejaron de decir
lo mismo.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import EstadoPuntoVenta, ModoBolsa, PuntoVenta
from apps.saldo import services
from apps.saldo.models import (
    BolsaPuntoVenta,
    ClaveSistema,
    EstadoReserva,
    Motivo,
    TipoOperacion,
)

from .factories import escenario

D = Decimal


class MargenDesdeLaCompra(TestCase):
    def test_el_cliente_paga_mil_y_recibe_mil_cuarenta(self):
        _, admin, cliente, _ = escenario(margen="4.00")

        operacion = services.traspasar_a_cliente(admin, cliente, D("1000"))

        self.assertEqual(services.bolsa_de(cliente).saldo, D("1040.0000"))
        self.assertTrue(operacion.folio.startswith("TRS-"))

    def test_la_bonificacion_sale_de_su_cuenta_y_no_del_administrador(self):
        inquilino, admin, cliente, _ = escenario(margen="4.00")
        antes = services.bolsa_de(admin).saldo

        services.traspasar_a_cliente(admin, cliente, D("1000"))

        # El administrador pone 1,000 exactos, no 1,040.
        self.assertEqual(services.bolsa_de(admin).saldo, antes - D("1000"))
        self.assertEqual(
            services.bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN).saldo,
            D("-40.0000"),
        )

    def test_los_tres_asientos_cuelgan_de_un_solo_folio(self):
        _, admin, cliente, _ = escenario(margen="4.00")

        operacion = services.traspasar_a_cliente(admin, cliente, D("1000"))

        self.assertEqual(operacion.asientos.count(), 3)
        self.assertEqual(sum(a.monto for a in operacion.asientos.all()), D("0"))


class SumaDeLimites(TestCase):
    def test_no_puede_exceder_la_bolsa_del_cliente(self):
        _, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))  # bolsa: 1,040

        otra = PuntoVenta.objects.create(
            inquilino=cliente.inquilino, cliente=cliente,
            nombre="Caja Fondo", estado=EstadoPuntoVenta.ACTIVO,
        )
        services.asignar_limite(pdv, D("800"))

        with self.assertRaises(services.LimiteExcedido):
            services.asignar_limite(otra, D("300"))  # 800 + 300 > 1,040

        services.asignar_limite(otra, D("240"))  # 800 + 240 = 1,040 exacto
        self.assertEqual(services.limites_de(cliente), D("1040.0000"))

    def test_asignar_no_mueve_dinero(self):
        _, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        antes = services.bolsa_de(cliente).saldo

        services.asignar_limite(pdv, D("500"))

        self.assertEqual(services.bolsa_de(cliente).saldo, antes)

    def test_en_bolsa_compartida_no_hay_limite(self):
        _, admin, cliente, pdv = escenario(modo=ModoBolsa.COMPARTIDA)
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        with self.assertRaises(services.OperacionNoPermitida):
            services.asignar_limite(pdv, D("500"))


class CicloDeVenta(TestCase):
    def test_reservar_aparta_pero_no_gasta(self):
        _, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("500"))

        reserva = services.reservar(pdv, D("100"))
        bolsa = services.bolsa_de(cliente)

        self.assertEqual(bolsa.saldo, D("1040.0000"))      # nada salió
        self.assertEqual(bolsa.reservado, D("100.0000"))   # pero está apartado
        self.assertEqual(bolsa.disponible, D("940.0000"))
        self.assertEqual(reserva.operacion.asientos.count(), 0)

    def test_capturar_convierte_la_reserva_en_cargo(self):
        inquilino, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("500"))

        reserva = services.capturar(services.reservar(pdv, D("100")))
        bolsa = services.bolsa_de(cliente)

        self.assertEqual(reserva.estado, EstadoReserva.CAPTURADA)
        self.assertEqual(bolsa.saldo, D("940.0000"))
        self.assertEqual(bolsa.reservado, D("0.0000"))
        self.assertEqual(
            services.bolsa_sistema(inquilino, ClaveSistema.COSTO_VENTA).saldo,
            D("100.0000"),
        )
        # El límite de la caja baja con la venta.
        self.assertEqual(BolsaPuntoVenta.objects.get(punto_venta=pdv).limite, D("400.0000"))

    def test_liberar_devuelve_el_disponible_sin_asientos(self):
        _, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("500"))

        reserva = services.liberar(services.reservar(pdv, D("100")))
        bolsa = services.bolsa_de(cliente)

        self.assertEqual(reserva.estado, EstadoReserva.LIBERADA)
        self.assertEqual(bolsa.saldo, D("1040.0000"))
        self.assertEqual(bolsa.reservado, D("0.0000"))
        self.assertEqual(reserva.operacion.asientos.count(), 0)
        self.assertEqual(BolsaPuntoVenta.objects.get(punto_venta=pdv).limite, D("500.0000"))

    def test_sin_resolver_deja_la_reserva_activa(self):
        _, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("500"))

        reserva = services.reservar(pdv, D("100"))
        # No se captura ni se libera: el proveedor no contestó.

        self.assertEqual(reserva.estado, EstadoReserva.ACTIVA)
        self.assertEqual(services.bolsa_de(cliente).reservado, D("100.0000"))
        # Y ese dinero no se puede retirar.
        self.assertEqual(services.disponible_para_retirar(cliente), D("440.0000"))

    def test_la_caja_no_vende_mas_alla_de_su_limite(self):
        _, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("150"))

        services.capturar(services.reservar(pdv, D("100")))
        with self.assertRaises(services.SaldoInsuficiente):
            services.reservar(pdv, D("100"))

    def test_una_caja_suspendida_no_vende(self):
        _, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("500"))
        services.suspender_cliente(cliente)

        with self.assertRaises(services.OperacionNoPermitida):
            services.reservar(pdv, D("100"))


class Devolucion(TestCase):
    def test_revierte_el_margen_en_proporcion(self):
        inquilino, admin, cliente, _ = escenario(margen="4.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        saldo_admin = services.bolsa_de(admin).saldo

        operacion = services.devolver(
            cliente, D("1040"), motivo=Motivo.CLIENTE_EQUIVOCADO, autorizado_por="Brenda"
        )

        self.assertEqual(services.bolsa_de(cliente).saldo, D("0.0000"))
        self.assertEqual(services.bolsa_de(admin).saldo, saldo_admin + D("1000"))
        self.assertEqual(
            services.bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN).saldo,
            D("0.0000"),
        )
        self.assertTrue(operacion.folio.startswith("DEV-"))

    def test_parcial_revierte_la_mitad_de_cada_cosa(self):
        _, admin, cliente, _ = escenario(margen="4.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        saldo_admin = services.bolsa_de(admin).saldo

        services.devolver(cliente, D("520"), motivo=Motivo.SOLICITADA)

        self.assertEqual(services.bolsa_de(cliente).saldo, D("520.0000"))
        self.assertEqual(services.bolsa_de(admin).saldo, saldo_admin + D("500"))

    def test_no_puede_tomar_saldo_comprometido_en_limites(self):
        _, admin, cliente, pdv = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("900"))

        # Bolsa 1,040, límites 900 → retirable 140.
        self.assertEqual(services.disponible_para_retirar(cliente), D("140.0000"))
        with self.assertRaises(services.SaldoInsuficiente):
            services.devolver(cliente, D("200"), motivo=Motivo.SOLICITADA)

    def test_exige_motivo(self):
        _, admin, cliente, _ = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        with self.assertRaises(services.MotivoRequerido):
            services.devolver(cliente, D("100"), motivo="")


class Ajuste(TestCase):
    def test_en_contra_retira_la_diferencia_exacta_sin_tocar_margen(self):
        inquilino, admin, cliente, _ = escenario(margen="4.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))  # acreditó 1,040
        saldo_admin = services.bolsa_de(admin).saldo

        # Debieron ser 1,040 y alguien aplicó 1,060: sobran 20.
        operacion = services.ajustar(
            cliente, D("20"), motivo=Motivo.CAPTURA, autorizado_por="Brenda"
        )

        self.assertEqual(services.bolsa_de(cliente).saldo, D("1020.0000"))
        # El administrador no se entera: el error estuvo en la pata del margen.
        self.assertEqual(services.bolsa_de(admin).saldo, saldo_admin)
        self.assertEqual(
            services.bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN).saldo,
            D("-20.0000"),
        )
        self.assertTrue(operacion.folio.startswith("AJU-"))

    def test_a_favor_debita_al_administrador(self):
        _, admin, cliente, _ = escenario(margen="4.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        saldo_admin = services.bolsa_de(admin).saldo

        services.ajustar(cliente, D("20"), motivo=Motivo.CAPTURA, a_favor=True)

        self.assertEqual(services.bolsa_de(cliente).saldo, D("1060.0000"))
        self.assertEqual(services.bolsa_de(admin).saldo, saldo_admin - D("20"))

    def test_a_favor_tiene_tope_por_operacion(self):
        _, admin, cliente, _ = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        with self.assertRaises(services.LimiteExcedido):
            services.ajustar(
                cliente, D("999999"), motivo=Motivo.CAPTURA, a_favor=True
            )

    def test_el_motivo_otro_exige_nota(self):
        _, admin, cliente, _ = escenario()
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        with self.assertRaises(services.MotivoRequerido):
            services.ajustar(cliente, D("20"), motivo=Motivo.OTRO, nota="   ")

        services.ajustar(cliente, D("20"), motivo=Motivo.OTRO, nota="Caso 4821")


class SuspensionEnCascada(TestCase):
    def test_alcanza_a_todas_las_cajas(self):
        _, _, cliente, pdv = escenario()
        otra = PuntoVenta.objects.create(
            inquilino=cliente.inquilino, cliente=cliente,
            nombre="Caja Fondo", estado=EstadoPuntoVenta.ACTIVO,
        )

        services.suspender_cliente(cliente)

        for caja in (pdv, otra):
            caja.refresh_from_db()
            self.assertEqual(caja.estado, EstadoPuntoVenta.SUSPENDIDO)

    def test_al_reactivar_cada_caja_vuelve_a_donde_estaba(self):
        _, _, cliente, pdv = escenario()
        pendiente = PuntoVenta.objects.create(
            inquilino=cliente.inquilino, cliente=cliente,
            nombre="Caja Móvil", estado=EstadoPuntoVenta.PENDIENTE,
        )
        manual = PuntoVenta.objects.create(
            inquilino=cliente.inquilino, cliente=cliente,
            nombre="Caja Bodega", estado=EstadoPuntoVenta.SUSPENDIDO,
        )

        services.suspender_cliente(cliente)
        services.reactivar_cliente(cliente)

        pdv.refresh_from_db()
        pendiente.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(pdv.estado, EstadoPuntoVenta.ACTIVO)
        self.assertEqual(pendiente.estado, EstadoPuntoVenta.PENDIENTE)
        # La que ya estaba suspendida a mano no revive.
        self.assertEqual(manual.estado, EstadoPuntoVenta.SUSPENDIDO)


class Folios(TestCase):
    def test_corren_por_tipo_y_por_anio(self):
        _, admin, cliente, pdv = escenario()
        services.asignar_limite  # referencia explícita, no se usa aquí

        uno = services.traspasar_a_cliente(admin, cliente, D("100"))
        dos = services.traspasar_a_cliente(admin, cliente, D("100"))
        devolucion = services.devolver(cliente, D("104"), motivo=Motivo.SOLICITADA)

        self.assertEqual(uno.folio[-6:], "000001")
        self.assertEqual(dos.folio[-6:], "000002")
        # La serie de devoluciones es independiente de la de traspasos.
        self.assertEqual(devolucion.folio[-6:], "000001")
        self.assertEqual(devolucion.tipo, TipoOperacion.DEVOLUCION)
