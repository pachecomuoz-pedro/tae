"""
La red por HTTP: altas, aprobaciones, restablecimiento y cambio obligatorio.

Igual que test_api.py, la idea es que la API no sea una puerta trasera: lo
que ya se probó a nivel de función se vuelve a probar entrando por HTTP.
"""
from decimal import Decimal

from django.test import Client, TestCase

from apps.core.models import EstadoCliente
from apps.identidad import acciones
from apps.identidad.tests.factories import CLAVE, Mundo

D = Decimal
NUEVA = "otra-contrasena-larga-2026"


class BaseRed(TestCase):
    def setUp(self):
        self.m = Mundo()
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("10000"))

    def como(self, correo, password=CLAVE):
        c = Client()
        r = c.post("/api/auth/entrar", {"correo": correo, "password": password},
                   content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        return c

    def post(self, c, ruta, datos):
        return c.post(ruta, datos, content_type="application/json")


class CambioObligatorio(BaseRed):
    def setUp(self):
        super().setUp()
        r = self.post(self.como("jorge@laermita.mx"), "/api/puntos-venta/alta",
                      {"nombre": "Caja 3", "correo": "caja3@laermita.mx"})
        self.assertEqual(r.status_code, 201, r.content)
        self.temporal = r.json()["credencial_temporal"]

    def test_con_temporal_solo_se_puede_cambiar_la_contrasena(self):
        c = self.como("caja3@laermita.mx", self.temporal)

        yo = c.get("/api/yo").json()
        self.assertTrue(yo["debe_cambiar_contrasena"])

        r = c.get("/api/saldo")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["clave"], "cambiar_contrasena")

        r = self.post(c, "/api/venta", {"sku": "TEL-TAE-100", "cuenta": "9991234567"})
        self.assertEqual(r.status_code, 403)

    def test_al_cambiarla_sigue_la_sesion_y_ya_opera(self):
        c = self.como("caja3@laermita.mx", self.temporal)
        r = self.post(c, "/api/auth/cambiar", {"actual": self.temporal, "nueva": NUEVA})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["debe_cambiar_contrasena"])

        # La misma sesión sigue viva: no hay que volver a entrar.
        self.assertEqual(c.get("/api/saldo").status_code, 200)

    def test_una_nueva_debil_devuelve_400_con_los_motivos(self):
        c = self.como("caja3@laermita.mx", self.temporal)
        r = self.post(c, "/api/auth/cambiar", {"actual": self.temporal, "nueva": "12345"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["clave"], "contrasena")
        self.assertTrue(r.json()["motivos"])


class Altas(BaseRed):
    def test_la_temporal_sale_sin_cache(self):
        r = self.post(self.como("brenda@eps.mx"), "/api/clientes/alta",
                      {"nombre": "Abarrotes El Sol", "correo": "sol@tienda.mx",
                       "margen_pct": "4"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r["Cache-Control"], "no-store")
        self.assertEqual(r.json()["estado"], EstadoCliente.APROBADO)
        self.assertEqual(len(r.json()["credencial_temporal"]), 12)

    def test_un_correo_repetido_devuelve_400_y_no_crea_nada(self):
        r = self.post(self.como("brenda@eps.mx"), "/api/clientes/alta",
                      {"nombre": "Repetido", "correo": "jorge@laermita.mx"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["clave"], "datos")
        self.assertFalse(self.m.admin.clientes.filter(nombre="Repetido").exists())

    def test_un_administrador_no_da_de_alta_administradores(self):
        r = self.post(self.como("brenda@eps.mx"), "/api/administradores/alta",
                      {"nombre": "Otro"})
        self.assertEqual(r.status_code, 403)

    def test_un_administrador_no_ve_la_lista_de_administradores(self):
        r = self.como("brenda@eps.mx").get("/api/red?tipo=administrador")
        self.assertEqual(r.status_code, 403)


class AprobacionesPorHTTP(BaseRed):
    def setUp(self):
        super().setUp()
        self.pendiente = acciones.crear_cliente(
            self.m.super_admin, administrador=self.m.admin, nombre="Tienda Doña Lupe"
        )

    def test_el_administrador_ve_y_aprueba_lo_de_su_red(self):
        c = self.como("brenda@eps.mx")
        lista = c.get("/api/aprobaciones").json()
        self.assertEqual([x["nombre"] for x in lista], ["Tienda Doña Lupe"])

        r = self.post(c, "/api/aprobaciones/aprobar",
                      {"tipo": "cliente", "uuid": str(self.pendiente.uuid)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["estado"], EstadoCliente.APROBADO)

    def test_el_administrador_de_otra_red_no_lo_ve_ni_lo_aprueba(self):
        c = self.como("raul@eps.mx")
        self.assertEqual(c.get("/api/aprobaciones").json(), [])
        r = self.post(c, "/api/aprobaciones/aprobar",
                      {"tipo": "cliente", "uuid": str(self.pendiente.uuid)})
        self.assertEqual(r.status_code, 403)

    def test_rechazar_sin_motivo_devuelve_400(self):
        r = self.post(self.como("brenda@eps.mx"), "/api/aprobaciones/rechazar",
                      {"tipo": "cliente", "uuid": str(self.pendiente.uuid), "motivo": ""})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["clave"], "motivo")


class RestablecerPorHTTP(BaseRed):
    def test_el_cliente_restablece_a_su_cajero_y_el_cajero_debe_cambiarla(self):
        r = self.post(self.como("jorge@laermita.mx"), "/api/usuarios/restablecer",
                      {"usuario": str(self.m.usuario_pdv.uuid)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r["Cache-Control"], "no-store")
        temporal = r.json()["credencial_temporal"]

        c = self.como("luis@laermita.mx", temporal)
        self.assertEqual(c.get("/api/saldo").status_code, 403)

    def test_el_cliente_no_restablece_a_un_cajero_ajeno(self):
        ajena = acciones.crear_punto_venta(
            self.m.super_admin, cliente=self.m.ajeno, nombre="Caja Ajena",
            correo="ajena@roble.mx", password=CLAVE, aprobar=True,
        )
        r = self.post(self.como("jorge@laermita.mx"), "/api/usuarios/restablecer",
                      {"usuario": str(ajena.usuarios.get().uuid)})
        self.assertEqual(r.status_code, 403)

    def test_restablecer_cierra_las_sesiones_abiertas_del_cajero(self):
        sesion_cajero = self.como("luis@laermita.mx")
        self.assertEqual(sesion_cajero.get("/api/saldo").status_code, 200)

        self.post(self.como("jorge@laermita.mx"), "/api/usuarios/restablecer",
                  {"usuario": str(self.m.usuario_pdv.uuid)})

        self.assertIn(sesion_cajero.get("/api/saldo").status_code, (401, 403))


class FondeoPorHTTP(BaseRed):
    """El Super Admin asigna saldo a mano a un administrador (decisión 23)."""

    def test_el_super_admin_fondea_y_el_administrador_no(self):
        from apps.saldo import services

        antes = services.bolsa_de(self.m.admin).saldo
        sa = Client()
        sa.force_login(self.m.super_admin)
        r = self.post(sa, "/api/administradores/fondeo",
                      {"administrador": str(self.m.admin.uuid), "monto": "5000"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(services.bolsa_de(self.m.admin).saldo, antes + D("5000"))

        r = self.post(self.como("brenda@eps.mx"), "/api/administradores/fondeo",
                      {"administrador": str(self.m.admin.uuid), "monto": "5000"})
        self.assertEqual(r.status_code, 403)
