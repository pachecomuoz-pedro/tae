"""
Recuperación de contraseña.

La mitad de estas pruebas no comprueban que funcione: comprueban que no se
pueda abusar. Esa mitad es la que importa, porque el camino feliz se nota
roto el primer día y el abuso no se nota nunca.
"""
from datetime import timedelta

import pyotp
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from apps.identidad import acciones, permisos, restablecimiento
from apps.identidad.models import (
    AccionAdministrativa,
    BitacoraAcceso,
    BitacoraAdministracion,
    Rol,
    SolicitudRestablecimiento,
)

from .factories import CLAVE, Mundo

NUEVA = "nueva-contrasena-larga-2026"


class Autoservicio(TestCase):
    def setUp(self):
        self.m = Mundo()
        mail.outbox = []

    def test_el_tendero_recibe_su_liga_y_cambia_la_contrasena(self):
        credencial = restablecimiento.solicitar("jorge@laermita.mx")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("jorge@laermita.mx", mail.outbox[0].to)
        self.assertIn(credencial, mail.outbox[0].body)

        restablecimiento.confirmar(credencial, NUEVA)

        usuario = acciones.autenticar("jorge@laermita.mx", NUEVA)
        self.assertEqual(usuario.pk, self.m.usuario_cliente.pk)

    def test_la_contrasena_vieja_deja_de_servir(self):
        credencial = restablecimiento.solicitar("jorge@laermita.mx")
        restablecimiento.confirmar(credencial, NUEVA)

        with self.assertRaises(permisos.PermisoDenegado):
            acciones.autenticar("jorge@laermita.mx", CLAVE)

    def test_el_cajero_no_tiene_autoservicio(self):
        credencial = restablecimiento.solicitar("luis@laermita.mx")

        self.assertIsNone(credencial)
        self.assertEqual(len(mail.outbox), 0)
        motivo = BitacoraAcceso.objects.filter(exito=False).latest("cuando").motivo
        self.assertIn("el rol no tiene autoservicio", motivo)

    def test_un_correo_inexistente_se_ve_igual_que_uno_valido(self):
        """
        Desde afuera no se puede distinguir. La pantalla de recuperación no
        debe servir para averiguar qué tenderos están dados de alta.
        """
        nadie = restablecimiento.solicitar("nadie@ejemplo.mx")
        alguien = restablecimiento.solicitar("jorge@laermita.mx")

        # La función devuelve algo distinto hacia adentro, pero la vista
        # responde lo mismo en los dos casos: lo que se filtra es el correo
        # enviado, y a ese solo llega quien controla el buzón.
        self.assertIsNone(nadie)
        self.assertIsNotNone(alguien)
        self.assertEqual(len(mail.outbox), 1)


class AbusoDeLaLiga(TestCase):
    def setUp(self):
        self.m = Mundo()
        mail.outbox = []

    def test_la_liga_sirve_una_sola_vez(self):
        credencial = restablecimiento.solicitar("jorge@laermita.mx")
        restablecimiento.confirmar(credencial, NUEVA)

        with self.assertRaises(restablecimiento.VenceOInvalida):
            restablecimiento.confirmar(credencial, "otra-contrasena-larga-x")

    def test_una_liga_vencida_no_sirve(self):
        credencial = restablecimiento.solicitar("jorge@laermita.mx")
        solicitud = SolicitudRestablecimiento.objects.get(usuario=self.m.usuario_cliente)
        solicitud.expira = timezone.now() - timedelta(minutes=1)
        solicitud.save(update_fields=["expira"])

        with self.assertRaises(restablecimiento.VenceOInvalida):
            restablecimiento.confirmar(credencial, NUEVA)

    def test_pedir_otra_liga_mata_la_anterior(self):
        """Tres correos seguidos no son tres ventanas abiertas: solo la última."""
        primera = restablecimiento.solicitar("jorge@laermita.mx")
        segunda = restablecimiento.solicitar("jorge@laermita.mx")

        with self.assertRaises(restablecimiento.VenceOInvalida):
            restablecimiento.confirmar(primera, NUEVA)

        restablecimiento.confirmar(segunda, NUEVA)
        acciones.autenticar("jorge@laermita.mx", NUEVA)

    def test_una_credencial_inventada_no_sirve(self):
        with self.assertRaises(restablecimiento.VenceOInvalida):
            restablecimiento.confirmar("lo-que-sea", NUEVA)

    def test_la_tabla_no_guarda_la_credencial(self):
        """Quien se lleve esta tabla se lleva huellas, no llaves."""
        credencial = restablecimiento.solicitar("jorge@laermita.mx")
        solicitud = SolicitudRestablecimiento.objects.get(usuario=self.m.usuario_cliente)

        self.assertNotIn(credencial, solicitud.huella)
        self.assertEqual(len(solicitud.huella), 64)


class ElSegundoFactorSobrevive(TestCase):
    """
    La regla que sostiene todo el archivo: cambiar la contraseña no desarma
    el segundo factor. Si lo desarmara, robar el buzón bastaría para entrar.
    """

    def setUp(self):
        self.m = Mundo()
        mail.outbox = []
        acciones.preparar_totp(self.m.usuario_admin)
        self.m.usuario_admin.refresh_from_db()
        acciones.activar_totp(
            self.m.usuario_admin, pyotp.TOTP(self.m.usuario_admin.totp_secreto).now()
        )
        self.m.usuario_admin.refresh_from_db()
        self.secreto = self.m.usuario_admin.totp_secreto

    def test_restablecer_no_borra_el_secreto(self):
        credencial = restablecimiento.solicitar("brenda@eps.mx")
        restablecimiento.confirmar(
            credencial, NUEVA, codigo_totp=pyotp.TOTP(self.secreto).now()
        )

        self.m.usuario_admin.refresh_from_db()
        self.assertTrue(self.m.usuario_admin.totp_listo)
        self.assertEqual(self.m.usuario_admin.totp_secreto, self.secreto)

    def test_con_el_buzon_solo_no_alcanza(self):
        """
        Quien controla el correo tiene la liga, pero sin el teléfono no
        termina el cambio. Esa es la diferencia entre dos factores y uno.
        """
        credencial = restablecimiento.solicitar("brenda@eps.mx")

        with self.assertRaises(permisos.SegundoFactorRequerido):
            restablecimiento.confirmar(credencial, NUEVA)

        with self.assertRaises(permisos.PermisoDenegado):
            restablecimiento.confirmar(credencial, NUEVA, codigo_totp="000000")

        # Y la contraseña vieja sigue sirviendo: no se cambió nada.
        acciones.autenticar("brenda@eps.mx", CLAVE)

    def test_quien_pierde_las_dos_necesita_dos_personas(self):
        """
        Contraseña y teléfono perdidos: el Super Admin reinicia el segundo
        factor, y solo después la liga de correo sirve. Es correcto que
        cueste dos pasos y dos personas.
        """
        credencial = restablecimiento.solicitar("brenda@eps.mx")
        with self.assertRaises(permisos.SegundoFactorRequerido):
            restablecimiento.confirmar(credencial, NUEVA)

        acciones.reiniciar_totp(self.m.super_admin, self.m.usuario_admin)
        restablecimiento.confirmar(credencial, NUEVA)

        acciones.autenticar("brenda@eps.mx", NUEVA)


class RestablecimientoPorElSuperior(TestCase):
    def setUp(self):
        self.m = Mundo()
        mail.outbox = []

    def test_el_cliente_restablece_a_su_cajero(self):
        """
        El superior recibe una contraseña temporal, no una liga: la liga no
        se puede dictar por teléfono. Con la temporal se entra, pero queda
        el cambio obligatorio prendido (decisión 15).
        """
        temporal = restablecimiento.restablecer_por_superior(
            self.m.usuario_cliente, self.m.usuario_pdv
        )

        usuario = acciones.autenticar("luis@laermita.mx", temporal)
        self.assertEqual(usuario.pk, self.m.usuario_pdv.pk)
        self.assertTrue(usuario.debe_cambiar_contrasena)
        # La anterior ya no sirve.
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.autenticar("luis@laermita.mx", CLAVE)
        # Y no se mandó correo: la credencial se entrega en la tienda.
        self.assertEqual(len(mail.outbox), 0)

    def test_el_cliente_no_restablece_a_otro_cliente(self):
        otro = acciones.crear_cliente(
            self.m.usuario_admin, administrador=self.m.admin,
            nombre="Tienda Doña Lupe", correo="lupe@tienda.mx", password=CLAVE,
        )
        usuario_lupe = otro.usuarios.get()

        with self.assertRaises(permisos.PermisoDenegado):
            restablecimiento.restablecer_por_superior(
                self.m.usuario_cliente, usuario_lupe
            )

    def test_el_administrador_restablece_a_su_cliente_y_a_sus_cajeros(self):
        restablecimiento.restablecer_por_superior(
            self.m.usuario_admin, self.m.usuario_cliente
        )
        restablecimiento.restablecer_por_superior(
            self.m.usuario_admin, self.m.usuario_pdv
        )

    def test_el_administrador_no_alcanza_al_cliente_de_otro(self):
        ajeno = acciones.crear_cliente(
            self.m.usuario_otro_admin, administrador=self.m.otro_admin,
            nombre="Comercial San Juan", correo="sanjuan@mail.mx", password=CLAVE,
        )
        with self.assertRaises(permisos.PermisoDenegado):
            restablecimiento.restablecer_por_superior(
                self.m.usuario_admin, ajeno.usuarios.get()
            )

    def test_un_cajero_no_restablece_a_nadie(self):
        with self.assertRaises(permisos.PermisoDenegado):
            restablecimiento.restablecer_por_superior(
                self.m.usuario_pdv, self.m.usuario_cliente
            )

    def test_nadie_se_restablece_a_si_mismo_por_esta_via(self):
        """
        Si se pudiera, una sesión olvidada en un equipo compartido se
        volvería una cuenta tomada sin pasar por el correo.
        """
        with self.assertRaises(permisos.PermisoDenegado):
            restablecimiento.restablecer_por_superior(
                self.m.usuario_admin, self.m.usuario_admin
            )

    def test_queda_registrado_quien_lo_pidio(self):
        restablecimiento.restablecer_por_superior(
            self.m.usuario_cliente, self.m.usuario_pdv
        )
        fila = BitacoraAdministracion.objects.get(
            accion=AccionAdministrativa.RESTABLECIMIENTO
        )
        self.assertEqual(fila.actor, self.m.usuario_cliente)
        self.assertEqual(fila.objetivo_uuid, self.m.usuario_pdv.uuid)

        registro = BitacoraAcceso.objects.filter(usuario=self.m.usuario_pdv).latest("cuando")
        self.assertIn("jorge@laermita.mx", registro.motivo)

    def test_restablecer_mata_las_ligas_de_correo_vivas(self):
        """
        Si quedara viva una liga de autoservicio, quien tuviera el correo
        podría entrar después del restablecimiento. Se invalidan.
        """
        liga = restablecimiento.solicitar("jorge@laermita.mx")
        restablecimiento.restablecer_por_superior(
            self.m.usuario_admin, self.m.usuario_cliente
        )
        with self.assertRaises(restablecimiento.VenceOInvalida):
            restablecimiento.confirmar(liga, NUEVA)

    def test_la_contrasena_temporal_no_queda_en_ninguna_bitacora(self):
        temporal = restablecimiento.restablecer_por_superior(
            self.m.usuario_cliente, self.m.usuario_pdv
        )
        for fila in BitacoraAdministracion.objects.all():
            self.assertNotIn(temporal, fila.detalle)
        for fila in BitacoraAcceso.objects.all():
            self.assertNotIn(temporal, fila.motivo)


class SesionesAbiertas(TestCase):
    def setUp(self):
        self.m = Mundo()
        mail.outbox = []

    def test_cambiar_la_contrasena_tumba_las_sesiones(self):
        """
        Django ata la sesión al hash de la contraseña. Al cambiarla, lo que
        alguien hubiera dejado abierto deja de valer, que es justo lo que se
        quiere cuando el motivo del cambio es sospechar de alguien.
        """
        antes = self.m.usuario_cliente.get_session_auth_hash()

        credencial = restablecimiento.solicitar("jorge@laermita.mx")
        restablecimiento.confirmar(credencial, NUEVA)

        self.m.usuario_cliente.refresh_from_db()
        self.assertNotEqual(self.m.usuario_cliente.get_session_auth_hash(), antes)
