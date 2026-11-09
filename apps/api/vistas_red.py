"""
La red: altas, aprobaciones, rechazos y restablecimiento de contraseña.

Mismas reglas que vistas.py: ninguna vista decide permisos ni toca modelos
para cambiar algo. Traducen JSON a argumentos, llaman a la acción y
devuelven el resultado. El permiso lo verifica `apps.identidad.acciones`.

Las respuestas que llevan una contraseña temporal (`credencial_temporal`)
salen con `Cache-Control: no-store`: es la única vez que esa contraseña
existe en claro, y no debe quedarse en la caché del navegador ni de un
proxy. Nunca se escribe en logs.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.core.models import (
    Administrador,
    Cliente,
    EstadoCliente,
    EstadoPuntoVenta,
    PuntoVenta,
)
from apps.identidad import acciones, permisos, restablecimiento
from apps.identidad.models import Rol, Usuario
from apps.saldo import services

from .permisos_http import Operativo
from .vistas import _ip, error, manejar


def _sin_cache(respuesta):
    respuesta["Cache-Control"] = "no-store"
    return respuesta


def _buscar(modelo, request, campo, mensaje):
    """Busca por uuid dentro del inquilino del usuario. Ajeno o inexistente: 403, sin distinguir."""
    entidad = modelo.objects.filter(
        uuid=request.data.get(campo) or request.query_params.get(campo),
        inquilino=request.user.inquilino,
    ).first()
    if entidad is None:
        raise permisos.PermisoDenegado(mensaje)
    return entidad


def _usuario_json(usuario):
    if usuario is None:
        return None
    return {
        "uuid": str(usuario.uuid),
        "correo": usuario.correo,
        "nombre": usuario.nombre,
        "debe_cambiar_contrasena": usuario.debe_cambiar_contrasena,
    }


def _alta_json(entidad, tipo):
    usuario = entidad.usuarios.first()
    return {
        "tipo": tipo,
        "uuid": str(entidad.uuid),
        "nombre": entidad.nombre,
        "estado": getattr(entidad, "estado", "activo"),
        "usuario": _usuario_json(usuario),
        "credencial_temporal": getattr(entidad, "credencial_temporal", None),
    }


# ---------------------------------------------------------------------------
# Consulta de la red
# ---------------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def red(request):
    """
    Lo que el usuario alcanza de un nivel: `?tipo=administrador|cliente|punto_venta`.

    Pensada para la pantalla "Usuarios de la red": identidad y estado. Los
    números de dinero detallados están en /clientes y /puntos-venta.
    """
    usuario = request.user
    tipo = request.query_params.get("tipo", "cliente")

    if tipo == "administrador":
        if usuario.rol != Rol.SUPER_ADMIN:
            raise permisos.PermisoDenegado("Solo el Super Admin ve los administradores")
        filas = Administrador.objects.filter(inquilino=usuario.inquilino)
        return Response([
            {
                "tipo": "administrador",
                "uuid": str(a.uuid),
                "nombre": a.nombre,
                "responsable": a.responsable,
                "estado": "activo" if a.activo else "inactivo",
                "padre": None,
                "saldo": str(services.bolsa_de(a).saldo),
                "usuario": _usuario_json(a.usuarios.first()),
            }
            for a in filas.order_by("nombre")
        ])

    if tipo == "cliente":
        filas = Cliente.objects.filter(inquilino=usuario.inquilino).select_related("administrador")
        if usuario.rol == Rol.ADMINISTRADOR:
            filas = filas.filter(administrador_id=usuario.administrador_id)
        elif usuario.rol != Rol.SUPER_ADMIN:
            raise permisos.PermisoDenegado("Ese rol no ve la lista de clientes")
        return Response([
            {
                "tipo": "cliente",
                "uuid": str(c.uuid),
                "nombre": c.nombre,
                "estado": c.estado,
                "motivo_rechazo": c.motivo_rechazo,
                "padre": c.administrador.nombre,
                "saldo": str(services.bolsa_de(c).saldo),
                "usuario": _usuario_json(c.usuarios.first()),
            }
            for c in filas.order_by("nombre")
        ])

    if tipo == "punto_venta":
        filas = PuntoVenta.objects.filter(inquilino=usuario.inquilino).select_related("cliente")
        if usuario.rol == Rol.ADMINISTRADOR:
            filas = filas.filter(cliente__administrador_id=usuario.administrador_id)
        elif usuario.rol == Rol.CLIENTE:
            filas = filas.filter(cliente_id=usuario.cliente_id)
        elif usuario.rol != Rol.SUPER_ADMIN:
            raise permisos.PermisoDenegado("Una caja no ve la red")
        salida = []
        for p in filas.order_by("cliente__nombre", "nombre"):
            limite = getattr(p, "limite", None)
            salida.append({
                "tipo": "punto_venta",
                "uuid": str(p.uuid),
                "nombre": p.nombre,
                "estado": p.estado,
                "motivo_rechazo": p.motivo_rechazo,
                "padre": p.cliente.nombre,
                "modo_bolsa": p.cliente.modo_bolsa,
                "limite": str(limite.limite) if limite else None,
                "usuario": _usuario_json(p.usuarios.first()),
            })
        return Response(salida)

    return error("tipo debe ser administrador, cliente o punto_venta",
                 status.HTTP_400_BAD_REQUEST, clave="datos")


# ---------------------------------------------------------------------------
# Altas
# ---------------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def alta_administrador(request):
    """Alta de un administrador. Solo el Super Admin; nace activo."""
    administrador = acciones.crear_administrador(
        request.user,
        nombre=request.data.get("nombre", "").strip(),
        responsable=request.data.get("responsable", "").strip(),
        correo_responsable=request.data.get("correo") or None,
    )
    return _sin_cache(Response(_alta_json(administrador, "administrador"),
                               status=status.HTTP_201_CREATED))


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def alta_cliente(request):
    """
    El administrador da de alta en su propia red sin mandar `administrador`;
    el Super Admin tiene que decir en cuál.
    """
    usuario = request.user
    if usuario.rol == Rol.ADMINISTRADOR and not request.data.get("administrador"):
        administrador = usuario.administrador
    else:
        administrador = _buscar(Administrador, request, "administrador",
                                "Ese administrador no existe o no es tuyo")
    cliente = acciones.crear_cliente(
        usuario,
        administrador=administrador,
        nombre=request.data.get("nombre", "").strip(),
        rfc=request.data.get("rfc", "").strip().upper(),
        margen_pct=request.data.get("margen_pct", "0"),
        modo_bolsa=request.data.get("modo_bolsa") or None,
        correo=request.data.get("correo") or None,
    )
    return _sin_cache(Response(_alta_json(cliente, "cliente"), status=status.HTTP_201_CREATED))


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def alta_punto_venta(request):
    """El cliente da de alta en su propia tienda sin mandar `cliente`."""
    usuario = request.user
    if usuario.rol == Rol.CLIENTE and not request.data.get("cliente"):
        cliente = usuario.cliente
    else:
        cliente = _buscar(Cliente, request, "cliente", "Ese cliente no existe o no es tuyo")
    pdv = acciones.crear_punto_venta(
        usuario,
        cliente=cliente,
        nombre=request.data.get("nombre", "").strip(),
        direccion=request.data.get("direccion", "").strip(),
        correo=request.data.get("correo") or None,
        limite=request.data.get("limite") or None,
    )
    return _sin_cache(Response(_alta_json(pdv, "punto_venta"), status=status.HTTP_201_CREATED))


# ---------------------------------------------------------------------------
# Aprobaciones
# ---------------------------------------------------------------------------
def _puede(fn, *args):
    try:
        fn(*args)
        return True
    except permisos.PermisoDenegado:
        return False


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def aprobaciones(request):
    """
    Lo pendiente que ESTE usuario puede aprobar. Alimenta la bandeja y el
    contador del menú. La lista sale de las mismas funciones de permisos que
    usa la acción, para que lo que se ve y lo que se permite no se separen.
    """
    usuario = request.user
    salida = []

    clientes = Cliente.objects.filter(
        inquilino=usuario.inquilino, estado=EstadoCliente.PENDIENTE
    ).select_related("administrador")
    for c in clientes:
        if _puede(permisos.puede_aprobar_cliente, usuario, c):
            salida.append({
                "tipo": "cliente", "uuid": str(c.uuid), "nombre": c.nombre,
                "padre": c.administrador.nombre, "rfc": c.rfc,
                "margen_pct": str(c.margen_pct), "creado": c.creado.isoformat(),
                "usuario": _usuario_json(c.usuarios.first()),
            })

    cajas = PuntoVenta.objects.filter(
        inquilino=usuario.inquilino, estado=EstadoPuntoVenta.PENDIENTE
    ).select_related("cliente")
    for p in cajas:
        if _puede(permisos.puede_aprobar_punto_venta, usuario, p):
            limite = getattr(p, "limite", None)
            salida.append({
                "tipo": "punto_venta", "uuid": str(p.uuid), "nombre": p.nombre,
                "padre": p.cliente.nombre, "direccion": p.direccion,
                "limite": str(limite.limite) if limite else None,
                "creado": p.creado.isoformat(),
                "usuario": _usuario_json(p.usuarios.first()),
            })

    return Response(sorted(salida, key=lambda x: x["creado"]))


def _pendiente(request):
    tipo = request.data.get("tipo")
    if tipo == "cliente":
        return tipo, _buscar(Cliente, request, "uuid", "Ese cliente no existe o no es tuyo")
    if tipo == "punto_venta":
        return tipo, _buscar(PuntoVenta, request, "uuid", "Esa caja no existe o no es tuya")
    raise permisos.PermisoDenegado("tipo debe ser cliente o punto_venta")


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def aprobar(request):
    """Aprueba un cliente o una caja pendiente: {"tipo", "uuid"}."""
    tipo, entidad = _pendiente(request)
    if tipo == "cliente":
        acciones.aprobar_cliente(request.user, entidad)
    else:
        acciones.aprobar_punto_venta(request.user, entidad)
    return Response({"tipo": tipo, "uuid": str(entidad.uuid), "estado": entidad.estado})


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def rechazar(request):
    """Rechaza un cliente o una caja pendiente con motivo: {"tipo", "uuid", "motivo"}."""
    tipo, entidad = _pendiente(request)
    motivo = request.data.get("motivo", "")
    if tipo == "cliente":
        acciones.rechazar_cliente(request.user, entidad, motivo=motivo)
    else:
        acciones.rechazar_punto_venta(request.user, entidad, motivo=motivo)
    return Response({"tipo": tipo, "uuid": str(entidad.uuid), "estado": entidad.estado})


# ---------------------------------------------------------------------------
# Restablecimiento por el superior
# ---------------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def restablecer_usuario(request):
    """
    Genera una contraseña temporal para alguien de abajo en la jerarquía.

    Devuelve la temporal una sola vez. Quien la recibe debe cambiarla al
    entrar, y sus sesiones abiertas se cierran en este momento.
    """
    objetivo = Usuario.objects.filter(
        uuid=request.data.get("usuario"), inquilino=request.user.inquilino
    ).first()
    if objetivo is None:
        raise permisos.PermisoDenegado("Ese usuario no existe o no está bajo tu alcance")
    temporal = restablecimiento.restablecer_por_superior(
        request.user, objetivo, origen=_ip(request)
    )
    return _sin_cache(Response({
        "usuario": _usuario_json(objetivo),
        "credencial_temporal": temporal,
    }))


# ---------------------------------------------------------------------------
# Saldo de los administradores
# ---------------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def fondear_administrador(request):
    """
    El Super Admin asigna saldo a un administrador: {"administrador", "monto"}.

    Es la entrada de dinero a la red y se captura a mano: no sale de Emida
    ni se descuenta del saldo de EPS en Emida (decisión 23). Queda en el
    ledger como compra, con quién la autorizó.
    """
    administrador = _buscar(Administrador, request, "administrador",
                            "Ese administrador no existe o no es tuyo")
    operacion = acciones.fondear_administrador(
        request.user, administrador, request.data.get("monto", "0")
    )
    return Response(
        {"folio": operacion.folio, "administrador": administrador.nombre,
         "saldo": str(services.bolsa_de(administrador).saldo)},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def fondear_cliente(request):
    """
    El Super Admin asigna saldo directo a un cliente: {"cliente", "monto"}.

    Sin margen y sin pasar por el administrador. A una caja no se le
    asigna dinero: se le fija un límite sobre la bolsa de su cliente
    (`puntos-venta/limite`), porque el dinero vive en la bolsa del cliente.
    """
    cliente = _buscar(Cliente, request, "cliente", "Ese cliente no existe o no es tuyo")
    operacion = acciones.fondear_cliente(request.user, cliente, request.data.get("monto", "0"))
    return Response(
        {"folio": operacion.folio, "cliente": cliente.nombre,
         "saldo": str(services.bolsa_de(cliente).saldo)},
        status=status.HTTP_201_CREATED,
    )
