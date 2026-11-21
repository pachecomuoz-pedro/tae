"""
Los dos endpoints del on-ramp (pesos → criptomoneda).

Misma regla que `vistas.py`: ninguna vista llama a `apps.saldo.services` ni
al proveedor. Todo pasa por `apps.identidad.acciones`, y los errores de
negocio los traduce `manejar` a su código HTTP.

  GET  /api/on-ramp/cotizacion?sku=&amount=   la tasa antes de confirmar; no mueve saldo
  POST /api/on-ramp/venta                     reserva, envía y cierra
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.identidad import acciones

from .permisos_http import Operativo
from .vistas import clave_de, error, manejar

# Campo de la petición → `clave` del error cuando falta. Son las mismas
# claves que ya entiende la pantalla (`ErrorAPI.consejo`).
_CAMPOS_REQUERIDOS = (("sku", "producto"), ("amount", "monto"), ("phoneNumber", "telefono"))


@api_view(["GET"])
@permission_classes([Operativo])
@manejar
def cotizacion(request):
    """GET /api/on-ramp/cotizacion?sku=<sku>&amount=<amount>"""
    sku = request.query_params.get("sku", "")
    amount = request.query_params.get("amount")
    if not sku:
        return error("Falta el producto (sku)", status.HTTP_400_BAD_REQUEST, clave="producto")
    if amount in (None, ""):
        return error("Falta el monto (amount)", status.HTTP_400_BAD_REQUEST, clave="monto")

    tasa = acciones.cotizar_on_ramp(request.user, sku=sku, amount=amount)
    # Como texto: son cifras de dinero y el proveedor las manda con muchos
    # decimales; la pantalla las muestra tal cual, sin pasar por un float.
    return Response({
        "rate": str(tasa["rate"]),
        "fee": str(tasa["fee"]),
        "fiatAmountAfterFee": str(tasa["fiatAmountAfterFee"]),
        "originalFiatAmount": str(tasa["originalFiatAmount"]),
    })


@api_view(["POST"])
@permission_classes([Operativo])
@manejar
def venta(request):
    """
    POST /api/on-ramp/venta  {sku, amount, phoneNumber}

    Contesta el desenlace tal cual, incluido `sin_resolver`: 201 aprobada,
    200 rechazada, 202 sin_resolver. Con una `Idempotency-Key` ya usada
    contesta el cierre original con `repetida: true` (200, o 202 si la
    original sigue sin resolver) sin volver a cobrar ni a llamar al
    proveedor. El teléfono nunca vuelve en la respuesta.
    """
    for campo, clave in _CAMPOS_REQUERIDOS:
        if request.data.get(campo) in (None, ""):
            return error(f"Falta el campo {campo}", status.HTTP_400_BAD_REQUEST, clave=clave)

    cierre = acciones.vender_on_ramp(
        request.user,
        sku=str(request.data["sku"]),
        amount=request.data["amount"],
        phone_number=request.data["phoneNumber"],
        idempotency_key=clave_de(request),
    )

    operacion = cierre.reserva.operacion
    cuerpo = {
        "folio": operacion.folio,
        "invoice_no": str(operacion.invoice_no),
        "desenlace": cierre.desenlace,
        "monto": str(cierre.reserva.monto),
        "transaction_id": cierre.transaction_id,
        "target_amount": cierre.target_amount,
        "target_asset": cierre.target_asset,
        "fee": cierre.fee,
        "detalle": cierre.detalle,
        # Lo que dijo el proveedor en un rechazo: es lo que la pantalla le
        # muestra al cajero (Requirement 7.6).
        "mensaje": cierre.mensaje,
    }
    codigos = {
        "aprobada": status.HTTP_201_CREATED,
        "rechazada": status.HTTP_200_OK,
        "sin_resolver": status.HTTP_202_ACCEPTED,
    }
    codigo = codigos.get(cierre.desenlace, status.HTTP_200_OK)
    if cierre.repetida:
        cuerpo["repetida"] = True
        cuerpo["aviso"] = f"Ya se había aplicado como {operacion.folio}"
        if codigo == status.HTTP_201_CREATED:
            codigo = status.HTTP_200_OK
    return Response(cuerpo, status=codigo)
