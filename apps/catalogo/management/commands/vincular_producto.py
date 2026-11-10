"""
Pone un producto de Emida en la pantalla del cajero.

La sincronización trae lo que Emida ofrece; esto decide qué se vende y con
qué nombre (decisión 12). Crea el producto lógico, su carrier si no existe,
y la ruta al product_id de Emida. Si el producto lógico ya existe, solo
agrega o actualiza la ruta.

Ejemplo (PowerShell, una sola línea):

    docker compose exec api python manage.py vincular_producto --product-id 2001 --sku TEL-TAE-100 --nombre "Telcel Tiempo Aire $100" --carrier Telcel --monto 100

Para ver los product_id disponibles:

    docker compose exec api python manage.py vincular_producto --listar
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.catalogo.models import (
    Carrier,
    ProductoDelProveedor,
    ProductoLogico,
    ProductoProveedor,
    Proveedor,
    TipoMonto,
    TipoProducto,
)
from apps.core.models import Inquilino


class Command(BaseCommand):
    help = "Crea (o actualiza) un producto de la pantalla ligado a un product_id de Emida."

    def add_arguments(self, parser):
        parser.add_argument("--listar", action="store_true",
                            help="Solo lista lo que Emida ofrece y si ya está vinculado.")
        parser.add_argument("--product-id")
        parser.add_argument("--sku", help="Identidad estable; es lo que lleva el código de barras.")
        parser.add_argument("--nombre")
        parser.add_argument("--carrier", help="Telcel, AT&T, Movistar…")
        parser.add_argument("--monto", help="Denominación fija. Sin él se usa el monto que reporta Emida.")
        parser.add_argument("--tipo", default=TipoProducto.TIEMPO_AIRE,
                            choices=[c for c, _ in TipoProducto.choices])
        parser.add_argument("--orden", type=int, default=100)
        parser.add_argument("--inquilino", default="scorpion")
        parser.add_argument("--proveedor", default="axexo")

    def handle(self, *args, **op):
        inquilino = Inquilino.objects.filter(clave=op["inquilino"]).first()
        if inquilino is None:
            raise CommandError(f"No existe el inquilino {op['inquilino']}")
        proveedor = Proveedor.objects.filter(inquilino=inquilino, clave=op["proveedor"]).first()
        if proveedor is None:
            raise CommandError(f"No existe el proveedor {op['proveedor']} en el catálogo")

        if op["listar"]:
            return self._listar(proveedor)

        faltan = [c for c in ("product_id", "sku", "nombre", "carrier") if not op[c]]
        if faltan:
            raise CommandError("Faltan: " + ", ".join("--" + c.replace("_", "-") for c in faltan))
        self._vincular(inquilino, proveedor, op)

    def _listar(self, proveedor):
        vinculados = set(
            ProductoProveedor.objects.filter(proveedor=proveedor).values_list("product_id", flat=True)
        )
        ofertas = ProductoDelProveedor.objects.filter(proveedor=proveedor, disponible=True)
        if not ofertas:
            self.stdout.write("Sin productos. Corre primero: python manage.py sincronizar_catalogo")
        for o in ofertas:
            marca = "vinculado" if o.product_id in vinculados else "—"
            monto = f"${o.monto:,.2f}" if o.monto is not None else "?"
            self.stdout.write(f"{o.product_id:>10}  {o.carrier[:18]:<18} {monto:>12}  {marca:<10} {o.descripcion[:60]}")

    @transaction.atomic
    def _vincular(self, inquilino, proveedor, op):
        oferta = ProductoDelProveedor.objects.filter(
            proveedor=proveedor, product_id=op["product_id"]
        ).first()
        if oferta is None:
            self.stderr.write(
                f"Aviso: {op['product_id']} no aparece en la última sincronización. "
                "Se vincula de todos modos; revisa que el product_id sea correcto."
            )
        monto = Decimal(op["monto"]) if op["monto"] else (oferta.monto if oferta else None)
        if monto is None:
            raise CommandError("No se conoce el monto: pásalo con --monto")

        carrier, _ = Carrier.objects.get_or_create(
            inquilino=inquilino, clave=slugify(op["carrier"])[:40],
            defaults={"nombre": op["carrier"], "tipo": op["tipo"]},
        )
        producto, creado = ProductoLogico.objects.get_or_create(
            inquilino=inquilino, sku=op["sku"],
            defaults={
                "carrier": carrier, "nombre": op["nombre"], "tipo": op["tipo"],
                "tipo_monto": TipoMonto.FIJO, "monto_fijo": monto, "orden": op["orden"],
            },
        )
        if creado:
            producto.full_clean()
        ruta, ruta_nueva = ProductoProveedor.objects.update_or_create(
            producto=producto, proveedor=proveedor,
            defaults={"product_id": op["product_id"], "activo": True,
                      "pausada_por_proveedor": bool(oferta and not oferta.disponible)},
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Creado' if creado else 'Ya existía'} {producto.sku} ({producto.nombre}) "
            f"→ {proveedor.clave}:{ruta.product_id}"
        ))
