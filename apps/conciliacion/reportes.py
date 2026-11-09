"""
Corte del día.

Lo que necesita quien cierra la caja: cuánto se vendió, cuánto se aprobó,
cuánto quedó sin resolver y cuánto dinero sigue apartado por eso.

El corte NO se calcula desde las reservas sino desde el ledger, porque el
ledger es lo que no se puede editar. Una reserva se puede liberar; un
asiento, no.
"""
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from apps.saldo.models import Asiento, EstadoReserva, Reserva, TipoOperacion

CERO = Decimal("0.0000")


def _rango(dia=None):
    base = dia or timezone.localtime().date()
    inicio = timezone.make_aware(
        timezone.datetime.combine(base, timezone.datetime.min.time())
    )
    return inicio, inicio + timezone.timedelta(days=1)


def corte(inquilino, *, dia=None, cliente=None, punto_venta=None):
    """
    Un corte por el alcance que se le pida: toda la plataforma, un cliente
    o una caja.
    """
    inicio, fin = _rango(dia)

    reservas = Reserva.objects.filter(
        inquilino=inquilino, creado__gte=inicio, creado__lt=fin
    )
    if punto_venta is not None:
        reservas = reservas.filter(punto_venta=punto_venta)
    elif cliente is not None:
        reservas = reservas.filter(punto_venta__cliente=cliente)

    por_estado = {
        fila["estado"]: fila
        for fila in reservas.values("estado").annotate(
            cuantas=Count("id"), importe=Sum("monto")
        )
    }

    def dato(estado):
        fila = por_estado.get(estado, {})
        return fila.get("cuantas", 0), fila.get("importe") or CERO

    aprobadas, vendido = dato(EstadoReserva.CAPTURADA)
    rechazadas, _ = dato(EstadoReserva.LIBERADA)
    sin_resolver, apartado = dato(EstadoReserva.ACTIVA)
    intentos = aprobadas + rechazadas + sin_resolver

    return {
        "dia": (dia or timezone.localtime().date()).isoformat(),
        "intentos": intentos,
        "aprobadas": aprobadas,
        "rechazadas": rechazadas,
        "sin_resolver": sin_resolver,
        "vendido": vendido,
        "apartado_sin_resolver": apartado,
        "tasa_aprobacion": (
            round(aprobadas / intentos * 100, 2) if intentos else None
        ),
    }


def corte_por_punto_venta(inquilino, *, dia=None, cliente=None):
    """El desglose que se imprime al cerrar la tienda."""
    from apps.core.models import PuntoVenta

    filtro = {"inquilino": inquilino}
    if cliente is not None:
        filtro["cliente"] = cliente

    salida = []
    for pdv in PuntoVenta.objects.filter(**filtro).select_related("cliente"):
        datos = corte(inquilino, dia=dia, punto_venta=pdv)
        if datos["intentos"] == 0:
            continue
        datos["punto_venta"] = pdv.nombre
        datos["cliente"] = pdv.cliente.nombre
        salida.append(datos)
    return sorted(salida, key=lambda d: d["vendido"], reverse=True)


def ventas_del_ledger(inquilino, *, dia=None):
    """
    El mismo total, calculado desde los asientos.

    Existe para contrastarlo contra el corte: si los dos números no
    coinciden, hay una captura que no dejó asiento o un asiento huérfano, y
    eso se tiene que ver antes de que alguien firme el cierre.
    """
    inicio, fin = _rango(dia)
    total = Asiento.objects.filter(
        operacion__inquilino=inquilino,
        operacion__tipo=TipoOperacion.VENTA,
        bolsa__tipo="cliente",
        creado__gte=inicio, creado__lt=fin,
    ).aggregate(t=Sum("monto"))["t"] or CERO
    return abs(total)
