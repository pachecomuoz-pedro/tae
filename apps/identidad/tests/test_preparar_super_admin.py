"""El comando que crea al primer Super Admin y activa su segundo factor."""
from io import StringIO
from unittest import mock

import pyotp
from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.core.models import Inquilino
from apps.identidad import acciones
from apps.identidad.models import Rol, Usuario


def codigo_de_la_aplicacion():
    """Hace de teléfono: calcula el código con el secreto que el comando acaba de guardar."""
    usuario = Usuario.objects.get(rol=Rol.SUPER_ADMIN)
    return pyotp.TOTP(usuario.totp_secreto).now()


class PrepararSuperAdmin(TestCase):
    def correr(self, *args, entrada):
        salida = StringIO()
        with mock.patch("builtins.input", side_effect=entrada):
            call_command("preparar_super_admin", *args, stdout=salida, stderr=StringIO())
        return salida.getvalue()

    def test_crea_inquilino_super_admin_con_temporal_y_segundo_factor_activo(self):
        salida = self.correr("--correo", "cesar@eps.mx", "--nombre", "César",
                             entrada=lambda _: codigo_de_la_aplicacion())

        usuario = Usuario.objects.get(correo="cesar@eps.mx")
        self.assertEqual(usuario.rol, Rol.SUPER_ADMIN)
        self.assertTrue(usuario.totp_listo)
        self.assertTrue(usuario.debe_cambiar_contrasena)
        self.assertEqual(usuario.inquilino, Inquilino.objects.get(clave="scorpion"))

        temporal = salida.split("cambiarla):")[1].split()[0]
        acciones.autenticar("cesar@eps.mx", temporal,
                            codigo_totp=pyotp.TOTP(usuario.totp_secreto).now())

    def test_si_el_codigo_nunca_es_correcto_no_queda_nada(self):
        with self.assertRaises(CommandError):
            self.correr("--correo", "cesar@eps.mx", entrada=["000000"] * 3)
        self.assertFalse(Usuario.objects.exists())
        self.assertFalse(Inquilino.objects.exists())

    def test_no_pisa_a_un_usuario_que_ya_existe(self):
        self.correr("--correo", "cesar@eps.mx", entrada=lambda _: codigo_de_la_aplicacion())
        with self.assertRaises(CommandError):
            self.correr("--correo", "cesar@eps.mx", entrada=lambda _: codigo_de_la_aplicacion())
