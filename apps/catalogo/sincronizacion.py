"""
Sincronización diaria del catálogo con el proveedor.

Las Reglas Generales de Emida piden que el punto de venta descargue y
actualice cada día sus productos (GetProductList y ProductFlowInfoService).
Esto hace exactamente eso, y nada más:

1. Copia lo que Emida lista para el terminal en ProductoDelProveedor.
2. Marca como no disponible lo que dejó de listar.
3. Pausa las rutas cuyo product_id desapareció y reanuda las que volvieron.

Lo que NO hace, a propósito: crear productos en la pantalla del cajero.
Qué se vende, con qué nombre y en qué orden lo decide una persona con
`vincular_producto` (decisión 12). Tampoco toca `activo` de las rutas: ese
campo es la decisión de una persona y la sincronización nunca la revierte.

Candado: si Emida responde con una lista vacía, no se cambia nada. Una lista
vacía por un error del lado del proveedor pausaría todas las ventas.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import ProductoDelProveedor, ProductoProveedor, Proveedor

log = logging.getLogger("tae.catalogo")

# No tenemos el contrato exacto de la respuesta, así que se buscan los
# nombres más probables. Lo que no se reconoce queda igual en `datos`.
CAMPOS_ID = ("productId", "id", "productID", "product_id")
CAMPOS_DESCRIPCION = ("description", "productDescription", "name", "productName")
CAMPOS_CARRIER = (
    "carrierName", "carrier", "carrierDescription", "carrier_name", "carrierDesc",
    "operator", "operatorName", "brand", "brandName", "providerName", "carrierId",
)

# Cuando Emida no manda la compañía en un campo propio, casi siempre viene en
# la descripción ("PRUEBA Recarga Movistar $10"). Se busca la marca ahí, en
# este orden: el primero que aparezca gana. Las claves van en minúsculas y
# sin acentos; el valor es como se muestra en la caja.
MARCAS = (
    ("telcel", "Telcel"),
    ("movistar", "Movistar"),
    ("at&t", "AT&T"), ("att ", "AT&T"), ("at t ", "AT&T"),
    ("unefon", "Unefon"),
    ("bait", "Bait"),
    ("virgin", "Virgin Mobile"),
    ("weex", "Weex"),
    ("altan", "Altan"),
    ("pillofon", "Pillofon"),
    ("oui", "OUI"),
    ("flash mobile", "Flash Mobile"),
    ("diri", "Diri"),
    ("izzi", "izzi"),
    ("cfe", "CFE"),
    ("telmex", "Telmex"),
    ("totalplay", "Totalplay"),
    ("megacable", "Megacable"),
    ("sky", "SKY"),
    ("dish", "Dish"),
    ("netflix", "Netflix"),
    ("spotify", "Spotify"),
    ("xbox", "Xbox"),
    ("playstation", "PlayStation"),
    ("nintendo", "Nintendo"),
    ("google play", "Google Play"),
    ("amazon", "Amazon"),
)


def inferir_carrier(*textos):
    """
    La marca que aparece en el texto, o "" si ninguna. Se busca palabra por
    palabra (con espacios alrededor) para que "bait" no coincida dentro de
    otra palabra.
    """
    import unicodedata

    texto = " ".join(str(t or "") for t in textos).lower()
    texto = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    relleno = f" {texto.replace('/', ' ').replace('-', ' ').replace('$', ' ')} "
    for clave, nombre in MARCAS:
        aguja = clave if clave.endswith(" ") else f"{clave} "
        if f" {aguja}" in relleno or (clave.endswith(" ") and f" {clave}" in relleno):
            return nombre
    return ""
CAMPOS_MONTO = ("amount", "price", "faceValue", "denomination", "productAmount")
CAMPOS_LISTA = ("items", "products", "data", "content", "productList", "result")


class SincronizacionVacia(Exception):
    """Emida no devolvió productos. No se tocó nada."""


def _primero(datos, campos):
    for campo in campos:
        valor = datos.get(campo)
        if valor not in (None, ""):
            # Un carrier puede venir como objeto: {"id": 1, "name": "Telcel"}.
            if isinstance(valor, dict):
                return valor.get("name") or valor.get("description") or valor.get("id")
            return valor
    return None


def _monto(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def extraer_lista(datos):
    """La respuesta puede ser una lista, o un objeto que envuelve la lista."""
    if isinstance(datos, list):
        return datos
    if isinstance(datos, dict):
        for campo in CAMPOS_LISTA:
            if isinstance(datos.get(campo), list):
                return datos[campo]
    return []


def _flujos_por_producto(cliente):
    """
    product-flow indexado por product_id. Si falla, se sigue sin él:
    los textos de ayuda son deseables, pero no justifican dejar de
    actualizar la disponibilidad de los productos.
    """
    try:
        respuesta = cliente.product_flow()
    except Exception as e:  # noqa: BLE001 — cualquier falla aquí es no crítica
        log.warning("product-flow no disponible: %s", e)
        return {}
    flujos = {}
    for item in extraer_lista(respuesta.datos):
        if isinstance(item, dict):
            pid = _primero(item, CAMPOS_ID)
            if pid is not None:
                flujos[str(pid)] = item
    return flujos


@transaction.atomic
def sincronizar(inquilino, cliente, *, proveedor_clave="axexo"):
    """
    Trae los productos del terminal y actualiza la base. Devuelve un resumen.

    Las llamadas al proveedor van antes de cualquier escritura: si Emida no
    contesta, la excepción sale sin haber tocado nada.
    """
    # En una base nueva (UAT recién instalado) el proveedor aún no existe:
    # se crea aquí para que la primera sincronización no exija un paso extra.
    proveedor, _ = Proveedor.objects.get_or_create(
        inquilino=inquilino, clave=proveedor_clave,
        defaults={"nombre": "axexo / Emida"},
    )

    lista = [x for x in extraer_lista(cliente.productos().datos) if isinstance(x, dict)]
    if not lista:
        raise SincronizacionVacia(
            "Emida no devolvió productos para este terminal. No se cambió nada."
        )
    flujos = _flujos_por_producto(cliente)

    ahora = timezone.now()
    vistos, nuevos = set(), []
    for item in lista:
        pid = _primero(item, CAMPOS_ID)
        if pid is None:
            continue
        pid = str(pid)
        vistos.add(pid)
        _, creado = ProductoDelProveedor.objects.update_or_create(
            proveedor=proveedor,
            product_id=pid,
            defaults={
                "descripcion": str(_primero(item, CAMPOS_DESCRIPCION) or "")[:200],
                "carrier": str(
                    _primero(item, CAMPOS_CARRIER)
                    or inferir_carrier(_primero(item, CAMPOS_DESCRIPCION))
                    or ""
                )[:80],
                "monto": _monto(_primero(item, CAMPOS_MONTO)),
                "datos": item,
                "flujo": flujos.get(pid),
                "disponible": True,
                "visto": ahora,
            },
        )
        if creado:
            nuevos.append(pid)

    desaparecidos = list(
        ProductoDelProveedor.objects.filter(proveedor=proveedor, disponible=True)
        .exclude(product_id__in=vistos)
        .values_list("product_id", flat=True)
    )
    ProductoDelProveedor.objects.filter(
        proveedor=proveedor, product_id__in=desaparecidos
    ).update(disponible=False)

    rutas = ProductoProveedor.objects.filter(proveedor=proveedor)
    pausadas = list(
        rutas.filter(pausada_por_proveedor=False).exclude(product_id__in=vistos)
        .values_list("product_id", flat=True)
    )
    reanudadas = list(
        rutas.filter(pausada_por_proveedor=True, product_id__in=vistos)
        .values_list("product_id", flat=True)
    )
    rutas.exclude(product_id__in=vistos).update(pausada_por_proveedor=True)
    rutas.filter(product_id__in=vistos).update(pausada_por_proveedor=False)

    vinculados = set(rutas.values_list("product_id", flat=True))
    resumen = {
        "productos": len(vistos),
        "nuevos": nuevos,
        "ya_no_listados": desaparecidos,
        "rutas_pausadas": pausadas,
        "rutas_reanudadas": reanudadas,
        "sin_vincular": sorted(vistos - vinculados),
        "con_flujo": sum(1 for pid in vistos if pid in flujos),
    }
    log.info("catálogo sincronizado: %s", {k: (len(v) if isinstance(v, list) else v)
                                            for k, v in resumen.items()})
    return resumen


# ---------------------------------------------------------------------------
# Carga inicial
# ---------------------------------------------------------------------------
def _tipo_de(oferta):
    """
    Tiempo aire, pin o servicio, según la categoría que reporte Emida
    (`productCategory` de GetProductListExt u otros nombres probables). Sin
    categoría, tiempo aire: es lo único que se certifica hoy.
    """
    from .models import TipoProducto

    texto = " ".join(
        str(oferta.datos.get(c, "")) for c in
        ("productCategory", "category", "categoryName", "productType", "type")
    ).lower()
    if "pin" in texto or "gift" in texto:
        return TipoProducto.PIN
    if "bill" in texto or "servic" in texto or "payment" in texto:
        return TipoProducto.SERVICIO
    return TipoProducto.TIEMPO_AIRE


def _carrier_legible(oferta):
    """
    El carrier guardado, salvo que sea solo un número (`carrierId`): un id
    no le dice nada al cajero, y para eso es mejor deducirlo del nombre.
    """
    valor = (oferta.carrier or "").strip()
    return "" if valor.isdigit() else valor


def reasignar_carriers(inquilino):
    """
    Mueve a su compañía los productos que quedaron en "Otros" porque Emida
    no mandó el carrier. Solo cambia el carrier, nunca el nombre ni el
    precio, y no toca lo que alguien ya acomodó a mano en otra compañía.

    Devuelve [(sku, compañía)].
    """
    from django.utils.text import slugify

    from .models import Carrier, ProductoLogico

    movidos = []
    for producto in ProductoLogico.objects.filter(
        inquilino=inquilino, carrier__clave="otros"
    ).select_related("carrier"):
        nombre = inferir_carrier(producto.nombre)
        if not nombre:
            continue
        carrier, _ = Carrier.objects.get_or_create(
            inquilino=inquilino, clave=slugify(nombre)[:40] or "otros",
            defaults={"nombre": nombre, "tipo": producto.carrier.tipo},
        )
        producto.carrier = carrier
        producto.save(update_fields=["carrier"])
        movidos.append((producto.sku, nombre))
    return movidos


@transaction.atomic
def vincular_todo(inquilino, *, proveedor_clave="axexo"):
    """
    Pone en la pantalla de la caja todo lo que Emida lista y aún no está
    vinculado. Solo se usa en la instalación (`manage.py instalar`), para que
    una tienda nueva arranque con el catálogo real completo.

    Después de instalar, la sincronización diaria vuelve a la regla de la
    decisión 24: trae lo nuevo, pero no lo publica solo.

    Solo vincula productos de denominación fija (con monto legible). Los de
    monto abierto o de recibo se reportan para darlos de alta a mano.
    """
    from django.utils.text import slugify

    from .models import Carrier, ProductoLogico, TipoMonto

    proveedor = Proveedor.objects.get(inquilino=inquilino, clave=proveedor_clave)
    vinculados = set(
        ProductoProveedor.objects.filter(proveedor=proveedor).values_list("product_id", flat=True)
    )
    creados, sin_monto = [], []
    ofertas = ProductoDelProveedor.objects.filter(proveedor=proveedor, disponible=True)
    for orden, oferta in enumerate(ofertas.order_by("carrier", "monto"), start=1):
        if oferta.product_id in vinculados:
            continue
        if oferta.monto is None or oferta.monto <= 0:
            sin_monto.append(oferta.product_id)
            continue
        nombre_carrier = _carrier_legible(oferta) or inferir_carrier(oferta.descripcion) or "Otros"
        tipo = _tipo_de(oferta)
        carrier, _ = Carrier.objects.get_or_create(
            inquilino=inquilino, clave=(slugify(nombre_carrier) or "otros")[:40],
            defaults={"nombre": nombre_carrier[:80], "tipo": tipo},
        )
        producto, _ = ProductoLogico.objects.get_or_create(
            inquilino=inquilino, sku=f"EMD-{oferta.product_id}"[:40],
            defaults={
                "carrier": carrier,
                "nombre": (oferta.descripcion or f"{nombre_carrier} ${oferta.monto:,.0f}")[:120],
                "tipo": tipo, "tipo_monto": TipoMonto.FIJO, "monto_fijo": oferta.monto,
                "orden": orden,
            },
        )
        ProductoProveedor.objects.create(
            producto=producto, proveedor=proveedor, product_id=oferta.product_id
        )
        creados.append(oferta.product_id)
    return {"vinculados": creados, "sin_monto": sin_monto}
