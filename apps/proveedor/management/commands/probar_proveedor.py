"""
Diagnóstico de la conexión con Emida, paso por paso, sin pasar por pantallas.

Solo hace consultas: login, carriers, productos, product-flow, saldo y,
opcionalmente, el lookup de un Invoice-No. NUNCA vende: una venta hecha
desde aquí no quedaría en el ledger y la conciliación la reportaría como un
cobro desconocido. Las ventas de prueba se hacen desde la pantalla de la caja.

En Windows (PowerShell), con Docker corriendo:

    docker compose exec api python manage.py probar_proveedor
    docker compose exec api python manage.py probar_proveedor --probar-prefijos
    docker compose exec api python manage.py probar_proveedor --lookup 12 --evidencia evidencias/sesion1

--evidencia guarda cada petición y respuesta en JSON con fecha y hora, como
pide el Plan de certificación (request, response, código HTTP, timestamp).
El token y la contraseña se enmascaran antes de escribir.
"""
import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.api.proveedor import obtener_cliente
from apps.catalogo.sincronizacion import extraer_lista
from apps.proveedor.cliente import ClienteProveedor, Config, ErrorProveedor
from apps.proveedor.saldo import leer_saldo

SECRETOS = ("authorization", "password", "token", "accesstoken", "access_token")


def _enmascarar(valor):
    """Copia del objeto con los secretos cambiados por asteriscos."""
    if isinstance(valor, dict):
        return {
            k: ("***" if k.lower() in SECRETOS else _enmascarar(v)) for k, v in valor.items()
        }
    if isinstance(valor, list):
        return [_enmascarar(v) for v in valor]
    return valor


class SesionGrabadora(requests.Session):
    """Una sesión de requests que guarda cada llamada en una carpeta, sin secretos."""

    def __init__(self, carpeta):
        super().__init__()
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def request(self, method, url, **kw):
        inicio = datetime.now()
        respuesta = super().request(method, url, **kw)
        self.n += 1
        try:
            cuerpo = respuesta.json()
        except ValueError:
            cuerpo = respuesta.text[:4000]
        ruta = url.split("://", 1)[-1].split("/", 1)[-1].replace("/", "_")[:80]
        archivo = self.carpeta / f"{inicio:%Y%m%d_%H%M%S}_{self.n:02d}_{ruta}.json"
        archivo.write_text(json.dumps({
            "timestamp": inicio.isoformat(timespec="seconds"),
            "request": {
                "metodo": method, "url": url,
                "headers": _enmascarar(dict(kw.get("headers") or {})),
                "body": _enmascarar(kw.get("json")),
            },
            "response": {"http": respuesta.status_code, "body": _enmascarar(cuerpo)},
            "duracion_ms": int((datetime.now() - inicio).total_seconds() * 1000),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return respuesta


class Command(BaseCommand):
    help = "Prueba login y consultas contra Emida. No vende."

    def add_arguments(self, parser):
        parser.add_argument("--probar-prefijos", action="store_true",
                            help="Prueba las consultas sin prefijo y con /integratorapi.")
        parser.add_argument("--lookup", help="Invoice-No a consultar (LookUpTransactionByInvoiceNo).")
        parser.add_argument("--evidencia", help="Carpeta donde guardar request/response en JSON.")

    # -- salida -----------------------------------------------------------
    def ok(self, texto):
        self.stdout.write(self.style.SUCCESS("  OK     ") + texto)

    def falla(self, texto):
        self.stdout.write(self.style.ERROR("  FALLA  ") + texto)

    def paso(self, titulo, funcion, resumir):
        inicio = time.monotonic()
        try:
            r = funcion()
        except ErrorProveedor as e:
            http = getattr(e, "http", None)
            self.falla(f"{titulo}: {e}" + (f" (HTTP {http})" if http else ""))
            return None
        segundos = time.monotonic() - inicio
        self.ok(f"{titulo}: HTTP {r.http} en {segundos:.1f} s. {resumir(r)}")
        return r

    # -- principal --------------------------------------------------------
    def handle(self, *args, **op):
        modo = settings.TAE_PROVEEDOR.get("modo")
        base = Config.desde_settings()
        self.stdout.write(self.style.MIGRATE_HEADING("Configuración"))
        self.stdout.write(f"  Modo:                 {modo}")
        self.stdout.write(f"  Base:                 {base.base_url or '(vacía)'}")
        self.stdout.write(f"  Prefijo operaciones:  {base.prefijo_operaciones or '(ninguno)'}")
        for nombre, valor in (("Usuario", base.usuario), ("Contraseña", base.contrasena),
                              ("Site-Id", base.site_id), ("Clerk-Id", base.clerk_id),
                              ("Merchant-Id", base.merchant_id)):
            estado = "configurado" if valor else "FALTA en el .env"
            if nombre in ("Site-Id", "Clerk-Id", "Merchant-Id") and valor:
                estado = str(valor)
            self.stdout.write(f"  {nombre + ':':<22}{estado}")
        if modo == "simulado":
            self.stdout.write(self.style.WARNING(
                "  Modo simulado: estas respuestas son del emulador, no de Emida. "
                "Pon PROVEEDOR_MODO=real para probar contra UAT."
            ))

        sesion = SesionGrabadora(op["evidencia"]) if op["evidencia"] else None
        if modo == "simulado":
            cliente = obtener_cliente()
        else:
            cliente = ClienteProveedor(base, sesion=sesion)

        self.stdout.write(self.style.MIGRATE_HEADING("\n1. Login"))
        try:
            cliente.token()
            self.ok("token obtenido")
        except ErrorProveedor as e:
            self.falla(str(e))
            self.stdout.write(
                "  Si dice 'timed out': la IP de este equipo no está autorizada en axexo.\n"
                "  Si dice HTTP 401: revisa PROVEEDOR_USUARIO y PROVEEDOR_CONTRASENA."
            )
            return

        if op["probar_prefijos"] and modo != "simulado":
            self.stdout.write(self.style.MIGRATE_HEADING("\n2. ¿Con qué prefijo responden las operaciones?"))
            for prefijo in ("", "/integratorapi"):
                prueba = ClienteProveedor(replace(base, prefijo_operaciones=prefijo), sesion=sesion)
                self.paso(f"carriers con prefijo '{prefijo or 'ninguno'}'", prueba.carriers,
                          lambda r: f"{len(extraer_lista(r.datos))} carriers")
            self.stdout.write(
                "  Usa en PROVEEDOR_PREFIJO_OPERACIONES el prefijo que respondió 200."
            )

        self.stdout.write(self.style.MIGRATE_HEADING("\n3. Consultas de información"))
        self.paso("GetCarrierList    /terminal/carriers", cliente.carriers,
                  lambda r: f"{len(extraer_lista(r.datos))} carriers")
        self.paso("GetProductList    /terminal/products", cliente.productos,
                  lambda r: f"{len(extraer_lista(r.datos))} productos")
        self.paso("GetProductListExt /terminal/products/ext", cliente.productos_ext,
                  lambda r: f"{len(extraer_lista(r.datos))} productos")
        self.paso("ProductFlowInfo   /terminal/product-flow", cliente.product_flow,
                  lambda r: f"{len(extraer_lista(r.datos))} productos con flujo")

        self.stdout.write(self.style.MIGRATE_HEADING("\n4. Saldo de EPS en Emida"))

        def saldo_legible(r):
            saldo, moneda = leer_saldo(r.datos)
            if saldo is None:
                return "Respondió, pero no se encontró el saldo. Respuesta: " + json.dumps(r.datos)[:300]
            return f"Saldo: {saldo:,.2f} {moneda}".strip()

        self.paso("Account Balance   /account/balance", cliente.saldo_merchant, saldo_legible)

        if op["lookup"]:
            self.stdout.write(self.style.MIGRATE_HEADING("\n5. LookUpTransactionByInvoiceNo"))
            self.paso(f"lookup {op['lookup']}", lambda: cliente.lookup(op["lookup"]),
                      lambda r: f"responseCode={r.response_code or '-'} "
                                f"aprobada={'sí' if r.aprobada else 'no'} {r.mensaje[:120]}")

        if sesion is not None and modo == "simulado":
            self.stdout.write("\nEn modo simulado no se guarda evidencia: no hubo llamadas a Emida.")
        elif sesion is not None:
            self.stdout.write(f"\nEvidencia guardada en {sesion.carpeta} ({sesion.n} llamadas).")
