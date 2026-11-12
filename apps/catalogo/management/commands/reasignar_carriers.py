"""
Acomoda por compañía los productos que quedaron en "Otros".

    docker compose exec api python manage.py reasignar_carriers

Pasa cuando Emida no manda el carrier en un campo propio: la instalación
los agrupó en "Otros" y la caja mostraba tres botones de $50 sin decir de
quién era cada uno. Deduce la compañía del nombre del producto
("PRUEBA Recarga Movistar $10" → Movistar). Se puede correr las veces que
sea: lo que ya está acomodado no se toca.
"""
from django.core.management.base import BaseCommand

from apps.catalogo.sincronizacion import reasignar_carriers
from apps.core.models import Inquilino


class Command(BaseCommand):
    help = 'Mueve a su compañía los productos que quedaron en "Otros".'

    def add_arguments(self, parser):
        parser.add_argument("--inquilino", default="scorpion")

    def handle(self, *args, inquilino, **opciones):
        movidos = reasignar_carriers(Inquilino.objects.get(clave=inquilino))
        for sku, nombre in movidos:
            self.stdout.write(f"  {sku} → {nombre}")
        self.stdout.write(self.style.SUCCESS(f"{len(movidos)} productos acomodados."))
