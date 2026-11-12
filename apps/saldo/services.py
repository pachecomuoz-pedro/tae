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
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import (
    Administrador,
    Cliente,
    EstadoAdministrador,
    EstadoCliente,
    EstadoPuntoVenta,
    ModoBolsa,
    PuntoVenta,
)

from .models import (
    Asiento,
    SecuenciaInvoice,
    Bolsa,
    BolsaPuntoVenta,
    ClaveSistema,
    EstadoReserva,
    EstadoRetencion,
    Motivo,
    MovimientoLimite,
    Operacion,
    Reserva,
    Retencion,
    Secuencia,
    TipoBolsa,
    TipoOperacion,
)

CENTAVO = Decimal("0.0001")
CERO = Decimal("0.0000")


def q(valor):
    """Cuatro decimales, siempre. Redondeo comercial."""
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def pesos(valor):
    """Para mensajes que lee una persona: $1,234.50. El ledger sigue en cuatro decimales."""
    return f"${Decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"


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


class RedRetenida(SaldoInsuficiente):
    """
    La venta dejaría la red por debajo de lo retenido por una corrección de
    fondeo (decisión 27). Es un caso de saldo insuficiente, pero con su
    propia clave en la API para que la pantalla diga qué hacer.
    """


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
                     punto_venta=None, operacion_origen=None, clave=None):
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
        # La restricción única (inquilino, clave) es la que de verdad impide
        # el doble cobro: si dos peticiones iguales llegan a la vez, la
        # segunda choca aquí y su transacción se deshace completa.
        clave_idempotencia=clave or None,
    )


class OperacionRepetida(ErrorSaldo):
    """La misma petición ya se aplicó: se devuelve la operación original."""

    def __init__(self, operacion):
        super().__init__(f"Ya se aplicó como {operacion.folio}")
        self.operacion = operacion


class RetencionRepetida(ErrorSaldo):
    """La misma corrección ya creó una retención."""

    def __init__(self, retencion):
        super().__init__("Esta corrección ya se aplicó")
        self.retencion = retencion


def operacion_por_clave(inquilino, clave):
    """La operación que ya se hizo con esa llave de idempotencia, o None."""
    if not clave:
        return None
    return Operacion.objects.filter(inquilino=inquilino, clave_idempotencia=clave).first()


def _ya_aplicada(inquilino, clave):
    """Si la llave ya se usó, corta antes de mover un centavo."""
    previa = operacion_por_clave(inquilino, clave)
    if previa is not None:
        raise OperacionRepetida(previa)


def _proporcion(titular, contraparte, tipos):
    """
    Cuánto pagó de verdad el titular por cada peso que tiene de esa
    contraparte, según sus propios asientos: (acreditado_neto, pagado_neto).

    Sirve para revertir el margen con el porcentaje con que se OTORGÓ y no
    con el que hoy tenga configurado. Si el margen cambió de 4% a 6% entre
    traspasos, el promedio ponderado de lo que de verdad pasó es la única
    cifra correcta; usar el 6% actual devolvería de más.
    """
    from django.db.models import Sum as _Sum

    ops = Operacion.objects.filter(
        tipo__in=tipos, asientos__bolsa=titular
    ).filter(asientos__bolsa=contraparte).values("pk")
    acreditado = Asiento.objects.filter(operacion__in=ops, bolsa=titular).aggregate(
        t=_Sum("monto"))["t"] or CERO
    pagado = -(Asiento.objects.filter(operacion__in=ops, bolsa=contraparte).aggregate(
        t=_Sum("monto"))["t"] or CERO)
    return q(acreditado), q(pagado)


# --------------------------------------------------------------------------
# Fondeo y traspaso
# --------------------------------------------------------------------------
@transaction.atomic
def fondear_administrador(administrador, monto, *, autorizado_por="", clave=None):
    """
    El Super Admin pone saldo en la bolsa de un administrador.

    Si el administrador tiene una retención activa, el fondeo la cubre
    primero (decisión 27): el fondeo se asienta completo con su folio y la
    liquidación sale aparte con el suyo, para que en la bitácora se vean
    las dos cosas. La lista de liquidaciones queda en `operacion.liquidaciones`.
    """
    monto = q(monto)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto a fondear debe ser positivo")

    inquilino = administrador.inquilino
    _ya_aplicada(inquilino, clave)
    retenciones = _bloquear_retenciones(administrador)
    # Margen del administrador: paga `monto` y recibe monto × (1 + margen).
    # La diferencia sale de la cuenta de bonificación de EPS, igual que el
    # margen del cliente (decisión 8).
    factor = Decimal(1) + Decimal(administrador.margen_pct) / Decimal(100)
    acreditado = q(monto * factor)
    bonificacion = q(acreditado - monto)
    operacion = _crear_operacion(
        inquilino, TipoOperacion.COMPRA, autorizado_por=autorizado_por, clave=clave,
    )
    movimientos = [
        (bolsa_sistema(inquilino, ClaveSistema.FONDEO), -monto),
        (bolsa_de(administrador), acreditado),
    ]
    if bonificacion != CERO:
        movimientos.append(
            (bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN), -bonificacion)
        )
    _asentar(operacion, movimientos)
    operacion.liquidaciones = _liquidar(
        administrador, retenciones, autorizado_por=autorizado_por
    )
    return operacion


@transaction.atomic
def fondear_cliente(cliente, monto, *, autorizado_por="", clave=None):
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
    _ya_aplicada(inquilino, clave)
    retenciones = _bloquear_retenciones(cliente)
    operacion = _crear_operacion(
        inquilino, TipoOperacion.COMPRA, autorizado_por=autorizado_por, clave=clave,
    )
    _asentar(
        operacion,
        [
            (bolsa_sistema(inquilino, ClaveSistema.FONDEO), -monto),
            (bolsa_de(cliente), monto),
        ],
    )
    operacion.liquidaciones = _liquidar(cliente, retenciones, autorizado_por=autorizado_por)
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
def traspasar_a_cliente(administrador, cliente, monto_pagado, *, autorizado_por="",
                        clave=None):
    """
    Carga de bolsa del administrador hacia un cliente, con su margen.

    Un solo folio para los tres asientos: el traspaso es una operación, no tres.
    """
    pagado, bonificacion, acreditado = _desglose_margen(cliente, monto_pagado)
    if pagado <= CERO:
        raise OperacionNoPermitida("El monto a traspasar debe ser positivo")
    if cliente.administrador_id != administrador.pk:
        raise OperacionNoPermitida("El cliente no pertenece a ese administrador")
    # Se releen de la base: una suspensión de hace un segundo tiene que
    # contar aunque quien llama traiga los objetos de antes.
    cliente = Cliente.objects.select_related("administrador").get(pk=cliente.pk)
    if not cliente.puede_operar:
        raise OperacionNoPermitida(f"{cliente.motivo_no_opera}: no puede recibir saldo")

    inquilino = administrador.inquilino
    _ya_aplicada(inquilino, clave)
    retenciones = _bloquear_retenciones(cliente)
    bolsa_admin = bolsa_de(administrador)
    bolsa_cliente = bolsa_de(cliente)
    bonif = bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN)
    bloqueadas = _bloquear([bolsa_admin, bolsa_cliente, bonif])
    bolsa_admin = bloqueadas[bolsa_admin.pk]
    if q(bolsa_admin.disponible) < pagado:
        raise SaldoInsuficiente(
            f"La bolsa del administrador tiene {bolsa_admin.disponible} y se piden {pagado}"
        )

    operacion = _crear_operacion(
        inquilino, TipoOperacion.TRASPASO, autorizado_por=autorizado_por, clave=clave,
    )
    movimientos = [(bolsa_admin, -pagado), (bolsa_cliente, acreditado)]
    if bonificacion != CERO:
        movimientos.append(
            (bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN), -bonificacion)
        )
    _asentar(operacion, movimientos)
    # Una retención del cliente (un fondeo directo de más que ya bajó a sus
    # cajas) se cubre con lo primero que le llegue, venga de EPS o de su
    # administrador: es lo que vuelve a dejar vender a su red.
    operacion.liquidaciones = _liquidar(cliente, retenciones, autorizado_por=autorizado_por)
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


def disponible_para_retirar(cliente, bolsa=None):
    """
    Lo que de verdad se puede sacar de la bolsa de un cliente.

    No es su saldo: hay dinero comprometido en los límites de sus cajas y en
    las ventas que todavía no cierran.

    Una venta en vuelo de una caja con límite ya está dentro de ese límite
    (el límite es lo que le queda por gastar, reservado incluido). Por eso
    solo se resta aparte lo reservado por ventas de cajas SIN límite, que
    son las de bolsa compartida. Restarlo dos veces daba un retirable
    negativo mientras una venta estaba en vuelo.
    """
    bolsa = bolsa or bolsa_de(cliente)
    reservado_en_limites = BolsaPuntoVenta.objects.filter(
        punto_venta__cliente=cliente
    ).aggregate(t=Sum("reservado"))["t"] or CERO
    libre = bolsa.saldo - limites_de(cliente) - (bolsa.reservado - reservado_en_limites)
    return q(max(libre, CERO))


@transaction.atomic
def asignar_limite(punto_venta, nuevo_limite, *, autorizado_por="", motivo="", nota=""):
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
            f"Hay {pesos(fila.reservado)} apartado en ventas en vuelo en esa caja: "
            f"el límite no puede quedar debajo de eso"
        )
    anterior = fila.limite
    if anterior == nuevo_limite:
        return fila
    if nuevo_limite < anterior and not motivo:
        # Bajar el límite es un retiro de saldo de la caja: lleva motivo,
        # igual que cualquier otro retiro.
        raise MotivoRequerido("Retirar saldo de una caja lleva motivo")
    if motivo == Motivo.OTRO and not (nota or "").strip():
        raise MotivoRequerido("El motivo 'Otro' exige una nota")
    fila.limite = nuevo_limite
    fila.save(update_fields=["limite", "actualizado"])
    MovimientoLimite.objects.create(
        inquilino=punto_venta.inquilino, punto_venta=punto_venta,
        anterior=anterior, nuevo=nuevo_limite, motivo=motivo, nota=nota[:500],
        autorizado_por=autorizado_por,
    )
    return fila


@transaction.atomic
def retirar_de_caja(punto_venta, monto, *, motivo, nota="", autorizado_por=""):
    """
    El cliente le quita saldo a una de sus cajas: baja su límite en `monto`.
    El dinero nunca salió de la bolsa del cliente (decisión 7), así que solo
    vuelve a estar libre para asignarlo a otra caja o devolverlo.

    Solo se retira lo que la caja no tiene apartado en ventas en vuelo.
    """
    monto = q(monto)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto a retirar debe ser positivo")
    if not motivo:
        raise MotivoRequerido("Todo retiro lleva motivo")
    # Mismo orden de candados que asignar_limite: bolsa del cliente y luego
    # la fila de la caja.
    Bolsa.objects.select_for_update().get(pk=bolsa_de(punto_venta.cliente).pk)
    fila = BolsaPuntoVenta.objects.select_for_update().filter(punto_venta=punto_venta).first()
    if fila is None or fila.limite <= CERO:
        raise SaldoInsuficiente(f"{punto_venta.nombre} no tiene saldo asignado")
    retirable = q(fila.limite - fila.reservado)
    if monto > retirable:
        raise SaldoInsuficiente(
            f"{punto_venta.nombre} tiene {pesos(retirable)} retirables"
            + (f" ({pesos(fila.reservado)} están en ventas en vuelo)" if fila.reservado else "")
        )
    return asignar_limite(
        punto_venta, q(fila.limite - monto), autorizado_por=autorizado_por,
        motivo=motivo, nota=nota,
    )


# --------------------------------------------------------------------------
# Devolución y ajuste
# --------------------------------------------------------------------------
@transaction.atomic
def devolver(cliente, monto_acreditado, *, motivo, nota="", autorizado_por="",
             operacion_origen=None, clave=None):
    """
    Revierte un traspaso, total o parcialmente: saldo del cliente de vuelta
    a su administrador.

    Tres reglas que la hacen segura:

    1. **Se bloquea antes de verificar.** Todas las bolsas que toca, en
       orden de llave, antes de leer cuánto hay. Dos retiros simultáneos no
       pueden pasar ambos la verificación.
    2. **Solo regresa lo que vino del administrador.** Si el Super Admin le
       abonó directo al cliente, ese dinero no es del administrador y no se
       le puede "devolver" a él: se retira hacia EPS.
    3. **El margen se revierte con el que se otorgó**, no con el que hoy
       tenga configurado (ver `_proporcion`). Con un `operacion_origen` de
       traspaso, se usa la proporción exacta de ese traspaso.
    """
    monto = q(monto_acreditado)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto a devolver debe ser positivo")
    if not motivo:
        raise MotivoRequerido("Toda devolución lleva motivo")

    inquilino = cliente.inquilino
    _ya_aplicada(inquilino, clave)
    b_cliente = bolsa_de(cliente)
    b_admin = bolsa_de(cliente.administrador)
    b_bonif = bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN)
    bloqueadas = _bloquear([b_cliente, b_admin, b_bonif])

    disponible = disponible_para_retirar(cliente, bolsa=bloqueadas[b_cliente.pk])
    if monto > disponible:
        raise SaldoInsuficiente(
            f"Solo hay {pesos(disponible)} retirable: el resto está asignado a cajas "
            f"o en ventas sin cerrar"
        )

    acreditado, pagado_total = _proporcion(
        b_cliente, b_admin, [TipoOperacion.TRASPASO, TipoOperacion.DEVOLUCION]
    )
    if monto > acreditado:
        raise SaldoInsuficiente(
            f"De su administrador solo ha recibido {pesos(max(acreditado, CERO))} netos: "
            f"lo demás se lo abonó EPS y se retira hacia EPS"
        )

    if operacion_origen is not None and operacion_origen.tipo == TipoOperacion.TRASPASO:
        a = operacion_origen.asientos.filter(bolsa=b_cliente).first()
        p = operacion_origen.asientos.filter(bolsa=b_admin).first()
        if a and p and a.monto > 0:
            acreditado, pagado_total = q(a.monto), q(-p.monto)

    pagado = q(monto * pagado_total / acreditado) if acreditado > 0 else monto
    pagado = min(pagado, monto)
    bonificacion = q(monto - pagado)

    operacion = _crear_operacion(
        inquilino,
        TipoOperacion.DEVOLUCION,
        motivo=motivo,
        nota=nota,
        autorizado_por=autorizado_por,
        operacion_origen=operacion_origen,
        clave=clave,
    )
    movimientos = [(b_cliente, -monto), (b_admin, pagado)]
    if bonificacion != CERO:
        movimientos.append((b_bonif, bonificacion))
    _asentar(operacion, movimientos)
    return operacion


@transaction.atomic
def ajustar(cliente, monto, *, motivo, nota="", autorizado_por="", a_favor=False,
            operacion_origen=None, clave=None):
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
    _ya_aplicada(inquilino, clave)
    bolsa_cliente = bolsa_de(cliente)
    bolsa_admin = bolsa_de(cliente.administrador)
    bonif = bolsa_sistema(inquilino, ClaveSistema.BONIFICACION_MARGEN)
    # Todo bloqueado antes de leer un solo saldo (ver devolver).
    bloqueadas = _bloquear([bolsa_cliente, bolsa_admin, bonif])
    bolsa_cliente = bloqueadas[bolsa_cliente.pk]
    bolsa_admin = bloqueadas[bolsa_admin.pk]

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
        disponible = disponible_para_retirar(cliente, bolsa=bolsa_cliente)
        if monto > disponible:
            raise SaldoInsuficiente(
                f"Solo hay {pesos(disponible)} retirable de la bolsa del cliente"
            )
        movimientos = [(bolsa_cliente, -monto), (bonif, monto)]

    operacion = _crear_operacion(
        inquilino,
        TipoOperacion.AJUSTE,
        motivo=motivo,
        nota=nota,
        autorizado_por=autorizado_por,
        operacion_origen=operacion_origen,
        clave=clave,
    )
    _asentar(operacion, movimientos)
    return operacion


# --------------------------------------------------------------------------
# Saldo de red, retiros a EPS y retenciones (decisión 27)
# --------------------------------------------------------------------------
def _clientes_de(administrador):
    return Cliente.objects.filter(administrador=administrador)


def saldo_red(entidad):
    """
    Todo lo que queda por vender en la rama de esa entidad.

    - Cliente: su bolsa completa (lo asignado a sus cajas vive ahí).
    - Administrador: su bolsa más las bolsas de todos sus clientes.

    Los traspasos internos no lo cambian; solo las ventas, los fondeos y
    los retiros a EPS.
    """
    if isinstance(entidad, Cliente):
        return q(bolsa_de(entidad).saldo)
    suma = Bolsa.objects.filter(cliente__administrador=entidad).aggregate(
        t=Sum("saldo"))["t"] or CERO
    return q(bolsa_de(entidad).saldo + suma)


def vendible_red(entidad):
    """Saldo de red menos lo apartado por ventas en vuelo."""
    if isinstance(entidad, Cliente):
        bolsa = bolsa_de(entidad)
        return q(bolsa.saldo - bolsa.reservado)
    fila = Bolsa.objects.filter(cliente__administrador=entidad).aggregate(
        s=Sum("saldo"), r=Sum("reservado"))
    propia = bolsa_de(entidad)
    return q(propia.saldo - propia.reservado + (fila["s"] or CERO) - (fila["r"] or CERO))


def _filtro_titular(entidad):
    if isinstance(entidad, Administrador):
        return Q(administrador=entidad)
    if isinstance(entidad, Cliente):
        return Q(cliente=entidad)
    raise OperacionNoPermitida(f"{entidad!r} no puede tener retenciones")


def retenido(entidad):
    """Lo que falta por liquidar de las retenciones activas de esa entidad."""
    total = Retencion.objects.filter(
        _filtro_titular(entidad), estado=EstadoRetencion.ACTIVA
    ).aggregate(t=Sum("pendiente"))["t"] or CERO
    return q(total)


def disponible_de(entidad):
    """
    Lo que esa entidad puede mover hoy sin tocar a nadie más.

    - Administrador: su bolsa propia (lo que puede traspasar).
    - Cliente: lo que no está asignado a cajas ni apartado en ventas.
    """
    if isinstance(entidad, Administrador):
        return q(bolsa_de(entidad).disponible)
    return disponible_para_retirar(entidad)


def resumen_saldos(entidad):
    """Los números que ven el Administrador y el Cliente: Disponible y Saldo red."""
    return {
        "disponible": str(disponible_de(entidad)),
        "saldo_red": str(saldo_red(entidad)),
        "retenido": str(retenido(entidad)),
    }


def _bloquear_retenciones(*entidades):
    """
    Bloquea las retenciones activas de esas entidades, en orden de pk.

    Va SIEMPRE antes de bloquear bolsas. Venta, fondeo y traspaso toman los
    candados en el mismo orden (retenciones, luego bolsas) y así no se
    abrazan entre sí.
    """
    filtro = Q(pk__in=[])
    for e in entidades:
        if e is not None:
            filtro |= _filtro_titular(e)
    return list(
        Retencion.objects.select_for_update()
        .filter(filtro, estado=EstadoRetencion.ACTIVA)
        .order_by("pk")
    )


def _bolsa_bloqueada_y_fondeo(titular):
    """
    La bolsa del titular y las dos cuentas de EPS (fondeo y bonificación),
    bloqueadas juntas en orden de pk antes de leer nada.
    """
    propia = bolsa_de(titular)
    fondeo = bolsa_sistema(titular.inquilino, ClaveSistema.FONDEO)
    bonif = bolsa_sistema(titular.inquilino, ClaveSistema.BONIFICACION_MARGEN)
    bloqueadas = _bloquear([propia, fondeo, bonif])
    return bloqueadas[propia.pk], bloqueadas[fondeo.pk]


def _retirable_bloqueado(titular, bolsa):
    if isinstance(titular, Administrador):
        return q(max(bolsa.saldo - bolsa.reservado, CERO))
    return disponible_para_retirar(titular, bolsa=bolsa)


def _asentar_retiro(titular, bolsa, fondeo, monto, *, motivo, nota, autorizado_por,
                    operacion_origen=None, retencion=None, clave=None):
    """
    Regresa `monto` a EPS. Si el titular es un administrador con margen, lo
    que fue bonificación vuelve a la cuenta de bonificación y lo que pagó a
    la de fondeo, en la proporción en que de verdad lo recibió. Así un
    retiro es el inverso exacto de sus fondeos.
    """
    operacion = _crear_operacion(
        titular.inquilino, TipoOperacion.RETIRO, motivo=motivo, nota=nota,
        autorizado_por=autorizado_por, operacion_origen=operacion_origen, clave=clave,
    )
    if retencion is not None:
        operacion.retencion = retencion
        operacion.save(update_fields=["retencion"])
    movimientos = [(bolsa, -monto)]
    a_bonificacion = CERO
    if isinstance(titular, Administrador):
        acreditado, pagado = _proporcion(
            bolsa, fondeo, [TipoOperacion.COMPRA, TipoOperacion.RETIRO]
        )
        if acreditado > 0 and pagado < acreditado:
            a_bonificacion = q(monto - q(monto * pagado / acreditado))
    movimientos.append((fondeo, q(monto - a_bonificacion)))
    if a_bonificacion != CERO:
        movimientos.append(
            (bolsa_sistema(titular.inquilino, ClaveSistema.BONIFICACION_MARGEN), a_bonificacion)
        )
    _asentar(operacion, movimientos)
    return operacion


@transaction.atomic
def retirar_a_eps(titular, monto, *, motivo, nota="", autorizado_por="",
                  operacion_origen=None, clave=None):
    """
    El Super Admin regresa saldo de un administrador o de un cliente a la
    cuenta de fondeo de EPS. Es el inverso exacto de un fondeo.

    Solo se toma lo disponible: nunca lo apartado por ventas en vuelo, y en
    un cliente tampoco lo asignado a sus cajas (para eso primero se baja el
    límite de la caja). Si hace falta más, ver `corregir_fondeo`.
    """
    monto = q(monto)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto a retirar debe ser positivo")
    if not motivo:
        raise MotivoRequerido("Todo retiro lleva motivo")

    _ya_aplicada(titular.inquilino, clave)
    bolsa, fondeo = _bolsa_bloqueada_y_fondeo(titular)
    disponible = _retirable_bloqueado(titular, bolsa)
    if monto > disponible:
        raise SaldoInsuficiente(
            f"{titular.nombre} tiene {pesos(disponible)} disponible para retirar y se piden {pesos(monto)}"
        )
    return _asentar_retiro(
        titular, bolsa, fondeo, monto, motivo=motivo, nota=nota,
        autorizado_por=autorizado_por, operacion_origen=operacion_origen, clave=clave,
    )


@transaction.atomic
def corregir_fondeo(titular, monto, *, motivo=Motivo.FONDEO_EXCEDENTE, nota="",
                    autorizado_por="", operacion_origen=None, clave=None):
    """
    Corrige un fondeo de más, aunque el dinero ya haya bajado por la cadena.

    1. Se retira directo lo que el titular tenga disponible.
    2. El faltante se convierte en una retención sobre su rama: la red
       sigue vendiendo hasta llegar a ese monto y ahí se detiene, hasta que
       un fondeo la cubra (decisión 27).

    Devuelve (retirado, operacion_o_None, retencion_o_None).
    """
    monto = q(monto)
    if monto <= CERO:
        raise OperacionNoPermitida("El monto a corregir debe ser positivo")
    if not motivo:
        raise MotivoRequerido("Toda corrección lleva motivo")
    if motivo == Motivo.OTRO and not nota.strip():
        raise MotivoRequerido("El motivo 'Otro' exige una nota")

    _ya_aplicada(titular.inquilino, clave)
    if clave:
        previa = Retencion.objects.filter(
            inquilino=titular.inquilino, clave_idempotencia=clave
        ).first()
        if previa is not None:
            raise RetencionRepetida(previa)
    bolsa, fondeo = _bolsa_bloqueada_y_fondeo(titular)
    directo = min(_retirable_bloqueado(titular, bolsa), monto)
    faltante = q(monto - directo)

    retencion = None
    if faltante > CERO:
        retencion = Retencion.objects.create(
            inquilino=titular.inquilino,
            administrador=titular if isinstance(titular, Administrador) else None,
            cliente=titular if isinstance(titular, Cliente) else None,
            monto=faltante,
            pendiente=faltante,
            motivo=motivo,
            nota=nota,
            autorizado_por=autorizado_por,
            operacion_origen=operacion_origen,
            clave_idempotencia=clave or None,
        )

    operacion = None
    if directo > CERO:
        operacion = _asentar_retiro(
            titular, bolsa, fondeo, directo, motivo=motivo, nota=nota,
            autorizado_por=autorizado_por, operacion_origen=operacion_origen,
            retencion=retencion, clave=clave,
        )
    return directo, operacion, retencion


def _liquidar(titular, retenciones, *, autorizado_por=""):
    """
    Cobra las retenciones ya bloqueadas con lo que el titular tenga
    disponible después de recibir dinero. Una operación por retención, para
    que cada liquidación tenga su folio y apunte a la retención que cubre.
    """
    if not retenciones:
        return []
    bolsa, fondeo = _bolsa_bloqueada_y_fondeo(titular)
    hechas = []
    for retencion in retenciones:
        disponible = _retirable_bloqueado(titular, bolsa)
        aplica = q(min(retencion.pendiente, disponible))
        if aplica <= CERO:
            break
        hechas.append(_asentar_retiro(
            titular, bolsa, fondeo, aplica,
            motivo=retencion.motivo,
            nota="Liquidación de retención por corrección de fondeo",
            autorizado_por=autorizado_por or "sistema",
            operacion_origen=retencion.operacion_origen,
            retencion=retencion,
        ))
        bolsa.refresh_from_db()
        retencion.pendiente = q(retencion.pendiente - aplica)
        campos = ["pendiente"]
        if retencion.pendiente <= CERO:
            retencion.estado = EstadoRetencion.LIQUIDADA
            retencion.resuelto = timezone.now()
            retencion.resuelto_por = autorizado_por or "sistema"
            campos += ["estado", "resuelto", "resuelto_por"]
        retencion.save(update_fields=campos)
    return hechas


@transaction.atomic
def cancelar_retencion(retencion, *, nota, autorizado_por=""):
    """
    El Super Admin la da por perdonada: la rama vuelve a vender todo su
    saldo. No mueve dinero; lo ya liquidado se queda liquidado.
    """
    if not (nota or "").strip():
        raise MotivoRequerido("Cancelar una retención exige una nota")
    retencion = Retencion.objects.select_for_update().get(pk=retencion.pk)
    if retencion.estado != EstadoRetencion.ACTIVA:
        raise OperacionNoPermitida(
            f"La retención ya está {retencion.get_estado_display().lower()}"
        )
    retencion.estado = EstadoRetencion.CANCELADA
    retencion.resuelto = timezone.now()
    retencion.resuelto_por = autorizado_por
    retencion.nota_resolucion = nota[:500]
    retencion.save(update_fields=["estado", "resuelto", "resuelto_por", "nota_resolucion"])
    return retencion


def _verificar_retenciones(punto_venta, retenciones, monto):
    """
    Con las retenciones de la rama ya bloqueadas, revisa que la venta deje
    a cada titular retenido con al menos lo retenido en su red.
    """
    cliente = punto_venta.cliente
    por_titular = {}
    for r in retenciones:
        titular = cliente.administrador if r.administrador_id else cliente
        clave = ("a", titular.pk) if r.administrador_id else ("c", titular.pk)
        actual = por_titular.get(clave, (titular, CERO))
        por_titular[clave] = (titular, q(actual[1] + r.pendiente))

    for titular, pendiente in por_titular.values():
        vendible = vendible_red(titular)
        if q(vendible - monto) < pendiente:
            quien = "tu administrador" if isinstance(titular, Administrador) else "tu cliente"
            raise RedRetenida(
                f"La red de {titular.nombre} tiene {pesos(pendiente)} retenidos por una "
                f"corrección de fondeo y solo puede vender "
                f"{pesos(max(vendible - pendiente, CERO))}. "
                f"Se libera cuando {quien} reciba fondeo."
            )


def estado_de_venta(punto_venta):
    """
    Si esta caja puede vender ahora y cuánto, con el motivo en palabras del
    cajero cuando no. Es lo que la pantalla muestra ANTES de vender, para
    no dejar capturar una venta que el servidor va a rechazar.

    Devuelve {"puede": bool, "motivo": str, "disponible": Decimal|None}.
    """
    pdv = PuntoVenta.objects.select_related("cliente__administrador").get(pk=punto_venta.pk)
    cliente = pdv.cliente
    admin = cliente.administrador

    def no(motivo):
        return {"puede": False, "motivo": motivo, "disponible": CERO}

    if pdv.estado != EstadoPuntoVenta.ACTIVO or not pdv.activo:
        return no(f"Esta caja está {pdv.get_estado_display().lower()}. Habla con tu cliente.")
    if not admin.puede_operar:
        return no("La red de tu administrador está suspendida. Habla con tu cliente.")
    if not cliente.puede_operar:
        return no(f"{cliente.nombre} está {cliente.get_estado_display().lower()}.")

    bolsa = bolsa_de(cliente)
    disponible = q(bolsa.saldo - bolsa.reservado)
    if cliente.modo_bolsa == ModoBolsa.ASIGNADA:
        fila = BolsaPuntoVenta.objects.filter(punto_venta=pdv).first()
        if fila is None or fila.limite <= CERO:
            return no(
                "Esta caja no tiene saldo asignado. Pide a tu cliente que le asigne "
                "un límite antes de vender."
            )
        disponible = min(disponible, q(fila.limite - fila.reservado))

    for titular in (admin, cliente):
        pendiente = retenido(titular)
        if pendiente > CERO:
            disponible = min(disponible, q(vendible_red(titular) - pendiente))

    if disponible <= CERO:
        if retenido(admin) > CERO or retenido(cliente) > CERO:
            return no(
                "Hay saldo retenido por una corrección de fondeo y la red ya llegó "
                "a ese monto. Se libera cuando reciba fondeo."
            )
        return no("No hay saldo para vender. Pide saldo a tu cliente.")
    return {"puede": True, "motivo": "", "disponible": q(disponible)}


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

    punto_venta = PuntoVenta.objects.select_related("cliente__administrador").get(
        pk=punto_venta.pk
    )
    if not punto_venta.puede_vender:
        raise OperacionNoPermitida(
            f"{punto_venta.nombre} no puede vender en este momento"
        )

    cliente = punto_venta.cliente
    # Candados en el orden de siempre: primero las retenciones de la rama
    # (si las hay), luego la bolsa. Sin retenciones esta consulta no bloquea
    # nada y la venta sigue igual que antes.
    retenciones = _bloquear_retenciones(cliente.administrador, cliente)
    bolsa = Bolsa.objects.select_for_update().get(pk=bolsa_de(cliente).pk)
    if retenciones:
        _verificar_retenciones(punto_venta, retenciones, monto)
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
    costo = bolsa_sistema(inquilino, ClaveSistema.COSTO_VENTA)

    # Primero se suelta lo apartado y después se cobra, con las dos bolsas
    # ya bloqueadas en orden. Al revés, hay un instante en que el apartado
    # supera al saldo y la base lo rechaza (bolsa_red_nunca_negativa).
    bloqueadas = _bloquear([reserva.bolsa, costo])
    bolsa = bloqueadas[reserva.bolsa_id]
    bolsa.reservado = q(bolsa.reservado - monto)
    bolsa.save(update_fields=["reservado", "actualizado"])

    _asentar(reserva.operacion, [(bolsa, -monto), (costo, monto)])

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


@transaction.atomic
def suspender_administrador(administrador, *, autorizado_por=""):
    """
    Detiene a toda la red del administrador: no traspasa, sus clientes no
    reciben saldo y ninguna de sus cajas vende.

    No reescribe el estado de cada cliente ni de cada caja: la cascada se
    deriva en Cliente.puede_operar. Así, al reactivar, cada quien queda
    como estaba, incluido un cliente que ya estaba suspendido a mano.
    """
    administrador = Administrador.objects.select_for_update().get(pk=administrador.pk)
    administrador.estado = EstadoAdministrador.SUSPENDIDO
    administrador.save(update_fields=["estado"])
    return administrador


@transaction.atomic
def reactivar_administrador(administrador, *, autorizado_por=""):
    """Levanta la suspensión. Ver suspender_administrador."""
    administrador = Administrador.objects.select_for_update().get(pk=administrador.pk)
    administrador.estado = EstadoAdministrador.ACTIVO
    administrador.save(update_fields=["estado"])
    return administrador


@transaction.atomic
def suspender_punto_venta(punto_venta, *, autorizado_por=""):
    """
    Suspende una sola caja. `estado_previo` queda vacío a propósito: es la
    marca de "suspendida a mano", y por eso reactivar al cliente no la revive.
    """
    pdv = PuntoVenta.objects.select_for_update().get(pk=punto_venta.pk)
    if pdv.estado != EstadoPuntoVenta.ACTIVO:
        raise OperacionNoPermitida(
            f"{pdv.nombre} está {pdv.get_estado_display().lower()}: solo se suspende una caja activa"
        )
    pdv.estado = EstadoPuntoVenta.SUSPENDIDO
    pdv.estado_previo = ""
    pdv.save(update_fields=["estado", "estado_previo"])
    return pdv


@transaction.atomic
def reactivar_punto_venta(punto_venta, *, autorizado_por=""):
    """
    Reactiva una caja suspendida a mano. Si la suspendió la cascada de su
    cliente, se reactiva reactivando al cliente: si no, al reactivar al
    cliente esta caja quedaría en un estado que nadie eligió.
    """
    pdv = PuntoVenta.objects.select_for_update().select_related("cliente").get(pk=punto_venta.pk)
    if pdv.estado != EstadoPuntoVenta.SUSPENDIDO:
        raise OperacionNoPermitida(f"{pdv.nombre} no está suspendida")
    if pdv.cliente.estado == EstadoCliente.SUSPENDIDO:
        raise OperacionNoPermitida(
            f"{pdv.cliente.nombre} está suspendido: reactiva primero al cliente"
        )
    pdv.estado = EstadoPuntoVenta.ACTIVO
    pdv.estado_previo = ""
    pdv.save(update_fields=["estado", "estado_previo"])
    return pdv


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
