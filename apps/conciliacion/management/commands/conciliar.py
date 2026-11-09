"""
Importa y concilia un archivo del proveedor.

    python manage.py conciliar archivo.csv --inquilino scorpion

Con --sin-resolver no aplica nada y solo reporta, para poder ver qué haría
antes de dejarlo hacerlo.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.conciliacion import servicios
from apps.core.models import Inquilino


class Command(BaseCommand):
    """Ver el docstring del módulo y docs/DESPLIEGUE.md para programarlo."""
    help = "Importa y concilia el archivo del proveedor"

    def add_arguments(self, parser):
        parser.add_argument("archivo")
        parser.add_argument("--inquilino", default="scorpion")
        parser.add_argument(
            "--sin-resolver", action="store_true",
            help="solo reporta; no captura ni libera nada",
        )

    def handle(self, *args, **opciones):
        try:
            inquilino = Inquilino.objects.get(clave=opciones["inquilino"])
        except Inquilino.DoesNotExist:
            raise CommandError(f"No existe el inquilino {opciones['inquilino']}")

        ruta = opciones["archivo"]
        try:
            with open(ruta, "rb") as f:
                contenido = f.read()
        except OSError as e:
            raise CommandError(f"No se pudo leer {ruta}: {e}")

        try:
            archivo = servicios.importar(
                inquilino, contenido, nombre=ruta.split("/")[-1]
            )
            servicios.conciliar(archivo, resolver=not opciones["sin_resolver"])
        except servicios.ErrorArchivo as e:
            raise CommandError(str(e))

        datos = servicios.resumen(archivo)
        self.stdout.write(f"Archivo: {datos['archivo']}")
        self.stdout.write(f"  renglones          {datos['renglones']}")
        self.stdout.write(f"  importe proveedor  {datos['importe_proveedor']}")
        self.stdout.write(f"  conciliados        {datos['conciliados']}")
        self.stdout.write(f"  diferencias        {datos['abiertas']}")
        for tipo, info in datos["por_tipo"].items():
            self.stdout.write(
                f"    {info['nombre']}: {info['cuantas']} por {info['impacto']}"
            )
        if datos["cuadra"]:
            self.stdout.write(self.style.SUCCESS("  CUADRA"))
        else:
            self.stdout.write(self.style.WARNING("  NO CUADRA: hay que revisarlo"))
