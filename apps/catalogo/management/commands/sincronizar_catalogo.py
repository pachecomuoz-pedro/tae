"""
Actualiza el catálogo con lo que Emida lista para el terminal.

Corre una vez al día solo, desde el servicio `tareas` (ver
apps/core/management/commands/programador.py). A mano, en Windows:

    docker compose exec web python manage.py sincronizar_catalogo

Qué hace y qué no: ver apps/catalogo/sincronizacion.py.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.api.proveedor import obtener_cliente
from apps.catalogo.sincronizacion import SincronizacionVacia, sincronizar
from apps.core.models import Inquilino
from apps.proveedor.cliente import ErrorProveedor


class Command(BaseCommand):
    help = "Descarga productos y product-flow de Emida y actualiza la base."

    def add_arguments(self, parser):
        parser.add_argument("--inquilino", default="scorpion")
        parser.add_argument("--proveedor", default="axexo",
                            help="Clave del proveedor en el catálogo.")

    def handle(self, *args, **op):
        inquilino = Inquilino.objects.filter(clave=op["inquilino"]).first()
        if inquilino is None:
            raise CommandError(f"No existe el inquilino {op['inquilino']}")
        try:
            r = sincronizar(inquilino, obtener_cliente(), proveedor_clave=op["proveedor"])
        except SincronizacionVacia as e:
            raise CommandError(str(e)) from e
        except ErrorProveedor as e:
            raise CommandError(f"Emida no respondió bien: {e}. No se cambió nada.") from e

        self.stdout.write(self.style.SUCCESS(f"Productos listados por Emida: {r['productos']}"))
        self.stdout.write(f"  Nuevos:                  {len(r['nuevos'])}")
        self.stdout.write(f"  Ya no listados:          {len(r['ya_no_listados'])}")
        self.stdout.write(f"  Rutas pausadas:          {', '.join(r['rutas_pausadas']) or 'ninguna'}")
        self.stdout.write(f"  Rutas reanudadas:        {', '.join(r['rutas_reanudadas']) or 'ninguna'}")
        self.stdout.write(f"  Con textos product-flow: {r['con_flujo']}")
        if r["sin_vincular"]:
            self.stdout.write(
                "  Sin vincular a la pantalla (usa vincular_producto): "
                + ", ".join(r["sin_vincular"][:30])
                + (" …" if len(r["sin_vincular"]) > 30 else "")
            )
