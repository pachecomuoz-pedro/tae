"""
La bitácora de Emida (logs/emida.log): lo que se escribe y lo que NO.

Dos cosas se vigilan aquí, y son las dos que revisa Emida:

  1. Que cada caso deje el rastro que pide el Plan de certificación (Fase 3 y
     5): request, response, HTTP, tiempos, el lookup tras un timeout y cómo
     cerró la venta.
  2. Que no se filtre nada sensible (sección 4.2 del Plan): ni JWT, ni PIN, ni
     el teléfono completo. Y, del otro lado, que lo que SÍ es evidencia
     (transactionId, controlNo) salga completo y no enmascarado.

En las pruebas el logger "tae" está en CRITICAL; `assertLogs` lo enciende solo
mientras dura cada prueba.
"""
import logging
import re
from datetime import datetime

from django.test import SimpleTestCase

from apps.proveedor import bitacora
from apps.proveedor.emulador import Emulador, emulador_de_certificacion
from apps.proveedor.modelos import Desenlace, RegistroEnvio

from .test_cierre import Base

CUENTA = "9991234567"
TOKEN = "jwt-de-prueba"  # el que da el emulador al hacer login


def linea(logs, evento, **campos):
    """La primera línea del log que empieza con `evento` y trae esos campos."""
    for texto in logs.output:
        cuerpo = texto.split("tae.emida:", 1)[-1].strip()
        if cuerpo.startswith(evento) and all(f"{k}={v}" in cuerpo for k, v in campos.items()):
            return cuerpo
    raise AssertionError(f"No hay {evento} {campos} en:\n" + "\n".join(logs.output))


def eventos(logs):
    """Los nombres de evento, en orden."""
    return [t.split("tae.emida:", 1)[-1].strip().split()[0] for t in logs.output]


class ConPin(Emulador):
    """Un proveedor que devuelve el PIN de una tarjeta de regalo."""

    def _armar(self, invoice, cuerpo):
        datos = super()._armar(invoice, cuerpo)
        datos["pin"] = "98765432"
        return datos


class Enmascarado(SimpleTestCase):
    def test_el_jwt_el_pin_y_la_contrasena_se_tapan(self):
        limpio = bitacora.sanear({"token": "abc", "password": "x", "pin": "1234", "PIN": "5678"})
        self.assertEqual(set(limpio.values()), {"***"})

    def test_el_authorization_conserva_el_esquema_pero_no_la_llave(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.firma-del-token"
        limpio = bitacora.sanear_cabeceras({"Authorization": f"Bearer {jwt}", "Site-Id": "9400822"})
        self.assertEqual(limpio["Authorization"], "***")
        self.assertEqual(limpio["Site-Id"], "9400822")

    def test_un_jwt_suelto_en_un_texto_tambien(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.firma-del-token"
        self.assertNotIn(jwt, bitacora.sanear_mensaje(f"fallo con {jwt} al entrar"))

    def test_el_telefono_deja_solo_los_ultimos_cuatro(self):
        self.assertEqual(bitacora.enmascarar_telefono(CUENTA), "******4567")
        limpio = bitacora.sanear({"accountId": CUENTA, "phoneNumber": CUENTA, "accountNumber": CUENTA})
        self.assertEqual(set(limpio.values()), {"******4567"})

    def test_un_telefono_repetido_en_el_mensaje_del_proveedor_se_tapa(self):
        limpio = bitacora.sanear({"responseMessage": f"PhoneNumber {CUENTA} no valido"})
        self.assertEqual(limpio["responseMessage"], "PhoneNumber ******4567 no valido")

    def test_la_evidencia_larga_no_se_toca(self):
        """transactionId y controlNo pueden pasar de 10 dígitos y son la evidencia."""
        datos = {"transactionId": "12345678901234", "controlNo": "8842019337123", "amount": "100.00"}
        self.assertEqual(bitacora.sanear(datos), datos)

    def test_no_modifica_el_original(self):
        original = {"token": "abc", "accountId": CUENTA}
        bitacora.sanear(original)
        self.assertEqual(original, {"token": "abc", "accountId": CUENTA})

    def test_un_body_enorme_se_recorta(self):
        texto = bitacora.cuerpo_para_log([{"productId": i} for i in range(5000)], "")
        self.assertLess(len(texto), bitacora.MAX_CUERPO + 60)
        self.assertIn("caracteres)", texto)

    def test_el_correo_del_login_se_tapa(self):
        self.assertEqual(bitacora.enmascarar_correo("pedro@empresa.mx"), "pe***@empresa.mx")

    def test_la_hora_sale_con_desfase_de_mexico_no_en_utc(self):
        registro = logging.LogRecord("tae.emida", logging.INFO, __file__, 1, "x", None, None)
        texto = bitacora.FormatoEmida().formatTime(registro)
        self.assertRegex(texto, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[-+]\d{2}:\d{2}$")
        # Mexico_City no tiene horario de verano desde 2022: siempre -06:00.
        self.assertTrue(texto.endswith("-06:00"), texto)
        datetime.fromisoformat(texto)  # y es una fecha válida


class LoQueQuedaEnLaBitacora(Base):
    def test_venta_aprobada_deja_request_response_veredicto_y_cierre(self):
        emulador = Emulador()
        with self.assertLogs("tae.emida", level="INFO") as logs:
            cierre = self.vender(emulador)
        self.assertEqual(cierre.desenlace, Desenlace.APROBADA)
        registro = RegistroEnvio.objects.get()
        inv = registro.invoice_no

        req = linea(logs, "REQ", metodo="topup", invoice=inv, verbo="POST")
        self.assertIn("/api/v1/transaction/topup", req)
        self.assertIn('"Site-Id":"9400822"', req)
        self.assertIn('"Invoice-No":"%s"' % inv, req)
        self.assertIn('"Authorization":"***"', req)

        res = linea(logs, "RES", metodo="topup", invoice=inv, http=200)
        self.assertRegex(res, r"ms=\d+")

        veredicto = linea(logs, "VEREDICTO", metodo="topup", invoice=inv, aprobada="si")
        self.assertIn(f"transaction_id={registro.transaction_id}", veredicto)
        self.assertIn(f"control_no={registro.control_no}", veredicto)

        linea(logs, "VENTA_INICIO", invoice=inv)
        linea(logs, "CIERRE", invoice=inv, desenlace="aprobada", saldo="capturado")

    def test_nada_sensible_llega_a_la_bitacora(self):
        emulador = ConPin()
        with self.assertLogs("tae.emida", level="DEBUG") as logs:
            self.vender(emulador)
        todo = "\n".join(logs.output)
        self.assertNotIn(TOKEN, todo)          # JWT
        self.assertNotIn("98765432", todo)     # PIN de la tarjeta
        self.assertNotIn(CUENTA, todo)         # teléfono completo, ni en el body ni en el mensaje
        self.assertIn("******4567", todo)      # pero sí sus últimos 4
        self.assertIn("PhoneNumber ******4567", todo)  # el eco del proveedor, tapado
        # El PIN y el teléfono completos siguen en la base: se tapan en el log, no en la evidencia.
        self.assertEqual(RegistroEnvio.objects.get().pin, "98765432")
        self.assertEqual(RegistroEnvio.objects.get().account_id, CUENTA)

    def test_la_evidencia_larga_sale_completa(self):
        with self.assertLogs("tae.emida", level="INFO") as logs:
            self.vender(Emulador())
        registro = RegistroEnvio.objects.get()
        self.assertGreaterEqual(len(registro.control_no), 10)
        self.assertIn(registro.control_no, "\n".join(logs.output))

    def test_tx_lk_08_timeout_y_lookup_quedan_en_orden(self):
        """El caso que más van a mirar: el envío no responde y se resuelve por lookup."""
        with self.assertLogs("tae.emida", level="INFO") as logs:
            cierre = self.vender(emulador_de_certificacion("TX-LK-08"))
        self.assertEqual(cierre.desenlace, Desenlace.APROBADA)
        inv = RegistroEnvio.objects.get().invoice_no

        orden = eventos(logs)
        # login, envío, timeout, espera a los 40 s, lookup y cierre; cada uno después del anterior.
        marcas = ["VENTA_INICIO", "TIMEOUT", "ESPERA_LOOKUP", "LOOKUP_INTENTO", "CIERRE"]
        posiciones = [orden.index(m) for m in marcas]
        self.assertEqual(posiciones, sorted(posiciones), orden)

        linea(logs, "TIMEOUT", invoice=inv, limite_s=30)
        # El emulador adelanta su propio reloj, no el real: aquí `t_s` sale en 0 y
        # la espera completa (40 s). En producción son 30 s de timeout y 10 de espera.
        espera = linea(logs, "ESPERA_LOOKUP", invoice=inv)
        self.assertRegex(espera, r"t_s=\d+\.\d espera_s=\d+\.\d")
        linea(logs, "REQ", metodo="lookup", invoice=inv)
        linea(logs, "LOOKUP_INTENTO", invoice=inv, intento="1/3")
        linea(logs, "CIERRE", invoice=inv, desenlace="aprobada", saldo="capturado")

    def test_tx_lk_10_sin_respuesta_queda_sin_resolver_con_la_reserva_viva(self):
        with self.assertLogs("tae.emida", level="INFO") as logs:
            cierre = self.vender(emulador_de_certificacion("TX-LK-10"))
        self.assertEqual(cierre.desenlace, Desenlace.SIN_RESOLVER)
        inv = RegistroEnvio.objects.get().invoice_no

        for n in ("1/3", "2/3", "3/3"):
            linea(logs, "LOOKUP_INTENTO", invoice=inv, intento=n)
        cierre_log = linea(logs, "CIERRE", invoice=inv, desenlace="sin_resolver", saldo="reserva_viva")
        # Un sin_resolver es de lo que más importa que se vea: sube a WARNING.
        self.assertTrue(any("WARNING" in t and "CIERRE" in t for t in logs.output), cierre_log)

    def test_un_rechazo_422_queda_como_warning_y_libera(self):
        with self.assertLogs("tae.emida", level="INFO") as logs:
            cierre = self.vender(Emulador(rechazar_http=422))
        self.assertEqual(cierre.desenlace, Desenlace.RECHAZADA)
        inv = RegistroEnvio.objects.get().invoice_no

        self.assertTrue(any("WARNING" in t and "RES" in t and "http=422" in t for t in logs.output))
        linea(logs, "CIERRE", invoice=inv, desenlace="rechazada", saldo="liberado")

    def test_el_timeout_dice_cuanto_se_espero(self):
        """Antes el mensaje decía 'no respondió en Nones' porque no se pasaba timeout."""
        emulador = emulador_de_certificacion("TX-LK-10")
        self.vender(emulador)
        intento = RegistroEnvio.objects.get().intentos_detalle.first()
        self.assertIn("30", intento.error)
        self.assertNotIn("None", intento.error)

    def test_el_login_tampoco_filtra_la_contrasena_ni_el_token(self):
        with self.assertLogs("tae.emida", level="DEBUG") as logs:
            self.vender(Emulador())
        todo = "\n".join(logs.output)
        login = linea(logs, "REQ", metodo="login")
        self.assertIn('"password":"***"', login)
        self.assertNotIn('"password":"x"', todo)
        self.assertNotIn(TOKEN, todo)
        linea(logs, "TOKEN", estado="obtenido")


class Archivo(SimpleTestCase):
    def test_cada_linea_trae_hora_nivel_y_logger(self):
        """El formato real del archivo: lo que se ve en logs/emida.log."""
        import io

        buffer = io.StringIO()
        manejador = logging.StreamHandler(buffer)
        manejador.setFormatter(bitacora.FormatoEmida(
            "{asctime} {levelname:<7} {name:<12} {message}", style="{"))
        logger = logging.getLogger("tae.emida")
        logger.addHandler(manejador)
        nivel, propaga = logger.level, logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False  # que no salte también a la consola de la suite
        try:
            bitacora.trazar("RES", metodo="topup", invoice="123", http=200, ms=412)
        finally:
            logger.removeHandler(manejador)
            logger.setLevel(nivel)
            logger.propagate = propaga
        self.assertRegex(
            buffer.getvalue(),
            r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}-06:00 INFO\s+tae\.emida\s+"
            r"RES\s+metodo=topup invoice=123 http=200 ms=412\n$",
        )
