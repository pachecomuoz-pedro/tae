"""
Bitácora de la conversación con Emida.

Existe para la certificación. El Plan (Fase 3 y Fase 5) pide por cada caso
request, response, código HTTP y timestamp, la Fase 3 dice "revisión de logs
por parte de Emida", y la sección 4.2 exige verificar que no haya datos
sensibles (JWT, PII) en los logs. Este módulo resuelve las dos cosas a la vez:
cada llamada deja su rastro completo y todo pasa por el mismo enmascarado.

Qué queda en la bitácora (logger `tae.emida`, archivo `logs/emida.log`):

    REQ              lo que se mandó: método, URL, headers y body
    RES              lo que contestó: HTTP, milisegundos y body
    VEREDICTO        cómo lo leímos: criterio de éxito, códigos, folios
    TIMEOUT          no hubo respuesta: cuánto esperamos
    LOOKUP_*         la consulta por Invoice-No que resuelve un timeout
    CIERRE           cómo terminó la venta y qué pasó con el saldo

Todas las líneas de una venta llevan `invoice=` para filtrarlas de un golpe.

Qué NUNCA queda: el JWT, la contraseña, el PIN de una tarjeta, ni el teléfono
completo (se conservan los últimos 4 dígitos). El teléfono ya estaba vetado en
los logs de on-ramp (Requirement 9.4); aquí se vuelve regla de todo el cliente.
"""
import json
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings

emida = logging.getLogger("tae.emida")

# Un body más largo que esto se recorta en el log. El catálogo completo de
# productos son cientos de KB y no aporta nada a una revisión; la evidencia
# completa de cada venta ya está en la base (IntentoProveedor).
MAX_CUERPO = 3000

# Se comparan sin mayúsculas ni guion bajo: `access_token` == `accessToken`.
_SECRETOS = {
    "authorization", "password", "contrasena", "token", "accesstoken",
    "refreshtoken", "apikey", "secret", "pin",
}
_TELEFONOS = {
    "accountid", "accountnumber", "phonenumber", "phone", "msisdn", "mobilenumber",
}
# Campos de texto libre del proveedor. Ahí a veces repite el número que le
# mandamos ("El número 55... no es válido"), y eso también es PII. Solo estos:
# un transactionId o un controlNo largo no se debe enmascarar, es la evidencia.
_MENSAJES = {"message", "responsemessage", "detail", "title", "description", "error"}

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_PORTADOR = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_NUMERO_LARGO = re.compile(r"\d{10,15}")


def _clave(nombre):
    return str(nombre).lower().replace("_", "").replace("-", "")


# ---------------------------------------------------------------------------
# Enmascarado
# ---------------------------------------------------------------------------
def enmascarar_telefono(valor):
    """5512345678 → ******5678. Lo que sobra alcanza para que soporte lo identifique."""
    texto = str(valor or "")
    if len(texto) <= 4:
        return "***" if texto else ""
    return "*" * (len(texto) - 4) + texto[-4:]


def enmascarar_correo(valor):
    """pedro@dominio.com → pe***@dominio.com"""
    texto = str(valor or "")
    usuario, arroba, dominio = texto.partition("@")
    if not arroba:
        return enmascarar_telefono(texto) if texto else ""
    return f"{usuario[:2]}***@{dominio}"


def sanear_mensaje(texto):
    """Un texto libre sin JWT ni números largos de teléfono."""
    texto = _PORTADOR.sub("Bearer ***", _JWT.sub("***JWT***", str(texto or "")))
    return _NUMERO_LARGO.sub(lambda m: enmascarar_telefono(m.group()), texto)


def sanear(valor, clave=""):
    """
    Copia de `valor` (dict, lista o escalar) con los datos sensibles
    enmascarados. No modifica el original: lo que se manda al proveedor y lo
    que se guarda en la base sigue completo.
    """
    k = _clave(clave)
    if k in _SECRETOS:
        return "***" if valor not in (None, "") else valor
    if k in _TELEFONOS and not isinstance(valor, (dict, list)):
        return enmascarar_telefono(valor) if valor not in (None, "") else valor
    if isinstance(valor, dict):
        return {c: sanear(v, c) for c, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [sanear(v, clave) for v in valor]
    if isinstance(valor, str):
        if k in _MENSAJES:
            return sanear_mensaje(valor)
        return _PORTADOR.sub("Bearer ***", _JWT.sub("***JWT***", valor))
    return valor


def sanear_cabeceras(cabeceras):
    """Los headers tal como salieron, con `Authorization: Bearer ***`."""
    return {c: sanear(v, c) for c, v in (cabeceras or {}).items()}


def recortar(texto, limite=MAX_CUERPO):
    if len(texto) <= limite:
        return texto
    return f"{texto[:limite]}…(+{len(texto) - limite} caracteres)"


def cuerpo_para_log(datos, texto, limite=MAX_CUERPO):
    """
    El body de una respuesta, listo para el log.

    `datos` es lo que dio `r.json()`, o None si la respuesta no era JSON; en
    ese caso se usa el texto recortado (p. ej. una página de error de un
    proxy). Se prefiere `datos` a re-leer el texto: así un transactionId o un
    controlNo largos, que son la evidencia, nunca pasan por el filtro de
    números de teléfono que solo aplica a texto libre.
    """
    if datos is None:
        return recortar(sanear_mensaje(texto), limite)
    return recortar(
        json.dumps(sanear(datos), ensure_ascii=False, separators=(",", ":"), default=str),
        limite,
    )


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------
def _valor(v):
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)
    texto = str(v)
    # Un JSON ya armado (el body de una respuesta) va tal cual: entrecomillarlo
    # llenaría la pantalla de \" y no se podría leer.
    if texto[:1] in ("{", "["):
        return texto
    # Con espacios se entrecomilla, para que `clave=valor` siga siendo
    # separable con un grep.
    if " " in texto or "=" in texto:
        return json.dumps(texto, ensure_ascii=False)
    return texto


def trazar(evento, /, *, nivel=logging.INFO, **campos):
    """
    Una línea de la bitácora de Emida: `EVENTO clave=valor clave=valor`.

    Los campos vacíos se omiten. Aunque quien llama ya enmascaró lo suyo, el
    mensaje final pasa otra vez por el filtro de JWT: si alguien agrega un
    campo nuevo y se le olvida, el token no llega al archivo.
    """
    partes = [f"{evento:<16}"]
    partes += [f"{k}={_valor(v)}" for k, v in campos.items() if v is not None and v != ""]
    mensaje = _PORTADOR.sub("Bearer ***", _JWT.sub("***JWT***", " ".join(partes)))
    emida.log(nivel, mensaje)


def milisegundos(desde):
    """Milisegundos transcurridos desde un `time.monotonic()`."""
    return int((time.monotonic() - desde) * 1000)


class FormatoEmida(logging.Formatter):
    """
    Marca de tiempo en la zona horaria del proyecto (America/Mexico_City) con
    milisegundos y desfase, p. ej. `2026-11-25 18:24:01.123-06:00`.

    Sin esto, un contenedor Docker (que corre en UTC) escribiría horas que no
    coinciden con las de la pantalla de la caja ni con las que ve Emida en su
    lado, y el timestamp es parte de la evidencia.
    """

    def formatTime(self, record, datefmt=None):
        zona = ZoneInfo(getattr(settings, "TIME_ZONE", "UTC"))
        return datetime.fromtimestamp(record.created, tz=zona).isoformat(
            sep=" ", timespec="milliseconds"
        )
