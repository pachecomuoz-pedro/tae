"""
El cierre de la venta contra el emulador.

Estas son las pruebas que corresponden a los escenarios TX-T y TX-LK de la
matriz de certificación. Cada una lleva el ID del caso.

Lo que se vigila en todas: que el emulador haya cobrado cero o una vez.
Nunca dos.
"""
from decimal import Decimal

from django.test import TestCase

from apps.identidad import acciones
from apps.identidad.tests.factories import Mundo
from apps.proveedor import venta as cierre_venta
from apps.proveedor.cliente import ClienteProveedor, Config, aprobada
from apps.proveedor.emulador import Emulador, emulador_de_certificacion
from apps.proveedor.modelos import Desenlace, RegistroEnvio
from apps.saldo import services
from apps.saldo.models import EstadoReserva

D = Decimal


def reloj_de(emulador):
    """
    En vez de dormir, adelanta el reloj del emulador. El protocolo vive los
    mismos segundos que viviría en producción y la prueba corre en
    milisegundos.
    """
    return emulador.avanzar


class Base(TestCase):
    def setUp(self):
        self.m = Mundo()
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("10000"))
        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("5000"))
        self.saldo_inicial = services.bolsa_de(self.m.cliente).saldo

    def cliente_con(self, emulador):
        cfg = Config(
            base_url="https://gw.ejemplo.com:10002",
            usuario="u", contrasena="x", site_id="9400822", clerk_id="CESAR45",
            # Los que indicó el proveedor: consultar a los 40 segundos del
            # envío y luego cada 10. Con tres intentos se alcanza hasta el
            # segundo 60, por encima del escenario más lento de la matriz.
            timeout_venta=30, timeout_lookup=15,
            lookup_desde_envio=40, espera_entre_lookups=10, max_lookups=3,
        )
        return ClienteProveedor(cfg, sesion=emulador)

    def vender(self, emulador):
        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)
        return cierre_venta.cerrar_venta(
            self.cliente_con(emulador), reserva,
            account_id="9991234567", product_id="TEL100",
            dormir=reloj_de(emulador),
        )


class CriterioDeAprobacion(TestCase):
    """
    El criterio que confirmó el proveedor: "siempre será el '00' y/o '0',
    sea TAE o PS, y el lookup responde con el mismo comportamiento".

    Estas pruebas antes decían otra cosa, porque la matriz de certificación
    sugería un criterio distinto por método. Se reescribieron cuando llegó
    la respuesta: documentaban una suposición, no un hecho.
    """

    def test_los_dos_codigos_aprueban_en_cualquier_metodo(self):
        for metodo in ("topup", "bill_payment", "lookup"):
            for codigo in ("0", "00"):
                self.assertTrue(
                    aprobada(metodo, {"responseCode": codigo}),
                    f"{metodo} con responseCode {codigo} debería aprobar",
                )

    def test_cualquier_otro_codigo_rechaza(self):
        for codigo in ("51", "1", "000", "", "ok"):
            self.assertFalse(aprobada("topup", {"responseCode": codigo}))

    def test_un_campo_ausente_nunca_cuenta_como_aprobacion(self):
        """La ausencia de la confirmación no es una confirmación."""
        self.assertFalse(aprobada("topup", {}))
        self.assertFalse(aprobada("topup", {"responseCode": None}))

    def test_si_viene_h2h_tiene_que_venir_en_cero(self):
        """
        No lo mencionaron en la respuesta, pero la matriz sí lo exige en
        PS-13. Lectura prudente: si lo mandan, cuenta; si no lo mandan, no
        se inventa un requisito que ellos no pidieron.
        """
        self.assertTrue(
            aprobada("bill_payment", {"responseCode": "00", "h2hResultCode": "0"})
        )
        self.assertTrue(aprobada("bill_payment", {"responseCode": "00"}))
        self.assertFalse(
            aprobada("bill_payment", {"responseCode": "00", "h2hResultCode": "7"})
        )

    def test_un_metodo_desconocido_nunca_aprueba(self):
        self.assertFalse(aprobada("inventado", {"responseCode": "0"}))


class VentaDirecta(Base):
    def test_tx_t_04_topup_exitoso_cobra_una_vez(self):
        emulador = Emulador(responde_en=0.2)
        resultado = self.vender(emulador)

        self.assertTrue(resultado.cobrada)
        self.assertEqual(emulador.cobros(), 1)
        self.assertEqual(
            services.bolsa_de(self.m.cliente).saldo, self.saldo_inicial - D("100")
        )
        self.assertEqual(services.bolsa_de(self.m.cliente).reservado, D("0.0000"))

        envio = RegistroEnvio.objects.get()
        self.assertEqual(envio.desenlace, Desenlace.APROBADA)
        self.assertTrue(envio.control_no)

    def test_un_rechazo_explicito_libera_el_saldo(self):
        emulador = Emulador(aprobar=False, response_code="51")
        resultado = self.vender(emulador)

        self.assertEqual(resultado.desenlace, Desenlace.RECHAZADA)
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, self.saldo_inicial)
        self.assertEqual(services.bolsa_de(self.m.cliente).reservado, D("0.0000"))
        self.assertEqual(emulador.cobros(), 0)

    def test_tx_lk_05_un_http_400_tambien_libera(self):
        """Fondos insuficientes del lado del proveedor: la venta no ocurrió."""
        emulador = Emulador(rechazar_http=400)
        resultado = self.vender(emulador)

        self.assertEqual(resultado.desenlace, Desenlace.RECHAZADA)
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, self.saldo_inicial)

    def test_val_t_09_un_422_es_rechazo_de_negocio(self):
        emulador = Emulador(rechazar_http=422)
        resultado = self.vender(emulador)

        self.assertEqual(resultado.desenlace, Desenlace.RECHAZADA)
        self.assertEqual(services.bolsa_de(self.m.cliente).reservado, D("0.0000"))


class EsquemaDeReintentos(Base):
    """Los cuatro escenarios de la matriz, con sus IDs."""

    def test_tx_lk_07_tarda_25s_y_cierra_sin_consultar(self):
        """
        Con el timeout en 30 segundos, una respuesta a los 25 llega sola.
        Este escenario dejó de necesitar consulta, que es mejor: una llamada
        menos al proveedor por cada venta lenta.
        """
        emulador = emulador_de_certificacion("TX-LK-07")
        resultado = self.vender(emulador)

        self.assertTrue(resultado.cobrada)
        self.assertEqual(emulador.cobros(), 1)
        self.assertEqual(len(emulador.topups), 1)
        self.assertEqual(len(emulador.lookups), 0)

    def test_tx_lk_08_tarda_38s_y_hace_falta_insistir(self):
        emulador = emulador_de_certificacion("TX-LK-08")
        resultado = self.vender(emulador)

        self.assertTrue(resultado.cobrada)
        self.assertEqual(emulador.cobros(), 1)
        self.assertEqual(len(emulador.topups), 1)

    def test_tx_lk_09_tarda_55s(self):
        emulador = emulador_de_certificacion("TX-LK-09")
        resultado = self.vender(emulador)

        self.assertEqual(emulador.cobros(), 1)
        self.assertEqual(len(emulador.topups), 1)

    def test_tx_lk_10_no_responde_nunca_y_queda_sin_resolver(self):
        """
        El caso importante. La venta pudo haber ocurrido, así que no se
        libera ni se cobra: la reserva se queda viva.
        """
        emulador = emulador_de_certificacion("TX-LK-10")
        resultado = self.vender(emulador)

        self.assertTrue(resultado.sin_resolver)
        self.assertEqual(resultado.reserva.estado, EstadoReserva.ACTIVA)

        bolsa = services.bolsa_de(self.m.cliente)
        self.assertEqual(bolsa.saldo, self.saldo_inicial)      # no se cobró
        self.assertEqual(bolsa.reservado, D("100.0000"))       # pero sigue apartado
        # Y por lo tanto ese dinero no se puede retirar.
        self.assertEqual(
            services.disponible_para_retirar(self.m.cliente),
            self.saldo_inicial - D("5000") - D("100"),
        )
        self.assertEqual(len(emulador.lookups), 3)

    def test_el_lookup_404_significa_que_nunca_entro(self):
        """Si el proveedor no la tiene, liberar es seguro."""
        emulador = Emulador(nunca_responde=False, responde_en=99)
        emulador.transacciones.clear()

        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)

        class SoloTimeoutYLuego404(Emulador):
            def _topup(self, headers, cuerpo, timeout):
                import requests as _r
                raise _r.Timeout("no contesta el envío")

        emulador_404 = SoloTimeoutYLuego404()

        resultado = cierre_venta.cerrar_venta(
            self.cliente_con(emulador_404), reserva,
            account_id="9991234567", product_id="TEL100",
            dormir=emulador_404.avanzar,
        )

        self.assertEqual(resultado.desenlace, Desenlace.RECHAZADA)
        self.assertEqual(services.bolsa_de(self.m.cliente).reservado, D("0.0000"))


class NuncaDobleDebito(Base):
    """TX-LK-01 y VAL-T-12: reenviar el mismo Invoice-No jamás cobra dos veces."""

    def test_reenviar_el_mismo_invoice_no_no_cobra_dos_veces(self):
        emulador = Emulador(responde_en=0.2)
        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)
        cliente = self.cliente_con(emulador)
        invoice = str(reserva.operacion.invoice_no)

        primera = cliente.topup(
            invoice_no=invoice, product_id="TEL100",
            account_id="9991234567", amount=D("100"),
        )
        segunda = cliente.topup(
            invoice_no=invoice, product_id="TEL100",
            account_id="9991234567", amount=D("100"),
        )

        self.assertEqual(emulador.cobros(), 1)
        self.assertEqual(primera.transaction_id, segunda.transaction_id)
        self.assertTrue(segunda.cuerpo.get("_duplicada"))

    def test_cada_venta_lleva_su_propio_invoice_no(self):
        uno = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)
        dos = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)

        self.assertIsNotNone(uno.operacion.invoice_no)
        self.assertNotEqual(uno.operacion.invoice_no, dos.operacion.invoice_no)
        self.assertEqual(uno.operacion.invoice_ciclo, dos.operacion.invoice_ciclo)


class ContadorInvoiceNo(TestCase):
    def setUp(self):
        self.m = Mundo()

    def test_da_la_vuelta_en_99999_y_sube_el_ciclo(self):
        from apps.saldo.models import SecuenciaInvoice

        SecuenciaInvoice.objects.create(
            inquilino=self.m.inquilino, ultimo=99998, ciclo=7
        )

        n1, c1 = services.siguiente_invoice_no(self.m.inquilino)
        n2, c2 = services.siguiente_invoice_no(self.m.inquilino)
        n3, c3 = services.siguiente_invoice_no(self.m.inquilino)

        self.assertEqual((n1, c1), (99999, 7))
        self.assertEqual((n2, c2), (1, 8))   # dio la vuelta
        self.assertEqual((n3, c3), (2, 8))

    def test_el_contador_es_uno_solo_para_toda_la_plataforma(self):
        """
        Un solo Site-Id significa un solo contador. No hay uno por punto de
        venta ni por cliente.
        """
        from apps.core.models import EstadoPuntoVenta, PuntoVenta

        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("10000"))
        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("2000"))
        otra = PuntoVenta.objects.create(
            inquilino=self.m.inquilino, cliente=self.m.cliente,
            nombre="Caja Dos", estado=EstadoPuntoVenta.ACTIVO,
        )
        acciones.asignar_limite(self.m.usuario_cliente, otra, D("2000"))
        from apps.identidad.models import Rol, Usuario
        from apps.identidad.tests.factories import CLAVE

        usuario_dos = Usuario.objects.crear(
            "cajero2@laermita.mx", Rol.PUNTO_VENTA, password=CLAVE,
            inquilino=self.m.inquilino, punto_venta=otra, nombre="Cajero Dos",
        )

        a = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)
        b = acciones.vender(usuario_dos, otra, self.m.producto)

        self.assertEqual(b.operacion.invoice_no, a.operacion.invoice_no + 1)


class EmuladorSinLogin(Emulador):
    """
    El login del proveedor falla a partir de la llamada `falla_desde`.

    `expiresIn` en cero obliga a pedir token en cada llamada, para poder
    hacer que falle justo en el momento que interesa: antes del envío o
    durante las consultas.
    """

    def __init__(self, *, falla_desde=1, **kw):
        super().__init__(**kw)
        self.falla_desde = falla_desde
        self.logins = 0

    def request(self, metodo, url, headers=None, json=None, timeout=None, **kw):
        if url.endswith("/api/v1/auth/login"):
            self.logins += 1
            if self.logins >= self.falla_desde:
                from apps.proveedor.emulador import _Resp
                return _Resp(503, {"detail": "login no disponible"})
            from apps.proveedor.emulador import _Resp
            return _Resp(200, {"token": "jwt", "expiresIn": 0})
        return super().request(metodo, url, headers=headers, json=json, timeout=timeout, **kw)


class SinAutenticacion(Base):
    """
    El caso que antes subía como 500 y dejaba el saldo atorado.

    La diferencia entre las dos pruebas es CUÁNDO falla el login, y de eso
    depende todo: antes del envío la venta no salió y se libera; después del
    envío la venta pudo haber ocurrido y la reserva se queda viva.
    """

    def test_si_el_login_falla_antes_del_envio_se_libera_y_avisa(self):
        emulador = EmuladorSinLogin(falla_desde=1)
        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)

        with self.assertRaises(cierre_venta.ProveedorNoDisponible):
            cierre_venta.cerrar_venta(
                self.cliente_con(emulador), reserva,
                account_id="9991234567", product_id="TEL100",
                dormir=reloj_de(emulador),
            )

        reserva.refresh_from_db()
        self.assertEqual(reserva.estado, EstadoReserva.LIBERADA)
        self.assertEqual(services.bolsa_de(self.m.cliente).reservado, D("0"))
        self.assertEqual(emulador.topups, [])  # la venta nunca salió
        registro = RegistroEnvio.objects.get(operacion=reserva.operacion)
        self.assertEqual(registro.desenlace, Desenlace.RECHAZADA)

    def test_si_el_login_falla_durante_las_consultas_la_reserva_sigue_viva(self):
        emulador = EmuladorSinLogin(falla_desde=2, nunca_responde=True)
        cierre = self.vender(emulador)

        self.assertEqual(cierre.desenlace, Desenlace.SIN_RESOLVER)
        cierre.reserva.refresh_from_db()
        self.assertEqual(cierre.reserva.estado, EstadoReserva.ACTIVA)
        self.assertEqual(len(emulador.topups), 1)
