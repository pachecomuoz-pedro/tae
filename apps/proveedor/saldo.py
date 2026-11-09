"""
Saldo de EPS en el proveedor (Emida) y su comparación con la red.

Dos números que NO son el mismo, y la pantalla los muestra juntos para
que nunca se confundan (decisión 23):

- Saldo en Emida: el dinero real de EPS con el proveedor. De ahí se
  descuenta cada venta aprobada. Solo lo ve el Super Admin.
- Asignado en la red: la suma de las bolsas de administradores y clientes,
  que el Super Admin asigna a mano. No sale ni entra de Emida.

La diferencia entre ambos es la señal que importa: si lo asignado supera lo
que hay en Emida, la red puede intentar vender más de lo que EPS puede
pagar, y el proveedor empezará a rechazar ventas por fondos insuficientes.
"""
from decimal import Decimal, InvalidOperation

from django.db.models import Sum

from apps.identidad import permisos
from apps.saldo.models import Bolsa, TipoBolsa

from .cliente import ErrorProveedor
from .modelos import ConsultaSaldoProveedor

# Nombres probables del campo de saldo, en orden de preferencia. No tenemos
# el contrato exacto de `account/balance`; si Emida usa otro nombre, el
# saldo queda nulo, la respuesta cruda se guarda y basta con agregarlo aquí.
CAMPOS_SALDO = (
    "availableBalance", "balance", "currentBalance", "available",
    "merchantBalance", "accountBalance", "amount", "saldo",
)
CAMPOS_MONEDA = ("currency", "currencyCode", "moneda")


def _numero(valor):
    try:
        return Decimal(str(valor).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def leer_saldo(datos, profundidad=0):
    """
    Busca el saldo en la respuesta del proveedor. Devuelve (saldo, moneda).

    Revisa el primer nivel y los objetos anidados (por ejemplo
    `{"account": {"balance": ...}}`), hasta dos niveles. Si no encuentra un
    número, devuelve (None, ""): mejor ningún número que uno equivocado.
    """
    if not isinstance(datos, dict) or profundidad > 2:
        return None, ""
    moneda = next((str(datos[c]) for c in CAMPOS_MONEDA if datos.get(c)), "")
    for campo in CAMPOS_SALDO:
        if campo in datos:
            numero = _numero(datos[campo])
            if numero is not None:
                return numero, moneda
    for valor in datos.values():
        if isinstance(valor, dict):
            numero, moneda_anidada = leer_saldo(valor, profundidad + 1)
            if numero is not None:
                return numero, moneda_anidada or moneda
    return None, moneda


def asignado_en_red(inquilino):
    """Suma de las bolsas de administradores y clientes. Las de sistema no cuentan."""
    total = Bolsa.objects.filter(
        inquilino=inquilino, tipo__in=[TipoBolsa.ADMINISTRADOR, TipoBolsa.CLIENTE]
    ).aggregate(total=Sum("saldo"))["total"]
    return total or Decimal("0")


def ultima_consulta(inquilino):
    return ConsultaSaldoProveedor.objects.filter(inquilino=inquilino).first()


def ultima_exitosa(inquilino):
    return (
        ConsultaSaldoProveedor.objects.filter(inquilino=inquilino, saldo__isnull=False)
        .first()
    )


def consultar(usuario, cliente):
    """
    Pregunta el saldo a Emida y guarda la consulta, salga bien o mal.

    Un error del proveedor no se propaga: queda guardado con su mensaje y
    la pantalla lo muestra junto al último saldo bueno. Para el Super Admin
    "Emida no contestó a las 10:42" es información, no una falla de la app.
    """
    permisos.puede_ver_saldo_proveedor(usuario)
    registro = ConsultaSaldoProveedor(inquilino=usuario.inquilino, consultado_por=usuario)
    try:
        respuesta = cliente.saldo_merchant()
    except ErrorProveedor as e:
        registro.http = getattr(e, "http", None)
        registro.error = str(e)[:300]
        registro.respuesta = getattr(e, "cuerpo", None) or None
    else:
        registro.http = respuesta.http
        registro.respuesta = respuesta.datos
        registro.saldo, registro.moneda = leer_saldo(respuesta.datos)
        if registro.saldo is None:
            registro.error = (
                "Emida respondió, pero no se encontró el saldo en la respuesta. "
                "Revisa la respuesta guardada y agrega el campo en CAMPOS_SALDO."
            )
    registro.save()
    return registro


def resumen(usuario):
    """Lo que muestra la pantalla de Exposición. No llama al proveedor."""
    permisos.puede_ver_saldo_proveedor(usuario)
    inquilino = usuario.inquilino
    ultima = ultima_consulta(inquilino)
    buena = ultima_exitosa(inquilino)
    red = asignado_en_red(inquilino)
    return {
        "saldo_emida": str(buena.saldo) if buena else None,
        "moneda": buena.moneda if buena else "",
        "consultado": buena.cuando.isoformat() if buena else None,
        "consultado_por": (buena.consultado_por.nombre if buena and buena.consultado_por else ""),
        # La última consulta puede haber fallado aunque haya un saldo bueno anterior.
        "ultimo_error": ultima.error if ultima and ultima.saldo is None else "",
        "ultimo_intento": ultima.cuando.isoformat() if ultima else None,
        "asignado_red": str(red),
        "diferencia": str(buena.saldo - red) if buena else None,
    }
