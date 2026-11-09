"""
Recuperación de contraseña, por los dos caminos.

1. Autoservicio por correo: liga de un solo uso (`solicitar` y `confirmar`).
2. Por el nivel de arriba: contraseña temporal con cambio obligatorio
   (`restablecer_por_superior`).

La regla que sostiene todo este archivo: **restablecer la contraseña no toca
el segundo factor**. Si lo tocara, robar un buzón de correo bastaría para
entrar y el TOTP no serviría de nada. Son dos llaves distintas y se recuperan
por caminos distintos: la contraseña por correo o por el superior, el segundo
factor solo por otro Super Admin.

La consecuencia práctica es incómoda a propósito: quien perdió la contraseña
Y el teléfono necesita dos personas distintas para volver. Es correcto que
cueste.
"""
import hashlib
import secrets

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from . import contrasenas, permisos
from .models import (
    AccionAdministrativa,
    BitacoraAcceso,
    SolicitudRestablecimiento,
    Usuario,
)


def _huella(credencial):
    """
    Solo la huella se guarda. La credencial viaja al correo y no vuelve a
    existir en ningún lado: si la tabla se filtra, no hay nada que usar.
    """
    return hashlib.sha256(credencial.encode()).hexdigest()


def _vigencia():
    return timezone.now() + settings.TAE["RESTABLECIMIENTO_VIGENCIA"]


@transaction.atomic
def _emitir(usuario, *, solicitado_por=None, origen=None):
    """
    Genera el vale e invalida los anteriores de ese usuario.

    Invalidar los previos importa: si alguien pide tres correos seguidos
    porque el primero tardó, los tres primeros quedan muertos y solo sirve
    el último. Dejar varios vivos multiplica las ventanas abiertas sin dar
    nada a cambio.
    """
    SolicitudRestablecimiento.objects.filter(
        usuario=usuario, usado__isnull=True
    ).update(usado=timezone.now())

    credencial = secrets.token_urlsafe(32)
    solicitud = SolicitudRestablecimiento.objects.create(
        usuario=usuario,
        huella=_huella(credencial),
        solicitado_por=solicitado_por,
        origen=origen,
        expira=_vigencia(),
    )
    return solicitud, credencial


# --------------------------------------------------------------------------
# Camino 1: autoservicio por correo
# --------------------------------------------------------------------------
def solicitar(correo, *, origen=None, enviar=True):
    """
    Siempre responde igual, exista el correo o no.

    Quien prueba correos en la pantalla de recuperación no debe poder
    distinguir "no existe" de "ya te mandé el enlace": eso convierte la
    pantalla en un verificador de qué tenderos están dados de alta.

    Devuelve la credencial solo para que las pruebas la puedan usar. En
    producción viaja únicamente en el correo.
    """
    usuario = Usuario.objects.filter(correo__iexact=correo, is_active=True).first()

    if usuario is None or not usuario.puede_autoservicio:
        motivo = (
            "correo desconocido" if usuario is None
            else "el rol no tiene autoservicio"
        )
        BitacoraAcceso.objects.create(
            usuario=usuario, correo_intentado=correo, exito=False,
            motivo=f"restablecimiento no emitido: {motivo}", origen=origen,
        )
        return None

    solicitud, credencial = _emitir(usuario, origen=origen)
    BitacoraAcceso.objects.create(
        usuario=usuario, correo_intentado=correo, exito=True,
        motivo="restablecimiento solicitado por el usuario", origen=origen,
    )

    if enviar:
        horas = int(settings.TAE["RESTABLECIMIENTO_VIGENCIA"].total_seconds() // 3600)
        send_mail(
            subject="Restablece tu contraseña · EPS TAE",
            message=(
                f"Hola {usuario.nombre}:\n\n"
                f"Recibimos una solicitud para cambiar tu contraseña. "
                f"Abre esta liga y elige una nueva:\n\n"
                f"{settings.TAE['URL_BASE']}/restablecer/{credencial}\n\n"
                f"La liga sirve una sola vez y vence en {horas} hora(s).\n"
                f"Si no fuiste tú, ignora este correo: tu contraseña no cambió.\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[usuario.correo],
            fail_silently=False,
        )
    return credencial


# --------------------------------------------------------------------------
# Camino 2: lo restablece el nivel de arriba
# --------------------------------------------------------------------------
@transaction.atomic
def restablecer_por_superior(usuario, objetivo, *, origen=None):
    """
    El administrador para su cliente o sus cajeros, el cliente para sus cajeros.

    Genera una contraseña temporal y la devuelve para que el superior se la
    entregue por el canal que ya tiene con esa persona: en la tienda, por
    teléfono. La plataforma no supone que el cajero tiene correo.

    Por qué una contraseña temporal y no una liga: la liga es larga y no se
    puede dictar por teléfono. La temporal se dicta, y como el superior la
    vio, el sistema obliga a cambiarla al primer acceso (decisión 15).

    Efectos:
    - La contraseña anterior deja de servir y las sesiones abiertas caen
      (Django ata la sesión al hash de la contraseña).
    - Las ligas de autoservicio que estuvieran vivas se invalidan: si no, una
      liga vieja en un correo robado seguiría sirviendo.
    - El segundo factor NO se toca (la regla de este archivo).
    """
    from .acciones import registrar

    permisos.puede_restablecer_contrasena(usuario, objetivo)
    SolicitudRestablecimiento.objects.filter(
        usuario=objetivo, usado__isnull=True
    ).update(usado=timezone.now())

    temporal = contrasenas.asignar_temporal(objetivo)
    BitacoraAcceso.objects.create(
        usuario=objetivo,
        correo_intentado=objetivo.correo,
        exito=True,
        motivo=f"restablecimiento emitido por {usuario.correo}",
        origen=origen,
    )
    registrar(usuario, AccionAdministrativa.RESTABLECIMIENTO, objetivo)
    return temporal


# --------------------------------------------------------------------------
# Consumir el vale
# --------------------------------------------------------------------------
class VenceOInvalida(Exception):
    """La credencial no sirve: ya se usó, venció, o nunca existió."""


@transaction.atomic
def confirmar(credencial, nueva_contrasena, *, codigo_totp=None, origen=None):
    """
    Cambia la contraseña y cierra todas las sesiones abiertas.

    Las sesiones se caen solas: Django las ata al hash de la contraseña, así
    que al cambiarlo, cualquier sesión que alguien hubiera dejado abierta
    deja de valer. Es justo lo que se quiere cuando el motivo del cambio es
    sospechar que alguien más entró.

    Si el usuario tiene segundo factor activo, aquí también se le pide. El
    correo prueba que controla el buzón; el código prueba que tiene el
    teléfono. Con una sola de las dos no alcanza.
    """
    solicitud = SolicitudRestablecimiento.objects.select_for_update().filter(
        huella=_huella(credencial)
    ).first()

    if solicitud is None:
        raise VenceOInvalida("Esa liga no es válida")
    if not solicitud.vigente:
        BitacoraAcceso.objects.create(
            usuario=solicitud.usuario, correo_intentado=solicitud.usuario.correo,
            exito=False, motivo="liga de restablecimiento vencida o ya usada",
            origen=origen,
        )
        raise VenceOInvalida("Esa liga ya se usó o venció")

    objetivo = solicitud.usuario

    if objetivo.totp_listo:
        from .acciones import verificar_totp

        if not codigo_totp:
            raise permisos.SegundoFactorRequerido(
                "Tu cuenta tiene segundo factor: hace falta tu código"
            )
        if not verificar_totp(objetivo, codigo_totp):
            BitacoraAcceso.objects.create(
                usuario=objetivo, correo_intentado=objetivo.correo, exito=False,
                motivo="código incorrecto al restablecer", origen=origen,
            )
            raise permisos.PermisoDenegado("Código incorrecto")

    try:
        validate_password(nueva_contrasena, user=objetivo)
    except ValidationError as e:
        raise contrasenas.ContrasenaNoValida(e.messages) from e

    objetivo.set_password(nueva_contrasena)
    # Quien elige su contraseña por la liga ya no tiene nada temporal.
    objetivo.debe_cambiar_contrasena = False
    objetivo.full_clean(exclude=["password"])
    # El segundo factor NO se toca. Es la regla que sostiene este archivo.
    objetivo.save(update_fields=["password", "debe_cambiar_contrasena"])

    solicitud.usado = timezone.now()
    solicitud.save(update_fields=["usado"])

    BitacoraAcceso.objects.create(
        usuario=objetivo, correo_intentado=objetivo.correo, exito=True,
        motivo="contraseña restablecida", origen=origen,
    )
    return objetivo
