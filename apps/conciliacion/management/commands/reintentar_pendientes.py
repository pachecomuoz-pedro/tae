"""
Vuelve a consultar las ventas que quedaron sin resolver.

    python manage.py reintentar_pendientes --inquilino scorpion

Esto es lo que corre cada pocos minutos por cron o por Celery. Sin esto, una
venta sin resolver se queda así para siempre: la reserva viva, el saldo del
tendero bloqueado, y nadie enterándose hasta que llame a reclamar.

Revisa tiempo aire y las ventas on-ramp (cripto), cada una con su propia
regla de cierre.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Inquilino
from apps.api.proveedor import obtener_cliente
from apps.proveedor.on_ramp import reintentar_sin_resolver_onramp
from apps.proveedor.venta import reintentar_sin_resolver


class Command(BaseCommand):
    """Ver el docstring del módulo. En producción corre por cron cada pocos minutos."""
    help = "Reintenta la consulta de las ventas sin resolver"

    def add_arguments(self, parser):
        parser.add_argument("--inquilino", default="scorpion")
        parser.add_argument(
            "--limite", type=int, default=None,
            help="Máximo de ventas por corrida. Sin él: 100 en tiempo aire y, en "
                 "on-ramp, TAE_ON_RAMP_LIMITE_CONCILIACION",
        )

    def handle(self, *args, **opciones):
        try:
            inquilino = Inquilino.objects.get(clave=opciones["inquilino"])
        except Inquilino.DoesNotExist:
            raise CommandError(f"No existe el inquilino {opciones['inquilino']}")

        # obtener_cliente respeta PROVEEDOR_MODO: en desarrollo consulta al
        # simulado y en producción al real, igual que la venta.
        cliente = obtener_cliente()
        resueltos = reintentar_sin_resolver(
            cliente, inquilino, limite=opciones["limite"] or 100
        )
        resueltos_cripto = reintentar_sin_resolver_onramp(
            cliente, inquilino, limite=opciones["limite"]
        )
        if not resueltos and not resueltos_cripto:
            self.stdout.write("Nada que resolver")
            return
        for cierre in resueltos:
            self.stdout.write(
                f"{cierre.reserva.operacion.folio}: {cierre.desenlace} · {cierre.detalle}"
            )
        for registro in resueltos_cripto:
            self.stdout.write(
                f"{registro.operacion.folio}: {registro.desenlace} · {registro.detalle} (cripto)"
            )
        total = len(resueltos) + len(resueltos_cripto)
        self.stdout.write(self.style.SUCCESS(f"{total} resueltas"))
