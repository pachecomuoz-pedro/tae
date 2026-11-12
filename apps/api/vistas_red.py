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
    ModoBolsa,
    PuntoVenta,
)
from apps.identidad import acciones, permisos, restablecimiento
from apps.identidad.models import Rol, Usuario
from apps.saldo import services
from apps.saldo.models import EstadoRetencion, Motivo, Operacion, Retencion

from .permisos_http import Operativo
from .vistas import _ip, clave_de, error, manejar


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
        "activo": usuario.is_active,
    }


def _sin_limite(pdv):
    """Una caja en bolsa asignada sin límite no puede vender: hay que decirlo al crearla."""
    if pdv.cliente.modo_bolsa != ModoBolsa.ASIGNADA:
        return False
    fila = getattr(pdv, "limite", None)
    return fila is None or fila.limite <= 0


def _alta_json(entidad, tipo):
    usuario = entidad.usuarios.first()
    advertencia = ""
    if tipo == "punto_venta" and _sin_limite(entidad):
        advertencia = (
            f"{entidad.cliente.nombre} opera con bolsa por punto de venta y esta caja "
            "no tiene límite: no podrá vender hasta que se le asigne uno."
        )
    return {
        "tipo": tipo,
        "uuid": str(entidad.uuid),
        "nombre": entidad.nombre,
        "estado": getattr(entidad, "estado", "activo"),
        "usuario": _usuario_json(usuario),
        "credencial_temporal": getattr(entidad, "credencial_temporal", None),
        "advertencia": advertencia,
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
                "margen_pct": str(a.margen_pct),
                "estado": a.estado if a.activo else "inactivo",
                "padre": None,
                "saldo": str(services.bolsa_de(a).saldo),
                **services.resumen_saldos(a),
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
                "modo_bolsa": c.modo_bolsa,
                "margen_pct": str(c.margen_pct),
                **services.resumen_saldos(c),
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
            venta = services.estado_de_venta(p)
            salida.append({
                "tipo": "punto_venta",
                "uuid": str(p.uuid),
                "nombre": p.nombre,
                "estado": p.estado,
                "motivo_rechazo": p.motivo_rechazo,
                "padre": p.cliente.nombre,
                "modo_bolsa": p.cliente.modo_bolsa,
                "limite": str(limite.limite) if limite else None,
                "puede_vender": venta["puede"],
                "motivo_no_vende": venta["motivo"],
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
        margen_pct=request.data.get("margen_pct", "0"),
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
            venta = services.estado_de_venta(p)
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
        request.user, administrador, request.data.get("monto", "0"), clave=clave_de(request),
    )
    return Response(
        {"folio": operacion.folio, "administrador": administrador.nombre,
         "saldo": str(services.bolsa_de(administrador).saldo),
         "liquidaciones": [o.folio for o in getattr(operacion, "liquidaciones", [])],
         **services.resumen_saldos(administrador)},
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
    operacion = acciones.fondear_cliente(
        request.user, cliente, request.data.get("monto", "0"), clave=clave_de(request),
    )
    return Response(
        {"folio": operacion.folio, "cliente": cliente.nombre,
         "saldo": str(services.bolsa_de(cliente).saldo),
         "liquidaciones": [o.folio for o in getattr(operacion, "liquidaciones", [])],
         **services.resumen_saldos(cliente)},
        status=status.HTTP_201_CREATED,
    )



# ---------------------------------------------------------------------------
# Retiros a EPS y retenciones (decisión 27)
# ---------------------------------------------------------------------------
def _titular(request):
    """Administrador o cliente, según {"tipo", "uuid"}."""
    tipo = request.data.get("tipo")
    if tipo == "administrador":
        return _buscar(Administrador, request, "uuid", "Ese administrador no existe o no es tuyo")
    if tipo == "cliente":
        return _buscar(Cliente, request, "uuid", "Ese cliente no existe o no es tuyo")
    raise permisos.PermisoDenegado("tipo debe ser administrador o cliente")


def _retencion_json(r):
    titular = r.titular
    return {
        "uuid": str(r.uuid),
        "tipo": "administrador" if r.administrador_id else "cliente",
        "titular": titular.nombre,
        "titular_uuid": str(titular.uuid),
        "monto": str(r.monto),
        "pendiente": str(r.pendiente),
        "estado": r.estado,
        "motivo": r.get_motivo_display(),
        "nota": r.nota,
        "autorizado_por": r.autorizado_por,
        "folio_origen": r.operacion_origen.folio if r.operacion_origen_id else "",
        "creado": r.creado.isoformat(),
        "saldo_red": str(services.saldo_red(titular)),
    }


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def retirar_a_eps(request):
    """
    El Super Admin regresa saldo a EPS:
    {"tipo": "administrador"|"cliente", "uuid", "monto", "motivo", "nota",
     "retener_faltante": bool, "folio_origen": opcional}.

    Con `retener_faltante`, lo que no esté disponible queda como retención
    sobre la red del titular en lugar de rechazarse.
    """
    titular = _titular(request)
    motivo = request.data.get("motivo", "")
    if motivo not in Motivo.values:
        return error("Motivo fuera del catálogo", status.HTTP_400_BAD_REQUEST, clave="motivo")

    origen = None
    folio = (request.data.get("folio_origen") or "").strip().upper()
    if folio:
        origen = Operacion.objects.filter(inquilino=request.user.inquilino, folio=folio).first()
        if origen is None:
            return error(f"No existe el folio {folio}", status.HTTP_400_BAD_REQUEST, clave="datos")

    retirado, operacion, retencion = acciones.retirar_a_eps(
        request.user, titular, request.data.get("monto", "0"),
        motivo=motivo, nota=request.data.get("nota", ""),
        retener_faltante=bool(request.data.get("retener_faltante")),
        operacion_origen=origen, clave=clave_de(request),
    )
    return Response(
        {
            "titular": titular.nombre,
            "retirado": str(retirado),
            "folio": operacion.folio if operacion else None,
            "retencion": _retencion_json(retencion) if retencion else None,
            **services.resumen_saldos(titular),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def retenciones(request):
    """
    Retenciones activas que el usuario alcanza. `?todas=1` incluye las
    liquidadas y canceladas (solo Super Admin).
    """
    usuario = request.user
    filas = Retencion.objects.filter(inquilino=usuario.inquilino).select_related(
        "administrador", "cliente", "operacion_origen"
    )
    if usuario.rol == Rol.SUPER_ADMIN:
        if not request.query_params.get("todas"):
            filas = filas.filter(estado=EstadoRetencion.ACTIVA)
    elif usuario.rol == Rol.ADMINISTRADOR:
        filas = filas.filter(estado=EstadoRetencion.ACTIVA).filter(
            administrador_id=usuario.administrador_id
        ) | filas.filter(
            estado=EstadoRetencion.ACTIVA, cliente__administrador_id=usuario.administrador_id
        )
    elif usuario.rol == Rol.CLIENTE:
        cliente = usuario.cliente
        filas = filas.filter(estado=EstadoRetencion.ACTIVA).filter(
            cliente=cliente
        ) | filas.filter(
            estado=EstadoRetencion.ACTIVA, administrador_id=cliente.administrador_id
        )
    else:
        raise permisos.PermisoDenegado("Una caja no consulta retenciones")
    return Response([_retencion_json(r) for r in filas.order_by("-creado")])


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def cancelar_retencion(request):
    """El Super Admin perdona lo que falta de una retención: {"uuid", "nota"}."""
    retencion = _buscar(Retencion, request, "uuid", "Esa retención no existe")
    resultado = acciones.cancelar_retencion(
        request.user, retencion, nota=request.data.get("nota", "")
    )
    return Response(_retencion_json(resultado))


# ---------------------------------------------------------------------------
# Suspensión por nivel y acceso de las personas
# ---------------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def suspension(request):
    """
    Suspende o, con `reactivar`, reactiva:
    {"tipo": "administrador"|"cliente"|"punto_venta", "uuid", "reactivar": bool}.

    - Administrador: detiene a toda su red (clientes y cajas).
    - Cliente: a él y, en cascada, a sus cajas.
    - Caja: solo a ella.
    """
    tipo = request.data.get("tipo")
    reactivar = bool(request.data.get("reactivar"))
    if tipo == "administrador":
        entidad = _buscar(Administrador, request, "uuid", "Ese administrador no existe o no es tuyo")
        fn = acciones.reactivar_administrador if reactivar else acciones.suspender_administrador
    elif tipo == "cliente":
        entidad = _buscar(Cliente, request, "uuid", "Ese cliente no existe o no es tuyo")
        fn = acciones.reactivar_cliente if reactivar else acciones.suspender_cliente
    elif tipo == "punto_venta":
        entidad = _buscar(PuntoVenta, request, "uuid", "Esa caja no existe o no es tuya")
        fn = acciones.reactivar_punto_venta if reactivar else acciones.suspender_punto_venta
    else:
        raise permisos.PermisoDenegado("tipo debe ser administrador, cliente o punto_venta")
    fn(request.user, entidad)
    entidad.refresh_from_db()
    return Response({"tipo": tipo, "uuid": str(entidad.uuid), "estado": entidad.estado})


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def acceso(request):
    """
    Desactiva o reactiva el acceso de una persona: {"usuario", "activo": bool}.
    No cambia el estado de su caja, cliente o administrador.
    """
    objetivo = Usuario.objects.filter(
        uuid=request.data.get("usuario"), inquilino=request.user.inquilino
    ).first()
    if objetivo is None:
        raise permisos.PermisoDenegado("Ese usuario no existe o no está bajo tu alcance")
    acciones.cambiar_acceso(request.user, objetivo, activo=bool(request.data.get("activo")))
    return Response(_usuario_json(objetivo))



@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def cambiar_margen(request):
    """
    Cambia el margen: {"tipo": "administrador"|"cliente", "uuid", "margen_pct"}.
    Aplica a lo que venga; lo ya fondeado o traspasado conserva el suyo.
    """
    entidad = _titular(request)
    acciones.cambiar_margen(request.user, entidad, request.data.get("margen_pct"))
    return Response({"nombre": entidad.nombre, "margen_pct": str(entidad.margen_pct)})
