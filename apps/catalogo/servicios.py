"""
Ruteo y validación de monto.

Dos preguntas que el catálogo tiene que saber contestar antes de que una
venta salga: a quién se le manda, y si el monto que tecleó el cajero es uno
que ese producto acepta.

La segunda parece trivial y no lo es. Un monto fuera de rango que llega al
proveedor vuelve como rechazo, y un rechazo cuesta una reserva, una
respuesta y un cajero explicándole a un cliente que no se pudo. Rechazarlo
aquí cuesta nada.

Decisión que gobierna este archivo: 12 (catálogo en dos capas).
"""
from decimal import ROUND_HALF_UP, Decimal

from .models import ProductoProveedor, TipoMonto

CENTAVO = Decimal("0.0001")
CERO = Decimal("0.0000")


def q(valor):
    """Redondea a centavos, el formato en que se validan los montos de catálogo."""
    return Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


class ErrorCatalogo(Exception):
    """Base de los errores de catálogo; la API traduce cada subclase a su código HTTP."""


class ProductoNoDisponible(ErrorCatalogo):
    """El producto está dado de baja, o su carrier lo está."""


class SinRuta(ErrorCatalogo):
    """Nadie puede surtir este producto ahora mismo."""


class MontoInvalido(ErrorCatalogo):
    """El monto no es uno que este producto acepte."""


def disponible(producto):
    """Un producto se vende solo si él, su carrier y alguna ruta están vivos."""
    if not producto.activo:
        raise ProductoNoDisponible(f"{producto.nombre} está dado de baja")
    if not producto.carrier.activo:
        raise ProductoNoDisponible(
            f"{producto.carrier.nombre} está suspendido: ninguno de sus productos se vende"
        )


def ruta_para(producto):
    """
    A quién se le manda esta venta.

    Gana la prioridad más baja entre las rutas vivas (activas y no pausadas
    por el proveedor en la última sincronización), con el proveedor vivo.
    Si no hay ninguna, el producto no se puede vender aunque esté activo:
    tener un botón en la pantalla no sirve de nada si nadie lo surte.
    """
    disponible(producto)
    ruta = (
        ProductoProveedor.objects.select_related("proveedor")
        .filter(producto=producto, activo=True, pausada_por_proveedor=False,
                proveedor__activo=True)
        .order_by("prioridad")
        .first()
    )
    if ruta is None:
        raise SinRuta(f"{producto.nombre} no tiene proveedor que lo surta")
    return ruta


def rutas_de_respaldo(producto):
    """
    Las demás, en orden. Sirven cuando la primera no contesta.

    Hoy devuelve una lista vacía casi siempre, porque solo hay un proveedor.
    Existe para que el día que haya dos, la capa de venta ya sepa pedirlas.
    """
    return list(
        ProductoProveedor.objects.select_related("proveedor")
        .filter(producto=producto, activo=True, pausada_por_proveedor=False,
                proveedor__activo=True)
        .order_by("prioridad")[1:]
    )


def validar_monto(producto, monto=None):
    """
    Devuelve el monto que se va a cobrar, o levanta diciendo por qué no.

    Para un producto de denominación fija ignora lo que venga y devuelve la
    denominación: el cajero eligió "Telcel $100", no tecleó cien.
    """
    disponible(producto)

    if producto.tipo_monto == TipoMonto.FIJO:
        return q(producto.monto_fijo)

    if producto.tipo_monto == TipoMonto.RECIBO:
        if monto is None:
            raise MontoInvalido(
                f"{producto.nombre} cobra lo que diga el recibo: falta consultarlo"
            )
        monto = q(monto)
        if monto <= CERO:
            raise MontoInvalido("El adeudo del recibo tiene que ser positivo")
        return monto

    # RANGO
    if monto is None:
        raise MontoInvalido(f"{producto.nombre} necesita que se capture un monto")
    monto = q(monto)
    if monto < q(producto.monto_min) or monto > q(producto.monto_max):
        raise MontoInvalido(
            f"{producto.nombre} acepta de {q(producto.monto_min)} a "
            f"{q(producto.monto_max)}, y se capturó {monto}"
        )
    if producto.multiplo:
        multiplo = q(producto.multiplo)
        if monto % multiplo != CERO:
            raise MontoInvalido(
                f"{producto.nombre} va en múltiplos de {multiplo}"
            )
    return monto


def por_codigo_barras(inquilino, codigo):
    """
    Lo que hace el lector cuando alguien escanea una etiqueta.

    Busca por sku porque eso es lo que lleva impreso el código. Si algún día
    cambia el precio del producto, la etiqueta sigue siendo válida: nunca
    llevó el precio.
    """
    from .models import ProductoLogico

    producto = ProductoLogico.objects.filter(
        inquilino=inquilino, sku=str(codigo).strip().upper()
    ).first()
    if producto is None:
        raise ProductoNoDisponible(f"No hay ningún producto con el código {codigo}")
    disponible(producto)
    return producto


def vendibles(inquilino, tipo=None):
    """
    Lo que la caja puede ofrecer ahora mismo: activos, de carrier activo y
    con al menos una ruta viva. Es la consulta que llena la pantalla.
    """
    from .models import ProductoLogico

    filtros = {"inquilino": inquilino, "activo": True, "carrier__activo": True}
    if tipo:
        filtros["tipo"] = tipo
    return (
        ProductoLogico.objects.filter(**filtros)
        .filter(rutas__activo=True, rutas__pausada_por_proveedor=False,
                rutas__proveedor__activo=True)
        .select_related("carrier")
        .distinct()
    )
