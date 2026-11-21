"""
El on-ramp (pesos → cripto) contra el emulador.

Los IDs TX-OR-01..08 de las pruebas de `CertificacionOnRamp` son los del
design.md de la spec. En el Anexo B de Emida (hoja "On Ramp") los mismos
escenarios llevan otra numeración; la equivalencia está en cada docstring.

Lo que se vigila en todas: que el emulador haya cobrado cero o una vez, y
que el dinero de la caja termine donde dice el desenlace:

  aprobada      → reserva CAPTURADA
  rechazada     → reserva LIBERADA
  sin_resolver  → reserva ACTIVA (el silencio no es un rechazo)
"""
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from apps.api import proveedor as puente
from apps.catalogo import servicios as catalogo
from apps.catalogo.models import (
    Carrier,
    ProductoLogico,
    ProductoProveedor,
    TipoMonto,
    TipoProducto,
)
from apps.identidad import acciones, permisos
from apps.identidad.tests.factories import Mundo
from apps.proveedor import on_ramp
from apps.proveedor.cliente import ClienteProveedor, Config, RechazoProveedor, Respuesta, SinRespuesta
from apps.proveedor.emulador import Emulador, _Resp
from apps.proveedor.modelos_on_ramp import DesenlaceOnRamp, IntentoOnRamp, RegistroOnRamp
from apps.saldo import services
from apps.saldo.models import EstadoReserva, Reserva, SecuenciaInvoice

D = Decimal
TELEFONO = "525598236495"


def cliente_con(emulador):
    cfg = Config(
        base_url="https://gw.ejemplo.com:10002", usuario="u", contrasena="x",
        site_id="9400822", clerk_id="CESAR45",
        timeout_venta=30, timeout_lookup=15,
        lookup_desde_envio=40, espera_entre_lookups=10, max_lookups=3,
    )
    return ClienteProveedor(cfg, sesion=emulador)


class Pendiente(Emulador):
    """El proveedor contesta 200, con transactionId, pero sin decidir (PENDING)."""

    def _on_ramp(self, headers, cuerpo, timeout):
        invoice = str(headers.get("Invoice-No") or "")
        respuesta = {"transactionId": "172670349", "status": "PENDING",
                     "targetAmount": 0.00285714, "targetAsset": "BTC", "fee": 0.105}
        self.transacciones[invoice] = respuesta
        return _Resp(200, respuesta)


class BaseOnRamp(TestCase):
    def setUp(self):
        self.m = Mundo()
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("10000"))
        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("5000"))

        self.carrier_cripto = Carrier.objects.create(
            inquilino=self.m.inquilino, clave="btc", nombre="BTC", tipo=TipoProducto.CRIPTO
        )
        self.cripto = ProductoLogico.objects.create(
            inquilino=self.m.inquilino, carrier=self.carrier_cripto, sku="CRIPTO-BTC",
            nombre="Crypto $1 - $500", tipo=TipoProducto.CRIPTO,
            tipo_monto=TipoMonto.RANGO, monto_min=D("1"), monto_max=D("500"),
        )
        ProductoProveedor.objects.create(
            producto=self.cripto, proveedor=self.m.proveedor, product_id="651"
        )
        self.emulador = Emulador(responde_en=0.2)
        self.usar(self.emulador)

    def usar(self, emulador):
        """Pone `emulador` como el proveedor de la venta."""
        self.emulador = emulador
        self.anterior = puente.usar_fabrica(lambda: cliente_con(emulador))
        self.addCleanup(puente.usar_fabrica, self.anterior)

    def vender(self, *, amount=100, phone_number=TELEFONO, usuario=None, **kw):
        return acciones.vender_on_ramp(
            usuario or self.m.usuario_pdv, sku="CRIPTO-BTC",
            amount=amount, phone_number=phone_number, **kw,
        )

    def bolsa(self):
        return services.bolsa_de(self.m.cliente)

    def estado_reserva(self, cierre):
        cierre.reserva.refresh_from_db()
        return cierre.reserva.estado

    def sin_efectos(self, saldo_antes):
        """Nada se apartó, nada se cobró, y el proveedor no recibió nada."""
        self.assertEqual(Reserva.objects.count(), 0)
        self.assertEqual(RegistroOnRamp.objects.count(), 0)
        self.assertEqual(self.emulador.on_ramps, [])
        bolsa = self.bolsa()
        bolsa.refresh_from_db()
        self.assertEqual((bolsa.saldo, bolsa.reservado), saldo_antes)


class CertificacionOnRamp(BaseOnRamp):
    def test_TX_OR_01_aprobada_en_el_primer_intento(self):
        """Anexo B TX-OR-04: on-ramp exitoso → status COMPLETED y la reserva se captura."""
        cierre = self.vender(amount=100)

        self.assertEqual(cierre.desenlace, "aprobada")
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.CAPTURADA)
        self.assertEqual(cierre.target_asset, "BTC")
        self.assertTrue(cierre.transaction_id)
        self.assertEqual(self.emulador.cobros(), 1)
        self.assertEqual(len(self.emulador.on_ramps), 1)

        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.APROBADA)
        self.assertEqual(registro.operacion, cierre.reserva.operacion)
        self.assertEqual(registro.invoice_no, str(cierre.reserva.operacion.invoice_no))
        self.assertEqual(registro.product_id, "651")
        self.assertEqual(registro.monto, D("100"))
        self.assertEqual(registro.transaction_id, cierre.transaction_id)
        self.assertIsNotNone(registro.cerrado)
        # Un intento "envio", y el contador lo refleja.
        self.assertEqual(list(registro.intentos_detalle.values_list("tipo", "aprobada")),
                         [("envio", True)])
        self.assertEqual(registro.intentos, 1)

    def test_TX_OR_02_rechazada_por_el_proveedor_libera(self):
        """HTTP 4xx (que no sea 401): la venta no ocurrió y el saldo se libera."""
        self.usar(Emulador(rechazar_http=422))
        antes = self.bolsa().saldo

        cierre = self.vender(amount=100)

        self.assertEqual(cierre.desenlace, "rechazada")
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.LIBERADA)
        self.assertEqual(cierre.mensaje, "Propósito")
        bolsa = self.bolsa()
        bolsa.refresh_from_db()
        self.assertEqual((bolsa.saldo, bolsa.reservado), (antes, D("0")))
        self.assertEqual(self.emulador.cobros(), 0)

        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.RECHAZADA)
        self.assertEqual(registro.intentos_detalle.get().http, 422)
        self.assertEqual(registro.response_message, "Propósito")

    def test_TX_OR_03_timeout_deja_la_reserva_activa(self):
        """El silencio no es un rechazo: sin respuesta, ni se cobra ni se libera."""
        self.usar(Emulador(nunca_responde=True))

        cierre = self.vender(amount=100)

        self.assertEqual(cierre.desenlace, "sin_resolver")
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.ACTIVA)
        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.SIN_RESOLVER)
        intento = registro.intentos_detalle.get()
        self.assertEqual((intento.tipo, intento.http, intento.aprobada), ("envio", None, False))
        # No hay consultas a mitad de la venta: el on-ramp no hace loop de lookups.
        self.assertEqual(self.emulador.lookups, [])
        self.assertEqual(len(self.emulador.on_ramps), 1)

    def test_TX_OR_04_http_5xx_deja_la_reserva_activa(self):
        """Un 5xx no dice si la transacción se hizo: no se libera."""
        self.usar(Emulador(rechazar_http=503))

        cierre = self.vender(amount=100)

        self.assertEqual(cierre.desenlace, "sin_resolver")
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.ACTIVA)
        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.SIN_RESOLVER)
        self.assertEqual(registro.intentos_detalle.get().http, 503)

    def test_TX_OR_05_http_401_libera_y_avisa_que_no_salio(self):
        """401: la venta nunca llegó al proveedor. Se libera y se levanta ProveedorNoDisponible."""
        self.usar(Emulador(rechazar_http=401))
        antes = self.bolsa().saldo

        with self.assertRaises(on_ramp.ProveedorNoDisponible):
            self.vender(amount=100)

        reserva = Reserva.objects.get()
        self.assertEqual(reserva.estado, EstadoReserva.LIBERADA)
        bolsa = self.bolsa()
        bolsa.refresh_from_db()
        self.assertEqual((bolsa.saldo, bolsa.reservado), (antes, D("0")))
        self.assertEqual(RegistroOnRamp.objects.get().desenlace, DesenlaceOnRamp.RECHAZADA)

    def test_TX_OR_06_monto_no_entero_no_toca_nada(self):
        """Anexo B TX-OR-02: amount '0' o '10.5' se rechaza antes de reservar y de llamar."""
        antes = self.bolsa()
        saldo = (antes.saldo, antes.reservado)
        for malo in (10.5, 0, -5, "10.5", "0", "abc", "", None, True, D("10"), 10.0):
            with self.subTest(amount=malo):
                with self.assertRaises(catalogo.MontoInvalido):
                    self.vender(amount=malo)
                self.sin_efectos(saldo)

    def test_TX_OR_07_telefono_de_11_digitos_no_toca_nada(self):
        """Anexo B TX-OR-05 y TX-OR-06: menos de 12 dígitos o con letras → sin reserva ni red."""
        antes = self.bolsa()
        saldo = (antes.saldo, antes.reservado)
        for malo in ("52559823649", "5255982364955", "52598236ABCD", "5255 9823649",
                     "+52559823649", "", "525598236495\n", 525598236495, None):
            with self.subTest(telefono=malo):
                with self.assertRaises(catalogo.TelefonoInvalido):
                    self.vender(phone_number=malo)
                self.sin_efectos(saldo)

    def test_TX_OR_08_la_misma_llave_devuelve_el_mismo_folio(self):
        """Repetir la Idempotency-Key de una venta aprobada no vuelve a cobrar ni a llamar."""
        primera = self.vender(amount=100, idempotency_key="llave-1")
        segunda = self.vender(amount=100, idempotency_key="llave-1")

        self.assertFalse(primera.repetida)
        self.assertTrue(segunda.repetida)
        self.assertEqual(segunda.desenlace, "aprobada")
        self.assertEqual(segunda.reserva.operacion.folio, primera.reserva.operacion.folio)
        self.assertEqual(Reserva.objects.count(), 1)
        self.assertEqual(len(self.emulador.on_ramps), 1)
        self.assertEqual(self.emulador.cobros(), 1)
        self.assertEqual(segunda.transaction_id, primera.transaction_id)

    def test_la_llave_repetida_de_una_sin_resolver_sigue_sin_resolver(self):
        """Requirement 6.4: la original pudo haber salido; se contesta igual, sin volver a mandar."""
        self.usar(Emulador(nunca_responde=True))
        primera = self.vender(amount=100, idempotency_key="llave-2")
        segunda = self.vender(amount=100, idempotency_key="llave-2")

        self.assertEqual(primera.desenlace, "sin_resolver")
        self.assertTrue(segunda.repetida)
        self.assertEqual(segunda.desenlace, "sin_resolver")
        self.assertEqual(segunda.reserva.operacion.folio, primera.reserva.operacion.folio)
        self.assertEqual(len(self.emulador.on_ramps), 1)
        self.assertEqual(Reserva.objects.count(), 1)

    def test_la_llave_se_cuenta_por_caja(self):
        """Requirement 6.3: la misma llave en otra caja no choca con la primera."""
        from apps.core.models import EstadoPuntoVenta, PuntoVenta
        from apps.identidad.models import Rol, Usuario
        from apps.identidad.tests.factories import CLAVE

        otra = PuntoVenta.objects.create(
            inquilino=self.m.inquilino, cliente=self.m.cliente,
            nombre="Caja Dos", estado=EstadoPuntoVenta.ACTIVO,
        )
        usuario_otra = Usuario.objects.crear(
            "ana@laermita.mx", Rol.PUNTO_VENTA, password=CLAVE,
            inquilino=self.m.inquilino, punto_venta=otra, nombre="Ana",
        )
        acciones.asignar_limite(self.m.usuario_cliente, otra, D("1000"))

        uno = self.vender(amount=10, idempotency_key="misma")
        dos = self.vender(amount=10, idempotency_key="misma", usuario=usuario_otra)

        self.assertFalse(dos.repetida)
        self.assertNotEqual(uno.reserva.operacion.folio, dos.reserva.operacion.folio)
        self.assertEqual(Reserva.objects.count(), 2)

    def test_cinco_ventas_con_montos_distintos_cada_una_con_su_cotizacion(self):
        """Anexo B TX-OR-04 (x5): en el log se ve la consulta de tasa antes de cada venta."""
        montos = [10, 25, 50, 75, 100]
        for monto in montos:
            tasa = acciones.cotizar_on_ramp(self.m.usuario_pdv, sku="CRIPTO-BTC", amount=monto)
            self.assertEqual(str(tasa["originalFiatAmount"]), str(monto))
            self.assertEqual(self.vender(amount=monto).desenlace, "aprobada")

        rutas = [("GET" if "exchange-rate" in c["ruta"] else "POST")
                 for c in self.emulador.llamadas if "/on-ramp" in c["ruta"]]
        self.assertEqual(rutas, ["GET", "POST"] * 5)
        enviados = [c["cuerpo"]["amount"] for c in self.emulador.on_ramps]
        self.assertEqual(enviados, [str(m) for m in montos])
        self.assertEqual(self.emulador.cobros(), 5)

    def test_cada_llamada_lleva_su_invoice_no_y_los_encabezados(self):
        # Cotizar y vender son dos pasos de la pantalla: `vender` no cotiza solo.
        acciones.cotizar_on_ramp(self.m.usuario_pdv, sku="CRIPTO-BTC", amount=100)
        self.vender(amount=100)
        cotizacion, venta = [c for c in self.emulador.llamadas if "/on-ramp" in c["ruta"]]
        self.assertIn("?amount=100", cotizacion["ruta"])
        for llamada in (cotizacion, venta):
            h = llamada["headers"]
            self.assertEqual(h["Product-Id"], "651")
            self.assertEqual(h["Site-Id"], "9400822")
            self.assertEqual(h["Clerk-Id"], "CESAR45")
            self.assertEqual(h["Language"], "2")
            self.assertTrue(h["Invoice-No"].isdigit())
        self.assertEqual(venta["cuerpo"], {"amount": "100", "phoneNumber": TELEFONO})

    def test_cotizar_no_toca_el_saldo_pero_si_consume_invoice(self):
        antes = self.bolsa()
        saldo = (antes.saldo, antes.reservado)
        ultimo = SecuenciaInvoice.objects.filter(inquilino=self.m.inquilino).first()
        ultimo = ultimo.ultimo if ultimo else 0

        tasa = acciones.cotizar_on_ramp(self.m.usuario_pdv, sku="CRIPTO-BTC", amount=100)

        self.assertEqual(set(tasa), {"originalFiatAmount", "rate", "fee", "fiatAmountAfterFee"})
        self.sin_efectos(saldo)
        consultas = [c for c in self.emulador.llamadas if "exchange-rate" in c["ruta"]]
        self.assertEqual(len(consultas), 1)
        self.assertEqual(consultas[0]["headers"]["Invoice-No"], str(ultimo + 1))

    def test_el_telefono_nunca_llega_a_los_logs(self):
        """Requirement 9.4: ni vendiendo bien, ni con rechazo, ni sin respuesta."""
        for emulador in (Emulador(), Emulador(rechazar_http=422), Emulador(nunca_responde=True),
                         Emulador(rechazar_http=503), Pendiente()):
            with self.subTest(emulador=type(emulador).__name__):
                self.usar(emulador)
                with self.assertLogs("tae", level="DEBUG") as logs:
                    self.vender(amount=10)
                self.assertNotIn(TELEFONO, "\n".join(logs.output))

    def test_el_telefono_solo_queda_en_la_evidencia(self):
        self.vender(amount=10)
        self.assertEqual(RegistroOnRamp.objects.get().phone_number, TELEFONO)
        # Y los intentos guardan lo que contestó el proveedor, no lo que se le mandó.
        self.assertNotIn(TELEFONO, IntentoOnRamp.objects.get().cuerpo)


class PermisosYProducto(BaseOnRamp):
    def test_solo_una_caja_vende_y_cotiza(self):
        for usuario in (self.m.usuario_cliente, self.m.usuario_admin, self.m.super_admin):
            with self.subTest(rol=usuario.rol):
                with self.assertRaises(permisos.PermisoDenegado):
                    self.vender(usuario=usuario)
                with self.assertRaises(permisos.PermisoDenegado):
                    acciones.cotizar_on_ramp(usuario, sku="CRIPTO-BTC", amount=10)
        self.assertEqual(self.emulador.llamadas, [])

    def test_un_producto_de_tiempo_aire_no_se_vende_como_cripto(self):
        with self.assertRaises(catalogo.ProductoNoDisponible):
            acciones.vender_on_ramp(
                self.m.usuario_pdv, sku="TEL-TAE-100", amount=100, phone_number=TELEFONO
            )
        self.assertEqual(Reserva.objects.count(), 0)

    def test_sin_ruta_no_aparta_saldo(self):
        ProductoProveedor.objects.filter(producto=self.cripto).update(activo=False)
        with self.assertRaises(catalogo.SinRuta):
            self.vender(amount=10)
        self.assertEqual(Reserva.objects.count(), 0)
        self.assertEqual(self.emulador.on_ramps, [])

    def test_el_monto_fuera_del_rango_del_producto_no_se_reserva(self):
        with self.assertRaises(catalogo.MontoInvalido):
            self.vender(amount=501)
        self.assertEqual(Reserva.objects.count(), 0)

    def test_sin_saldo_no_se_llama_al_proveedor(self):
        for _ in range(10):  # los 5,000 asignados a la caja
            self.assertEqual(self.vender(amount=500).desenlace, "aprobada")
        llamadas = len(self.emulador.on_ramps)

        with self.assertRaises(services.SaldoInsuficiente):
            self.vender(amount=500)

        self.assertEqual(len(self.emulador.on_ramps), llamadas)
        self.assertEqual(Reserva.objects.count(), 10)


class RespuestasQueNoConcluyen(BaseOnRamp):
    """Lo que dice el proveedor cuando no dice sí ni no."""

    def test_estado_pendiente_no_libera(self):
        self.usar(Pendiente())
        cierre = self.vender(amount=100)

        self.assertEqual(cierre.desenlace, "sin_resolver")
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.ACTIVA)
        # Y se queda con el identificador, que es lo que usa la conciliación.
        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.transaction_id, "172670349")
        self.assertEqual(registro.target_asset, "BTC")
        self.assertEqual(cierre.transaction_id, "172670349")

    def test_estado_fallido_explicito_si_libera(self):
        self.usar(Emulador(aprobar=False))
        cierre = self.vender(amount=100)

        self.assertEqual(cierre.desenlace, "rechazada")
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.LIBERADA)
        self.assertEqual(self.emulador.cobros(), 0)

    def test_si_no_se_puede_autenticar_la_venta_no_salio(self):
        """Falla el login antes de enviar: se libera y se avisa, igual que el 401."""
        class SinLogin(Emulador):
            def request(self, metodo, url, **kw):
                if url.endswith("/api/v1/auth/login"):
                    return _Resp(500, {})
                return super().request(metodo, url, **kw)

        self.usar(SinLogin())
        with self.assertRaises(on_ramp.ProveedorNoDisponible):
            self.vender(amount=100)
        self.assertEqual(Reserva.objects.get().estado, EstadoReserva.LIBERADA)
        self.assertEqual(self.emulador.on_ramps, [])
        self.assertEqual(RegistroOnRamp.objects.get().desenlace, DesenlaceOnRamp.RECHAZADA)


class CotizacionOnRamp(BaseOnRamp):
    def test_el_valor_se_devuelve_tal_cual_lo_manda_el_proveedor(self):
        """Requirement 2.2: ni se redondea ni se convierte."""
        crudo = {"originalFiatAmount": 8, "rate": 71.558142779962, "fee": 0.84,
                 "fiatAmountAfterFee": 7.16}

        class ConTasa(Emulador):
            def _on_ramp_exchange_rate(self, ruta, timeout):
                return _Resp(200, dict(crudo))

        tasa = on_ramp.exchange_rate(
            cliente_con(ConTasa()), product_id="651", invoice_no=1, amount=8
        )
        self.assertEqual(tasa, crudo)
        self.assertIs(type(tasa["rate"]), float)

    def test_400_es_monto_invalido_y_422_producto_no_disponible(self):
        for http, esperado in ((400, catalogo.MontoInvalido), (422, catalogo.ProductoNoDisponible)):
            with self.subTest(http=http):
                class Rechaza(Emulador):
                    def _on_ramp_exchange_rate(self, ruta, timeout, _h=http):
                        return _Resp(_h, {"detail": "no"})

                with self.assertRaises(esperado):
                    on_ramp.exchange_rate(
                        cliente_con(Rechaza()), product_id="651", invoice_no=1, amount=8
                    )

    def test_cualquier_otro_error_es_sin_respuesta(self):
        for http in (401, 404, 500, 503):
            with self.subTest(http=http):
                class Falla(Emulador):
                    def _on_ramp_exchange_rate(self, ruta, timeout, _h=http):
                        return _Resp(_h, {"detail": "no"})

                with self.assertRaises(SinRespuesta):
                    on_ramp.exchange_rate(
                        cliente_con(Falla()), product_id="651", invoice_no=1, amount=8
                    )

    def test_respuesta_sin_todos_los_campos_no_es_una_tasa(self):
        class Incompleta(Emulador):
            def _on_ramp_exchange_rate(self, ruta, timeout):
                return _Resp(200, {"rate": "17.5"})

        with self.assertRaises(SinRespuesta):
            on_ramp.exchange_rate(
                cliente_con(Incompleta()), product_id="651", invoice_no=1, amount=8
            )

    def test_el_monto_malo_no_sale_a_la_red(self):
        emulador = Emulador()
        for malo in (0, -1, 1.5, "1.5", "01", "", None, 1_000_000_000):
            with self.subTest(amount=malo):
                with self.assertRaises(catalogo.MontoInvalido):
                    on_ramp.exchange_rate(
                        cliente_con(emulador), product_id="651", invoice_no=1, amount=malo
                    )
        self.assertEqual(emulador.llamadas, [])


class MontoEntero(SimpleTestCase):
    def test_acepta_enteros_positivos_y_su_texto(self):
        for bueno, esperado in ((1, 1), (999_999_999, 999_999_999), ("10", 10), ("999999999", 999_999_999)):
            self.assertEqual(on_ramp.monto_entero(bueno), esperado)

    def test_rechaza_todo_lo_demas(self):
        for malo in (0, -1, 10.0, 10.5, D("10"), True, False, "0", "-1", "1.0", " 10", "10 ", "٣",
                     "1e3", "", None, [], {}, 1_000_000_000):
            with self.assertRaises(catalogo.MontoInvalido, msg=repr(malo)):
                on_ramp.monto_entero(malo)

    def test_el_tope_es_configurable(self):
        self.assertEqual(on_ramp.monto_entero(999_999, maximo=999_999), 999_999)
        with self.assertRaises(catalogo.MontoInvalido):
            on_ramp.monto_entero(1_000_000, maximo=999_999)


class ValidacionCripto(SimpleTestCase):
    """Task 3: un producto cripto en rango solo acepta enteros de 1 a 99,999."""

    def producto(self, minimo, maximo, tipo=TipoProducto.CRIPTO):
        return ProductoLogico(
            sku="X", nombre="X", tipo=tipo, tipo_monto=TipoMonto.RANGO,
            monto_min=minimo, monto_max=maximo,
        )

    def test_acepta_enteros_de_1_a_99999(self):
        for minimo, maximo in ((1, 99999), (D("1.0000"), D("500.0000")), (5, 5)):
            self.producto(minimo, maximo).clean()

    def test_rechaza_decimales_ceros_negativos_y_el_exceso(self):
        for campo, minimo, maximo in (
            ("monto_min", D("1.5"), 10), ("monto_max", 1, D("10.5")),
            ("monto_min", 0, 10), ("monto_min", -1, 10),
            ("monto_max", 1, 100_000), ("monto_min", 100_000, 100_001),
            ("monto_min", D("NaN"), 10), ("monto_max", 1, D("Infinity")),
        ):
            with self.subTest(minimo=minimo, maximo=maximo):
                with self.assertRaises(ValidationError) as e:
                    self.producto(minimo, maximo).clean()
                self.assertIn(campo, e.exception.message_dict)

    def test_la_regla_es_solo_para_cripto(self):
        # Tiempo aire sí puede llevar decimales en su rango.
        self.producto(D("0.5"), D("10.5"), tipo=TipoProducto.TIEMPO_AIRE).clean()


class ConciliacionOnRamp(BaseOnRamp):
    """`reintentar_sin_resolver_onramp`: lo que decide con una venta que quedó abierta."""

    class Consulta:
        """Un cliente que solo sabe contestar la consulta de conciliación."""

        def __init__(self, respuesta=None, falla=None):
            self.respuesta, self.falla, self.consultas = respuesta, falla, []

        def lookup(self, invoice_no):
            self.consultas.append(invoice_no)
            if self.falla:
                raise self.falla
            return self.respuesta

    def sin_resolver_con_id(self):
        self.usar(Pendiente())
        return self.vender(amount=100)

    def concilia(self, **kw):
        consulta = self.Consulta(**kw)
        resueltos = on_ramp.reintentar_sin_resolver_onramp(consulta, self.m.inquilino)
        return consulta, resueltos

    def test_aprobada_por_el_proveedor_se_captura(self):
        cierre = self.sin_resolver_con_id()
        respuesta = Respuesta.desde("lookup", 200, {"responseCode": "0", "transactionId": 1})

        consulta, resueltos = self.concilia(respuesta=respuesta)

        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.CAPTURADA)
        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.APROBADA)
        self.assertEqual(resueltos, [registro])
        # Se pregunta por el invoice_no: es lo único que acepta el endpoint.
        self.assertEqual(consulta.consultas, [registro.invoice_no])
        self.assertEqual(
            list(registro.intentos_detalle.values_list("tipo", "resultado")),
            [("envio", ""), ("conciliacion", "")],
        )
        self.assertEqual(registro.intentos, 2)

    def test_rechazo_explicito_libera(self):
        cierre = self.sin_resolver_con_id()
        respuesta = Respuesta.desde("lookup", 200, {"responseCode": "51", "responseMessage": "no"})

        self.concilia(respuesta=respuesta)

        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.LIBERADA)
        self.assertEqual(RegistroOnRamp.objects.get().desenlace, DesenlaceOnRamp.RECHAZADA)

    def test_respuesta_vacia_no_concluye_nada(self):
        cierre = self.sin_resolver_con_id()

        _, resueltos = self.concilia(respuesta=Respuesta.desde("lookup", 200, {}))

        self.assertEqual(resueltos, [])
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.ACTIVA)
        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.SIN_RESOLVER)
        self.assertEqual(registro.intentos_detalle.last().resultado, "error_proveedor")

    def test_un_404_no_se_toma_como_que_la_venta_nunca_entro(self):
        """En tiempo aire el 404 libera; aquí no está confirmado que el lookup cubra on-ramp."""
        cierre = self.sin_resolver_con_id()

        self.concilia(falla=RechazoProveedor("no existe", http=404))

        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.ACTIVA)
        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.SIN_RESOLVER)
        intento = registro.intentos_detalle.last()
        self.assertEqual((intento.resultado, intento.http), ("error_proveedor", 404))

    def test_timeout_deja_todo_como_estaba(self):
        cierre = self.sin_resolver_con_id()

        self.concilia(falla=SinRespuesta("no contestó"))

        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.ACTIVA)
        self.assertEqual(RegistroOnRamp.objects.get().desenlace, DesenlaceOnRamp.SIN_RESOLVER)

    def test_sin_transaction_id_pasa_a_revision_manual_sin_preguntar(self):
        self.usar(Emulador(nunca_responde=True))
        cierre = self.vender(amount=100)

        consulta, resueltos = self.concilia(respuesta=None)

        self.assertEqual(consulta.consultas, [])
        self.assertEqual(resueltos, [])
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.ACTIVA)
        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.REQUIERE_REVISION_MANUAL)
        intento = registro.intentos_detalle.last()
        self.assertEqual((intento.tipo, intento.resultado), ("conciliacion", "sin_identificador"))

    def test_error_al_capturar_deja_el_registro_igual(self):
        cierre = self.sin_resolver_con_id()
        respuesta = Respuesta.desde("lookup", 200, {"responseCode": "0"})
        with mock.patch.object(services, "capturar", side_effect=RuntimeError("falló")):
            _, resueltos = self.concilia(respuesta=respuesta)

        self.assertEqual(resueltos, [])
        self.assertEqual(self.estado_reserva(cierre), EstadoReserva.ACTIVA)
        registro = RegistroOnRamp.objects.get()
        self.assertEqual(registro.desenlace, DesenlaceOnRamp.SIN_RESOLVER)
        self.assertEqual(registro.intentos_detalle.last().resultado, "error_captura")

    def test_lo_ya_resuelto_no_se_vuelve_a_consultar(self):
        self.vender(amount=100)  # aprobada
        consulta, resueltos = self.concilia(respuesta=None)
        self.assertEqual((consulta.consultas, resueltos), ([], []))

    def test_el_limite_se_acota_entre_1_y_500(self):
        self.sin_resolver_con_id()
        self.usar(Pendiente())
        self.vender(amount=10)
        consulta = self.Consulta(respuesta=Respuesta.desde("lookup", 200, {}))
        on_ramp.reintentar_sin_resolver_onramp(consulta, self.m.inquilino, limite=0)
        self.assertEqual(len(consulta.consultas), 1)  # 0 se acota a 1
