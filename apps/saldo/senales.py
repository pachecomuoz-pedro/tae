"""
Toda bolsa existe desde que nace su dueño.

Antes las bolsas se creaban la primera vez que se usaban. Bajo carga eso es
peligroso: dos operaciones que crean al mismo tiempo la primera bolsa de un
cliente y la cuenta de bonificación se bloquean entre sí dentro de MySQL y
una de las dos se aborta. Creándolas al dar de alta al inquilino, al
administrador o al cliente, ninguna operación de dinero inserta bolsas: solo
bloquea filas que ya existen.

`bolsa_de` y `bolsa_sistema` conservan su `get_or_create` como respaldo.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import Administrador, Cliente, Inquilino

from .models import Bolsa, ClaveSistema, TipoBolsa


@receiver(post_save, sender=Inquilino)
def cuentas_de_sistema(sender, instance, created, **kwargs):
    if not created:
        return
    for clave in ClaveSistema.values:
        Bolsa.objects.get_or_create(
            inquilino=instance, tipo=TipoBolsa.SISTEMA, clave_sistema=clave,
        )


@receiver(post_save, sender=Administrador)
def bolsa_del_administrador(sender, instance, created, **kwargs):
    if created:
        Bolsa.objects.get_or_create(
            administrador=instance,
            defaults={"inquilino": instance.inquilino, "tipo": TipoBolsa.ADMINISTRADOR},
        )


@receiver(post_save, sender=Cliente)
def bolsa_del_cliente(sender, instance, created, **kwargs):
    if created:
        Bolsa.objects.get_or_create(
            cliente=instance,
            defaults={"inquilino": instance.inquilino, "tipo": TipoBolsa.CLIENTE},
        )
