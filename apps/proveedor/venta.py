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

from .bitacora import enmascarar_telefono, sanear_mensaje, trazar
from .cliente import NoAutenticado, RechazoProveedor, Respuesta, SinRespuesta
from .modelos import Desenlace, IntentoProveedor, RegistroEnvio

log = logging.getLogger("tae.venta")

# Qué pasó con el saldo del tendero en cada desenlace, dicho en una palabra
# para la bitácora de Emida.
_SALDO = {
    Desenlace.APROBADA: "capturado",
    Desenlace.RECHAZADA: "liberado",
    Desenlace.SIN_RESOLVER: "reserva_viva",
}


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
    trazar(
        "VENTA_INICIO", folio=reserva.operacion.folio, invoice=invoice_no,
        product=product_id, monto=reserva.monto, cuenta=enmascarar_telefono(account_id),
        politica={"timeout_s": cfg.timeout_venta, "lookup_a_los_s": cfg.lookup_desde_envio,
                  "cada_s": cfg.espera_entre_lookups, "max_lookups": cfg.max_lookups},
    )

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
    trazar(
        "ESPERA_LOOKUP", nivel=logging.WARNING, invoice=invoice_no,
        t_s=round(time.monotonic() - salio, 1), espera_s=round(max(falta, 0), 1),
        nota="sin respuesta al envio; la reserva sigue viva y se consulta por Invoice-No",
    )
    dormir(max(falta, 0))

    for intento in range(1, cfg.max_lookups + 1):
        n = f"{intento}/{cfg.max_lookups}"
        trazar(
            "LOOKUP_INTENTO", invoice=invoice_no, intento=n,
            t_s=round(time.monotonic() - salio, 1),
        )
        try:
            respuesta = cliente.lookup(invoice_no)
            _registrar(registro, tipo="lookup", respuesta=respuesta)

            if respuesta.aprobada:
                trazar(
                    "LOOKUP_RESULTADO", invoice=invoice_no, intento=n,
                    response_code=respuesta.response_code, h2h=respuesta.h2h_result_code,
                    conclusion="aprobada: existe y fue aprobada, se cobra",
                )
                return _resolver(registro, reserva, respuesta, intentos=intento)

            # Contestó con un código que no es de aprobación. Puede ser que
            # la rechazó, o que todavía la está procesando. La diferencia la
            # da el folio del carrier: sin folio, no hay nada entregado.
            if respuesta.control_no or respuesta.transaction_id:
                trazar(
                    "LOOKUP_RESULTADO", invoice=invoice_no, intento=n,
                    response_code=respuesta.response_code, h2h=respuesta.h2h_result_code,
                    conclusion="existe y NO fue aprobada, se libera",
                )
                return _resolver(registro, reserva, respuesta, intentos=intento)

            trazar(
                "LOOKUP_RESULTADO", invoice=invoice_no, intento=n,
                response_code=respuesta.response_code, h2h=respuesta.h2h_result_code,
                conclusion="sin informacion aun, se vuelve a consultar",
            )
            log.info(
                "lookup %s intento %s: todavía sin resolver", invoice_no, intento
            )

        except RechazoProveedor as e:
            if e.http == 404:
                # El proveedor no conoce ese Invoice-No: la venta nunca entró.
                trazar(
                    "LOOKUP_RESULTADO", invoice=invoice_no, intento=n, http=404,
                    conclusion="el proveedor no tiene la transaccion, se libera",
                )
                _registrar(registro, tipo="lookup", error=str(e), rechazo=e)
                services.liberar(reserva)
                return _cerrar(
                    registro, Desenlace.RECHAZADA, reserva,
                    detalle="El proveedor no tiene registro de la venta",
                    intentos=intento,
                )
            trazar(
                "LOOKUP_RESULTADO", nivel=logging.WARNING, invoice=invoice_no,
                intento=n, http=e.http,
                conclusion="rechazo que no prueba nada, se vuelve a consultar",
            )
            _registrar(registro, tipo="lookup", error=str(e))

        except (SinRespuesta, NoAutenticado) as e:
            # NoAutenticado aquí NO libera: la venta ya salió y pudo haberse
            # aplicado. Un login caído durante la consulta es, para efectos
            # de dinero, lo mismo que un silencio.
            trazar(
                "LOOKUP_RESULTADO", nivel=logging.WARNING, invoice=invoice_no,
                intento=n, error=type(e).__name__,
                conclusion="la consulta tampoco respondio, no se libera",
            )
            _registrar(registro, tipo="lookup", error=str(e))

        if intento < cfg.max_lookups:
            trazar(
                "ESPERA_LOOKUP", invoice=invoice_no, espera_s=cfg.espera_entre_lookups,
                nota="intervalo entre consultas",
            )
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
        registro.carrier_control_no = respuesta.carrier_control_no
        registro.response_code = respuesta.response_code
        registro.h2h_result_code = respuesta.h2h_result_code
        registro.pin = respuesta.pin
    registro.response_message = (mensaje or getattr(respuesta, "mensaje", "") or "")[:4000]
    registro.save()

    reserva.refresh_from_db()
    trazar(
        "CIERRE",
        nivel=logging.WARNING if desenlace == Desenlace.SIN_RESOLVER else logging.INFO,
        folio=reserva.operacion.folio, invoice=registro.invoice_no,
        desenlace=desenlace, intentos=intentos, saldo=_SALDO.get(desenlace, ""),
        response_code=registro.response_code, h2h=registro.h2h_result_code,
        transaction_id=registro.transaction_id, control_no=registro.control_no,
        carrier_control_no=registro.carrier_control_no,
        duracion_s=round((registro.cerrado - registro.creado).total_seconds(), 1),
        # En una aprobada el detalle solo repite el folio, que ya va completo
        # en control_no; el filtro de teléfonos lo enmascararía.
        detalle=None if desenlace == Desenlace.APROBADA else sanear_mensaje(detalle)[:300],
    )
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
        trazar(
            "CONCILIACION", invoice=registro.invoice_no, folio=registro.operacion.folio,
            nota="reintento de una venta sin resolver, consulta por Invoice-No",
        )
        try:
            respuesta = cliente.lookup(registro.invoice_no)
            _registrar(registro, tipo="lookup", respuesta=respuesta)
            if respuesta.aprobada or respuesta.control_no:
                resueltos.append(_resolver(registro, reserva, respuesta, intentos=0))
            else:
                trazar(
                    "LOOKUP_RESULTADO", invoice=registro.invoice_no,
                    response_code=respuesta.response_code,
                    conclusion="sin informacion aun, la reserva sigue viva",
                )
        except RechazoProveedor as e:
            _registrar(registro, tipo="lookup", error=str(e))
            if e.http == 404:
                trazar(
                    "LOOKUP_RESULTADO", invoice=registro.invoice_no, http=404,
                    conclusion="el proveedor no tiene la transaccion, se libera",
                )
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
