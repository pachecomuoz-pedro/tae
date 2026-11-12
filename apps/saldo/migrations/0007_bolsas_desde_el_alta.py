"""Crea las bolsas que falten: cada inquilino, administrador y cliente ya existentes."""
from django.db import migrations

CLAVES = ("FONDEO_EPS", "BONIFICACION_MARGEN", "COSTO_VENTA_PROVEEDOR")


def crear(apps, schema_editor):
    Bolsa = apps.get_model("saldo", "Bolsa")
    Inquilino = apps.get_model("core", "Inquilino")
    Administrador = apps.get_model("core", "Administrador")
    Cliente = apps.get_model("core", "Cliente")
    for inq in Inquilino.objects.all():
        for clave in CLAVES:
            Bolsa.objects.get_or_create(inquilino=inq, tipo="sistema", clave_sistema=clave)
    for a in Administrador.objects.all():
        Bolsa.objects.get_or_create(
            administrador=a, defaults={"inquilino_id": a.inquilino_id, "tipo": "administrador"}
        )
    for c in Cliente.objects.all():
        Bolsa.objects.get_or_create(
            cliente=c, defaults={"inquilino_id": c.inquilino_id, "tipo": "cliente"}
        )


class Migration(migrations.Migration):
    dependencies = [
        ("saldo", "0006_blindaje_retiros_e_historial_limites"),
        ("core", "0004_administrador_margen_pct"),
    ]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
