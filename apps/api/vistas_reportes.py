"""
Transacciones y movimientos de saldo, vistos por jerarquía.

La regla que manda en todo el archivo: **cada nivel ve lo suyo y lo de
abajo, y nunca el saldo de arriba.**

- Caja: sus ventas y los cambios a su límite. No ve la bolsa de su cliente.
- Cliente: lo de todas sus cajas y los movimientos de SU bolsa.
- Administrador: lo de sus clientes y sus cajas, y los movimientos de su
  bolsa y de las de sus clientes.
- Super Admin: toda la plataforma.

La visibilidad se decide en `_alcance_*`, una sola vez, antes de cualquier
filtro que mande la pantalla. Un filtro nunca puede ampliar el alcance.

Movimientos combina dos fuentes: los asientos del ledger (fondeos,
traspasos, devoluciones, retiros, ajustes) y los cambios de límite de las
cajas, que no mueven dinero (decisión 7) pero son "traspasos" para quien los
vive.
"""
import csv
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.identidad import permisos
from apps.identidad.models import Rol
from apps.saldo.models import (
    Asiento,
    MovimientoLimite,
    Operacion,
    TipoBolsa,
    TipoOperacion,
)

from .permisos_http import Operativo
from .vistas import manejar

POR_PAGINA = 25
MAX_POR_PAGINA = 200
MAX_EXPORTAR = 50_000

ESTADO_VENTA = {
    "aprobada": "Aprobada",
    "rechazada": "Rechazada",
    "sin_resolver": "Sin resolver",
    "pendiente": "En proceso",
    "duplicada": "Duplicada",
}

CONCEPTO = {
    TipoOperacion.COMPRA: "Fondeo",
    TipoOperacion.TRASPASO: "Traspaso",
    TipoOperacion.DEVOLUCION: "Devolución",
    TipoOperacion.AJUSTE: "Ajuste",
    TipoOperacion.RETIRO: "Retiro a EPS",
}


# ---------------------------------------------------------------------------
# Filtros comunes
# ---------------------------------------------------------------------------
def _fecha(texto, fin=False):
    try:
        d = datetime.strptime(texto, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return timezone.make_aware(datetime.combine(d, time.max if fin else time.min))


def _rango(request):
    """Fechas del filtro; por omisión, los últimos 30 días."""
    hasta = _fecha(request.query_params.get("hasta"), fin=True) or timezone.now()
    desde = _fecha(request.query_params.get("desde")) or (hasta - timedelta(days=30))
    return desde, hasta


def _pagina(request):
    try:
        pagina = max(int(request.query_params.get("pagina", 1)), 1)
        tam = min(max(int(request.query_params.get("por_pagina", POR_PAGINA)), 1), MAX_POR_PAGINA)
    except ValueError:
        pagina, tam = 1, POR_PAGINA
    return pagina, tam


def _d(v):
    return str(Decimal(v).quantize(Decimal("0.01"))) if v is not None else None


def _csv(nombre, encabezados, filas):
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
    respuesta.write("\ufeff")  # BOM: que Excel abra los acentos bien
    w = csv.writer(respuesta)
    w.writerow(encabezados)
    for f in filas:
        w.writerow(f)
    return respuesta


# ---------------------------------------------------------------------------
# Transacciones (ventas)
# ---------------------------------------------------------------------------
def _alcance_ventas(usuario):
    """Las ventas que este usuario puede ver. Nada de lo que mande la pantalla lo amplía."""
    base = Operacion.objects.filter(inquilino=usuario.inquilino, tipo=TipoOperacion.VENTA)
    if usuario.rol == Rol.SUPER_ADMIN:
        return base
    if usuario.rol == Rol.ADMINISTRADOR:
        return base.filter(punto_venta__cliente__administrador_id=usuario.administrador_id)
    if usuario.rol == Rol.CLIENTE:
        return base.filter(punto_venta__cliente_id=usuario.cliente_id)
    if usuario.rol == Rol.PUNTO_VENTA:
        return base.filter(punto_venta_id=usuario.punto_venta_id)
    raise permisos.PermisoDenegado("Sin acceso a transacciones")


def _ventas(request):
    usuario = request.user
    desde, hasta = _rango(request)
    qs = (
        _alcance_ventas(usuario)
        .filter(creado__range=(desde, hasta))
        .select_related(
            "punto_venta__cliente__administrador", "producto__carrier", "envio",
        )
        .order_by("-creado", "-pk")
    )
    estado = request.query_params.get("estado")
    if estado in ESTADO_VENTA:
        qs = qs.filter(envio__desenlace=estado)
    texto = (request.query_params.get("q") or "").strip()
    if texto:
        qs = qs.filter(
            Q(folio__icontains=texto)
            | Q(envio__account_id__icontains=texto)
            | Q(envio__control_no__icontains=texto)
            | Q(envio__carrier_control_no__icontains=texto)
            | Q(envio__transaction_id__icontains=texto)
            | Q(producto__nombre__icontains=texto)
            | Q(punto_venta__nombre__icontains=texto)
            | Q(punto_venta__cliente__nombre__icontains=texto)
        )
    return qs


def _saldo_despues(op, usuario):
    """
    Saldo del cliente después de la venta. Solo lo ve el cliente y quien
    está arriba: la caja no conoce la bolsa de su cliente.
    """
    if usuario.rol == Rol.PUNTO_VENTA:
        return None
    asiento = next((a for a in op.asientos.all() if a.bolsa.tipo == TipoBolsa.CLIENTE), None)
    return _d(asiento.saldo_resultante) if asiento else None


def _venta_json(op, usuario, detalle=False):
    envio = getattr(op, "envio", None)
    pdv = op.punto_venta
    cliente = pdv.cliente if pdv else None
    fila = {
        "folio": op.folio,
        "fecha": op.creado.isoformat(),
        "producto": op.producto.nombre if op.producto else "",
        "compania": op.producto.carrier.nombre if op.producto else "",
        "monto": _d(envio.monto if envio else 0),
        "telefono": envio.account_id if envio else "",
        "estado": envio.desenlace if envio else "pendiente",
        "estado_texto": ESTADO_VENTA.get(envio.desenlace if envio else "pendiente", "—"),
        "punto_venta": pdv.nombre if pdv else "",
        "cliente": cliente.nombre if cliente else "",
        "administrador": (
            cliente.administrador.nombre
            if cliente and usuario.rol in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR) else None
        ),
        "autorizacion_carrier": envio.carrier_control_no if envio else "",
        "autorizacion_emida": envio.control_no if envio else "",
        "transaccion_emida": envio.transaction_id if envio else "",
        "saldo_despues": _saldo_despues(op, usuario),
    }
    if detalle and envio:
        fila.update({
            "response_code": envio.response_code,
            "h2h_result_code": envio.h2h_result_code,
            "mensaje": envio.response_message or envio.detalle,
            "invoice_no": envio.invoice_no,
            "product_id": envio.product_id,
            "intentos": envio.intentos,
            "cerrado": envio.cerrado.isoformat() if envio.cerrado else None,
            "pin": envio.pin,
        })
    return fila


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def transacciones(request):
    """
    Ventas con su estado y sus autorizaciones, por jerarquía.
    Filtros: desde, hasta (AAAA-MM-DD), q, estado, pagina, por_pagina.
    """
    qs = _ventas(request).prefetch_related("asientos__bolsa")
    pagina, tam = _pagina(request)
    total = qs.count()
    filas = qs[(pagina - 1) * tam: pagina * tam]
    return Response({
        "total": total, "pagina": pagina, "por_pagina": tam,
        "filas": [_venta_json(op, request.user) for op in filas],
    })


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def transaccion(request, folio):
    """El detalle de una venta (el ojito), si está dentro del alcance."""
    op = (
        _alcance_ventas(request.user).filter(folio=folio)
        .select_related("punto_venta__cliente__administrador", "producto__carrier", "envio")
        .prefetch_related("asientos__bolsa").first()
    )
    if op is None:
        raise permisos.PermisoDenegado("Esa transacción no existe o no está bajo tu alcance")
    return Response(_venta_json(op, request.user, detalle=True))


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def exportar_transacciones(request):
    """Las mismas filas del filtro, en CSV para Excel."""
    usuario = request.user
    qs = _ventas(request).prefetch_related("asientos__bolsa")[:MAX_EXPORTAR]
    encabezados = ["Fecha", "Folio", "Producto", "Compañía", "Monto", "Teléfono", "Estado",
                   "Punto de venta", "Cliente"]
    if usuario.rol in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR):
        encabezados.append("Administrador")
    encabezados += ["Autorización carrier", "Autorización Emida", "Transacción Emida"]
    if usuario.rol != Rol.PUNTO_VENTA:
        encabezados.append("Saldo cliente después")

    def filas():
        for op in qs:
            v = _venta_json(op, usuario)
            fila = [timezone.localtime(op.creado).strftime("%Y-%m-%d %H:%M:%S"), v["folio"],
                    v["producto"], v["compania"], v["monto"], v["telefono"], v["estado_texto"],
                    v["punto_venta"], v["cliente"]]
            if usuario.rol in (Rol.SUPER_ADMIN, Rol.ADMINISTRADOR):
                fila.append(v["administrador"])
            fila += [v["autorizacion_carrier"], v["autorizacion_emida"], v["transaccion_emida"]]
            if usuario.rol != Rol.PUNTO_VENTA:
                fila.append(v["saldo_despues"])
            yield fila

    return _csv("transacciones.csv", encabezados, filas())


# ---------------------------------------------------------------------------
# Movimientos de saldo (traspasos y demás)
# ---------------------------------------------------------------------------
def _alcance_bolsas(usuario):
    """Filtro de las bolsas cuyos movimientos ve este usuario. Nunca las de arriba."""
    if usuario.rol == Rol.SUPER_ADMIN:
        return Q(bolsa__inquilino=usuario.inquilino, bolsa__tipo__in=[
            TipoBolsa.ADMINISTRADOR, TipoBolsa.CLIENTE])
    if usuario.rol == Rol.ADMINISTRADOR:
        return (Q(bolsa__administrador_id=usuario.administrador_id)
                | Q(bolsa__cliente__administrador_id=usuario.administrador_id))
    if usuario.rol == Rol.CLIENTE:
        return Q(bolsa__cliente_id=usuario.cliente_id)
    return None  # una caja no tiene bolsa propia: solo ve su límite


def _alcance_limites(usuario):
    base = MovimientoLimite.objects.filter(inquilino=usuario.inquilino)
    if usuario.rol == Rol.SUPER_ADMIN:
        return base
    if usuario.rol == Rol.ADMINISTRADOR:
        return base.filter(punto_venta__cliente__administrador_id=usuario.administrador_id)
    if usuario.rol == Rol.CLIENTE:
        return base.filter(punto_venta__cliente_id=usuario.cliente_id)
    if usuario.rol == Rol.PUNTO_VENTA:
        return base.filter(punto_venta_id=usuario.punto_venta_id)
    raise permisos.PermisoDenegado("Sin acceso a movimientos")


def _concepto(asiento, nombres):
    """
    En palabras de quien lo lee, sin revelar el saldo de nadie más: "Traspaso
    de Scorpion Centro" dice de dónde vino, no cuánto le queda a Scorpion.
    """
    op = asiento.operacion
    base = CONCEPTO.get(op.tipo, op.get_tipo_display())
    otros = [n for pk, n in nombres.get(op.pk, []) if pk != asiento.bolsa_id]
    if op.tipo == TipoOperacion.TRASPASO:
        return f"{base} {'de' if asiento.monto > 0 else 'a'} {otros[0]}" if otros else base
    if op.tipo == TipoOperacion.DEVOLUCION:
        return f"{base} {'a' if asiento.monto < 0 else 'de'} {otros[0]}" if otros else base
    if op.tipo == TipoOperacion.COMPRA:
        return "Fondeo de EPS"
    return base


def _titular(bolsa):
    if bolsa.tipo == TipoBolsa.ADMINISTRADOR:
        return bolsa.administrador.nombre, "Administrador"
    return bolsa.cliente.nombre, "Cliente"


def _movimientos(request, limite_filas):
    """Asientos y cambios de límite del alcance, mezclados por fecha (más reciente primero)."""
    usuario = request.user
    desde, hasta = _rango(request)
    texto = (request.query_params.get("q") or "").strip()
    tipo = request.query_params.get("tipo") or ""

    filas = []
    alcance = _alcance_bolsas(usuario)
    if alcance is not None and tipo != "limite":
        asientos = (
            Asiento.objects.filter(alcance, creado__range=(desde, hasta))
            .exclude(operacion__tipo=TipoOperacion.VENTA)
            .select_related("operacion", "bolsa__administrador", "bolsa__cliente")
            .order_by("-creado", "-pk")
        )
        if tipo in CONCEPTO:
            asientos = asientos.filter(operacion__tipo=tipo)
        if texto:
            asientos = asientos.filter(
                Q(operacion__folio__icontains=texto)
                | Q(bolsa__administrador__nombre__icontains=texto)
                | Q(bolsa__cliente__nombre__icontains=texto)
                | Q(operacion__autorizado_por__icontains=texto)
            )
        asientos = list(asientos[:limite_filas])

        # Los nombres de la contraparte (solo nombres de la red, nunca saldos).
        ops = {a.operacion_id for a in asientos}
        nombres = {}
        for a in Asiento.objects.filter(operacion_id__in=ops).exclude(
            bolsa__tipo=TipoBolsa.SISTEMA
        ).select_related("bolsa__administrador", "bolsa__cliente"):
            nombres.setdefault(a.operacion_id, []).append((a.bolsa_id, _titular(a.bolsa)[0]))

        for a in asientos:
            quien, nivel = _titular(a.bolsa)
            filas.append({
                "fecha": a.creado.isoformat(),
                "folio": a.operacion.folio,
                "tipo": a.operacion.tipo,
                "concepto": _concepto(a, nombres),
                "autorizado_por": a.operacion.autorizado_por,
                "afectado": quien,
                "nivel": nivel,
                "movimiento": "abono" if a.monto > 0 else "cargo",
                "saldo_anterior": _d(a.saldo_resultante - a.monto),
                "monto": _d(a.monto),
                "saldo_actual": _d(a.saldo_resultante),
                "motivo": a.operacion.get_motivo_display() if a.operacion.motivo else "",
                "nota": a.operacion.nota,
            })

    if tipo in ("", "limite"):
        limites = (
            _alcance_limites(usuario).filter(creado__range=(desde, hasta))
            .select_related("punto_venta").order_by("-creado", "-pk")
        )
        if texto:
            limites = limites.filter(
                Q(punto_venta__nombre__icontains=texto) | Q(autorizado_por__icontains=texto)
            )
        for m in limites[:limite_filas]:
            dif = m.nuevo - m.anterior
            filas.append({
                "fecha": m.creado.isoformat(),
                "folio": "",
                "tipo": "limite",
                "concepto": "Asignación a caja" if dif > 0 else "Retiro de caja",
                "autorizado_por": m.autorizado_por,
                "afectado": m.punto_venta.nombre,
                "nivel": "Punto de venta",
                "movimiento": "abono" if dif > 0 else "cargo",
                "saldo_anterior": _d(m.anterior),
                "monto": _d(dif),
                "saldo_actual": _d(m.nuevo),
                "motivo": dict(_motivos()).get(m.motivo, m.motivo),
                "nota": m.nota,
            })

    filas.sort(key=lambda f: f["fecha"], reverse=True)
    return filas


def _motivos():
    from apps.saldo.models import Motivo
    return Motivo.choices


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def movimientos(request):
    """
    Fondeos, traspasos, devoluciones, retiros, ajustes y cambios de límite.
    Filtros: desde, hasta, q, tipo (CMP, TRS, DEV, AJU, RET o limite), pagina.
    """
    pagina, tam = _pagina(request)
    filas = _movimientos(request, pagina * tam + 1)
    total_visible = len(filas)
    return Response({
        "pagina": pagina, "por_pagina": tam,
        "hay_mas": total_visible > pagina * tam,
        "filas": filas[(pagina - 1) * tam: pagina * tam],
    })


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def exportar_movimientos(request):
    filas = _movimientos(request, MAX_EXPORTAR)
    encabezados = ["Fecha", "Folio", "Concepto", "Autorizó", "Afectado", "Nivel", "Movimiento",
                   "Saldo anterior", "Monto", "Saldo actual", "Motivo", "Nota"]
    salida = (
        [timezone.localtime(datetime.fromisoformat(f["fecha"])).strftime("%Y-%m-%d %H:%M:%S"),
         f["folio"], f["concepto"], f["autorizado_por"], f["afectado"], f["nivel"],
         f["movimiento"], f["saldo_anterior"], f["monto"], f["saldo_actual"], f["motivo"], f["nota"]]
        for f in filas
    )
    return _csv("movimientos.csv", encabezados, salida)
