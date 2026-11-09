"""
La API que consumen las pantallas.

Regla sin excepciones en este archivo: **ninguna vista llama a
`apps.saldo.services` ni a los modelos para mover algo**. Todo pasa por
`apps.identidad.acciones`, que es donde viven las tres compuertas.

Las vistas solo hacen tres cosas: traducir JSON a argumentos, llamar a la
acción, y traducir la excepción a un código HTTP. Si alguna vez una vista
necesita "solo esta vez" saltarse una acción, lo que hay que cambiar es la
acción.

Los códigos de error no son decorativos. 403 es que no te toca, 409 es que
la regla de negocio lo impide, 402 es que no alcanza el saldo. La pantalla
decide qué decirle al cajero según cuál llegue, y un 500 genérico la deja
sin nada que decir.

Decisión que gobierna este archivo: 3 (vista → acción → servicio).
"""
import logging

from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.catalogo import servicios as catalogo
from apps.catalogo.models import ProductoLogico
from apps.core.models import Cliente, PuntoVenta
from apps.identidad import acciones, contrasenas, permisos, restablecimiento
from apps.proveedor.venta import ProveedorNoDisponible
from apps.saldo import services
from apps.saldo.models import Motivo

from .permisos_http import Operativo
from .proveedor import cerrar_con_proveedor

log = logging.getLogger("tae.api")


# ---------------------------------------------------------------------------
# Traducción de errores
# ---------------------------------------------------------------------------
def error(mensaje, codigo, *, clave=""):
    """Respuesta de error con la forma única de la API: {"error": mensaje, "clave": tipo}."""
    return Response({"error": str(mensaje), "clave": clave}, status=codigo)


def manejar(fn):
    """
    Un solo lugar que convierte excepciones de negocio en códigos HTTP.

    Está centralizado para que todas las pantallas reciban la misma forma
    ante el mismo problema. Repartido por las vistas, cada una inventa su
    propio contrato y el frontend termina con un `if` por endpoint.
    """

    def envuelto(request, *args, **kwargs):
        try:
            return fn(request, *args, **kwargs)
        except permisos.PermisoDenegado as e:
            return error(e, status.HTTP_403_FORBIDDEN, clave="permiso")
        except permisos.SegundoFactorRequerido as e:
            return error(e, status.HTTP_401_UNAUTHORIZED, clave="segundo_factor")
        except permisos.TopeDiarioExcedido as e:
            return error(e, status.HTTP_409_CONFLICT, clave="tope_diario")
        except services.SaldoInsuficiente as e:
            return error(e, status.HTTP_402_PAYMENT_REQUIRED, clave="saldo")
        except services.LimiteExcedido as e:
            return error(e, status.HTTP_409_CONFLICT, clave="limite")
        except services.MotivoRequerido as e:
            return error(e, status.HTTP_400_BAD_REQUEST, clave="motivo")
        except services.OperacionNoPermitida as e:
            return error(e, status.HTTP_409_CONFLICT, clave="operacion")
        except catalogo.MontoInvalido as e:
            return error(e, status.HTTP_400_BAD_REQUEST, clave="monto")
        except catalogo.SinRuta as e:
            return error(e, status.HTTP_503_SERVICE_UNAVAILABLE, clave="sin_ruta")
        except catalogo.ProductoNoDisponible as e:
            return error(e, status.HTTP_404_NOT_FOUND, clave="producto")
        except restablecimiento.VenceOInvalida as e:
            return error(e, status.HTTP_410_GONE, clave="liga")
        except contrasenas.ContrasenaNoValida as e:
            return Response(
                {"error": str(e), "clave": "contrasena", "motivos": e.mensajes},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ProveedorNoDisponible as e:
            # La venta no salió y la reserva ya se liberó: 503 y "reintenta".
            return error(e, status.HTTP_503_SERVICE_UNAVAILABLE, clave="proveedor")
        except ValidationError as e:
            # Datos de alta que el modelo no acepta, típicamente un correo
            # que ya existe. Se devuelven los mensajes tal cual, por campo.
            mensajes = getattr(e, "message_dict", None) or {"__all__": e.messages}
            primero = next(iter(mensajes.values()))[0]
            return Response(
                {"error": primero, "clave": "datos", "campos": mensajes},
                status=status.HTTP_400_BAD_REQUEST,
            )

    envuelto.__name__ = fn.__name__
    return envuelto


def _ip(request):
    return request.META.get("REMOTE_ADDR") or None


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([AllowAny])
@manejar
def entrar(request):
    """Inicia sesión. Puede pedir el segundo factor (401, clave segundo_factor)."""
    usuario = acciones.autenticar(
        request.data.get("correo", ""),
        request.data.get("password", ""),
        codigo_totp=request.data.get("codigo_totp"),
        origen=_ip(request),
    )
    django_login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
    return Response(_perfil(usuario))


@api_view(["POST"])
@permission_classes([IsAuthenticated])  # salir siempre se puede, aun con temporal
def salir(request):
    """Cierra la sesión. Siempre permitido, aun con contraseña temporal."""
    django_logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([AllowAny])
@manejar
def pedir_restablecimiento(request):
    """
    Siempre 202, exista o no el correo. La pantalla de recuperación no debe
    servir para averiguar qué correos están dados de alta.
    """
    restablecimiento.solicitar(request.data.get("correo", ""), origen=_ip(request))
    return Response(status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
@permission_classes([AllowAny])
@manejar
def confirmar_restablecimiento(request):
    """Consume la liga del correo y fija la contraseña nueva. 410 si la liga venció o ya se usó."""
    restablecimiento.confirmar(
        request.data.get("credencial", ""),
        request.data.get("password", ""),
        codigo_totp=request.data.get("codigo_totp"),
        origen=_ip(request),
    )
    return Response(status=status.HTTP_204_NO_CONTENT)


def _perfil(usuario):
    """
    Lo que la pantalla necesita para dibujarse: quién es y qué puede.

    Las capacidades se mandan para que el frontend sepa qué botones pintar.
    No son el permiso: el permiso se verifica otra vez en el servidor cuando
    se aprieta el botón.
    """
    return {
        "uuid": str(usuario.uuid),
        "nombre": usuario.nombre,
        "correo": usuario.correo,
        "rol": usuario.rol,
        "rol_nombre": usuario.get_rol_display(),
        "entidad": getattr(usuario.ancla, "nombre", ""),
        "totp_listo": usuario.totp_listo,
        # Si es True, la pantalla muestra solo el formulario de cambio: la
        # API niega todo lo demás hasta que se cambie (permisos_http.Operativo).
        "debe_cambiar_contrasena": usuario.debe_cambiar_contrasena,
        "capacidades": {
            "vender": usuario.rol == "punto_venta",
            "traspasar": usuario.rol in ("super_admin", "administrador"),
            "retirar": usuario.rol in ("super_admin", "administrador"),
            "asignar_limite": usuario.rol in ("super_admin", "administrador", "cliente"),
            "crear_cliente": usuario.rol in ("super_admin", "administrador"),
            "crear_punto_venta": usuario.rol in ("super_admin", "administrador", "cliente"),
            "crear_administrador": usuario.rol == "super_admin",
            "ver_red": usuario.rol in ("super_admin", "administrador", "cliente"),
            "aprobar": usuario.rol in ("super_admin", "administrador", "cliente"),
            "restablecer": usuario.rol in ("super_admin", "administrador", "cliente"),
        },
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])  # el perfil dice si hay que cambiar la contraseña
def yo(request):
    """Perfil del usuario en sesión: rol, capacidades y si debe cambiar su contraseña."""
    return Response(_perfil(request.user))


@api_view(["POST"])
@permission_classes([IsAuthenticated])  # justo lo que puede hacer quien trae temporal
@manejar
def cambiar_contrasena(request):
    """
    El usuario cambia su propia contraseña; apaga el cambio obligatorio.

    Cambiar el hash invalidaría la sesión con la que se hizo el cambio;
    `update_session_auth_hash` la conserva para que la persona no tenga que
    volver a entrar. Las demás sesiones de ese usuario sí se caen.
    """
    usuario = contrasenas.cambiar(
        request.user,
        request.data.get("actual", ""),
        request.data.get("nueva", ""),
    )
    update_session_auth_hash(request, usuario)
    return Response(_perfil(usuario))


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
def _producto_json(p):
    return {
        "sku": p.sku,
        "nombre": p.nombre,
        "carrier": p.carrier.nombre,
        "tipo": p.tipo,
        "tipo_monto": p.tipo_monto,
        "monto_fijo": str(p.monto_fijo) if p.monto_fijo is not None else None,
        "monto_min": str(p.monto_min) if p.monto_min is not None else None,
        "monto_max": str(p.monto_max) if p.monto_max is not None else None,
        "multiplo": str(p.multiplo) if p.multiplo is not None else None,
        "etiqueta_monto": p.etiqueta_monto,
        "codigo_barras": p.codigo_barras,
    }


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def listar_catalogo(request):
    """Productos que la caja puede vender ahora (con ruta a un proveedor). `?tipo=` filtra."""
    tipo = request.query_params.get("tipo") or None
    productos = catalogo.vendibles(request.user.inquilino, tipo)
    return Response([_producto_json(p) for p in productos])


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def buscar_por_codigo(request, codigo):
    """Resuelve un código de barras escaneado a su producto."""
    producto = catalogo.por_codigo_barras(request.user.inquilino, codigo)
    return Response(_producto_json(producto))


# ---------------------------------------------------------------------------
# Venta
# ---------------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def vender(request):
    """
    La venta completa: reserva, envío al proveedor y cierre.

    La respuesta lleva el desenlace tal cual, incluido `sin_resolver`. La
    pantalla NO debe traducir eso a "aprobada" ni a "rechazada": son tres
    estados distintos y el cajero tiene que ver el que es.
    """
    punto_venta = PuntoVenta.objects.filter(
        uuid=request.data.get("punto_venta"), inquilino=request.user.inquilino
    ).first()
    if punto_venta is None:
        # Un cajero siempre vende en la suya; se la resolvemos.
        punto_venta = request.user.punto_venta
    if punto_venta is None:
        return error("Falta el punto de venta", status.HTTP_400_BAD_REQUEST)

    producto = ProductoLogico.objects.filter(
        sku=str(request.data.get("sku", "")).upper(), inquilino=request.user.inquilino
    ).first()
    if producto is None:
        return error("Producto desconocido", status.HTTP_404_NOT_FOUND, clave="producto")

    reserva = acciones.vender(
        request.user, punto_venta, producto, request.data.get("monto")
    )
    cierre = cerrar_con_proveedor(
        reserva,
        account_id=request.data.get("cuenta", ""),
        producto=producto,
    )

    cuerpo = {
        "folio": reserva.operacion.folio,
        "invoice_no": reserva.operacion.invoice_no,
        "desenlace": cierre.desenlace,
        "monto": str(reserva.monto),
        "detalle": cierre.detalle,
        "autorizacion": getattr(cierre.respuesta, "control_no", "") or "",
    }
    codigos = {
        "aprobada": status.HTTP_201_CREATED,
        "rechazada": status.HTTP_200_OK,
        "sin_resolver": status.HTTP_202_ACCEPTED,
    }
    return Response(cuerpo, status=codigos.get(cierre.desenlace, status.HTTP_200_OK))


# ---------------------------------------------------------------------------
# Saldo
# ---------------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def mi_saldo(request):
    """Saldo que le toca ver a quien pregunta: su bolsa, o la de su cliente y el límite de su caja."""
    usuario = request.user
    cliente = usuario.cliente or getattr(usuario.punto_venta, "cliente", None)

    if cliente is not None:
        bolsa = services.bolsa_de(cliente)
        datos = {
            "titular": cliente.nombre,
            "saldo": str(bolsa.saldo),
            "reservado": str(bolsa.reservado),
            "disponible": str(bolsa.disponible),
            "limites_asignados": str(services.limites_de(cliente)),
            "retirable": str(services.disponible_para_retirar(cliente)),
            "modo_bolsa": cliente.modo_bolsa,
        }
        if usuario.punto_venta is not None:
            limite = getattr(usuario.punto_venta, "limite", None)
            datos["mi_caja"] = {
                "nombre": usuario.punto_venta.nombre,
                "limite": str(limite.limite) if limite else None,
                "disponible": str(limite.disponible) if limite else None,
            }
        return Response(datos)

    if usuario.administrador is not None:
        bolsa = services.bolsa_de(usuario.administrador)
        return Response({
            "titular": usuario.administrador.nombre,
            "saldo": str(bolsa.saldo),
            "reservado": str(bolsa.reservado),
            "disponible": str(bolsa.disponible),
        })

    return Response({"titular": "Super Admin", "saldo": None})


# ---------------------------------------------------------------------------
# Administración
# ---------------------------------------------------------------------------
def _cliente_de(request, campo="cliente"):
    cliente = Cliente.objects.filter(
        uuid=request.data.get(campo), inquilino=request.user.inquilino
    ).first()
    if cliente is None:
        raise permisos.PermisoDenegado("Ese cliente no existe o no es tuyo")
    return cliente


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def listar_clientes(request):
    """
    Los clientes que este usuario alcanza.

    Trae `retirable` además del saldo porque son números distintos y
    confundirlos lleva a intentar devoluciones que el servidor va a
    rechazar: la bolsa incluye lo comprometido en límites de cajas y en
    ventas sin cerrar.
    """
    usuario = request.user
    filtro = {"inquilino": usuario.inquilino}
    if usuario.rol == "administrador":
        filtro["administrador_id"] = usuario.administrador_id
    elif usuario.rol not in ("super_admin",):
        raise permisos.PermisoDenegado("Ese rol no ve la lista de clientes")

    salida = []
    for cliente in Cliente.objects.filter(**filtro).select_related("administrador"):
        bolsa = services.bolsa_de(cliente)
        salida.append({
            "uuid": str(cliente.uuid),
            "nombre": cliente.nombre,
            "rfc": cliente.rfc,
            "administrador": cliente.administrador.nombre,
            "margen_pct": str(cliente.margen_pct),
            "modo_bolsa": cliente.modo_bolsa,
            "estado": cliente.estado,
            "saldo": str(bolsa.saldo),
            "reservado": str(bolsa.reservado),
            "limites_asignados": str(services.limites_de(cliente)),
            "retirable": str(services.disponible_para_retirar(cliente)),
            "puntos_venta": PuntoVenta.objects.filter(cliente=cliente).count(),
        })
    return Response(sorted(salida, key=lambda c: c["nombre"]))


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def traspasar(request):
    """Traspaso del administrador a un cliente. Sobre el umbral pide `codigo_totp`."""
    cliente = _cliente_de(request)
    operacion = acciones.traspasar(
        request.user, cliente, request.data.get("monto", "0"),
        codigo_totp=request.data.get("codigo_totp"),
    )
    bolsa = services.bolsa_de(cliente)
    return Response(
        {
            "folio": operacion.folio,
            "cliente": cliente.nombre,
            "saldo_cliente": str(bolsa.saldo),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def retirar(request):
    """Devolución o ajuste, según `tipo`. Las dos exigen motivo."""
    cliente = _cliente_de(request)
    motivo = request.data.get("motivo", "")
    if motivo not in Motivo.values:
        return error("Motivo fuera del catálogo", status.HTTP_400_BAD_REQUEST, clave="motivo")

    comun = {"motivo": motivo, "nota": request.data.get("nota", "")}
    if request.data.get("tipo") == "ajuste":
        operacion = acciones.ajustar(
            request.user, cliente, request.data.get("monto", "0"),
            a_favor=bool(request.data.get("a_favor")), **comun,
        )
    else:
        operacion = acciones.devolver(
            request.user, cliente, request.data.get("monto", "0"), **comun
        )

    return Response(
        {
            "folio": operacion.folio,
            "cliente": cliente.nombre,
            "saldo_cliente": str(services.bolsa_de(cliente).saldo),
            "retirable": str(services.disponible_para_retirar(cliente)),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def asignar_limite(request):
    """Fija el límite de gasto de una caja. No mueve dinero (decisión 7)."""
    punto_venta = PuntoVenta.objects.filter(
        uuid=request.data.get("punto_venta"), inquilino=request.user.inquilino
    ).first()
    if punto_venta is None:
        raise permisos.PermisoDenegado("Ese punto de venta no existe o no es tuyo")

    fila = acciones.asignar_limite(
        request.user, punto_venta, request.data.get("monto", "0")
    )
    cliente = punto_venta.cliente
    return Response({
        "punto_venta": punto_venta.nombre,
        "limite": str(fila.limite),
        "suma_limites": str(services.limites_de(cliente)),
        "retirable": str(services.disponible_para_retirar(cliente)),
    })


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def listar_puntos_venta(request):
    """Cajas que el usuario alcanza, con su límite."""
    usuario = request.user
    filtro = {"inquilino": usuario.inquilino}
    if usuario.cliente_id:
        filtro["cliente_id"] = usuario.cliente_id
    elif usuario.administrador_id:
        filtro["cliente__administrador_id"] = usuario.administrador_id
    elif usuario.punto_venta_id:
        filtro["pk"] = usuario.punto_venta_id

    salida = []
    for pdv in PuntoVenta.objects.filter(**filtro).select_related("cliente"):
        limite = getattr(pdv, "limite", None)
        salida.append({
            "uuid": str(pdv.uuid),
            "nombre": pdv.nombre,
            "direccion": pdv.direccion,
            "estado": pdv.estado,
            "cliente": pdv.cliente.nombre,
            "modo_bolsa": pdv.cliente.modo_bolsa,
            "limite": str(limite.limite) if limite else None,
        })
    return Response(salida)


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def suspender(request):
    """Suspende o, con `reactivar`, reactiva a un cliente y en cascada a sus cajas."""
    cliente = _cliente_de(request)
    if request.data.get("reactivar"):
        acciones.reactivar_cliente(request.user, cliente)
    else:
        acciones.suspender_cliente(request.user, cliente)
    cliente.refresh_from_db()
    cajas = list(
        PuntoVenta.objects.filter(cliente=cliente).values_list("nombre", "estado")
    )
    return Response({
        "cliente": cliente.nombre,
        "estado": cliente.estado,
        "puntos_venta": [{"nombre": n, "estado": e} for n, e in cajas],
    })
