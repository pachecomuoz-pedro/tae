"""
Muestra qué pasó con una venta: lo que se envió, cada llamada al proveedor
y lo que contestó.

    docker compose exec api python manage.py ver_venta VTA-2026-000004

Es la primera herramienta cuando una venta sale rechazada o sin resolver y
no se sabe por qué. Lee la base, no llama al proveedor.

Muestra el cuerpo de la respuesta tal como llegó. En una venta de pines ese
cuerpo trae el PIN: no se pega completo en un chat ni en un ticket sin
borrarlo antes.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.proveedor.modelos import RegistroEnvio


class Command(BaseCommand):
    help = "Muestra el envío y las respuestas del proveedor de una venta, por folio."

    def add_arguments(self, parser):
        parser.add_argument("folio", help="Folio de la venta, p. ej. VTA-2026-000004")

    def handle(self, *args, folio, **opciones):
        registro = (
            RegistroEnvio.objects.filter(operacion__folio=folio.strip().upper())
            .select_related("operacion", "operacion__punto_venta", "operacion__producto")
            .first()
        )
        if registro is None:
            raise CommandError(
                f"No hay envío al proveedor para {folio}. Si la venta se rechazó antes "
                "de salir (saldo, permiso, caja sin límite), no llegó a esta tabla."
            )

        op = registro.operacion
        e = self.stdout.write
        e(self.style.MIGRATE_HEADING(f"Venta {op.folio}"))
        e(f"  Caja:        {getattr(op.punto_venta, 'nombre', '—')}")
        e(f"  Producto:    {getattr(op.producto, 'nombre', '—')} (Product-Id {registro.product_id})")
        e(f"  Número:      {registro.account_id}")
        e(f"  Monto:       {registro.monto}")
        e(f"  Invoice-No:  {registro.invoice_no}")
        e(f"  Desenlace:   {registro.get_desenlace_display()}")
        e(f"  Detalle:     {registro.detalle or '—'}")
        e(f"  responseCode {registro.response_code or '—'} · h2hResultCode "
          f"{registro.h2h_result_code or '—'} · controlNo {registro.control_no or '—'}")
        e("")
        for i, intento in enumerate(registro.intentos_detalle.order_by("pk"), start=1):
            e(self.style.MIGRATE_LABEL(
                f"  {i}. {intento.tipo} · HTTP {intento.http or '—'} · "
                f"responseCode {intento.response_code or '—'} · "
                f"{'APROBADA' if intento.aprobada else 'no aprobada'}"
            ))
            if intento.error:
                e(f"     error:  {intento.error}")
            if intento.cuerpo:
                e(f"     cuerpo: {intento.cuerpo}")
