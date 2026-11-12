"""
El cierre de una venta: timeout, consulta y reintentos.

Esta es la pieza que decide si una venta se cobra o se libera, y es donde un
error cuesta dinero que no vuelve. Vale la pena leerla entera antes de
tocarla.

La regla de la que cuelga todo: **el silencio no es un rechazo**. Si el
proveedor no contesta, la venta puede haber ocurrido. Liberar el saldo ahí
significa que el tendero vuelve a vender, el proveedor cobra las dos, y
nadie se entera hasta la conciliación del día siguiente.

Por eso el único camino de un timeout es preguntar por Invoice-No hasta que
el proveedor diga sí o diga no. Si ni así contesta, la venta queda
SIN_RESOLVER con su reserva viva, y la resuelve la conciliación. Una reserva
atorada molesta a un tendero; un doble cobro le cuesta a alguien.

Decisión que gobierna este archivo: 10 (docs/DECISIONES.md).
"""
import json
import logging
import time
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.saldo import services
from apps.saldo.models import EstadoReserva

from .cliente import NoAutenticado, RechazoProveedor, Respuesta, SinRespuesta
from .modelos import Desenlace, IntentoProveedor, RegistroEnvio

log = logging.getLogger("tae.venta")


class ProveedorNoDisponible(Exception):
    """
    La venta no llegó a salir: no hubo forma de autenticarse con el proveedor.

    Se distingue de SinRespuesta a propósito. Aquí sabemos con certeza que
    el proveedor nunca recibió la venta, porque falló el login que va antes
    del envío; por eso liberar la reserva es seguro. La API lo traduce a 503
    para que el cajero lea "intenta de nuevo" y no un error genérico.
    """


@dataclass
class Cierre:
    """Cómo terminó la venta y con qué evidencia."""

    desenlace: str
    reserva: object
    respuesta: Respuesta = None
    intentos: int = 0
    detalle: str = ""
    # Lo que dijo el proveedor: su texto y su código. Es lo que la pantalla
    # le muestra al cajero en un rechazo, y lo que soporte necesita.
    mensaje: str = ""
    codigo: str = ""

    @property
    def cobrada(self):
        return self.desenlace == Desenlace.APROBADA

    @property
    def sin_resolver(self):
        return self.desenlace == Desenlace.SIN_RESOLVER


def _registrar(registro, *, tipo, respuesta=None, error="", rechazo=None):
    """
    Una fila por llamada al proveedor. En un rechazo 4xx también se guarda
    el cuerpo que mandó el proveedor: es donde viene el motivo, y sin él no
    hay forma de diagnosticar después (`manage.py ver_venta FOLIO`).
    """
    cuerpo = getattr(respuesta, "crudo", "") or ""
    http = getattr(respuesta, "http", 0) or 0
    codigo = getattr(respuesta, "response_code", "") or ""
    if rechazo is not None:
        cuerpo = json.dumps(rechazo.cuerpo, ensure_ascii=False, default=str)
        http = rechazo.http or 0
        codigo = rechazo.codigo or ""
    IntentoProveedor.objects.create(
        envio=registro,
        tipo=tipo,
        http=http,
        response_code=codigo,
        h2h_result_code=getattr(respuesta, "h2h_result_code", "") or "",
        aprobada=bool(getattr(respuesta, "aprobada", False)),
        cuerpo=cuerpo[:4000],
        error=error[:500],
    )


def cerrar_venta(cliente, reserva, *, account_id, product_id, dormir=time.sleep):
    """
    Manda la venta y la deja resuelta, o explícitamente sin resolver.

    `dormir` se inyecta para que las pruebas no esperen de verdad. En
    producción es time.sleep.
    """
    cfg = cliente.config
    invoice_no = str(reserva.operacion.invoice_no)

    registro = RegistroEnvio.objects.create(
        operacion=reserva.operacion,
        invoice_no=invoice_no,
        product_id=str(product_id),
        account_id=str(account_id),
        monto=reserva.monto,
    )

    # --- primer envío ----------------------------------------------------
    # Se marca cuándo salió: el proveedor pide la primera consulta a los 40
    # segundos CONTADOS DESDE EL ENVÍO, no desde que se nos acabó la espera.
    salio = time.monotonic()

    try:
        respuesta = cliente.topup(
            invoice_no=invoice_no, product_id=product_id,
            account_id=account_id, amount=reserva.monto,
        )
        _registrar(registro, tipo="envio", respuesta=respuesta)
        return _resolver(registro, reserva, respuesta, intentos=0)

    except RechazoProveedor as e:
        # Rechazo explícito: la venta no ocurrió. 400, 404, 422 y el 401 ya
        # vienen por aquí. Liberar es seguro porque el proveedor lo dijo.
        _registrar(registro, tipo="envio", error=str(e), rechazo=e)
        services.liberar(reserva)
        return _cerrar(
            registro, Desenlace.RECHAZADA, reserva,
            detalle=f"HTTP {e.http}: {e}", intentos=0,
            mensaje=str(e), codigo=e.codigo or f"HTTP {e.http}",
        )

    except NoAutenticado as e:
        # Falló el login, que ocurre ANTES de mandar la venta: la venta no
        # salió. Es el único caso, junto con un rechazo explícito, en que
        # liberar es seguro. Sin este manejo la excepción subía como 500 y la
        # reserva quedaba viva para siempre con el saldo del tendero atorado.
        _registrar(registro, tipo="envio", error=str(e))
        services.liberar(reserva)
        _cerrar(
            registro, Desenlace.RECHAZADA, reserva,
            detalle=f"No salió: sin autenticación con el proveedor ({e})"[:500],
        )
        log.error("venta %s no salió: %s", reserva.operacion.folio, e)
        raise ProveedorNoDisponible(
            "El proveedor no está disponible en este momento. La venta no se "
            "hizo y el saldo no se tocó; intenta de nuevo en unos minutos."
        ) from e

    except SinRespuesta as e:
        _registrar(registro, tipo="envio", error=str(e))
        log.warning("venta %s sin respuesta, se consulta", reserva.operacion.folio)

    # --- el proveedor no contestó: se consulta ---------------------------
    # Lo que falte para llegar a los 40 segundos desde el envío.
    falta = cfg.lookup_desde_envio - (time.monotonic() - salio)
    dormir(max(falta, 0))

    for intento in range(1, cfg.max_lookups + 1):
        try:
            respuesta = cliente.lookup(invoice_no)
            _registrar(registro, tipo="lookup", respuesta=respuesta)

            if respuesta.aprobada:
                return _resolver(registro, reserva, respuesta, intentos=intento)

            # Contestó con un código que no es de aprobación. Puede ser que
            # la rechazó, o que todavía la está procesando. La diferencia la
            # da el folio del carrier: sin folio, no hay nada entregado.
            if respuesta.control_no or respuesta.transaction_id:
                return _resolver(registro, reserva, respuesta, intentos=intento)

            log.info(
                "lookup %s intento %s: todavía sin resolver", invoice_no, intento
            )

        except RechazoProveedor as e:
            if e.http == 404:
                # El proveedor no conoce ese Invoice-No: la venta nunca entró.
                _registrar(registro, tipo="lookup", error=str(e), rechazo=e)
                services.liberar(reserva)
                return _cerrar(
                    registro, Desenlace.RECHAZADA, reserva,
                    detalle="El proveedor no tiene registro de la venta",
                    intentos=intento,
                )
            _registrar(registro, tipo="lookup", error=str(e))

        except (SinRespuesta, NoAutenticado) as e:
            # NoAutenticado aquí NO libera: la venta ya salió y pudo haberse
            # aplicado. Un login caído durante la consulta es, para efectos
            # de dinero, lo mismo que un silencio.
            _registrar(registro, tipo="lookup", error=str(e))

        if intento < cfg.max_lookups:
            dormir(cfg.espera_entre_lookups)

    # --- se agotaron los intentos ----------------------------------------
    # La reserva se queda ACTIVA. No se libera y no se cobra: no sabemos.
    return _cerrar(
        registro, Desenlace.SIN_RESOLVER, reserva,
        detalle=f"{cfg.max_lookups} consultas sin respuesta concluyente",
        intentos=cfg.max_lookups,
    )


def _resolver(registro, reserva, respuesta, *, intentos):
    """Aplica lo que el proveedor dijo: cobrar o liberar."""
    if respuesta.aprobada:
        services.capturar(reserva)
        return _cerrar(
            registro, Desenlace.APROBADA, reserva, respuesta=respuesta,
            intentos=intentos,
            detalle=f"autorización {respuesta.control_no or respuesta.transaction_id}",
        )

    services.liberar(reserva)
    codigo = respuesta.response_code or "—"
    return _cerrar(
        registro, Desenlace.RECHAZADA, reserva, respuesta=respuesta,
        intentos=intentos,
        detalle=f"{respuesta.mensaje or 'Sin mensaje'} (responseCode {codigo})",
    )


@transaction.atomic
def _cerrar(registro, desenlace, reserva, *, respuesta=None, intentos=0, detalle="",
            mensaje="", codigo=""):
    registro.desenlace = desenlace
    registro.intentos = intentos
    registro.detalle = detalle[:500]
    registro.cerrado = timezone.now()
    if respuesta is not None:
        registro.transaction_id = respuesta.transaction_id
        registro.control_no = respuesta.control_no
        registro.response_code = respuesta.response_code
        registro.h2h_result_code = respuesta.h2h_result_code
    registro.save()

    reserva.refresh_from_db()
    if respuesta is not None:
        mensaje = mensaje or respuesta.mensaje
        codigo = codigo or respuesta.response_code
    return Cierre(
        desenlace=desenlace, reserva=reserva, respuesta=respuesta,
        intentos=intentos, detalle=detalle, mensaje=mensaje or "", codigo=codigo or "",
    )


# ---------------------------------------------------------------------------
# Conciliación de lo que quedó sin resolver
# ---------------------------------------------------------------------------
def reintentar_sin_resolver(cliente, inquilino, *, limite=100, dormir=time.sleep):
    """
    Vuelve a consultar lo que quedó abierto. Esto corre en el proceso
    nocturno, y también se puede disparar a mano.

    Una reserva no se libera por vieja. Se libera cuando el proveedor dice
    que no existe, o cuando el archivo de conciliación lo confirma. El
    tiempo no es evidencia.
    """
    pendientes = RegistroEnvio.objects.filter(
        operacion__inquilino=inquilino, desenlace=Desenlace.SIN_RESOLVER
    ).select_related("operacion")[:limite]

    resueltos = []
    for registro in pendientes:
        reserva = getattr(registro.operacion, "reserva", None)
        if reserva is None or reserva.estado != EstadoReserva.ACTIVA:
            continue
        try:
            respuesta = cliente.lookup(registro.invoice_no)
            _registrar(registro, tipo="lookup", respuesta=respuesta)
            if respuesta.aprobada or respuesta.control_no:
                resueltos.append(_resolver(registro, reserva, respuesta, intentos=0))
        except RechazoProveedor as e:
            _registrar(registro, tipo="lookup", error=str(e))
            if e.http == 404:
                services.liberar(reserva)
                resueltos.append(
                    _cerrar(
                        registro, Desenlace.RECHAZADA, reserva,
                        detalle="Conciliación: el proveedor no la tiene",
                    )
                )
        except (SinRespuesta, NoAutenticado) as e:
            # Igual que en el cierre: sin login no sabemos nada, y no saber
            # nunca libera una reserva.
            _registrar(registro, tipo="lookup", error=str(e))
    return resueltos
