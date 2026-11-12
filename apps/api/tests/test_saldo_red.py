"""
Retiro a EPS, retenciones, suspensión por nivel y acceso, entrando por HTTP.

Lo mismo que test_retenciones.py prueba en el motor, aquí se prueba que la
API no lo deje hacer a quien no toca y que devuelva lo que la pantalla
necesita para explicarlo.
"""
from decimal import Decimal

from apps.api import proveedor as puente
from apps.core.models import EstadoAdministrador
from apps.identidad.models import Usuario
from apps.proveedor.emulador import Emulador
from apps.saldo import services

from .test_api import BaseAPI, cliente_con

D = Decimal
VENTA = {"sku": "TEL-TAE-100", "cuenta": "9991234567"}


class BaseSaldoRed(BaseAPI):
    def como(self, correo):
        """El Super Admin entra con segundo factor; aquí se le abre la sesión directo."""
        if correo == "cesar@eps.mx":
            from django.test import Client

            c = Client()
            c.force_login(self.m.super_admin)
            return c
        return super().como(correo)


class RetiroAEPSPorHTTP(BaseSaldoRed):
    def test_el_super_admin_retira_de_un_administrador(self):
        antes = services.bolsa_de(self.m.admin).saldo
        r = self.como("cesar@eps.mx").post(
            "/api/retiros/eps",
            {"tipo": "administrador", "uuid": str(self.m.admin.uuid), "monto": "4",
             "motivo": "fondeo_excedente"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["folio"].startswith("RET-"))
        self.assertEqual(services.bolsa_de(self.m.admin).saldo, antes - D("4"))

    def test_un_administrador_no_retira_hacia_eps(self):
        r = self.como("brenda@eps.mx").post(
            "/api/retiros/eps",
            {"tipo": "cliente", "uuid": str(self.m.cliente.uuid), "monto": "4",
             "motivo": "fondeo_excedente"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_sin_retener_faltante_no_retira_mas_de_lo_disponible(self):
        r = self.como("cesar@eps.mx").post(
            "/api/retiros/eps",
            {"tipo": "cliente", "uuid": str(self.m.cliente.uuid), "monto": "99999",
             "motivo": "fondeo_excedente"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 402)


class RetencionPorHTTP(BaseSaldoRed):
    """
    El cliente tiene 10,400 y su caja 5,000 de límite. Se corrige un fondeo
    de más de 5,350 al cliente: 5,400 están libres... así que se pide más
    de lo libre para forzar la retención.
    """

    def retener(self, monto):
        r = self.como("cesar@eps.mx").post(
            "/api/retiros/eps",
            {"tipo": "cliente", "uuid": str(self.m.cliente.uuid), "monto": monto,
             "motivo": "fondeo_excedente", "retener_faltante": True},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_retira_lo_libre_y_retiene_el_resto(self):
        datos = self.retener("5450")
        self.assertEqual(D(datos["retirado"]), D("5400"))
        self.assertEqual(D(datos["retencion"]["pendiente"]), D("50"))
        self.assertEqual(D(datos["retenido"]), D("50"))

    def test_la_caja_ve_por_que_no_puede_vender_y_la_venta_dice_retencion(self):
        self.retener("10400")  # retira 5,400 y retiene los 5,000 de la caja
        cajero = self.como("luis@laermita.mx")

        saldo = cajero.get("/api/saldo").json()
        self.assertFalse(saldo["venta"]["puede"])
        self.assertIn("retenido", saldo["venta"]["motivo"])

        r = cajero.post("/api/venta", VENTA, content_type="application/json")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.json()["clave"], "retencion")
        self.assertEqual(self.emulador.cobros(), 0)

    def test_el_fondeo_la_liquida_y_la_lista_queda_vacia(self):
        self.retener("10400")
        sa = self.como("cesar@eps.mx")
        self.assertEqual(len(sa.get("/api/retenciones").json()), 1)

        r = sa.post("/api/clientes/fondeo",
                    {"cliente": str(self.m.cliente.uuid), "monto": "5000"},
                    content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.json()["liquidaciones"]), 1)
        self.assertEqual(sa.get("/api/retenciones").json(), [])

        r = self.como("luis@laermita.mx").post("/api/venta", VENTA,
                                              content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_el_cliente_ve_su_retencion_pero_no_la_cancela(self):
        datos = self.retener("10400")
        jorge = self.como("jorge@laermita.mx")
        self.assertEqual(len(jorge.get("/api/retenciones").json()), 1)

        r = jorge.post("/api/retenciones/cancelar",
                       {"uuid": datos["retencion"]["uuid"], "nota": "por favor"},
                       content_type="application/json")
        self.assertEqual(r.status_code, 403)


class DosSaldos(BaseSaldoRed):
    def test_el_cliente_y_el_administrador_ven_disponible_y_saldo_red(self):
        cliente = self.como("jorge@laermita.mx").get("/api/saldo").json()
        self.assertEqual(D(cliente["saldo_red"]), D("10400"))
        self.assertEqual(D(cliente["disponible"]), D("5400"))  # 5,000 están en la caja

        admin = self.como("brenda@eps.mx").get("/api/saldo").json()
        self.assertEqual(
            D(admin["saldo_red"]), D(admin["disponible"]) + D("10400")
        )

    def test_la_caja_no_ve_la_bolsa_de_su_cliente(self):
        datos = self.como("luis@laermita.mx").get("/api/saldo").json()
        self.assertNotIn("retirable", datos)
        self.assertNotIn("saldo_red", datos)
        self.assertEqual(D(datos["mi_caja"]["disponible"]), D("5000"))
        self.assertTrue(datos["venta"]["puede"])


class SuspensionPorHTTP(BaseSaldoRed):
    def test_el_super_admin_suspende_a_un_administrador_y_su_red_deja_de_vender(self):
        sa = self.como("cesar@eps.mx")
        r = sa.post("/api/red/suspension",
                    {"tipo": "administrador", "uuid": str(self.m.admin.uuid)},
                    content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["estado"], EstadoAdministrador.SUSPENDIDO)

        r = self.como("luis@laermita.mx").post("/api/venta", VENTA,
                                              content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("suspendida", r.json()["error"])

        sa.post("/api/red/suspension",
                {"tipo": "administrador", "uuid": str(self.m.admin.uuid), "reactivar": True},
                content_type="application/json")
        r = self.como("luis@laermita.mx").post("/api/venta", VENTA,
                                              content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_un_administrador_no_suspende_administradores(self):
        r = self.como("brenda@eps.mx").post(
            "/api/red/suspension",
            {"tipo": "administrador", "uuid": str(self.m.otro_admin.uuid)},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_el_cliente_suspende_una_de_sus_cajas(self):
        jorge = self.como("jorge@laermita.mx")
        r = jorge.post("/api/red/suspension",
                       {"tipo": "punto_venta", "uuid": str(self.m.pdv.uuid)},
                       content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["estado"], "suspendido")

        saldo = self.como("luis@laermita.mx").get("/api/saldo").json()
        self.assertFalse(saldo["venta"]["puede"])


class AccesoPorHTTP(BaseSaldoRed):
    def test_el_cliente_desactiva_a_un_cajero_y_ya_no_entra(self):
        cajero = Usuario.objects.get(correo="luis@laermita.mx")
        r = self.como("jorge@laermita.mx").post(
            "/api/usuarios/acceso", {"usuario": str(cajero.uuid), "activo": False},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["activo"])

        from django.test import Client
        from apps.identidad.tests.factories import CLAVE

        r = Client().post("/api/auth/entrar",
                          {"correo": "luis@laermita.mx", "password": CLAVE},
                          content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("desactivado", r.json()["error"])

        self.como("jorge@laermita.mx").post(
            "/api/usuarios/acceso", {"usuario": str(cajero.uuid), "activo": True},
            content_type="application/json",
        )
        self.como("luis@laermita.mx")  # vuelve a entrar

    def test_desactivar_cierra_la_sesion_abierta(self):
        sesion = self.como("luis@laermita.mx")
        cajero = Usuario.objects.get(correo="luis@laermita.mx")
        self.como("jorge@laermita.mx").post(
            "/api/usuarios/acceso", {"usuario": str(cajero.uuid), "activo": False},
            content_type="application/json",
        )
        self.assertIn(sesion.get("/api/saldo").status_code, (401, 403))

    def test_nadie_se_desactiva_a_si_mismo(self):
        yo = Usuario.objects.get(correo="jorge@laermita.mx")
        r = self.como("jorge@laermita.mx").post(
            "/api/usuarios/acceso", {"usuario": str(yo.uuid), "activo": False},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_una_caja_no_desactiva_a_nadie(self):
        jorge = Usuario.objects.get(correo="jorge@laermita.mx")
        r = self.como("luis@laermita.mx").post(
            "/api/usuarios/acceso", {"usuario": str(jorge.uuid), "activo": False},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)


class MotivoDelRechazo(BaseSaldoRed):
    def test_la_venta_rechazada_trae_el_mensaje_y_el_codigo_del_proveedor(self):
        self.emulador = Emulador(aprobar=False, response_code="51")
        puente.usar_fabrica(lambda: cliente_con(self.emulador))

        datos = self.como("luis@laermita.mx").post(
            "/api/venta", VENTA, content_type="application/json"
        ).json()
        self.assertEqual(datos["desenlace"], "rechazada")
        self.assertEqual(datos["codigo"], "51")
        self.assertTrue(datos["mensaje"])

    def test_un_rechazo_4xx_tambien_trae_el_motivo(self):
        self.emulador = Emulador(rechazar_http=422)
        puente.usar_fabrica(lambda: cliente_con(self.emulador))

        datos = self.como("luis@laermita.mx").post(
            "/api/venta", VENTA, content_type="application/json"
        ).json()
        self.assertEqual(datos["desenlace"], "rechazada")
        self.assertIn("rechaza a propósito", datos["mensaje"])
        self.assertEqual(datos["codigo"], "HTTP 422")
