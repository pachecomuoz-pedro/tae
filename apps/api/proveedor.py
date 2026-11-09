"""
El puente entre la venta y el proveedor.

Existe como una función sola para que las pruebas puedan sustituir el
cliente sin tocar las vistas, y para que el día que haya un segundo
proveedor el ruteo se resuelva aquí y no repartido por la API.
"""
from django.conf import settings

from apps.catalogo import servicios as catalogo
from apps.proveedor.cliente import ClienteProveedor, Config
from apps.proveedor.emulador import Emulador
from apps.proveedor.venta import cerrar_venta

# Las pruebas ponen aquí una fábrica que devuelve un cliente con emulador.
_fabrica = None

# Un solo emulador por proceso en modo simulado: tiene que recordar las
# ventas que "procesó" para poder contestar el lookup de esa misma venta.
_emulador = None


def usar_fabrica(fn):
    """Sustituye el cliente del proveedor. Solo para pruebas y para el simulador."""
    global _fabrica
    anterior = _fabrica
    _fabrica = fn
    return anterior


def obtener_cliente():
    """
    El cliente del proveedor que usa la venta.

    Orden: una fábrica puesta por las pruebas; luego el modo simulado si
    PROVEEDOR_MODO=simulado; si no, el proveedor real. El modo simulado está
    prohibido con DEBUG apagado (lo impide settings.py), así que en
    producción siempre se llega al real. Ver docs/DECISIONES.md, decisión 18.
    """
    global _emulador
    if _fabrica is not None:
        return _fabrica()
    if settings.TAE_PROVEEDOR.get("modo") == "simulado":
        if _emulador is None:
            _emulador = Emulador(responde_en=0.2)
        cfg = Config.desde_settings()
        cfg.base_url = cfg.base_url or "https://simulado.local"
        return ClienteProveedor(cfg, sesion=_emulador)
    return ClienteProveedor()


def cerrar_con_proveedor(reserva, *, account_id, producto):
    """
    Resuelve a qué proveedor se manda y cierra la venta.

    La ruta se vuelve a pedir aquí aunque ya se validó al reservar: entre un
    momento y otro alguien pudo apagar el producto, y mandar una venta a una
    ruta muerta es un rechazo que se podía evitar.
    """
    ruta = catalogo.ruta_para(producto)
    return cerrar_venta(
        obtener_cliente(), reserva,
        account_id=account_id, product_id=ruta.product_id,
    )
