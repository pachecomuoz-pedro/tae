"""
Crea el primer Super Admin y activa su segundo factor desde la terminal.

Existe porque el Super Admin no puede entrar sin segundo factor (decisión
14) y la activación necesita a alguien que ya esté dentro. Este comando
rompe ese círculo: se corre en el servidor, donde solo entra quien
administra la plataforma.

Uso:
    python manage.py preparar_super_admin --correo cesar@eps.mx --nombre "César González"

Con un usuario que ya existe, para (re)activar solo su segundo factor:
    python manage.py preparar_super_admin --correo cesar@eps.mx --solo-totp

--solo-totp sirve para el Super Admin que perdió el teléfono y para los
administradores, que necesitan segundo factor para traspasos sobre el
umbral y todavía no tienen pantalla para activarlo.
"""
import io

import pyotp
import qrcode
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Inquilino
from apps.identidad import acciones, contrasenas
from apps.identidad.models import AccionAdministrativa, Rol, Usuario

INTENTOS = 3


class Command(BaseCommand):
    """Ver el docstring del módulo."""
    help = "Crea el Super Admin (con contraseña temporal) y activa su segundo factor."

    def add_arguments(self, parser):
        parser.add_argument("--correo", required=True)
        parser.add_argument("--nombre", default="Super Admin")
        parser.add_argument("--inquilino", default="scorpion",
                            help="Clave del inquilino. Se crea si no existe.")
        parser.add_argument("--nombre-inquilino", default="Scorpion")
        parser.add_argument("--solo-totp", action="store_true",
                            help="No crea nada: vuelve a generar el segundo factor de un Super Admin existente.")

    @transaction.atomic
    def handle(self, *args, **op):
        """
        Todo en una transacción: si el código del segundo factor no se
        confirma, no queda ni el usuario ni el inquilino a medias, y se puede
        volver a correr desde cero.
        """
        correo = op["correo"].strip().lower()
        existente = Usuario.objects.filter(correo__iexact=correo).first()

        if op["solo_totp"]:
            if existente is None or existente.rol not in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR):
                raise CommandError(f"{correo} no es un Super Admin ni un administrador existente")
            usuario, temporal = existente, None
        else:
            if existente is not None:
                raise CommandError(
                    f"{correo} ya existe. Si perdió el teléfono usa --solo-totp; "
                    "si olvidó la contraseña, usa `manage.py changepassword`."
                )
            usuario, temporal = self._crear(correo, op)

        self._activar_totp(usuario)

        self.stdout.write(self.style.SUCCESS(f"\nSegundo factor activo para {usuario.correo}"))
        if temporal:
            # Única vez que la temporal aparece. No se guarda en ningún log.
            self.stdout.write(
                "\nContraseña temporal (se muestra UNA vez; al entrar se pide cambiarla):\n"
                f"    {temporal}\n"
            )

    def _crear(self, correo, op):
        inquilino, creado = Inquilino.objects.get_or_create(
            clave=op["inquilino"], defaults={"nombre": op["nombre_inquilino"]}
        )
        if creado:
            self.stdout.write(f"Inquilino creado: {inquilino.nombre} ({inquilino.clave})")

        usuario = Usuario.objects.create_superuser(
            correo, password=None, inquilino=inquilino, nombre=op["nombre"]
        )
        temporal = contrasenas.asignar_temporal(usuario)
        acciones.registrar(None, AccionAdministrativa.ALTA, usuario,
                           detalle="Super Admin creado desde la terminal")
        return usuario, temporal

    def _activar_totp(self, usuario):
        """Muestra el QR, pide el primer código y lo activa. Sin código correcto no queda activo."""
        uri = acciones.preparar_totp(usuario)
        secreto = pyotp.parse_uri(uri).secret

        self.stdout.write(
            "\nEscanea este código con tu aplicación de autenticación "
            "(Google Authenticator, Microsoft Authenticator, 1Password...):\n"
        )
        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        # Se arma en memoria y se escribe de una vez: el envoltorio de salida
        # de Django agrega un salto de línea en cada write y deformaría el QR.
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        self.stdout.write(buf.getvalue(), ending="")
        self.stdout.write(f"\nSi no puedes escanear, captura esta clave a mano: {secreto}\n")

        for intento in range(1, INTENTOS + 1):
            codigo = input("Código de seis dígitos que muestra la aplicación: ").strip()
            if acciones.activar_totp(usuario, codigo):
                acciones.registrar(None, AccionAdministrativa.REINICIO_TOTP, usuario,
                                   detalle="segundo factor activado desde la terminal")
                return
            self.stderr.write(f"Código incorrecto ({intento}/{INTENTOS}).")

        raise CommandError(
            "No se activó el segundo factor y no se guardó nada. Revisa que la hora "
            "del teléfono y del servidor sean correctas y vuelve a correr el comando."
        )
