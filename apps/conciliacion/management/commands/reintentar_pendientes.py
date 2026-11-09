"""
Vuelve a consultar las ventas que quedaron sin resolver.

    python manage.py reintentar_pendientes --inquilino scorpion

Esto es lo que corre cada pocos minutos por cron o por Celery. Sin esto, una
venta sin resolver se queda así para siempre: la reserva viva, el saldo del
tendero bloqueado, y nadie enterándose hasta que llame a reclamar.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Inquilino
from apps.api.proveedor import obtener_cliente
from apps.proveedor.venta import reintentar_sin_resolver


class Command(BaseCommand):
    """Ver el docstring del módulo. En producción corre por cron cada pocos minutos."""
    help = "Reintenta la consulta de las ventas sin resolver"

    def add_arguments(self, parser):
        parser.add_argument("--inquilino", default="scorpion")
        parser.add_argument("--limite", type=int, default=100)

    def handle(self, *args, **opciones):
        try:
            inquilino = Inquilino.objects.get(clave=opciones["inquilino"])
        except Inquilino.DoesNotExist:
            raise CommandError(f"No existe el inquilino {opciones['inquilino']}")

        # obtener_cliente respeta PROVEEDOR_MODO: en desarrollo consulta al
        # simulado y en producción al real, igual que la venta.
        resueltos = reintentar_sin_resolver(
            obtener_cliente(), inquilino, limite=opciones["limite"]
        )
        if not resueltos:
            self.stdout.write("Nada que resolver")
            return
        for cierre in resueltos:
            self.stdout.write(
                f"{cierre.reserva.operacion.folio}: {cierre.desenlace} · {cierre.detalle}"
            )
        self.stdout.write(self.style.SUCCESS(f"{len(resueltos)} resueltas"))
