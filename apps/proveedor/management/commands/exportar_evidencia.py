"""
Evidencia de las ventas enviadas a Emida, para la certificación y el reporte diario.

El Plan de certificación pide, por cada caso, request, response, código HTTP
y timestamp (Fase 5), y durante el desarrollo un reporte diario de cuántas
transacciones, cuáles y por qué valor (Fase 2). Esto genera las dos cosas a
partir de lo que la plataforma ya guarda de cada venta (RegistroEnvio e
IntentoProveedor):

    <carpeta>/VTA-2026-000012.json   una venta: request y cada respuesta
    <carpeta>/reporte_emida.csv      formato del archivo de conciliación de
                                     Emida, exitosas y no exitosas
    resumen en pantalla              cuántas y por cuánto, por desenlace

En Windows (PowerShell), con Docker corriendo. La carpeta se escribe dentro
del proyecto, así que la ves en el Explorador de Windows:

    docker compose exec api python manage.py exportar_evidencia --desde 2026-11-10 --carpeta evidencias/TA-20261110

Usa barras normales (/) en --carpeta: el comando corre dentro del
contenedor, que es Linux.
"""
import csv
import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.proveedor.modelos import RegistroEnvio


def _fecha(texto, fin=False):
    try:
        dia = datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError as e:
        raise CommandError(f"Fecha inválida {texto!r}: usa AAAA-MM-DD") from e
    momento = datetime.combine(dia + timedelta(days=1) if fin else dia, time.min)
    return timezone.make_aware(momento)


def _json_o_texto(texto):
    try:
        return json.loads(texto)
    except (TypeError, ValueError):
        return texto


class Command(BaseCommand):
    help = "Exporta la evidencia de las ventas y el reporte en formato de conciliación de Emida."

    def add_arguments(self, parser):
        parser.add_argument("--desde", required=True, help="AAAA-MM-DD")
        parser.add_argument("--hasta", help="AAAA-MM-DD (incluido). Por omisión, el mismo día.")
        parser.add_argument("--carpeta", required=True)

    def handle(self, *args, **op):
        desde = _fecha(op["desde"])
        hasta = _fecha(op["hasta"] or op["desde"], fin=True)
        carpeta = Path(op["carpeta"])
        carpeta.mkdir(parents=True, exist_ok=True)

        cfg = settings.TAE_PROVEEDOR
        base = f"{cfg.get('base_url', '')}{cfg.get('prefijo_operaciones', '')}"
        envios = (
            RegistroEnvio.objects.filter(creado__gte=desde, creado__lt=hasta)
            .select_related("operacion")
            .prefetch_related("intentos_detalle")
            .order_by("creado")
        )

        totales = defaultdict(lambda: [0, Decimal("0")])
        with open(carpeta / "reporte_emida.csv", "w", newline="", encoding="utf-8") as f:
            reporte = csv.writer(f)
            # Mismas columnas y orden que Conciliation_File_Emida_Example.csv.
            reporte.writerow(["Date_Time", "Terminal_Id", "ResponseTransaction_Id", "Invoice_Id",
                              "Product_Id", "Amount_Id", "Account ID", "ResponseCode"])
            for e in envios:
                folio = e.operacion.folio
                totales[e.desenlace][0] += 1
                totales[e.desenlace][1] += e.monto

                evidencia = {
                    "folio": folio,
                    "caso_matriz": "",  # llénalo al armar la carpeta: TX-T-04, TX-LK-07…
                    "desenlace": e.desenlace,
                    "request": {
                        "metodo": "POST",
                        "url": f"{base}/api/v1/transaction/topup",
                        "headers": {
                            "Authorization": "Bearer ***",
                            "Site-Id": str(cfg.get("site_id", "")),
                            "Clerk-Id": str(cfg.get("clerk_id", "")),
                            "Language": str(cfg.get("language", "")),
                            "Invoice-No": e.invoice_no,
                            "Product-Id": e.product_id,
                        },
                        "body": {"accountId": e.account_id, "amount": str(e.monto)},
                        "timestamp": timezone.localtime(e.creado).isoformat(timespec="seconds"),
                    },
                    "intentos": [
                        {
                            "tipo": i.tipo,
                            "timestamp": timezone.localtime(i.cuando).isoformat(timespec="seconds"),
                            "http": i.http,
                            "responseCode": i.response_code,
                            "h2hResultCode": i.h2h_result_code,
                            "aprobada": i.aprobada,
                            "body": _json_o_texto(i.cuerpo),
                            "error": i.error,
                        }
                        for i in e.intentos_detalle.all()
                    ],
                    "transactionId": e.transaction_id,
                    "controlNo": e.control_no,
                    "cerrado": (timezone.localtime(e.cerrado).isoformat(timespec="seconds")
                                if e.cerrado else None),
                }
                (carpeta / f"{folio}.json").write_text(
                    json.dumps(evidencia, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                reporte.writerow([
                    timezone.localtime(e.creado).strftime("%d/%m/%Y %H:%M"),
                    cfg.get("site_id", ""),
                    e.transaction_id,
                    e.invoice_no,
                    e.product_id,
                    f"{e.monto.normalize():f}",
                    e.account_id,
                    e.response_code,
                ])

        total = sum(n for n, _ in totales.values())
        self.stdout.write(self.style.SUCCESS(f"{total} ventas exportadas a {carpeta}"))
        for desenlace, (n, monto) in sorted(totales.items()):
            self.stdout.write(f"  {desenlace:<14} {n:>5}   ${monto:,.2f}")
