"""
Quién puede hacer qué. Un archivo, todas las reglas.

Están aquí y no repartidas entre las vistas porque un permiso escrito en dos
lugares termina diciendo dos cosas distintas, y el día que cambie una regla
alguien va a actualizar solo una de ellas.

Cada función devuelve None si se puede, o levanta. Levantar y no devolver
False es deliberado: obliga a que quien llama maneje el caso en lugar de
ignorar un booleano por descuido.

Decisiones: 3 (el permiso siempre en medio), 4 (roles), 14 (segundo factor).
"""
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import EstadoCliente, EstadoPuntoVenta, ModoBolsa
from apps.saldo.models import Asiento, TipoOperacion

from .models import Rol


class PermisoDenegado(Exception):
    """El usuario no tiene alcance sobre esa entidad o ese rol no hace eso."""


class SegundoFactorRequerido(Exception):
    """La operación procede, pero exige un código válido antes de aplicarse."""


class TopeDiarioExcedido(Exception):
    """El administrador ya movió hoy lo que tiene permitido mover."""


def _d(valor):
    return Decimal(str(valor))


# --------------------------------------------------------------------------
# Alta y aprobación
# --------------------------------------------------------------------------
def puede_crear_administrador(usuario):
    """Solo el Super Admin da de alta administradores."""
    if usuario.rol != Rol.SUPER_ADMIN:
        raise PermisoDenegado("Solo el Super Admin da de alta administradores")


def puede_crear_cliente(usuario, administrador):
    """
    El Super Admin puede crear clientes en cualquier administrador; un
    administrador solo dentro del suyo.
    """
    if usuario.rol == Rol.SUPER_ADMIN:
        if not usuario.alcanza_administrador(administrador):
            raise PermisoDenegado("Ese administrador es de otro inquilino")
        return
    if usuario.rol == Rol.ADMINISTRADOR:
        if administrador.pk != usuario.administrador_id:
            raise PermisoDenegado("No puedes crear clientes en otro administrador")
        return
    raise PermisoDenegado("Ese rol no da de alta clientes")


def puede_aprobar_cliente(usuario, cliente):
    """
    La aprobación es del administrador. El Super Admin también puede, porque
    es quien destraba cuando el administrador no está.
    """
    if usuario.rol not in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR):
        raise PermisoDenegado("Un cliente no se aprueba a sí mismo")
    if not usuario.alcanza_cliente(cliente):
        raise PermisoDenegado(f"{cliente.nombre} no está bajo tu alcance")


def puede_crear_punto_venta(usuario, cliente):
    """
    El cliente da de alta sus propias cajas y las aprueba. Es lo que permite
    que un tendero abra una caja el sábado sin esperar a nadie.
    """
    if not usuario.alcanza_cliente(cliente):
        raise PermisoDenegado(f"{cliente.nombre} no está bajo tu alcance")
    if usuario.rol == Rol.PUNTO_VENTA:
        raise PermisoDenegado("Una caja no da de alta otras cajas")


def puede_aprobar_punto_venta(usuario, punto_venta):
    """Aprueban (y rechazan) su cliente, su administrador o el Super Admin; nunca la caja."""
    if usuario.rol == Rol.PUNTO_VENTA:
        raise PermisoDenegado("Una caja no se aprueba a sí misma")
    if not usuario.alcanza_punto_venta(punto_venta):
        raise PermisoDenegado(f"{punto_venta.nombre} no está bajo tu alcance")


# --------------------------------------------------------------------------
# Movimientos de saldo
# --------------------------------------------------------------------------
def puede_fondear_administrador(usuario, administrador):
    """Solo el Super Admin carga saldo a un administrador de su inquilino."""
    if usuario.rol != Rol.SUPER_ADMIN:
        raise PermisoDenegado("Solo el Super Admin fondea administradores")
    if not usuario.alcanza_administrador(administrador):
        raise PermisoDenegado("Ese administrador es de otro inquilino")


def puede_fondear_cliente(usuario, cliente):
    """
    Solo el Super Admin asigna saldo directo a un cliente (decisión 23).
    Un administrador le pasa saldo con un traspaso, que sí descuenta de su
    propia bolsa y aplica margen.
    """
    if usuario.rol != Rol.SUPER_ADMIN:
        raise PermisoDenegado("Solo el Super Admin asigna saldo directo a un cliente")
    if not usuario.alcanza_cliente(cliente):
        raise PermisoDenegado("Ese cliente es de otro inquilino")
    if not cliente.puede_operar:
        raise PermisoDenegado(f"{cliente.nombre} no está aprobado o está suspendido")


def traspasado_hoy(administrador):
    """Lo que salió hoy de la bolsa de ese administrador en traspasos."""
    inicio = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    total = Asiento.objects.filter(
        bolsa__administrador=administrador,
        operacion__tipo=TipoOperacion.TRASPASO,
        creado__gte=inicio,
        monto__lt=0,
    ).aggregate(t=Sum("monto"))["t"] or Decimal("0")
    return abs(total)


def puede_traspasar(usuario, cliente, monto, *, totp_verificado=False):
    """
    Tres compuertas, en este orden: alcance, tope diario, segundo factor.

    El tope diario va antes que el segundo factor a propósito. Si alguien ya
    rebasó su límite del día, no tiene caso pedirle un código: la respuesta
    es no, aunque lo teclee bien.
    """
    if usuario.rol not in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR):
        raise PermisoDenegado("Ese rol no traspasa saldo a clientes")
    if not usuario.alcanza_cliente(cliente):
        raise PermisoDenegado(f"{cliente.nombre} no está bajo tu alcance")
    if cliente.estado != EstadoCliente.APROBADO:
        raise PermisoDenegado(
            f"{cliente.nombre} está {cliente.get_estado_display().lower()}"
        )

    monto = _d(monto)
    administrador = cliente.administrador
    tope = _d(settings.TAE["TRASPASO_TOPE_DIARIO"])
    ya = traspasado_hoy(administrador)
    if ya + monto > tope:
        raise TopeDiarioExcedido(
            f"{administrador.nombre} lleva {ya} hoy y el tope diario es {tope}. "
            f"Se avisó al Super Admin."
        )

    umbral = _d(settings.TAE["TRASPASO_UMBRAL_2FA"])
    if monto > umbral and not totp_verificado:
        raise SegundoFactorRequerido(
            f"Un traspaso de {monto} supera el umbral de {umbral} y necesita tu código"
        )


def puede_retirar(usuario, cliente):
    """Devoluciones y ajustes. Mismo alcance que el traspaso."""
    if usuario.rol not in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR):
        raise PermisoDenegado("Ese rol no retira saldo de un cliente")
    if not usuario.alcanza_cliente(cliente):
        raise PermisoDenegado(f"{cliente.nombre} no está bajo tu alcance")


def puede_asignar_limite(usuario, punto_venta):
    """
    Repartir la bolsa entre sus cajas es cosa del cliente. El administrador
    también puede, para ayudarlo por teléfono.
    """
    if usuario.rol == Rol.PUNTO_VENTA:
        raise PermisoDenegado("Una caja no se asigna saldo a sí misma")
    if not usuario.alcanza_punto_venta(punto_venta):
        raise PermisoDenegado(f"{punto_venta.nombre} no está bajo tu alcance")
    if punto_venta.cliente.modo_bolsa != ModoBolsa.ASIGNADA:
        raise PermisoDenegado(
            f"{punto_venta.cliente.nombre} opera en bolsa compartida"
        )


def puede_suspender_cliente(usuario, cliente):
    """Suspenden o reactivan su administrador o el Super Admin."""
    if usuario.rol not in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR):
        raise PermisoDenegado("Ese rol no suspende clientes")
    if not usuario.alcanza_cliente(cliente):
        raise PermisoDenegado(f"{cliente.nombre} no está bajo tu alcance")


# --------------------------------------------------------------------------
# Venta
# --------------------------------------------------------------------------
def puede_vender(usuario, punto_venta):
    """
    Las tres compuertas de la venta, verificadas en el servidor.

    La primera es la que la gente olvida: el usuario tiene que estar asignado
    a ESA caja. Un cajero con credenciales válidas de otra tienda no vende
    aquí, aunque la pantalla se le abra.

    El cliente no vende nunca: siempre vende una caja. Es una regla del
    negocio, no una limitación técnica.
    """
    if usuario.rol == Rol.CLIENTE:
        raise PermisoDenegado(
            "Un cliente no vende: la venta la hace un punto de venta suyo"
        )
    if usuario.rol in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR):
        raise PermisoDenegado("Ese rol administra, no vende")
    if punto_venta.pk != usuario.punto_venta_id:
        raise PermisoDenegado(
            f"{usuario.nombre} no está asignado a {punto_venta.nombre}"
        )
    if punto_venta.estado != EstadoPuntoVenta.ACTIVO:
        raise PermisoDenegado(
            f"{punto_venta.nombre} está {punto_venta.get_estado_display().lower()}"
        )
    if not punto_venta.cliente.puede_operar:
        raise PermisoDenegado(
            f"{punto_venta.cliente.nombre} está "
            f"{punto_venta.cliente.get_estado_display().lower()}"
        )


# --------------------------------------------------------------------------
# Segundo factor
# --------------------------------------------------------------------------
def puede_restablecer_contrasena(usuario, objetivo):
    """
    El camino de arriba hacia abajo: cada nivel restablece al de abajo.

    Es el camino que de verdad se va a usar. Un tendero que no puede vender
    el sábado no espera un correo: le habla a su administrador. Y el cajero
    no tiene otra vía.

    Nadie se restablece a sí mismo por aquí: para eso está el autoservicio,
    que exige el correo. Si un usuario pudiera restablecerse solo desde su
    sesión, una sesión olvidada en un equipo compartido se volvería una
    cuenta tomada.
    """
    if objetivo.pk == usuario.pk:
        raise PermisoDenegado(
            "Para tu propia contraseña usa el restablecimiento por correo"
        )
    if objetivo.inquilino_id != usuario.inquilino_id:
        raise PermisoDenegado("Ese usuario es de otro inquilino")

    if usuario.rol == Rol.SUPER_ADMIN:
        return
    if usuario.rol == Rol.ADMINISTRADOR:
        # Sus clientes y los cajeros de sus clientes.
        if objetivo.rol == Rol.CLIENTE and usuario.alcanza_cliente(objetivo.cliente):
            return
        if objetivo.rol == Rol.PUNTO_VENTA and usuario.alcanza_punto_venta(
            objetivo.punto_venta
        ):
            return
        raise PermisoDenegado(f"{objetivo.nombre} no está bajo tu alcance")
    if usuario.rol == Rol.CLIENTE:
        # Solo sus cajeros.
        if objetivo.rol == Rol.PUNTO_VENTA and usuario.alcanza_punto_venta(
            objetivo.punto_venta
        ):
            return
        raise PermisoDenegado("Un cliente solo restablece a sus propios cajeros")
    raise PermisoDenegado("Una caja no restablece contraseñas")


def puede_reiniciar_totp(usuario, objetivo):
    """
    Cuando alguien pierde el teléfono. Lo resuelve el Super Admin desde su
    panel: con tres o cinco administradores es más simple que repartir
    códigos de respaldo, y queda registrado quién lo hizo.
    """
    if usuario.rol != Rol.SUPER_ADMIN:
        raise PermisoDenegado("Solo el Super Admin reinicia el segundo factor")
    if objetivo.inquilino_id != usuario.inquilino_id:
        raise PermisoDenegado("Ese usuario es de otro inquilino")
    if objetivo.pk == usuario.pk:
        raise PermisoDenegado(
            "No puedes reiniciar tu propio segundo factor: pídeselo a otro Super Admin"
        )


def puede_ver_saldo_proveedor(usuario):
    """
    Solo el Super Admin ve y consulta el saldo de EPS en Emida.

    Es el dinero real de EPS con el proveedor. Para los demás roles no
    significa nada (sus saldos los asigna el Super Admin a mano, decisión
    23) y mostrarlo solo invitaría a confundirlo con el propio.
    """
    if usuario.rol != Rol.SUPER_ADMIN:
        raise PermisoDenegado("Solo el Super Admin consulta el saldo del proveedor")

