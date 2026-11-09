"""
Instalación de primera vez (`manage.py instalar`) y saldo asignado a mano.

La instalación deja solo cuatro usuarios, sin saldo, y trae de Emida el
catálogo real y el saldo de EPS. Aquí Emida es el emulador.
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase

from apps.api import proveedor as puente
from apps.catalogo import servicios as catalogo
from apps.core.models import Inquilino
from apps.identidad import acciones, permisos
from apps.identidad.models import Rol, Usuario
from apps.proveedor.cliente import ClienteProveedor, Config
from apps.proveedor.emulador import Emulador
from apps.proveedor.modelos import ConsultaSaldoProveedor
from apps.saldo import services
from apps.saldo.models import Bolsa, TipoBolsa

from .factories import Mundo

D = Decimal


class Instalar(TestCase):
    def setUp(self):
        self.emulador = Emulador()
        cfg = Config(base_url="https://emida.test", usuario="u", contrasena="p",
                     site_id="9400822", clerk_id="1", merchant_id="77")
        puente.usar_fabrica(lambda: ClienteProveedor(cfg, sesion=self.emulador))
        self.addCleanup(puente.usar_fabrica, None)

    def instalar(self, *args, entorno=None):
        salida = StringIO()
        with mock.patch.dict("os.environ", entorno or {}, clear=False):
            call_command("instalar", "--sin-totp", *args, stdout=salida)
        return salida.getvalue()

    def test_crea_solo_cuatro_usuarios_sin_saldo(self):
        self.instalar("--sin-emida")
        roles = sorted(Usuario.objects.values_list("rol", flat=True))
        self.assertEqual(roles, sorted([Rol.SUPER_ADMIN, Rol.ADMINISTRADOR, Rol.CLIENTE,
                                        Rol.PUNTO_VENTA]))
        self.assertTrue(all(u.debe_cambiar_contrasena for u in Usuario.objects.all()))
        saldos = Bolsa.objects.filter(tipo__in=[TipoBolsa.ADMINISTRADOR, TipoBolsa.CLIENTE])
        self.assertTrue(all(b.saldo == 0 for b in saldos))
        self.assertFalse(ConsultaSaldoProveedor.objects.exists())

    def test_con_clave_inicial_todos_entran_con_ella_y_deben_cambiarla(self):
        clave = "clave-inicial-larga-2026"
        salida = self.instalar("--sin-emida", entorno={"TAE_CLAVE_INICIAL": clave})
        self.assertIn("TAE_CLAVE_INICIAL", salida)
        usuario = acciones.autenticar("luis@laermita.mx", clave)
        self.assertTrue(usuario.debe_cambiar_contrasena)

    def test_sin_clave_cada_uno_recibe_una_aleatoria(self):
        salida = self.instalar("--sin-emida", entorno={"TAE_CLAVE_INICIAL": ""})
        linea = [l for l in salida.splitlines() if "luis@laermita.mx" in l][0]
        temporal = linea.split()[-1]
        acciones.autenticar("luis@laermita.mx", temporal)

    def test_trae_de_emida_el_catalogo_real_y_el_saldo(self):
        salida = self.instalar()
        inquilino = Inquilino.objects.get(clave="scorpion")
        self.assertEqual(catalogo.vendibles(inquilino).count(), 9)
        self.assertEqual(ConsultaSaldoProveedor.objects.get().saldo, D("250000.00"))
        self.assertIn("Saldo de EPS en Emida", salida)

    def test_volver_a_correrlo_no_toca_a_los_usuarios(self):
        self.instalar("--sin-emida")
        hashes = dict(Usuario.objects.values_list("correo", "password"))
        salida = self.instalar("--sin-emida")
        self.assertIn("ya existía", salida)
        self.assertEqual(dict(Usuario.objects.values_list("correo", "password")), hashes)
        self.assertEqual(Usuario.objects.count(), 4)


class SaldoAsignadoPorElSuperAdmin(TestCase):
    """Decisión 23: el Super Admin asigna saldo a mano a cualquiera de la red."""

    def setUp(self):
        self.m = Mundo()

    def test_asigna_directo_a_un_cliente_sin_margen(self):
        antes = services.bolsa_de(self.m.cliente).saldo
        acciones.fondear_cliente(self.m.super_admin, self.m.cliente, D("1000"))
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, antes + D("1000"))

    def test_el_administrador_no_asigna_directo(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.fondear_cliente(self.m.usuario_admin, self.m.cliente, D("1000"))

    def test_por_http_cliente_y_limite_de_caja(self):
        sa = Client()
        sa.force_login(self.m.super_admin)
        r = sa.post("/api/clientes/fondeo", {"cliente": str(self.m.cliente.uuid), "monto": "2000"},
                    content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        r = sa.post("/api/puntos-venta/limite", {"punto_venta": str(self.m.pdv.uuid), "monto": "500"},
                    content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["limite"], "500.0000")
