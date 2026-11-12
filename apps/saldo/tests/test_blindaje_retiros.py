"""
Retiros a prueba de balas.

Cada prueba ataca una forma concreta en que un retiro podría sacar dinero
que no existe o sacarlo dos veces:

- **Simultaneidad real.** Hilos de verdad contra MySQL pidiendo más de lo
  que hay al mismo tiempo. Solo deben pasar los que caben.
- **Repetición.** La misma petición dos veces (doble clic, red que
  reintenta), una tras otra y al mismo tiempo. Solo una mueve dinero.
- **La base como última defensa.** Aunque el código se equivocara, MySQL
  rechaza una bolsa de la red en negativo.
- **El margen correcto.** Devolver regresa lo que de verdad se pagó, aunque
  el margen haya cambiado después.
- **Lo que es de quién.** A un administrador solo se le devuelve lo que él
  traspasó; lo que abonó EPS no es suyo.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase

from apps.core.models import ModoBolsa
from apps.saldo import services
from apps.saldo.models import (
    Bolsa,
    BolsaPuntoVenta,
    ClaveSistema,
    Motivo,
    MovimientoLimite,
    Operacion,
    Retencion,
    TipoOperacion,
)

from .factories import escenario
from .test_concurrencia import en_hilo

D = Decimal
CAPTURA = Motivo.CAPTURA


def intentar(funcion):
    """Corre `funcion` y devuelve 'ok' o el nombre del error de negocio."""
    try:
        funcion()
        return "ok"
    except services.ErrorSaldo as e:
        return type(e).__name__


# ---------------------------------------------------------------------------
# Simultaneidad real
# ---------------------------------------------------------------------------
class RetirosSimultaneos(TransactionTestCase):
    def test_diez_devoluciones_a_la_vez_no_sacan_mas_de_lo_que_hay(self):
        inquilino, admin, cliente, _ = escenario(margen="0.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        @en_hilo
        def devolver(_):
            return intentar(lambda: services.devolver(cliente, D("300"), motivo=CAPTURA))

        with ThreadPoolExecutor(max_workers=10) as pool:
            resultados = list(pool.map(devolver, range(10)))

        self.assertEqual(resultados.count("ok"), 3)  # 3 × 300 = 900 ≤ 1000
        self.assertEqual(services.bolsa_de(cliente).saldo, D("100.0000"))
        self.assertEqual(services.bolsa_de(admin).saldo, D("900.0000"))
        self.assertEqual(services.cuadra(inquilino), (True, []))

    def test_diez_retiros_a_eps_a_la_vez(self):
        inquilino, admin, _, _ = escenario(fondeo="1000.00")

        @en_hilo
        def retirar(_):
            return intentar(lambda: services.retirar_a_eps(admin, D("250"), motivo=CAPTURA))

        with ThreadPoolExecutor(max_workers=10) as pool:
            resultados = list(pool.map(retirar, range(10)))

        self.assertEqual(resultados.count("ok"), 4)
        self.assertEqual(services.bolsa_de(admin).saldo, D("0.0000"))
        self.assertEqual(services.cuadra(inquilino), (True, []))

    def test_retiro_y_asignacion_de_limites_al_mismo_tiempo(self):
        """
        El cliente reparte su bolsa entre cajas mientras su administrador le
        retira saldo. Entre los dos nunca pueden comprometer más de lo que hay.
        """
        inquilino, admin, cliente, pdv = escenario(margen="0.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        @en_hilo
        def tarea(i):
            if i % 2:
                return intentar(lambda: services.devolver(cliente, D("200"), motivo=CAPTURA))
            return intentar(lambda: services.asignar_limite(pdv, D(str(100 * (i + 2)))))

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(tarea, range(10)))

        bolsa = Bolsa.objects.get(pk=services.bolsa_de(cliente).pk)
        limite = BolsaPuntoVenta.objects.get(punto_venta=pdv).limite
        self.assertLessEqual(limite, bolsa.saldo)
        self.assertGreaterEqual(bolsa.saldo, D("0"))
        self.assertEqual(services.cuadra(inquilino), (True, []))

    def test_la_misma_llave_al_mismo_tiempo_mueve_dinero_una_sola_vez(self):
        inquilino, admin, cliente, _ = escenario(margen="0.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        @en_hilo
        def doble_clic(_):
            try:
                services.devolver(cliente, D("100"), motivo=CAPTURA, clave="clic-1")
                return "ok"
            except services.OperacionRepetida:
                return "repetida"
            except IntegrityError:
                return "chocó"  # la base frenó a la segunda

        with ThreadPoolExecutor(max_workers=8) as pool:
            resultados = list(pool.map(doble_clic, range(8)))

        self.assertEqual(resultados.count("ok"), 1)
        self.assertEqual(Operacion.objects.filter(tipo=TipoOperacion.DEVOLUCION).count(), 1)
        self.assertEqual(services.bolsa_de(cliente).saldo, D("900.0000"))


# ---------------------------------------------------------------------------
# Repetición e idempotencia
# ---------------------------------------------------------------------------
class Idempotencia(TestCase):
    def test_la_misma_devolucion_dos_veces_se_aplica_una(self):
        _, admin, cliente, _ = escenario(margen="0.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        primera = services.devolver(cliente, D("100"), motivo=CAPTURA, clave="k1")
        with self.assertRaises(services.OperacionRepetida) as e:
            services.devolver(cliente, D("100"), motivo=CAPTURA, clave="k1")

        self.assertEqual(e.exception.operacion.pk, primera.pk)
        self.assertEqual(services.bolsa_de(cliente).saldo, D("900.0000"))

    def test_llaves_distintas_son_operaciones_distintas(self):
        _, admin, cliente, _ = escenario(margen="0.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.devolver(cliente, D("100"), motivo=CAPTURA, clave="k1")
        services.devolver(cliente, D("100"), motivo=CAPTURA, clave="k2")
        self.assertEqual(services.bolsa_de(cliente).saldo, D("800.0000"))

    def test_aplica_a_traspasos_fondeos_y_retiros_a_eps(self):
        _, admin, cliente, _ = escenario(margen="0.00", fondeo="1000.00")
        llamadas = [
            lambda: services.traspasar_a_cliente(admin, cliente, D("10"), clave="t"),
            lambda: services.fondear_administrador(admin, D("10"), clave="f"),
            lambda: services.fondear_cliente(cliente, D("10"), clave="fc"),
            lambda: services.retirar_a_eps(admin, D("10"), motivo=CAPTURA, clave="r"),
        ]
        for llamada in llamadas:
            llamada()
            with self.assertRaises(services.OperacionRepetida):
                llamada()

    def test_una_correccion_que_solo_retiene_tampoco_se_duplica(self):
        _, admin, cliente, pdv = escenario(margen="0.00", fondeo="110.00")
        services.traspasar_a_cliente(admin, cliente, D("110"))
        services.corregir_fondeo(admin, D("4"), clave="c1")
        with self.assertRaises(services.RetencionRepetida):
            services.corregir_fondeo(admin, D("4"), clave="c1")
        self.assertEqual(Retencion.objects.count(), 1)


# ---------------------------------------------------------------------------
# La base de datos como última defensa
# ---------------------------------------------------------------------------
class LaBaseNoAceptaNegativos(TestCase):
    def test_una_bolsa_de_la_red_no_puede_quedar_en_negativo(self):
        _, admin, _, _ = escenario(fondeo="100.00")
        bolsa = services.bolsa_de(admin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Bolsa.objects.filter(pk=bolsa.pk).update(saldo=D("-0.01"))

    def test_ni_con_mas_apartado_que_saldo(self):
        _, admin, _, _ = escenario(fondeo="100.00")
        bolsa = services.bolsa_de(admin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Bolsa.objects.filter(pk=bolsa.pk).update(reservado=D("100.01"))

    def test_las_cuentas_de_eps_si_van_en_negativo(self):
        """Son la contrapartida de lo que EPS pone en la red."""
        inquilino, _, _, _ = escenario(fondeo="100.00")
        fondeo = services.bolsa_sistema(inquilino, ClaveSistema.FONDEO)
        self.assertLess(fondeo.saldo, 0)

    def test_el_limite_de_una_caja_tampoco(self):
        _, admin, cliente, pdv = escenario(margen="0.00", fondeo="100.00")
        services.traspasar_a_cliente(admin, cliente, D("100"))
        fila = services.asignar_limite(pdv, D("50"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            BolsaPuntoVenta.objects.filter(pk=fila.pk).update(limite=D("-1"))


# ---------------------------------------------------------------------------
# El margen con el que se otorgó
# ---------------------------------------------------------------------------
class MargenCorrecto(TestCase):
    def test_devolver_usa_el_margen_que_se_otorgo_no_el_actual(self):
        inquilino, admin, cliente, _ = escenario(margen="4.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))  # recibe 1,040
        cliente.margen_pct = D("10.00")
        cliente.save()

        services.devolver(cliente, D("1040"), motivo=CAPTURA)

        # El administrador recupera exactamente lo que pagó, ni un peso más.
        self.assertEqual(services.bolsa_de(admin).saldo, D("1000.0000"))
        self.assertEqual(services.bolsa_de(cliente).saldo, D("0.0000"))
        self.assertEqual(services.cuadra(inquilino), (True, []))

    def test_con_varios_margenes_usa_el_promedio_de_lo_que_paso(self):
        _, admin, cliente, _ = escenario(margen="0.00", fondeo="2000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))  # 1,000 al 0%
        cliente.margen_pct = D("10.00")
        cliente.save()
        services.traspasar_a_cliente(admin, cliente, D("1000"))  # 1,100 al 10%

        services.devolver(cliente, D("2100"), motivo=CAPTURA)
        self.assertEqual(services.bolsa_de(admin).saldo, D("2000.0000"))

    def test_el_administrador_con_margen_recibe_su_bonificacion(self):
        inquilino, admin, _, _ = escenario(fondeo="0.01")
        admin.margen_pct = D("5.00")
        admin.save()

        services.fondear_administrador(admin, D("1000"))

        self.assertEqual(services.bolsa_de(admin).saldo, D("1050.0100"))
        bonif = services.bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN)
        self.assertEqual(bonif.saldo, D("-50.0000"))
        self.assertEqual(services.cuadra(inquilino), (True, []))

    def test_retirarle_todo_a_eps_regresa_la_bonificacion_a_su_cuenta(self):
        inquilino, admin, _, _ = escenario(fondeo="0.01")
        services.retirar_a_eps(admin, D("0.01"), motivo=CAPTURA)
        admin.margen_pct = D("5.00")
        admin.save()
        services.fondear_administrador(admin, D("1000"))

        services.retirar_a_eps(admin, D("1050"), motivo=CAPTURA)

        bonif = services.bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN)
        fondeo = services.bolsa_sistema(inquilino, ClaveSistema.FONDEO)
        self.assertEqual(bonif.saldo, D("0.0000"))
        self.assertEqual(fondeo.saldo, D("0.0000"))


# ---------------------------------------------------------------------------
# Lo que es de quién
# ---------------------------------------------------------------------------
class LoQueEsDeQuien(TestCase):
    def test_no_se_le_devuelve_al_administrador_lo_que_abono_eps(self):
        _, admin, cliente, _ = escenario(margen="0.00", fondeo="1000.00")
        services.fondear_cliente(cliente, D("500"))  # EPS directo

        with self.assertRaises(services.SaldoInsuficiente):
            services.devolver(cliente, D("1"), motivo=CAPTURA)
        services.retirar_a_eps(cliente, D("500"), motivo=CAPTURA)  # esto sí

    def test_solo_lo_neto_que_traspaso(self):
        _, admin, cliente, _ = escenario(margen="0.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("300"))
        services.fondear_cliente(cliente, D("500"))
        services.devolver(cliente, D("100"), motivo=CAPTURA)

        with self.assertRaises(services.SaldoInsuficiente):
            services.devolver(cliente, D("201"), motivo=CAPTURA)
        services.devolver(cliente, D("200"), motivo=CAPTURA)


# ---------------------------------------------------------------------------
# Retiro de saldo de una caja
# ---------------------------------------------------------------------------
class RetiroDeCaja(TestCase):
    def preparar(self):
        _, admin, cliente, pdv = escenario(margen="0.00", fondeo="1000.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("600"), autorizado_por="Jorge")
        return cliente, pdv

    def test_baja_el_limite_y_deja_rastro(self):
        cliente, pdv = self.preparar()

        services.retirar_de_caja(pdv, D("250"), motivo=CAPTURA, autorizado_por="Jorge")

        self.assertEqual(BolsaPuntoVenta.objects.get(punto_venta=pdv).limite, D("350.0000"))
        ultimo = MovimientoLimite.objects.filter(punto_venta=pdv).latest("pk")
        self.assertEqual((ultimo.anterior, ultimo.nuevo), (D("600.0000"), D("350.0000")))
        self.assertEqual(ultimo.autorizado_por, "Jorge")
        self.assertEqual(services.disponible_de(cliente), D("650.0000"))

    def test_no_toca_lo_apartado_por_una_venta_en_vuelo(self):
        _, pdv = self.preparar()
        services.reservar(pdv, D("500"))

        with self.assertRaises(services.SaldoInsuficiente):
            services.retirar_de_caja(pdv, D("101"), motivo=CAPTURA)
        services.retirar_de_caja(pdv, D("100"), motivo=CAPTURA)

    def test_exige_motivo_tambien_al_bajar_el_limite_directo(self):
        _, pdv = self.preparar()
        with self.assertRaises(services.MotivoRequerido):
            services.asignar_limite(pdv, D("100"))
        services.asignar_limite(pdv, D("900"))  # subir no lo exige

    def test_una_caja_en_bolsa_compartida_no_tiene_que_retirar(self):
        _, admin, cliente, pdv = escenario(modo=ModoBolsa.COMPARTIDA, fondeo="100.00")
        with self.assertRaises(services.OperacionNoPermitida):
            services.asignar_limite(pdv, D("10"))
