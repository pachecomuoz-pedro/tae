"""
Contraseñas temporales y el cambio obligatorio.

Regla de este archivo: **nadie elige ni conoce la contraseña definitiva de
otra persona**. Quien da de alta o restablece a alguien recibe una
contraseña temporal, la entrega por el canal que ya tiene con esa persona
(en la tienda, por teléfono) y el sistema obliga a cambiarla al entrar.
Mientras no la cambie, la API le niega todo lo demás.

Ver docs/DECISIONES.md, decisión 15.
"""
import secrets

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from . import permisos
from .models import AccionAdministrativa

# Sin 0/O, 1/l/I: la temporal se dicta por teléfono o se copia de una
# pantalla, y un carácter ambiguo es una llamada de vuelta.
ALFABETO = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"

# Doce, igual que el mínimo de AUTH_PASSWORD_VALIDATORS. Una temporal más
# corta que lo que se le exige a la definitiva sería el eslabón débil.
LONGITUD = 12


class ContrasenaNoValida(Exception):
    """La contraseña nueva no cumple las reglas. Trae los motivos en `mensajes`."""

    def __init__(self, mensajes):
        super().__init__(" ".join(mensajes))
        self.mensajes = list(mensajes)


def generar_temporal():
    """Una contraseña aleatoria de un solo uso práctico. No se guarda en claro en ningún lado."""
    return "".join(secrets.choice(ALFABETO) for _ in range(LONGITUD))


def asignar_temporal(usuario):
    """
    Pone una contraseña temporal y prende el cambio obligatorio.

    Devuelve la temporal para mostrarla UNA vez a quien hizo el alta o el
    restablecimiento. No se escribe en logs ni en la bitácora.

    Cambiar el hash invalida las sesiones abiertas del usuario: Django ata la
    sesión al hash. Es lo que se quiere al restablecer por sospecha.
    """
    temporal = generar_temporal()
    usuario.set_password(temporal)
    usuario.debe_cambiar_contrasena = True
    usuario.save(update_fields=["password", "debe_cambiar_contrasena"])
    return temporal


@transaction.atomic
def cambiar(usuario, actual, nueva):
    """
    El usuario cambia su propia contraseña. Apaga el cambio obligatorio.

    Pide la actual aunque la persona ya tenga sesión: una sesión olvidada en
    un equipo compartido no debe bastar para quedarse con la cuenta.

    La nueva pasa por los validadores de Django (12 caracteres mínimo, no
    común, no solo números, no parecida al correo) y no puede ser igual a la
    actual: si lo fuera, la temporal que vio el superior seguiría sirviendo.
    """
    # Import aquí para evitar el ciclo acciones → contrasenas → acciones.
    from .acciones import registrar

    if not usuario.check_password(actual):
        raise permisos.PermisoDenegado("La contraseña actual no es correcta")
    if actual == nueva:
        raise ContrasenaNoValida(["La nueva tiene que ser distinta a la actual."])
    try:
        validate_password(nueva, user=usuario)
    except ValidationError as e:
        raise ContrasenaNoValida(e.messages) from e

    era_obligatorio = usuario.debe_cambiar_contrasena
    usuario.set_password(nueva)
    usuario.debe_cambiar_contrasena = False
    usuario.save(update_fields=["password", "debe_cambiar_contrasena"])
    registrar(
        usuario, AccionAdministrativa.CAMBIO_CONTRASENA, usuario,
        detalle="primer acceso o tras restablecimiento" if era_obligatorio else "voluntario",
    )
    return usuario
