"""
Lógica de negocio para transacciones On-Ramp (pesos → cripto).

Tres funciones públicas:

  exchange_rate                   consulta informativa de tasa. No mueve saldo.
  ejecutar_on_ramp                reserva → proveedor → captura/liberación.
  reintentar_sin_resolver_onramp  conciliación de lo que quedó abierto.

La diferencia operativa respecto al tiempo aire: no hay loop de consultas
a mitad de la venta. Si el proveedor no contesta, la venta queda
SIN_RESOLVER de inmediato y la resuelve la conciliación, que pregunta por
Invoice-No con el lookup estándar (es lo único que ese endpoint acepta).

Regla fundamental idéntica a venta.py: el silencio no es un rechazo.
Un timeout, un 5xx o un estado que no es ni COMPLETED ni un fallo explícito
dejan la reserva viva; solo se libera con un rechazo que el proveedor dijo
en claro.

phone_number nunca aparece en ningún log.*() (Requirement 9.4).
"""
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.catalogo.servicios import MontoInvalido, ProductoNoDisponible
from apps.saldo import services
from apps.saldo.models import EstadoReserva

from .bitacora import sanear_mensaje, trazar
from .cliente import NoAutenticado, RechazoProveedor, SinRespuesta
from .modelos_on_ramp import DesenlaceOnRamp, IntentoOnRamp, RegistroOnRamp

log = logging.getLogger("tae.on_ramp")

# Qué pasó con el saldo en cada desenlace, para la bitácora de Emida.
_SALDO = {
    DesenlaceOnRamp.APROBADA: "capturado",
    DesenlaceOnRamp.RECHAZADA: "liberado",
    DesenlaceOnRamp.SIN_RESOLVER: "reserva_viva",
}


# ---------------------------------------------------------------------------
# Excepción re-exportada (misma semántica que en venta.py)
# ---------------------------------------------------------------------------
from .venta import ProveedorNoDisponible  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
# Tope del monto en pesos que acepta el proveedor (Requirement 2.1 y 3.2).
MONTO_MAXIMO = 999_999_999

# Los cuatro campos que tiene que traer una cotización para serlo.
CAMPOS_TASA = ("originalFiatAmount", "rate", "fee", "fiatAmountAfterFee")

# Estados con los que el proveedor dice, en claro, que NO se hizo. Cualquier
# otro estado que no sea COMPLETED (PENDING, PROCESSING, uno que no
# conocemos, o ninguno) no autoriza a liberar: es un "todavía no sé".
ESTADOS_FALLIDOS = frozenset({"FAILED", "REJECTED", "DECLINED", "CANCELLED", "CANCELED"})

# Cuántas filas procesa la conciliación por corrida. Configurable por
# TAE_ON_RAMP_LIMITE_CONCILIACION sin tocar código (Requirement 8.1).
LIMITE_CONCILIACION_MIN = 1
LIMITE_CONCILIACION_MAX = 500
LIMITE_CONCILIACION_DEFECTO = 100

_ENTERO_POSITIVO = re.compile(r"[1-9][0-9]*")


# ---------------------------------------------------------------------------
# Resultado de la ejecución
# ---------------------------------------------------------------------------
@dataclass
class CierreOnRamp:
    """Cómo terminó la transacción on-ramp y con qué evidencia."""

    desenlace: str          # "aprobada" | "rechazada" | "sin_resolver"
    reserva: object
    detalle: str = ""
    transaction_id: str = ""
    target_amount: str = ""
    target_asset: str = ""
    fee: str = ""
    mensaje: str = ""
    # True cuando es la respuesta a una Idempotency-Key ya usada: no se
    # volvió a reservar ni a llamar al proveedor (Requirement 6.3 y 6.4).
    repetida: bool = False


# ---------------------------------------------------------------------------
# Validación del monto
# ---------------------------------------------------------------------------
def monto_entero(valor, *, maximo=None):
    """
    Devuelve `valor` como entero de pesos, o levanta MontoInvalido.

    Acepta un `int` (no un bool) o un texto de dígitos ASCII sin ceros a la
    izquierda, sin espacios ni signo: exactamente lo que el proveedor acepta
    (`^[1-9]\\d*$`). Un float como 10.0, un Decimal o "1e3" se rechazan aunque
    valgan lo mismo que un entero: si el monto llegó con forma de decimal,
    alguien lo calculó o lo tecleó mal, y cobrar "lo más parecido" es cobrar
    algo que nadie pidió.
    """
    tope = MONTO_MAXIMO if maximo is None else maximo
    invalido = MontoInvalido(
        f"El monto debe ser un entero de pesos entre 1 y {tope:,}; se recibió {valor!r}"
    )
    if isinstance(valor, bool):
        raise invalido
    if isinstance(valor, int):
        entero = valor
    elif isinstance(valor, str) and _ENTERO_POSITIVO.fullmatch(valor):
        entero = int(valor)
    else:
        raise invalido
    if not 1 <= entero <= tope:
        raise invalido
    return entero


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _contar_intentos(registro):
    """`registro.intentos` es siempre el número de filas IntentoOnRamp (Requirement 9.1)."""
    registro.intentos = registro.intentos_detalle.count()
    registro.save(update_fields=["intentos"])


def _registrar_on_ramp(registro, *, tipo, respuesta=None, error="", rechazo=None):
    """
    Crea una fila IntentoOnRamp por cada llamada al proveedor.

    cuerpo y error se truncan antes de persistir (Requirement 3.8, 9.2).
    `http` queda nulo cuando no hubo respuesta. phone_number nunca aparece
    aquí: se guarda lo que contestó el proveedor, no lo que se le mandó.
    """
    if rechazo is not None:
        cuerpo = json.dumps(rechazo.cuerpo, ensure_ascii=False, default=str)
        http = rechazo.http or None
        aprobada = False
    elif respuesta is not None:
        cuerpo = getattr(respuesta, "crudo", "") or ""
        http = getattr(respuesta, "http", None) or None
        aprobada = bool(getattr(respuesta, "aprobada", False))
    else:
        cuerpo = ""
        http = None
        aprobada = False

    intento = IntentoOnRamp.objects.create(
        registro=registro,
        tipo=tipo,
        http=http,
        aprobada=aprobada,
        cuerpo=cuerpo[:4000],
        error=str(error)[:500],
    )
    _contar_intentos(registro)
    return intento


def _decimal_o_none(valor):
    """Un monto del proveedor como Decimal, o None si no es un número usable."""
    if valor in (None, ""):
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return numero if numero.is_finite() else None


@transaction.atomic
def _cerrar_registro(registro, desenlace, *, respuesta=None, detalle="", mensaje=""):
    """Actualiza el RegistroOnRamp con el desenlace final y lo persiste."""
    registro.desenlace = desenlace
    registro.cerrado = timezone.now()
    registro.detalle = str(detalle)[:1000]

    texto = mensaje
    if respuesta is not None:
        cuerpo = respuesta.cuerpo or {}
        registro.transaction_id = str(cuerpo.get("transactionId") or "")[:100]
        registro.target_amount = _decimal_o_none(cuerpo.get("targetAmount"))
        registro.target_asset = str(cuerpo.get("targetAsset") or "")[:20]
        registro.fee = _decimal_o_none(cuerpo.get("fee"))
        texto = texto or respuesta.mensaje or cuerpo.get("detail") or ""
    registro.response_message = str(texto)[:4000]

    registro.save()

    # El teléfono no se incluye aquí ni en ningún trazar de este archivo.
    cuerpo = (respuesta.cuerpo or {}) if respuesta is not None else {}
    trazar(
        "ON_RAMP_CIERRE",
        nivel=logging.WARNING if desenlace == DesenlaceOnRamp.SIN_RESOLVER else logging.INFO,
        folio=registro.operacion.folio, invoice=registro.invoice_no,
        desenlace=desenlace, saldo=_SALDO.get(desenlace, ""),
        status=cuerpo.get("status"), transaction_id=registro.transaction_id,
        target_asset=registro.target_asset, target_amount=registro.target_amount,
        fee=registro.fee,
        detalle=None if desenlace == DesenlaceOnRamp.APROBADA else sanear_mensaje(detalle)[:300],
    )


def _cierre_con_respuesta(desenlace, reserva, respuesta, *, detalle=""):
    """El CierreOnRamp que se le devuelve a quien llamó, con lo que dijo el proveedor."""
    cuerpo = respuesta.cuerpo or {}
    return CierreOnRamp(
        desenlace=desenlace,
        reserva=reserva,
        detalle=detalle,
        transaction_id=str(cuerpo.get("transactionId") or ""),
        target_amount=str(cuerpo.get("targetAmount") or ""),
        target_asset=str(cuerpo.get("targetAsset") or ""),
        fee=str(cuerpo.get("fee") or ""),
        mensaje=respuesta.mensaje or "",
    )


# ---------------------------------------------------------------------------
# Consulta de tasa (sin cargo)
# ---------------------------------------------------------------------------
def exchange_rate(cliente, *, product_id, invoice_no, amount):
    """
    Consulta la tasa de cambio con el proveedor. No mueve saldo.

    Antes de salir a la red, `amount` tiene que ser un entero válido
    (`monto_entero`); si no, MontoInvalido y no se llama a nadie.

    Traduce lo que dice el proveedor:
      - HTTP 400                 → MontoInvalido
      - HTTP 422                 → ProductoNoDisponible
      - cualquier otro error     → SinRespuesta
      - login caído              → ProveedorNoDisponible
      - 200 sin los cuatro campos → SinRespuesta (no es una cotización)

    Devuelve {originalFiatAmount, rate, fee, fiatAmountAfterFee} tal cual
    los mandó el proveedor, sin redondear ni convertir (Requirement 2.2).
    """
    monto = monto_entero(amount)

    try:
        respuesta = cliente.on_ramp_exchange_rate(
            product_id=product_id, invoice_no=invoice_no, amount=monto,
        )
    except RechazoProveedor as e:
        if e.http == 400:
            raise MontoInvalido(f"El proveedor rechazó el monto: {e}") from e
        if e.http == 422:
            raise ProductoNoDisponible(
                f"El producto no está disponible en el proveedor: {e}"
            ) from e
        raise SinRespuesta(
            f"El proveedor no pudo dar la tasa (HTTP {e.http}): {e}"
        ) from e
    except NoAutenticado as e:
        raise ProveedorNoDisponible(
            "El proveedor no está disponible en este momento; intenta de nuevo en unos minutos."
        ) from e

    cuerpo = respuesta.cuerpo or {}
    faltan = [c for c in CAMPOS_TASA if cuerpo.get(c) is None]
    if faltan:
        raise SinRespuesta(
            f"La respuesta del proveedor no es una cotización (faltan: {', '.join(faltan)})"
        )
    return {campo: cuerpo[campo] for campo in CAMPOS_TASA}


# ---------------------------------------------------------------------------
# Ejecución de la transacción
# ---------------------------------------------------------------------------
def ejecutar_on_ramp(cliente, reserva, *, product_id, phone_number):
    """
    Manda la venta on-ramp y la deja resuelta, o explícitamente sin resolver.

      aprobada      status COMPLETED                → captura
      rechazada     HTTP 4xx (no 401) o fallo dicho → libera
      401 o login   la venta nunca salió            → libera y ProveedorNoDisponible
      sin_resolver  timeout, 5xx, PENDING, o algo   → la reserva sigue ACTIVA
                    que no es un sí ni un no

    Siempre crea el RegistroOnRamp ANTES de llamar (Requirement 3.7) y deja
    una fila IntentoOnRamp por la llamada (Requirement 3.8).

    phone_number nunca aparece en log.*() (Requirement 9.4).
    """
    invoice_no = str(reserva.operacion.invoice_no)
    folio = reserva.operacion.folio

    registro = RegistroOnRamp.objects.create(
        operacion=reserva.operacion,
        invoice_no=invoice_no,
        product_id=str(product_id),
        phone_number=str(phone_number),  # solo auditoría, nunca en logs
        monto=reserva.monto,
    )
    trazar(
        "ON_RAMP_INICIO", folio=folio, invoice=invoice_no, product=product_id,
        monto=reserva.monto,
    )

    # --- la llamada al proveedor ------------------------------------------
    try:
        respuesta = cliente.on_ramp(
            invoice_no=invoice_no,
            product_id=product_id,
            amount=reserva.monto,
            phone_number=phone_number,
        )

    except RechazoProveedor as e:
        _registrar_on_ramp(registro, tipo="envio", error=str(e), rechazo=e)

        if e.http == 401:
            # El token venció ANTES de que la venta saliera: liberar es
            # seguro (Requirement 3.6).
            services.liberar(reserva)
            _cerrar_registro(
                registro, DesenlaceOnRamp.RECHAZADA,
                detalle=f"No autenticado con el proveedor: HTTP {e.http}",
                mensaje=str(e),
            )
            log.error("on_ramp %s no salió: sin autenticación con el proveedor", folio)
            raise ProveedorNoDisponible(
                "El proveedor no está disponible. La transacción no se hizo "
                "y el saldo no se tocó; intenta de nuevo en unos minutos."
            ) from e

        if (e.http or 0) >= 500:
            # Un 5xx no dice si la transacción se hizo: no se libera
            # (Requirement 3.9).
            log.warning("on_ramp %s: HTTP %s del proveedor; queda sin_resolver", folio, e.http)
            return _dejar_sin_resolver(
                registro, reserva, detalle=f"HTTP {e.http}: {str(e)[:900]}",
            )

        # 4xx que no es 401: rechazo explícito, la venta no ocurrió (Req. 3.4)
        services.liberar(reserva)
        _cerrar_registro(
            registro, DesenlaceOnRamp.RECHAZADA,
            detalle=f"HTTP {e.http}: {str(e)[:900]}",
            mensaje=str(e),
        )
        return CierreOnRamp(
            desenlace="rechazada", reserva=reserva,
            mensaje=str(e), detalle=f"HTTP {e.http}: {str(e)[:900]}",
        )

    except NoAutenticado as e:
        # Falló el login, que va ANTES de mandar la venta: no salió.
        _registrar_on_ramp(registro, tipo="envio", error=str(e))
        services.liberar(reserva)
        _cerrar_registro(
            registro, DesenlaceOnRamp.RECHAZADA,
            detalle=f"No salió: sin autenticación con el proveedor ({e})"[:1000],
        )
        log.error("on_ramp %s no salió: login con el proveedor caído", folio)
        raise ProveedorNoDisponible(
            "El proveedor no está disponible. La transacción no se hizo "
            "y el saldo no se tocó; intenta de nuevo en unos minutos."
        ) from e

    except SinRespuesta as e:
        # El silencio no es un rechazo (Requirement 3.5).
        _registrar_on_ramp(registro, tipo="envio", error=str(e))
        log.warning("on_ramp %s sin respuesta del proveedor; queda sin_resolver", folio)
        return _dejar_sin_resolver(registro, reserva, detalle=str(e)[:1000])

    except Exception:
        # Error inesperado: igual que el silencio, no se libera.
        _registrar_on_ramp(registro, tipo="envio", error="Error inesperado")
        log.exception("on_ramp %s error inesperado; queda sin_resolver", folio)
        return _dejar_sin_resolver(
            registro, reserva, detalle="Error inesperado en la llamada al proveedor",
        )

    # --- el proveedor contestó 2xx ------------------------------------------
    _registrar_on_ramp(registro, tipo="envio", respuesta=respuesta)
    cuerpo = respuesta.cuerpo or {}
    estado = str(cuerpo.get("status") or "").upper()

    if respuesta.aprobada:
        # COMPLETED: cobrar (Requirement 3.3).
        try:
            services.capturar(reserva)
        except Exception:
            # El proveedor ya la hizo pero no pudimos cobrarla: la reserva
            # sigue viva y la conciliación reintenta. Nunca se libera.
            log.exception("on_ramp %s aprobada pero falló la captura", folio)
            _cerrar_registro(
                registro, DesenlaceOnRamp.SIN_RESOLVER, respuesta=respuesta,
                detalle="Aprobada por el proveedor; falló la captura, queda para conciliar",
            )
            return _cierre_con_respuesta(
                "sin_resolver", reserva, respuesta,
                detalle="Aprobada por el proveedor; falló la captura, queda para conciliar",
            )
        detalle = f"transactionId {respuesta.transaction_id}"
        _cerrar_registro(
            registro, DesenlaceOnRamp.APROBADA, respuesta=respuesta, detalle=detalle,
        )
        return _cierre_con_respuesta("aprobada", reserva, respuesta, detalle=detalle)

    if estado in ESTADOS_FALLIDOS:
        services.liberar(reserva)
        detalle = f"El proveedor respondió {estado} (HTTP {respuesta.http})"
        _cerrar_registro(
            registro, DesenlaceOnRamp.RECHAZADA, respuesta=respuesta, detalle=detalle,
        )
        return _cierre_con_respuesta("rechazada", reserva, respuesta, detalle=detalle)

    # PENDING, PROCESSING, o un estado que no conocemos: ni sí ni no.
    detalle = f"El proveedor respondió {estado or 'sin estado'} (HTTP {respuesta.http})"
    log.warning("on_ramp %s: %s; queda sin_resolver", folio, detalle)
    _cerrar_registro(
        registro, DesenlaceOnRamp.SIN_RESOLVER, respuesta=respuesta, detalle=detalle,
    )
    return _cierre_con_respuesta("sin_resolver", reserva, respuesta, detalle=detalle)


def _dejar_sin_resolver(registro, reserva, *, detalle):
    """Cierra el registro como SIN_RESOLVER con la reserva viva."""
    _cerrar_registro(registro, DesenlaceOnRamp.SIN_RESOLVER, detalle=detalle)
    return CierreOnRamp(desenlace="sin_resolver", reserva=reserva, detalle=detalle[:1000])


# ---------------------------------------------------------------------------
# Conciliación de transacciones sin resolver
# ---------------------------------------------------------------------------
def _limite_conciliacion(limite):
    """El límite pedido (o el de settings) acotado entre 1 y 500 (Requirement 8.1)."""
    if limite is None:
        limite = settings.TAE.get("ON_RAMP_LIMITE_CONCILIACION", LIMITE_CONCILIACION_DEFECTO)
    try:
        limite = int(limite)
    except (TypeError, ValueError):
        limite = LIMITE_CONCILIACION_DEFECTO
    return max(LIMITE_CONCILIACION_MIN, min(LIMITE_CONCILIACION_MAX, limite))


def reintentar_sin_resolver_onramp(cliente, inquilino, *, limite=None):
    """
    Vuelve a consultar lo que quedó sin resolver. Corre en el proceso de
    reintentos (`reintentar_pendientes`) y se puede disparar a mano.

    Para cada fila SIN_RESOLVER con su reserva todavía activa:

      1. Crea el IntentoOnRamp(tipo="conciliacion") ANTES de hacer nada
         más (Requirement 8.3).
      2. Sin transactionId la fila pasa a requiere_revision_manual y no se
         pregunta nada (Requirement 8.2): si nunca llegó respuesta, ni
         siquiera sabemos si el proveedor la recibió.
      3. Con transactionId se pregunta con el lookup estándar, que solo
         acepta Invoice-No (es lo único que ese endpoint recibe):
           - aprobada      → captura  → aprobada            (Req. 8.4)
           - rechazo claro → libera   → rechazada           (Req. 8.5)
           - cualquier otra cosa (404, vacío, "en proceso", timeout) → la
             fila sigue sin_resolver con resultado error_proveedor (Req. 8.6)

    Un 404 NO libera, a diferencia del tiempo aire: no está confirmado que
    el lookup cubra operaciones on-ramp, y que no la encuentre no prueba
    que no exista. El tiempo tampoco es evidencia.

    Devuelve los RegistroOnRamp que se resolvieron en esta corrida.
    phone_number nunca aparece en log.*() (Requirement 9.4).
    """
    pendientes = list(
        RegistroOnRamp.objects.filter(
            operacion__inquilino=inquilino,
            desenlace=DesenlaceOnRamp.SIN_RESOLVER,
        ).select_related("operacion").order_by("id")[: _limite_conciliacion(limite)]
    )

    resueltos = []
    for registro in pendientes:
        reserva = getattr(registro.operacion, "reserva", None)
        if reserva is None or reserva.estado != EstadoReserva.ACTIVA:
            continue

        intento = IntentoOnRamp.objects.create(registro=registro, tipo="conciliacion")
        _contar_intentos(registro)

        if not registro.transaction_id:
            intento.resultado = "sin_identificador"
            intento.save(update_fields=["resultado"])
            registro.desenlace = DesenlaceOnRamp.REQUIERE_REVISION_MANUAL
            registro.detalle = "Sin transactionId: hay que revisarla a mano con el proveedor"
            registro.save(update_fields=["desenlace", "detalle"])
            log.warning(
                "on_ramp conciliacion %s: sin transactionId → requiere_revision_manual",
                registro.invoice_no,
            )
            continue

        trazar(
            "CONCILIACION", invoice=registro.invoice_no, folio=registro.operacion.folio,
            nota="reintento de on-ramp sin resolver, consulta por Invoice-No",
        )
        try:
            respuesta = cliente.lookup(registro.invoice_no)
        except RechazoProveedor as e:
            intento.http = e.http or None
            intento.cuerpo = json.dumps(e.cuerpo, ensure_ascii=False, default=str)[:4000]
            _fallo_de_conciliacion(intento, "error_proveedor", str(e))
            log.warning(
                "on_ramp conciliacion %s: el proveedor contestó HTTP %s",
                registro.invoice_no, e.http,
            )
            continue
        except (SinRespuesta, NoAutenticado) as e:
            _fallo_de_conciliacion(intento, "error_proveedor", str(e))
            log.warning("on_ramp conciliacion %s: sin respuesta del proveedor", registro.invoice_no)
            continue
        except Exception as e:
            _fallo_de_conciliacion(intento, "error_proveedor", str(e))
            log.error("on_ramp conciliacion %s: error inesperado: %s", registro.invoice_no, e)
            continue

        intento.http = getattr(respuesta, "http", None) or None
        intento.aprobada = bool(respuesta.aprobada)
        intento.cuerpo = (getattr(respuesta, "crudo", "") or "")[:4000]

        if respuesta.aprobada:
            if _aplicar_conciliacion(
                registro, reserva, intento, respuesta,
                accion=services.capturar, resultado_error="error_captura",
                desenlace=DesenlaceOnRamp.APROBADA,
                detalle="Conciliación: el proveedor la confirmó",
            ):
                resueltos.append(registro)
        elif respuesta.response_code:
            # Contestó con un código que no es de aprobación: es un no dicho
            # en claro. Sin código ("En proceso", vacío) no concluye nada.
            if _aplicar_conciliacion(
                registro, reserva, intento, respuesta,
                accion=services.liberar, resultado_error="error_liberacion",
                desenlace=DesenlaceOnRamp.RECHAZADA,
                detalle=f"Conciliación: el proveedor la rechazó (responseCode {respuesta.response_code})",
            ):
                resueltos.append(registro)
        else:
            _fallo_de_conciliacion(
                intento, "error_proveedor",
                respuesta.mensaje or "El proveedor no concluyó nada",
            )
            log.info("on_ramp conciliacion %s: el proveedor no concluyó", registro.invoice_no)

    return resueltos


def _fallo_de_conciliacion(intento, resultado, error):
    """La consulta no concluyó: el intento lo deja dicho y la fila no se toca."""
    intento.resultado = resultado
    intento.error = str(error)[:500]
    intento.save()


def _aplicar_conciliacion(registro, reserva, intento, respuesta, *, accion, resultado_error,
                          desenlace, detalle):
    """
    Cobra o libera según lo concluido, y cierra el registro. Si la acción
    falla, el intento lo dice y el registro queda como estaba: seguirá
    sin_resolver y se vuelve a intentar en la próxima corrida.
    """
    try:
        with transaction.atomic():
            accion(reserva)
            intento.save()
            registro.desenlace = desenlace
            registro.cerrado = timezone.now()
            registro.detalle = detalle[:1000]
            if respuesta.transaction_id and not registro.transaction_id:
                registro.transaction_id = respuesta.transaction_id[:100]
            registro.save()
    except Exception as e:
        # Se leen de la base: el rollback no deshace lo que quedó en memoria.
        registro.refresh_from_db()
        _fallo_de_conciliacion(intento, resultado_error, str(e))
        log.error("on_ramp conciliacion %s: %s: %s", registro.invoice_no, resultado_error, e)
        return False
    log.info("on_ramp conciliacion %s: %s", registro.invoice_no, desenlace)
    trazar(
        "ON_RAMP_CIERRE", folio=registro.operacion.folio, invoice=registro.invoice_no,
        desenlace=desenlace, saldo=_SALDO.get(desenlace, ""),
        transaction_id=registro.transaction_id, origen="conciliacion",
        response_code=respuesta.response_code,
    )
    return True
