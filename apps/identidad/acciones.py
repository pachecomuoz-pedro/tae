"""
La capa que las vistas van a llamar.

Cada función de aquí hace lo mismo en el mismo orden: verifica el permiso,
ejecuta, y deja registro. `apps.saldo.services` no sabe quién es el usuario
y así debe seguir: ahí vive la contabilidad, aquí la autorización.

Si una vista llama directo a `services`, se salta este archivo y con él las
tres compuertas. Esa es la razón de que exista la separación.

Todo lo que cambia la red (altas, aprobaciones, rechazos, suspensiones,
restablecimientos) deja además una fila en BitacoraAdministracion por medio
de `registrar`. Ver docs/DECISIONES.md, decisión 17.
"""
import secrets
from decimal import Decimal

import pyotp
from django.db import transaction
from django.utils import timezone

from apps.core.models import (
    Administrador,
    Cliente,
    EstadoCliente,
    EstadoPuntoVenta,
    PuntoVenta,
)
from apps.catalogo import servicios as catalogo
from apps.saldo import services

from . import contrasenas, permisos
from .models import (
    AccionAdministrativa,
    BitacoraAcceso,
    BitacoraAdministracion,
    Rol,
    Usuario,
)


# --------------------------------------------------------------------------
# Bitácora de administración
# --------------------------------------------------------------------------
_TIPO_OBJETIVO = {
    Administrador: "administrador",
    Cliente: "cliente",
    PuntoVenta: "punto_venta",
    Usuario: "usuario",
}


def registrar(actor, accion, objetivo, *, detalle=""):
    """
    Deja constancia de un cambio en la red. Nunca recibe contraseñas.

    Se llama dentro de la misma transacción que el cambio: si el cambio se
    revierte, la fila de bitácora también, y la bitácora nunca dice que pasó
    algo que no pasó.
    """
    BitacoraAdministracion.objects.create(
        actor=actor,
        actor_nombre=getattr(actor, "nombre", "") or "sistema",
        inquilino=getattr(objetivo, "inquilino", None) or getattr(actor, "inquilino", None),
        accion=accion,
        objetivo_tipo=_TIPO_OBJETIVO.get(type(objetivo), type(objetivo).__name__.lower()),
        objetivo_uuid=getattr(objetivo, "uuid", None),
        objetivo_nombre=getattr(objetivo, "nombre", "") or str(objetivo),
        detalle=detalle[:240],
    )


def _alta_usuario(correo, rol, *, password, **ancla):
    """
    Crea el usuario de una entidad recién dada de alta.

    Sin `password`, que es el caso de la API, se le asigna una contraseña
    temporal y se le obliga a cambiarla al entrar; la temporal se devuelve
    para mostrarla una sola vez a quien hizo el alta. Con `password` (datos
    de demostración y pruebas) se respeta tal cual y no hay cambio obligatorio.

    Devuelve (usuario, temporal_o_None).
    """
    usuario = Usuario.objects.crear(correo, rol, password=password, **ancla)
    if password:
        return usuario, None
    return usuario, contrasenas.asignar_temporal(usuario)


def _aprueba_quien_crea(usuario):
    """
    Lo que crea quien aprueba nace aprobado; lo que crea el Super Admin
    espera a su administrador (o a su cliente, si es una caja), que es quien
    conoce y responde por esa entidad. Ver docs/DECISIONES.md, decisión 16.
    """
    return usuario.rol != Rol.SUPER_ADMIN


# --------------------------------------------------------------------------
# Alta de la jerarquía
# --------------------------------------------------------------------------
@transaction.atomic
def crear_administrador(usuario, *, nombre, responsable="", correo_responsable=None,
                        password=None):
    """
    Alta de un administrador. Siempre nace activo: solo lo crea el Super Admin.

    Si trae correo, se crea su usuario y la contraseña temporal queda en
    `administrador.credencial_temporal` (atributo en memoria, no se guarda).
    """
    permisos.puede_crear_administrador(usuario)
    administrador = Administrador.objects.create(
        inquilino=usuario.inquilino, nombre=nombre, responsable=responsable
    )
    services.bolsa_de(administrador)  # la bolsa nace con la entidad, en cero
    administrador.credencial_temporal = None
    if correo_responsable:
        _, administrador.credencial_temporal = _alta_usuario(
            correo_responsable,
            Rol.ADMINISTRADOR,
            password=password,
            inquilino=usuario.inquilino,
            administrador=administrador,
            nombre=responsable or nombre,
        )
    registrar(usuario, AccionAdministrativa.ALTA, administrador)
    return administrador


@transaction.atomic
def crear_cliente(usuario, *, administrador, nombre, rfc="", margen_pct="0",
                  modo_bolsa=None, correo=None, password=None, aprobar=None):
    """
    Alta de un cliente.

    Si lo crea su administrador, nace aprobado y puede recibir saldo de
    inmediato: quien lo crea es quien lo aprueba, y pedir una segunda firma
    solo agrega un día de espera sin agregar control, porque el control real
    está en el saldo que se le transfiere, no en el alta.

    Si lo crea el Super Admin, nace pendiente hasta que su administrador lo
    apruebe (`aprobar=None` decide según quién crea; True/False lo fuerzan).

    Con correo, la contraseña temporal queda en `cliente.credencial_temporal`.
    """
    permisos.puede_crear_cliente(usuario, administrador)
    if aprobar is None:
        aprobar = _aprueba_quien_crea(usuario)

    datos = {
        "inquilino": administrador.inquilino,
        "administrador": administrador,
        "nombre": nombre,
        "rfc": rfc,
        "margen_pct": Decimal(str(margen_pct)),
        "estado": EstadoCliente.APROBADO if aprobar else EstadoCliente.PENDIENTE,
    }
    if modo_bolsa:
        datos["modo_bolsa"] = modo_bolsa
    cliente = Cliente.objects.create(**datos)
    services.bolsa_de(cliente)

    cliente.credencial_temporal = None
    if correo:
        _, cliente.credencial_temporal = _alta_usuario(
            correo, Rol.CLIENTE, password=password,
            inquilino=cliente.inquilino, cliente=cliente, nombre=nombre,
        )
    registrar(usuario, AccionAdministrativa.ALTA, cliente,
              detalle="" if aprobar else "pendiente de aprobación")
    return cliente


@transaction.atomic
def aprobar_cliente(usuario, cliente):
    """Aprueba un cliente pendiente o rechazado. Un suspendido se reactiva, no se aprueba."""
    permisos.puede_aprobar_cliente(usuario, cliente)
    if cliente.estado == EstadoCliente.SUSPENDIDO:
        raise permisos.PermisoDenegado(
            "Un cliente suspendido se reactiva, no se aprueba: "
            "la suspensión se levanta a propósito"
        )
    cliente.estado = EstadoCliente.APROBADO
    cliente.motivo_rechazo = ""
    cliente.save(update_fields=["estado", "motivo_rechazo"])
    registrar(usuario, AccionAdministrativa.APROBACION, cliente)
    return cliente


def _exigir_motivo(motivo):
    motivo = (motivo or "").strip()
    if not motivo:
        raise services.MotivoRequerido("Un rechazo lleva motivo")
    return motivo


@transaction.atomic
def rechazar_cliente(usuario, cliente, *, motivo):
    """
    El administrador dice que no a un alta que hizo el Super Admin.

    Solo se rechaza lo pendiente: un cliente que ya operó se suspende, no se
    rechaza, porque tiene historia de dinero. El rechazo es reversible:
    `aprobar_cliente` lo puede aprobar después si se corrigió el problema.
    """
    permisos.puede_aprobar_cliente(usuario, cliente)
    if cliente.estado != EstadoCliente.PENDIENTE:
        raise permisos.PermisoDenegado("Solo se rechaza un cliente pendiente")
    motivo = _exigir_motivo(motivo)
    cliente.estado = EstadoCliente.RECHAZADO
    cliente.motivo_rechazo = motivo
    cliente.save(update_fields=["estado", "motivo_rechazo"])
    registrar(usuario, AccionAdministrativa.RECHAZO, cliente, detalle=motivo)
    return cliente


@transaction.atomic
def crear_punto_venta(usuario, *, cliente, nombre, direccion="", correo=None,
                      password=None, aprobar=None, limite=None):
    """
    Alta de una caja.

    Nace activa si la crea su cliente o su administrador, y pendiente si la
    crea el Super Admin (ver `_aprueba_quien_crea`).

    `limite`, si viene y el cliente opera en bolsa asignada, se reserva en
    el mismo momento del alta aunque la caja quede pendiente: así el cliente
    no puede prometer ese dinero a otra caja mientras se aprueba. Si la caja
    se rechaza, el límite vuelve a cero. Ver docs/DECISIONES.md, decisión 7.
    """
    permisos.puede_crear_punto_venta(usuario, cliente)
    if aprobar is None:
        aprobar = _aprueba_quien_crea(usuario)
    pdv = PuntoVenta.objects.create(
        inquilino=cliente.inquilino,
        cliente=cliente,
        nombre=nombre,
        direccion=direccion,
        estado=EstadoPuntoVenta.ACTIVO if aprobar else EstadoPuntoVenta.PENDIENTE,
    )
    pdv.credencial_temporal = None
    if correo:
        _, pdv.credencial_temporal = _alta_usuario(
            correo, Rol.PUNTO_VENTA, password=password,
            inquilino=cliente.inquilino, punto_venta=pdv, nombre=nombre,
        )
    if limite not in (None, "", 0):
        permisos.puede_asignar_limite(usuario, pdv)
        services.asignar_limite(pdv, limite, autorizado_por=usuario.nombre)
    registrar(usuario, AccionAdministrativa.ALTA, pdv,
              detalle="" if aprobar else "pendiente de aprobación")
    return pdv


@transaction.atomic
def aprobar_punto_venta(usuario, punto_venta):
    """Aprueba una caja pendiente o rechazada."""
    permisos.puede_aprobar_punto_venta(usuario, punto_venta)
    punto_venta.estado = EstadoPuntoVenta.ACTIVO
    punto_venta.estado_previo = ""
    punto_venta.motivo_rechazo = ""
    punto_venta.save(update_fields=["estado", "estado_previo", "motivo_rechazo"])
    registrar(usuario, AccionAdministrativa.APROBACION, punto_venta)
    return punto_venta


@transaction.atomic
def rechazar_punto_venta(usuario, punto_venta, *, motivo):
    """
    Rechaza una caja pendiente y libera lo que tenía reservado como límite.

    Liberar aquí es seguro: una caja pendiente nunca pudo vender, así que su
    límite es solo una promesa, no dinero en vuelo.
    """
    permisos.puede_aprobar_punto_venta(usuario, punto_venta)
    if punto_venta.estado != EstadoPuntoVenta.PENDIENTE:
        raise permisos.PermisoDenegado("Solo se rechaza una caja pendiente")
    motivo = _exigir_motivo(motivo)

    fila = getattr(punto_venta, "limite", None)
    if fila is not None and fila.limite > 0:
        services.asignar_limite(punto_venta, 0, autorizado_por=usuario.nombre)

    punto_venta.estado = EstadoPuntoVenta.RECHAZADO
    punto_venta.motivo_rechazo = motivo
    punto_venta.save(update_fields=["estado", "motivo_rechazo"])
    registrar(usuario, AccionAdministrativa.RECHAZO, punto_venta, detalle=motivo)
    return punto_venta


# --------------------------------------------------------------------------
# Saldo, con el permiso delante
# --------------------------------------------------------------------------
def fondear_administrador(usuario, administrador, monto):
    """El Super Admin carga saldo a un administrador."""
    permisos.puede_fondear_administrador(usuario, administrador)
    return services.fondear_administrador(
        administrador, monto, autorizado_por=usuario.nombre
    )


def fondear_cliente(usuario, cliente, monto):
    """El Super Admin asigna saldo directo a un cliente. Ver services.fondear_cliente."""
    permisos.puede_fondear_cliente(usuario, cliente)
    return services.fondear_cliente(cliente, monto, autorizado_por=usuario.nombre)


def traspasar(usuario, cliente, monto_pagado, *, codigo_totp=None):
    """
    El traspaso de un administrador a un cliente.

    Si el monto supera el umbral, el código es obligatorio. Verificarlo aquí
    y no en la pantalla es lo que hace que sirva: una pantalla se puede
    saltar, esta función no.
    """
    verificado = bool(codigo_totp) and verificar_totp(usuario, codigo_totp)
    permisos.puede_traspasar(
        usuario, cliente, monto_pagado, totp_verificado=verificado
    )
    return services.traspasar_a_cliente(
        cliente.administrador, cliente, monto_pagado, autorizado_por=usuario.nombre
    )


def devolver(usuario, cliente, monto_acreditado, *, motivo, nota=""):
    """Devuelve saldo de un cliente al administrador. Exige motivo del catálogo."""
    permisos.puede_retirar(usuario, cliente)
    return services.devolver(
        cliente, monto_acreditado, motivo=motivo, nota=nota,
        autorizado_por=usuario.nombre,
    )


def ajustar(usuario, cliente, monto, *, motivo, nota="", a_favor=False):
    """Corrige el saldo de un cliente con motivo. A favor tiene tope (TAE_AJUSTE_FAVOR_TOPE)."""
    permisos.puede_retirar(usuario, cliente)
    return services.ajustar(
        cliente, monto, motivo=motivo, nota=nota, a_favor=a_favor,
        autorizado_por=usuario.nombre,
    )


def asignar_limite(usuario, punto_venta, monto):
    """Fija el límite de una caja, con el permiso verificado antes."""
    permisos.puede_asignar_limite(usuario, punto_venta)
    return services.asignar_limite(
        punto_venta, monto, autorizado_por=usuario.nombre
    )


@transaction.atomic
def suspender_cliente(usuario, cliente):
    """Suspende al cliente y, en cascada, a sus cajas. Ver services.suspender_cliente."""
    permisos.puede_suspender_cliente(usuario, cliente)
    resultado = services.suspender_cliente(cliente, autorizado_por=usuario.nombre)
    registrar(usuario, AccionAdministrativa.SUSPENSION, cliente)
    return resultado


@transaction.atomic
def reactivar_cliente(usuario, cliente):
    """Cada caja vuelve al estado que tenía antes de la suspensión, no todas a activo."""
    permisos.puede_suspender_cliente(usuario, cliente)
    resultado = services.reactivar_cliente(cliente, autorizado_por=usuario.nombre)
    registrar(usuario, AccionAdministrativa.REACTIVACION, cliente)
    return resultado


# --------------------------------------------------------------------------
# Retiros a EPS y retenciones (decisión 27)
# --------------------------------------------------------------------------
@transaction.atomic
def retirar_a_eps(usuario, titular, monto, *, motivo, nota="", retener_faltante=False,
                  operacion_origen=None):
    """
    El Super Admin regresa saldo de un administrador o un cliente a EPS.

    Sin `retener_faltante`, solo retira lo disponible y si no alcanza dice
    que no. Con `retener_faltante` es una corrección de fondeo: retira lo
    que haya y el resto queda como retención sobre la rama.

    Devuelve (retirado, operacion_o_None, retencion_o_None).
    """
    permisos.puede_retirar_a_eps(usuario, titular)
    if retener_faltante:
        retirado, operacion, retencion = services.corregir_fondeo(
            titular, monto, motivo=motivo, nota=nota, autorizado_por=usuario.nombre,
            operacion_origen=operacion_origen,
        )
    else:
        operacion = services.retirar_a_eps(
            titular, monto, motivo=motivo, nota=nota, autorizado_por=usuario.nombre,
            operacion_origen=operacion_origen,
        )
        retirado, retencion = services.q(monto), None

    if operacion is not None:
        registrar(usuario, AccionAdministrativa.RETIRO, titular,
                  detalle=f"{operacion.folio} por {retirado}")
    if retencion is not None:
        registrar(usuario, AccionAdministrativa.RETENCION, titular,
                  detalle=f"{retencion.pendiente} retenidos en su red")
    return retirado, operacion, retencion


@transaction.atomic
def cancelar_retencion(usuario, retencion, *, nota):
    """El Super Admin perdona lo que falta de una retención, con nota."""
    permisos.puede_cancelar_retencion(usuario, retencion)
    resultado = services.cancelar_retencion(retencion, nota=nota, autorizado_por=usuario.nombre)
    registrar(usuario, AccionAdministrativa.CANCELA_RETENCION, retencion.titular,
              detalle=f"{resultado.pendiente} perdonados: {nota}")
    return resultado


# --------------------------------------------------------------------------
# Suspensión en todos los niveles y acceso de las personas
# --------------------------------------------------------------------------
@transaction.atomic
def suspender_administrador(usuario, administrador):
    """Detiene toda la red del administrador. Ver services.suspender_administrador."""
    permisos.puede_suspender_administrador(usuario, administrador)
    resultado = services.suspender_administrador(administrador, autorizado_por=usuario.nombre)
    registrar(usuario, AccionAdministrativa.SUSPENSION, administrador)
    return resultado


@transaction.atomic
def reactivar_administrador(usuario, administrador):
    """Levanta la suspensión de un administrador y con ella la de su red."""
    permisos.puede_suspender_administrador(usuario, administrador)
    resultado = services.reactivar_administrador(administrador, autorizado_por=usuario.nombre)
    registrar(usuario, AccionAdministrativa.REACTIVACION, administrador)
    return resultado


@transaction.atomic
def suspender_punto_venta(usuario, punto_venta):
    """Suspende una sola caja."""
    permisos.puede_suspender_punto_venta(usuario, punto_venta)
    resultado = services.suspender_punto_venta(punto_venta, autorizado_por=usuario.nombre)
    registrar(usuario, AccionAdministrativa.SUSPENSION, punto_venta)
    return resultado


@transaction.atomic
def reactivar_punto_venta(usuario, punto_venta):
    """Reactiva una caja suspendida a mano."""
    permisos.puede_suspender_punto_venta(usuario, punto_venta)
    resultado = services.reactivar_punto_venta(punto_venta, autorizado_por=usuario.nombre)
    registrar(usuario, AccionAdministrativa.REACTIVACION, punto_venta)
    return resultado


@transaction.atomic
def cambiar_acceso(usuario, objetivo, *, activo):
    """
    Desactiva o reactiva el acceso de UNA persona sin tocar su entidad: un
    cajero que se fue deja de entrar, y la caja sigue vendiendo con los
    demás cajeros.

    Desactivar cierra sus sesiones abiertas: Django deja de reconocer la
    sesión de un usuario inactivo en la siguiente petición.
    """
    permisos.puede_cambiar_acceso(usuario, objetivo)
    objetivo.is_active = bool(activo)
    objetivo.save(update_fields=["is_active"])
    registrar(
        usuario,
        AccionAdministrativa.REACTIVACION_ACCESO if activo else AccionAdministrativa.DESACTIVACION,
        objetivo,
    )
    return objetivo


def vender(usuario, punto_venta, producto, monto=None):
    """
    Reserva el monto de una venta. Quien la captura o libera es la capa de
    proveedor, que todavía no existe.

    El orden de las cuatro comprobaciones no es casual: primero lo que no
    cuesta nada. El permiso, el monto y la ruta se resuelven con lo que ya
    está en memoria o en una consulta barata. Reservar es lo único que
    bloquea filas y mueve saldo, y por eso va al final. Una venta que iba a
    fallar de todos modos no debe haber tocado el dinero.
    """
    permisos.puede_vender(usuario, punto_venta)
    monto_final = catalogo.validar_monto(producto, monto)
    catalogo.ruta_para(producto)
    return services.reservar(punto_venta, monto_final, producto=producto)


# --------------------------------------------------------------------------
# Segundo factor
# --------------------------------------------------------------------------
def preparar_totp(usuario, *, emisor="EPS TAE"):
    """
    Genera el secreto y devuelve la URI que se pinta como código QR.

    El secreto se guarda pero `totp_activado` sigue vacío: no cuenta como
    activo hasta que la persona teclee un código correcto. Si se diera por
    activo al mostrar el QR, alguien que cierre la ventana antes de escanear
    quedaría fuera de su propia cuenta.
    """
    usuario.totp_secreto = pyotp.random_base32()
    usuario.totp_activado = None
    usuario.save(update_fields=["totp_secreto", "totp_activado"])
    return pyotp.TOTP(usuario.totp_secreto).provisioning_uri(
        name=usuario.correo, issuer_name=emisor
    )


def activar_totp(usuario, codigo):
    """Confirma el alta con el primer código correcto."""
    if not usuario.totp_secreto:
        raise permisos.PermisoDenegado("No hay un segundo factor en proceso de alta")
    if not pyotp.TOTP(usuario.totp_secreto).verify(codigo, valid_window=1):
        return False
    usuario.totp_activado = timezone.now()
    usuario.save(update_fields=["totp_activado"])
    return True


def verificar_totp(usuario, codigo):
    """
    `valid_window=1` acepta el código anterior y el siguiente: treinta
    segundos de margen por relojes que no están perfectamente en hora. Sin
    eso, la mitad de los intentos legítimos fallan y la gente desactiva el
    segundo factor, que es el peor resultado posible.
    """
    if not usuario.totp_listo:
        return False
    return pyotp.TOTP(usuario.totp_secreto).verify(codigo, valid_window=1)


@transaction.atomic
def reiniciar_totp(usuario, objetivo):
    """El Super Admin lo borra cuando alguien perdió el teléfono."""
    permisos.puede_reiniciar_totp(usuario, objetivo)
    objetivo.totp_secreto = ""
    objetivo.totp_activado = None
    objetivo.save(update_fields=["totp_secreto", "totp_activado"])
    BitacoraAcceso.objects.create(
        usuario=objetivo,
        correo_intentado=objetivo.correo,
        exito=True,
        motivo=f"segundo factor reiniciado por {usuario.correo}",
    )
    registrar(usuario, AccionAdministrativa.REINICIO_TOTP, objetivo)
    return objetivo


# --------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------
def autenticar(correo, password, *, codigo_totp=None, origen=None):
    """
    Devuelve el usuario o levanta. Toda entrada, exitosa o no, deja registro.

    La respuesta es la misma para correo inexistente y para contraseña
    incorrecta: decir cuál de los dos falló le confirma a quien prueba
    correos cuáles existen.
    """
    usuario = Usuario.objects.filter(correo__iexact=correo).first()

    def rechazar(motivo):
        BitacoraAcceso.objects.create(
            usuario=usuario, correo_intentado=correo, exito=False,
            motivo=motivo, origen=origen,
        )
        raise permisos.PermisoDenegado("Correo o contraseña incorrectos")

    if usuario is None:
        # Se compara de todos modos para que el tiempo de respuesta no delate
        # si el correo existe.
        Usuario().set_password(secrets.token_urlsafe(16))
        rechazar("correo desconocido")

    if not usuario.check_password(password):
        rechazar("contraseña incorrecta")

    # Igual que con una cuenta pendiente: quien llegó aquí conoce la
    # contraseña, así que se le dice el motivo real en lugar de hacerlo
    # dudar de lo que tecleó.
    if not usuario.is_active:
        BitacoraAcceso.objects.create(
            usuario=usuario, correo_intentado=correo, exito=False,
            motivo="acceso desactivado", origen=origen,
        )
        raise permisos.PermisoDenegado(
            "Tu acceso está desactivado. Habla con quien te dio de alta."
        )

    # Una cuenta cuya entidad está pendiente o rechazada no entra, aunque la
    # contraseña sea correcta. Se dice el motivo real porque quien llegó
    # aquí ya demostró conocer la contraseña: no hay nada que ocultarle.
    estado_ancla = getattr(usuario.ancla, "estado", None)
    if estado_ancla in (EstadoCliente.PENDIENTE, EstadoPuntoVenta.PENDIENTE):
        BitacoraAcceso.objects.create(
            usuario=usuario, correo_intentado=correo, exito=False,
            motivo="cuenta pendiente de aprobación", origen=origen,
        )
        raise permisos.PermisoDenegado(
            "Tu cuenta todavía no está aprobada. Pídele a quien te dio de alta que la apruebe."
        )
    if estado_ancla in (EstadoCliente.RECHAZADO, EstadoPuntoVenta.RECHAZADO):
        BitacoraAcceso.objects.create(
            usuario=usuario, correo_intentado=correo, exito=False,
            motivo="cuenta rechazada", origen=origen,
        )
        raise permisos.PermisoDenegado("El alta de tu cuenta fue rechazada.")

    if usuario.exige_totp_al_entrar:
        if not usuario.totp_listo:
            BitacoraAcceso.objects.create(
                usuario=usuario, correo_intentado=correo, exito=False,
                motivo="segundo factor sin activar", origen=origen,
            )
            raise permisos.SegundoFactorRequerido(
                "Este rol necesita segundo factor y todavía no está activado"
            )
        if not codigo_totp:
            BitacoraAcceso.objects.create(
                usuario=usuario, correo_intentado=correo, exito=False,
                motivo="falta el código", origen=origen,
            )
            raise permisos.SegundoFactorRequerido("Falta tu código de seis dígitos")
        if not verificar_totp(usuario, codigo_totp):
            rechazar("código incorrecto")

    BitacoraAcceso.objects.create(
        usuario=usuario, correo_intentado=correo, exito=True, origen=origen
    )
    return usuario
