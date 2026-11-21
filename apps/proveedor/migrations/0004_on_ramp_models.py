import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('proveedor', '0003_autorizacion_carrier_mensaje_pin'),
        ('saldo', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='RegistroOnRamp',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('operacion', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='envio_on_ramp',
                    to='saldo.operacion',
                )),
                ('invoice_no', models.CharField(max_length=100)),
                ('product_id', models.CharField(max_length=100)),
                ('phone_number', models.CharField(max_length=20)),
                ('monto', models.DecimalField(decimal_places=4, max_digits=18)),
                ('desenlace', models.CharField(
                    choices=[
                        ('pendiente', 'Pendiente'),
                        ('aprobada', 'Aprobada'),
                        ('rechazada', 'Rechazada'),
                        ('sin_resolver', 'Sin resolver'),
                        ('requiere_revision_manual', 'Requiere revisión manual'),
                    ],
                    default='pendiente',
                    max_length=25,
                )),
                ('intentos', models.PositiveSmallIntegerField(default=0)),
                ('detalle', models.CharField(blank=True, max_length=1000)),
                ('transaction_id', models.CharField(blank=True, max_length=100)),
                ('target_amount', models.DecimalField(blank=True, decimal_places=4, max_digits=18, null=True)),
                ('target_asset', models.CharField(blank=True, max_length=20)),
                ('fee', models.DecimalField(blank=True, decimal_places=4, max_digits=18, null=True)),
                ('response_message', models.TextField(blank=True)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('cerrado', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'proveedor_registro_on_ramp',
            },
        ),
        migrations.CreateModel(
            name='IntentoOnRamp',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('registro', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='intentos_detalle',
                    to='proveedor.registroonramp',
                )),
                ('tipo', models.CharField(max_length=20)),
                ('http', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('aprobada', models.BooleanField(default=False)),
                ('cuerpo', models.TextField(blank=True)),
                ('error', models.CharField(blank=True, max_length=500)),
                ('resultado', models.CharField(blank=True, max_length=40)),
                ('cuando', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'proveedor_intento_on_ramp',
                'ordering': ['cuando', 'id'],
            },
        ),
    ]
