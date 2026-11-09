"""
Concurrencia. Estas son las pruebas que justifican el diseño.

Corren sobre MySQL/MariaDB con InnoDB y bloqueo de fila real, con hilos de
verdad, no simulados. Sobre SQLite pasarían siempre y no probarían nada:
SQLite serializa las escrituras, así que el sobregiro que buscamos no puede
ocurrir ahí aunque el código estuviera mal.

Por eso son TransactionTestCase y no TestCase: necesitan que cada hilo vea
lo que los otros hilos confirmaron.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.core.models import EstadoPuntoVenta, ModoBolsa, PuntoVenta
from apps.saldo import services
from apps.saldo.models import Bolsa, Operacion

from .factories import escenario

D = Decimal


def en_hilo(funcion):
    """
    Corre algo en un hilo y cierra su conexión al terminar.

    Sin el cierre, cada hilo deja una conexión abierta y la prueba se cuelga
    al destruir la base.
    """

    def envuelto(*args, **kwargs):
        try:
            return funcion(*args, **kwargs)
        finally:
            connections.close_all()

    return envuelto


class VentasSimultaneas(TransactionTestCase):
    """
    El caso que de verdad importa: varias cajas del mismo cliente vendiendo
    al mismo tiempo cuando el saldo alcanza para unas cuantas ventas.
    """

    def test_no_hay_sobregiro_con_veinticinco_cajas_a_la_vez(self):
        _, admin, cliente, pdv = escenario(modo=ModoBolsa.COMPARTIDA, margen="0.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))

        cajas = [pdv] + [
            PuntoVenta.objects.create(
                inquilino=cliente.inquilino, cliente=cliente,
                nombre=f"Caja {i}", estado=EstadoPuntoVenta.ACTIVO,
            )
            for i in range(24)
        ]

        @en_hilo
        def vender(caja):
            try:
                services.capturar(services.reservar(caja, D("100")))
                return True
            except services.SaldoInsuficiente:
                return False

        with ThreadPoolExecutor(max_workers=25) as pool:
            resultados = list(pool.map(vender, cajas))

        bolsa = services.bolsa_de(cliente)
        # Alcanzaba para exactamente diez ventas de cien.
        self.assertEqual(sum(resultados), 10)
        self.assertEqual(bolsa.saldo, D("0.0000"))
        self.assertGreaterEqual(bolsa.saldo, D("0"))
        self.assertEqual(bolsa.reservado, D("0.0000"))

        ok, problemas = services.cuadra(cliente.inquilino)
        self.assertTrue(ok, problemas)

    def test_el_limite_de_una_caja_aguanta_la_concurrencia(self):
        _, admin, cliente, pdv = escenario(margen="0.00")
        services.traspasar_a_cliente(admin, cliente, D("1000"))
        services.asignar_limite(pdv, D("300"))

        @en_hilo
        def vender(_):
            try:
                services.capturar(services.reservar(pdv, D("100")))
                return True
            except services.SaldoInsuficiente:
                return False

        with ThreadPoolExecutor(max_workers=12) as pool:
            resultados = list(pool.map(vender, range(12)))

        # El límite era 300: tres ventas, ni una más, aunque la bolsa del
        # cliente tenía 1,000.
        self.assertEqual(sum(resultados), 3)
        self.assertEqual(services.bolsa_de(cliente).saldo, D("700.0000"))

        ok, problemas = services.cuadra(cliente.inquilino)
        self.assertTrue(ok, problemas)

    def test_ninguna_bolsa_queda_negativa_bajo_carga_mixta(self):
        """
        Ventas, devoluciones y ajustes al mismo tiempo sobre las mismas
        bolsas. Lo que se busca aquí no es un número exacto sino que nada
        quede en negativo y que el ledger cuadre al final.
        """
        _, admin, cliente, pdv = escenario(modo=ModoBolsa.COMPARTIDA, margen="4.00")
        services.traspasar_a_cliente(admin, cliente, D("5000"))

        cajas = [pdv] + [
            PuntoVenta.objects.create(
                inquilino=cliente.inquilino, cliente=cliente,
                nombre=f"Caja {i}", estado=EstadoPuntoVenta.ACTIVO,
            )
            for i in range(9)
        ]

        @en_hilo
        def trabajo(i):
            try:
                if i % 5 == 0:
                    services.devolver(
                        cliente, D("104"), motivo="solicitada", autorizado_por="prueba"
                    )
                elif i % 7 == 0:
                    services.ajustar(cliente, D("10"), motivo="captura")
                else:
                    services.capturar(services.reservar(cajas[i % 10], D("50")))
                return "ok"
            except services.ErrorSaldo as e:
                return type(e).__name__

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(trabajo, range(60)))

        for bolsa in Bolsa.objects.filter(inquilino=cliente.inquilino):
            if bolsa.es_sistema:
                continue  # las cuentas de contrapartida sí van en negativo
            self.assertGreaterEqual(
                bolsa.saldo, D("0"), f"{bolsa.etiqueta} quedó en {bolsa.saldo}"
            )
            self.assertGreaterEqual(bolsa.reservado, D("0"))

        ok, problemas = services.cuadra(cliente.inquilino)
        self.assertTrue(ok, problemas)


class FoliosBajoConcurrencia(TransactionTestCase):
    def test_treinta_traspasos_simultaneos_dan_treinta_folios_distintos(self):
        _, admin, cliente, _ = escenario(margen="0.00")

        @en_hilo
        def traspasar(_):
            return services.traspasar_a_cliente(admin, cliente, D("10")).folio

        with ThreadPoolExecutor(max_workers=15) as pool:
            folios = list(pool.map(traspasar, range(30)))

        self.assertEqual(len(folios), 30)
        self.assertEqual(len(set(folios)), 30, "se repitió un folio")
        self.assertEqual(
            Operacion.objects.filter(tipo="TRS").count(), 30
        )


class BloqueoDeFilaDeVerdad(TransactionTestCase):
    """
    Si esta prueba corriera sobre un motor sin bloqueo de fila, pasaría por
    accidente. Aquí se confirma que el motor es el que creemos.
    """

    def test_las_tablas_de_saldo_estan_en_innodb(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'saldo%%'"
            )
            motores = dict(cur.fetchall())

        self.assertTrue(motores, "no se encontraron las tablas de saldo")
        for tabla, motor in motores.items():
            self.assertEqual(motor, "InnoDB", f"{tabla} está en {motor}")
