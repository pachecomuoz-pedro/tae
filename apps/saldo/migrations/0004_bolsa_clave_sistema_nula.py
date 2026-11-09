"""
Fuerza `clave_sistema` a nulable en bases que vienen de la versión vieja.

Historia, porque importa: edité `0001_initial` después de aplicarla. Django
guarda que ya corrió y nunca la repite, así que una base creada antes del
cambio quedó con la columna NOT NULL mientras el modelo decía lo contrario.
Las pruebas no lo vieron porque crean la base desde cero cada vez.

El primer intento de arreglo fue un `AlterField`, y salió **no-op**: Django
compara el campo nuevo contra su propio estado de migraciones, no contra la
base real, y en su estado la columna ya era nulable. Por eso aquí va SQL
explícito. `MODIFY COLUMN ... NULL` es idempotente: sobre una base correcta
no cambia nada, sobre una divergente la corrige.

La regla que se rompió y no se vuelve a romper: **una migración aplicada no
se edita, se corrige con otra encima.**
"""
from django.db import migrations

ARREGLAR = "ALTER TABLE saldo_bolsa MODIFY COLUMN clave_sistema varchar(32) NULL;"
DESHACER = "ALTER TABLE saldo_bolsa MODIFY COLUMN clave_sistema varchar(32) NOT NULL;"


class Migration(migrations.Migration):

    dependencies = [
        ("saldo", "0003_operacion_invoice_ciclo_operacion_invoice_no_and_more"),
    ]

    operations = [
        migrations.RunSQL(sql=ARREGLAR, reverse_sql=DESHACER),
    ]
