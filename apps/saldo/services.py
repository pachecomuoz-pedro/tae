"""
Las operaciones del motor de saldo.

Regla única de concurrencia: toda escritura bloquea las filas de bolsa que va
a tocar, SIEMPRE en orden ascendente de llave primaria. Dos operaciones que
tocan las mismas dos bolsas las toman en el mismo orden y por lo tanto no se
abrazan. El día que alguien bloquee en otro orden aparecen interbloqueos bajo
carga, que es el tipo de falla que no se reproduce en una laptop.

Ninguna función de aquí escribe un saldo sin dejar su asiento. Esa es la
razón de que `_asentar` sea el único camino para mover dinero.

Decisiones que gobiernan este archivo (docs/DECISIONES.md): 5 ledger de
partida doble, 6 reservar no es gastar, 7 límites, 8 margen, 9 orden de bloqueo.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.models import Cliente, EstadoCliente, EstadoPuntoVenta, ModoBolsa, PuntoVenta

from .models import (
    Asiento,
    SecuenciaInvoice,
    Bolsa,
    BolsaPuntoVenta,
    ClaveSistema,
    EstadoReserva,
    Motivo,
    Operacion,
    Reserva,
    Secuencia,
    TipoBolsa,
    TipoOperacion,
)

CENTAVO = Decimal("0.0001")
CERO = Decimal("0.0000")


def q(valor):
    """Cuatro decimales, siempre. Redondeo comercial."""
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Errores. Cada uno dice qué regla se rompió, no "operación inválida".
# --------------------------------------------------------------------------
class ErrorSaldo(Exception):
    """Base de los errores de saldo. La API traduce cada uno a su código HTTP."""


class SaldoInsuficiente(ErrorSaldo):
    """No alcanza el disponible (402 en la API)."""


class LimiteExcedido(ErrorSaldo):
    """La suma de límites rebasaría la bolsa, o el límite de la caja no alcanza (409)."""


class MotivoRequerido(ErrorSaldo):
    """La operación exige un motivo y no vino (400)."""


class OperacionNoPermitida(ErrorSaldo):
    """La regla de negocio lo impide (409)."""


class PartidaDescuadrada(ErrorSaldo):
    """Los asientos no suman cero. Nunca debe ocurrir: si ocurre, es un error de programación."""


# --------------------------------------------------------------------------
# Folios
# --------------------------------------------------------------------------
def siguiente_folio(inquilino, tipo, cuando=None):
    """
    Toma el siguiente número de la serie con la fila bloqueada.

    La serie corre por tipo y por año: VTA-2026-048213. Reinicia cada año,
    cosa que no importa porque el año forma parte del folio.
    """
    anio = (cuando or timezone.now()).year
    fila, _ = Secuencia.objects.get_or_create(inquilino=inquilino, tipo=tipo, anio=anio)
    fila = Secuencia.objects.select_for_update().get(pk=fila.pk)
    fila.ultimo += 1
    fila.save(update_fields=["ultimo"])
    return f"{tipo}-{anio}-{fila.ultimo:06d}"


def siguiente_invoice_no(inquilino):
    """
    El siguiente número hacia el proveedor, con la fila bloqueada.

    Devuelve (numero, ciclo). Al pasar de 99,999 regresa a 1 y sube el ciclo.
    """
    fila, _ = SecuenciaInvoice.objects.get_or_create(inquilino=inquilino)
    fila = SecuenciaInvoice.objects.select_for_update().get(pk=fila.pk)
    if fila.ultimo >= SecuenciaInvoice.TOPE:
        fila.ultimo = 1
        fila.ciclo += 1
    else:
        fila.ultimo += 1
    fila.save(update_fields=["ultimo", "ciclo"])
    return fila.ultimo, fila.ciclo


# --------------------------------------------------------------------------
# Bolsas
# --------------------------------------------------------------------------
def bolsa_sistema(inquilino, clave):
    """Cuenta interna del inquilino (proveedor, bonificación de margen, etc.). Se crea si no existe."""
    bolsa, _ = Bolsa.objects.get_or_create(
        inquilino=inquilino,
        tipo=TipoBolsa.SISTEMA,
        clave_sistema=clave,
        defaults={"saldo": CERO},
    )
    return bolsa


def bolsa_de(entidad):
    """La bolsa de un administrador o de un cliente, creándola si hace falta."""
    from apps.core.models import Administrador

    if isinstance(entidad, Administrador):
        bolsa, _ = Bolsa.objects.get_or_create(
            administrador=entidad,
            defaults={"inquilino": entidad.inquilino, "tipo": TipoBolsa.ADMINISTRADOR},
        )
        return bolsa
    if isinstance(entidad, Cliente):
        bolsa, _ = Bolsa.objects.get_or_create(
            cliente=entidad,
            defaults={"inquilino": entidad.inquilino, "tipo": TipoBolsa.CLIENTE},
        )
        return bolsa
    raise OperacionNoPermitida(f"{entidad!r} no tiene bolsa")


def _bloquear(bolsas):
    """
    Devuelve las bolsas releídas y bloqueadas, en orden ascendente de pk.

    El orden es lo que evita los interbloqueos. No es cosmético.
    """
    pks = sorted({b.pk for b in bolsas})
    filas = Bolsa.objects.select_for_update().filter(pk__in=pks).order_by("pk")
    return {fila.pk: fila for fila in filas}


# --------------------------------------------------------------------------
# El único camino para mover dinero
# --------------------------------------------------------------------------
def _asentar(operacion, movimientos):
    """
    Escribe los asientos de una operación y actualiza los saldos.

    `movimientos` es una lista de (bolsa, monto con signo). La suma tiene que
    dar cero: si no, la operación está mal planteada y se rechaza antes de
    escribir nada. Eso es la partida doble, comprobada en cada escritura y no
    solo en una prueba.
    """
    total = sum((q(m) for _, m in movimientos), CERO)
    if total != CERO:
        raise PartidaDescuadrada(
            f"Los asientos de {operacion.folio} suman {total}, deberían sumar cero"
        )

    bloqueadas = _bloquear([b for b, _ in movimientos])
    asientos = []
    for bolsa, monto in movimientos:
        fila = bloqueadas[bolsa.pk]
        fila.saldo = q(fila.saldo + q(monto))
        fila.save(update_fields=["saldo", "actualizado"])
        asientos.append(
            Asiento(
                operacion=operacion,
                bolsa=fila,
                monto=q(monto),
                saldo_resultante=fila.saldo,
            )
        )
    Asiento.objects.bulk_create(asientos)
    return asientos


def _crear_operacion(inquilino, tipo, *, motivo="", nota="", autorizado_por="",
                     punto_venta=None, operacion_origen=None):
    if motivo == Motivo.OTRO and not nota.strip():
        raise MotivoRequerido("El motivo 'Otro' exige una nota")
    return Operacion.objects.create(
        inquilino=inquilino,
        tipo=tipo,
        folio=siguiente_folio(inquilino, tipo),
        motivo=motivo,
        nota=nota,
        autorizado_por=autorizado_por,
        punto_venta=punto_venta,
        operacion_origen=operacion_origen,
    )


# --------------------------------------------------------------------------
# Fondeo y traspaso
# --------------------------------------------------------------------------
@transaction.atomic
def fondear_administrador(administrador, monto, *, autorizado_por=""):
    """El Super Admin pone saldo en la bolsa de un administrador."""
    monto = q(monto)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto a fondear debe ser positivo")

    inquilino = administrador.inquilino
    operacion = _crear_operacion(
        inquilino, TipoOperacion.COMPRA, autorizado_por=autorizado_por
    )
    _asentar(
        operacion,
        [
            (bolsa_sistema(inquilino, ClaveSistema.FONDEO), -monto),
            (bolsa_de(administrador), monto),
        ],
    )
    return operacion


def fondear_cliente(cliente, monto, *, autorizado_por=""):
    """
    El Super Admin pone saldo directo en la bolsa de un cliente.

    Igual que el fondeo de un administrador: el dinero entra a la red desde
    la cuenta de fondeo de EPS y no pasa por el administrador del cliente.
    Sin margen: el margen es el premio por comprarle al administrador
    (decisión 8), y aquí no hay compra, hay una asignación manual.
    No toca el saldo de EPS en Emida (decisión 23).
    """
    monto = q(monto)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto a asignar debe ser positivo")

    inquilino = cliente.inquilino
    operacion = _crear_operacion(
        inquilino, TipoOperacion.COMPRA, autorizado_por=autorizado_por
    )
    _asentar(
        operacion,
        [
            (bolsa_sistema(inquilino, ClaveSistema.FONDEO), -monto),
            (bolsa_de(cliente), monto),
        ],
    )
    return operacion


def _desglose_margen(cliente, monto_pagado):
    """
    El margen se aplica desde la compra: el cliente paga 1,000 con 4% y su
    bolsa recibe 1,040. Los 40 no salen del administrador, salen de la cuenta
    de bonificación. Si salieran del administrador, su bolsa se vaciaría más
    rápido de lo que la fondean y el número dejaría de significar nada.
    """
    pagado = q(monto_pagado)
    acreditado = q(pagado * (Decimal(1) + cliente.margen_pct / Decimal(100)))
    return pagado, q(acreditado - pagado), acreditado


@transaction.atomic
def traspasar_a_cliente(administrador, cliente, monto_pagado, *, autorizado_por=""):
    """
    Carga de bolsa del administrador hacia un cliente, con su margen.

    Un solo folio para los tres asientos: el traspaso es una operación, no tres.
    """
    pagado, bonificacion, acreditado = _desglose_margen(cliente, monto_pagado)
    if pagado <= CERO:
        raise OperacionNoPermitida("El monto a traspasar debe ser positivo")
    if cliente.administrador_id != administrador.pk:
        raise OperacionNoPermitida("El cliente no pertenece a ese administrador")
    if not cliente.puede_operar:
        raise OperacionNoPermitida(
            f"{cliente.nombre} está {cliente.get_estado_display().lower()}: no puede recibir saldo"
        )

    inquilino = administrador.inquilino
    bolsa_admin = bolsa_de(administrador)
    if q(bolsa_admin.disponible) < pagado:
        raise SaldoInsuficiente(
            f"La bolsa del administrador tiene {bolsa_admin.disponible} y se piden {pagado}"
        )

    operacion = _crear_operacion(
        inquilino, TipoOperacion.TRASPASO, autorizado_por=autorizado_por
    )
    movimientos = [(bolsa_admin, -pagado), (bolsa_de(cliente), acreditado)]
    if bonificacion != CERO:
        movimientos.append(
            (bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN), -bonificacion)
        )
    _asentar(operacion, movimientos)
    return operacion


# --------------------------------------------------------------------------
# Límites de punto de venta
# --------------------------------------------------------------------------
def limites_de(cliente, excluyendo=None):
    """Suma de los límites vigentes de las cajas del cliente."""
    filas = BolsaPuntoVenta.objects.filter(punto_venta__cliente=cliente)
    if excluyendo is not None:
        filas = filas.exclude(punto_venta_id=excluyendo.pk)
    return q(sum((f.limite for f in filas), CERO))


def disponible_para_retirar(cliente):
    """
    Lo que de verdad se puede sacar de la bolsa de un cliente.

    No es su saldo: hay dinero comprometido en los límites de sus cajas y en
    las ventas que todavía no cierran.
    """
    bolsa = bolsa_de(cliente)
    return q(bolsa.saldo - limites_de(cliente) - bolsa.reservado)


@transaction.atomic
def asignar_limite(punto_venta, nuevo_limite, *, autorizado_por=""):
    """
    Fija el techo de gasto de una caja. Reemplaza el valor anterior.

    No mueve dinero y por eso no genera asientos: el saldo sigue en la bolsa
    del cliente. Lo que sí hace es respetar la regla de que la suma de los
    límites nunca exceda esa bolsa.
    """
    nuevo_limite = q(nuevo_limite)
    if nuevo_limite < CERO:
        raise OperacionNoPermitida("El límite no puede ser negativo")

    cliente = punto_venta.cliente
    if cliente.modo_bolsa != ModoBolsa.ASIGNADA:
        raise OperacionNoPermitida(
            f"{cliente.nombre} opera en bolsa compartida: sus cajas no llevan límite"
        )

    bolsa = Bolsa.objects.select_for_update().get(pk=bolsa_de(cliente).pk)
    otros = limites_de(cliente, excluyendo=punto_venta)
    if q(otros + nuevo_limite) > bolsa.saldo:
        raise LimiteExcedido(
            f"La suma de límites quedaría en {q(otros + nuevo_limite)} y la bolsa "
            f"del cliente tiene {bolsa.saldo}"
        )

    fila, _ = BolsaPuntoVenta.objects.select_for_update().get_or_create(
        punto_venta=punto_venta
    )
    if nuevo_limite < fila.reservado:
        raise LimiteExcedido(
            f"Hay {fila.reservado} reservado en ventas en vuelo en esa caja"
        )
    fila.limite = nuevo_limite
    fila.save(update_fields=["limite", "actualizado"])
    return fila


# --------------------------------------------------------------------------
# Devolución y ajuste
# --------------------------------------------------------------------------
@transaction.atomic
def devolver(cliente, monto_acreditado, *, motivo, nota="", autorizado_por="",
             operacion_origen=None):
    """
    Revierte un traspaso, total o parcialmente.

    El margen se revierte en la misma proporción en que se otorgó: si se
    acreditó 1,040 por un pago de 1,000, devolver los 1,040 regresa 1,000 al
    administrador y 40 a la bonificación. Devolver la mitad regresa la mitad
    de cada cosa.
    """
    monto = q(monto_acreditado)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto a devolver debe ser positivo")
    if not motivo:
        raise MotivoRequerido("Toda devolución lleva motivo")

    disponible = disponible_para_retirar(cliente)
    if monto > disponible:
        raise SaldoInsuficiente(
            f"Solo hay {disponible} retirable: el resto está en límites de cajas "
            f"o en ventas sin cerrar"
        )

    factor = Decimal(1) + cliente.margen_pct / Decimal(100)
    pagado = q(monto / factor)
    bonificacion = q(monto - pagado)

    inquilino = cliente.inquilino
    operacion = _crear_operacion(
        inquilino,
        TipoOperacion.DEVOLUCION,
        motivo=motivo,
        nota=nota,
        autorizado_por=autorizado_por,
        operacion_origen=operacion_origen,
    )
    movimientos = [
        (bolsa_de(cliente), -monto),
        (bolsa_de(cliente.administrador), pagado),
    ]
    if bonificacion != CERO:
        movimientos.append(
            (bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN), bonificacion)
        )
    _asentar(operacion, movimientos)
    return operacion


@transaction.atomic
def ajustar(cliente, monto, *, motivo, nota="", autorizado_por="", a_favor=False,
            operacion_origen=None):
    """
    Corrige una diferencia contra lo que debió aplicarse.

    No recalcula margen: corrige el resultado, no la base. Si debían
    acreditarse 1,040 y se aplicaron 1,060, el ajuste en contra retira 20
    exactos contra la cuenta de bonificación, que fue la que absorbió el error.

    El ajuste a favor es el peligroso: acredita saldo, y saldo acreditado es
    tiempo aire que alguien puede vender. Por eso debita la bolsa del
    administrador y tiene tope por operación.
    """
    monto = q(monto)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto del ajuste debe ser positivo")
    if not motivo:
        raise MotivoRequerido("Todo ajuste lleva motivo")

    inquilino = cliente.inquilino
    bolsa_cliente = bolsa_de(cliente)
    bolsa_admin = bolsa_de(cliente.administrador)

    if a_favor:
        tope = q(settings.TAE["AJUSTE_FAVOR_TOPE"])
        if monto > tope:
            raise LimiteExcedido(
                f"Un ajuste a favor no puede exceder {tope} por operación; "
                f"para más, se hace un traspaso"
            )
        if q(bolsa_admin.disponible) < monto:
            raise SaldoInsuficiente(
                "El ajuste a favor sale de la bolsa del administrador y no alcanza"
            )
        movimientos = [(bolsa_admin, -monto), (bolsa_cliente, monto)]
    else:
        disponible = disponible_para_retirar(cliente)
        if monto > disponible:
            raise SaldoInsuficiente(
                f"Solo hay {disponible} retirable de la bolsa del cliente"
            )
        movimientos = [
            (bolsa_cliente, -monto),
            (bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN), monto),
        ]

    operacion = _crear_operacion(
        inquilino,
        TipoOperacion.AJUSTE,
        motivo=motivo,
        nota=nota,
        autorizado_por=autorizado_por,
        operacion_origen=operacion_origen,
    )
    _asentar(operacion, movimientos)
    return operacion


# --------------------------------------------------------------------------
# El ciclo de la venta
# --------------------------------------------------------------------------
@transaction.atomic
def reservar(punto_venta, monto, *, producto=None):
    """
    Aparta el dinero de una venta antes de mandarla al proveedor.

    Reservar y no debitar es toda la diferencia. Emida no tiene reversa: si
    debitáramos y la venta fallara, habría que devolver, y devolver lo que
    nunca salió es cómo se pierden pesos en el camino. Mientras la respuesta
    no llegue, el dinero está apartado pero no gastado.
    """
    monto = q(monto)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto de la venta debe ser positivo")

    punto_venta = PuntoVenta.objects.select_related("cliente").get(pk=punto_venta.pk)
    if not punto_venta.puede_vender:
        raise OperacionNoPermitida(
            f"{punto_venta.nombre} no puede vender en este momento"
        )

    cliente = punto_venta.cliente
    bolsa = Bolsa.objects.select_for_update().get(pk=bolsa_de(cliente).pk)
    if q(bolsa.saldo - bolsa.reservado) < monto:
        raise SaldoInsuficiente(
            f"La bolsa de {cliente.nombre} tiene {q(bolsa.saldo - bolsa.reservado)} "
            f"disponible y la venta pide {monto}"
        )

    fila_limite = None
    if cliente.modo_bolsa == ModoBolsa.ASIGNADA:
        fila_limite = (
            BolsaPuntoVenta.objects.select_for_update()
            .filter(punto_venta=punto_venta)
            .first()
        )
        if fila_limite is None:
            raise SaldoInsuficiente(
                f"{punto_venta.nombre} no tiene saldo asignado"
            )
        if q(fila_limite.limite - fila_limite.reservado) < monto:
            raise SaldoInsuficiente(
                f"{punto_venta.nombre} tiene {q(fila_limite.limite - fila_limite.reservado)} "
                f"de su bolsa asignada y la venta pide {monto}"
            )
        fila_limite.reservado = q(fila_limite.reservado + monto)
        fila_limite.save(update_fields=["reservado", "actualizado"])

    bolsa.reservado = q(bolsa.reservado + monto)
    bolsa.save(update_fields=["reservado", "actualizado"])

    operacion = _crear_operacion(
        cliente.inquilino, TipoOperacion.VENTA, punto_venta=punto_venta
    )
    numero, ciclo = siguiente_invoice_no(cliente.inquilino)
    operacion.invoice_no = numero
    operacion.invoice_ciclo = ciclo
    if producto is not None:
        operacion.producto = producto
    operacion.save(update_fields=["producto", "invoice_no", "invoice_ciclo"])
    return Reserva.objects.create(
        inquilino=cliente.inquilino,
        bolsa=bolsa,
        bolsa_punto_venta=fila_limite,
        punto_venta=punto_venta,
        operacion=operacion,
        monto=monto,
    )


@transaction.atomic
def capturar(reserva):
    """
    El proveedor aprobó: la reserva se vuelve cargo firme.

    Solo se llama cuando responseCode es 00 y h2hResultCode es 0. Las dos
    cosas, no una.
    """
    reserva = Reserva.objects.select_for_update().get(pk=reserva.pk)
    if reserva.estado != EstadoReserva.ACTIVA:
        raise OperacionNoPermitida(
            f"La reserva ya está {reserva.get_estado_display().lower()}"
        )

    monto = q(reserva.monto)
    inquilino = reserva.inquilino

    _asentar(
        reserva.operacion,
        [
            (reserva.bolsa, -monto),
            (bolsa_sistema(inquilino, ClaveSistema.COSTO_VENTA), monto),
        ],
    )

    bolsa = Bolsa.objects.select_for_update().get(pk=reserva.bolsa_id)
    bolsa.reservado = q(bolsa.reservado - monto)
    bolsa.save(update_fields=["reservado", "actualizado"])

    if reserva.bolsa_punto_venta_id:
        fila = BolsaPuntoVenta.objects.select_for_update().get(
            pk=reserva.bolsa_punto_venta_id
        )
        fila.reservado = q(fila.reservado - monto)
        fila.limite = q(fila.limite - monto)
        fila.save(update_fields=["reservado", "limite", "actualizado"])

    reserva.estado = EstadoReserva.CAPTURADA
    reserva.resuelto = timezone.now()
    reserva.save(update_fields=["estado", "resuelto"])
    return reserva


@transaction.atomic
def liberar(reserva):
    """
    El proveedor rechazó: el dinero vuelve a estar disponible.

    No genera asientos porque nunca salió nada. Reservar no es gastar.
    """
    reserva = Reserva.objects.select_for_update().get(pk=reserva.pk)
    if reserva.estado != EstadoReserva.ACTIVA:
        raise OperacionNoPermitida(
            f"La reserva ya está {reserva.get_estado_display().lower()}"
        )

    monto = q(reserva.monto)
    bolsa = Bolsa.objects.select_for_update().get(pk=reserva.bolsa_id)
    bolsa.reservado = q(bolsa.reservado - monto)
    bolsa.save(update_fields=["reservado", "actualizado"])

    if reserva.bolsa_punto_venta_id:
        fila = BolsaPuntoVenta.objects.select_for_update().get(
            pk=reserva.bolsa_punto_venta_id
        )
        fila.reservado = q(fila.reservado - monto)
        fila.save(update_fields=["reservado", "actualizado"])

    reserva.estado = EstadoReserva.LIBERADA
    reserva.resuelto = timezone.now()
    reserva.save(update_fields=["estado", "resuelto"])
    return reserva


# --------------------------------------------------------------------------
# Contención
# --------------------------------------------------------------------------
@transaction.atomic
def suspender_cliente(cliente, *, autorizado_por=""):
    """
    Congela al cliente y, en cascada, a todas sus cajas.

    Sin la cascada la contención se fuga por abajo: una caja con límite
    asignado seguiría vendiendo contra él aunque el cliente esté suspendido.

    Lo que no detiene son las ventas que ya salieron hacia el proveedor. Esas
    llegan en los siguientes segundos y sus reservas se resuelven solas, así
    que el saldo se estabiliza poco después y no en el instante exacto.
    """
    cliente = Cliente.objects.select_for_update().get(pk=cliente.pk)
    cliente.estado = EstadoCliente.SUSPENDIDO
    cliente.save(update_fields=["estado"])

    for pdv in PuntoVenta.objects.select_for_update().filter(cliente=cliente):
        if pdv.estado == EstadoPuntoVenta.SUSPENDIDO:
            continue
        pdv.estado_previo = pdv.estado
        pdv.estado = EstadoPuntoVenta.SUSPENDIDO
        pdv.save(update_fields=["estado", "estado_previo"])
    return cliente


@transaction.atomic
def reactivar_cliente(cliente, *, autorizado_por=""):
    """
    Devuelve a cada caja al estado que tenía, no todas a activo.

    Una caja que estaba suspendida a mano antes de la cascada debe seguir
    suspendida después.
    """
    cliente = Cliente.objects.select_for_update().get(pk=cliente.pk)
    cliente.estado = EstadoCliente.APROBADO
    cliente.save(update_fields=["estado"])

    for pdv in PuntoVenta.objects.select_for_update().filter(cliente=cliente):
        if not pdv.estado_previo:
            continue
        pdv.estado = pdv.estado_previo
        pdv.estado_previo = ""
        pdv.save(update_fields=["estado", "estado_previo"])
    return cliente


# --------------------------------------------------------------------------
# Comprobación
# --------------------------------------------------------------------------
def cuadra(inquilino):
    """
    Dos comprobaciones que tienen que dar verdadero siempre:

    1. El saldo materializado de cada bolsa es igual a la suma de sus asientos.
    2. La suma de todas las bolsas del inquilino es cero, porque todo lo que
       entró a algún lado salió de otro.

    Devuelve (bool, lista de descuadres).
    """
    from django.db.models import Sum

    problemas = []
    total = CERO
    for bolsa in Bolsa.objects.filter(inquilino=inquilino):
        suma = bolsa.asientos.aggregate(t=Sum("monto"))["t"] or CERO
        if q(suma) != q(bolsa.saldo):
            problemas.append(
                f"{bolsa.etiqueta}: materializado {q(bolsa.saldo)}, ledger {q(suma)}"
            )
        total = q(total + q(bolsa.saldo))

    if total != CERO:
        problemas.append(f"La suma de todas las bolsas es {total}, debería ser cero")
    return not problemas, problemas
