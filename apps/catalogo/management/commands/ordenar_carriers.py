"""
Fija el orden de las compañías en la pantalla de venta.

Las 8 primeras ocupan los botones fijos; el resto va a "Otros". Las que no
se nombran quedan detrás, en orden alfabético.

    python manage.py ordenar_carriers Telcel Movistar "AT&T" Unefon Bait \
        "Soriana Móvil" "Virgin Mobile" Bigcel
    python manage.py ordenar_carriers --ver
"""
import os
import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalogo.models import Carrier, TipoProducto
from apps.core.models import Inquilino


def _clave(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    ).lower().strip()


class Command(BaseCommand):
    help = "Fija qué compañías de tiempo aire van primero en la pantalla de venta."

    def add_arguments(self, parser):
        parser.add_argument("nombres", nargs="*", help="Compañías en el orden deseado")
        parser.add_argument("--inquilino", default=os.environ.get("TAE_INQUILINO") or "scorpion")
        parser.add_argument("--ver", action="store_true", help="Solo muestra el orden actual")

    def handle(self, *args, nombres, inquilino, ver, **opciones):
        qs = Inquilino.objects.all()
        inq = qs.filter(clave=inquilino).first() if inquilino else (qs.first() if qs.count() == 1 else None)
        if inq is None:
            raise CommandError("Indica el inquilino con --inquilino (o TAE_INQUILINO en .env).")

        carriers = list(Carrier.objects.filter(inquilino=inq, tipo=TipoProducto.TIEMPO_AIRE))
        if ver or not nombres:
            self._mostrar(carriers)
            return

        por_clave = {_clave(c.nombre): c for c in carriers}
        faltan = [n for n in nombres if _clave(n) not in por_clave]
        if faltan:
            disponibles = ", ".join(sorted(c.nombre for c in carriers))
            raise CommandError(f"No están en el catálogo: {', '.join(faltan)}. Hay: {disponibles}")

        with transaction.atomic():
            elegidos = [por_clave[_clave(n)] for n in nombres]
            for i, c in enumerate(elegidos, start=1):
                c.orden = i
                c.save(update_fields=["orden"])
            resto = sorted((c for c in carriers if c not in elegidos), key=lambda c: c.nombre)
            for i, c in enumerate(resto, start=len(elegidos) + 1):
                c.orden = 100 + i
                c.save(update_fields=["orden"])

        self._mostrar(Carrier.objects.filter(inquilino=inq, tipo=TipoProducto.TIEMPO_AIRE))

    def _mostrar(self, carriers):
        for i, c in enumerate(sorted(carriers, key=lambda c: (c.orden, c.nombre)), start=1):
            lugar = f"botón {i}" if i <= 8 else "Otros"
            activo = "" if c.activo else "  (inactiva)"
            self.stdout.write(f"{i:>3}. {c.nombre:<24} {lugar}{activo}")
