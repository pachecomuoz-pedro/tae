"""
Tareas programadas de la plataforma, dentro de Docker.

Hace lo que haría un crontab, pero funciona igual en Windows (que no tiene
crontab) y en el servidor Linux, porque corre en su propio contenedor
(`tareas` en docker-compose.yml y en deploy/docker-compose.prod.yml).

    Tarea                   Cuándo                              Variable
    sincronizar_catalogo    una vez al día, a TAE_HORA_CATALOGO   TAE_HORA_CATALOGO=04:00
    reintentar_pendientes   cada TAE_REINTENTOS_MINUTOS minutos   TAE_REINTENTOS_MINUTOS=5

Equivale a este crontab:

    0 4 * * *     python manage.py sincronizar_catalogo
    */5 * * * *   python manage.py reintentar_pendientes

Reglas:
- Una tarea que falla se registra en la bitácora y se reintenta en su
  siguiente turno; nunca tumba al programador ni a las demás tareas.
- Si el contenedor arranca después de la hora del catálogo y hoy no se ha
  sincronizado, sincroniza al arrancar: un reinicio no debe costar un día.
- Las tareas corren en serie, nunca dos a la vez.
"""
import logging
import os
import time
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

log = logging.getLogger("tae.programador")

# Cada cuánto despierta a revisar si toca algo. Más corto no aporta nada.
PULSO_SEGUNDOS = 30


def toca_catalogo(ahora, hora, ultima):
    """
    ¿Toca sincronizar el catálogo? Sí, si ya pasó la hora de hoy y la
    última sincronización fue antes de esa hora de hoy (o nunca).
    """
    objetivo = ahora.replace(hour=hora[0], minute=hora[1], second=0, microsecond=0)
    if ahora < objetivo:
        return False
    return ultima is None or ultima < objetivo


def toca_reintento(ahora, cada_minutos, ultima):
    return ultima is None or ahora - ultima >= timedelta(minutes=cada_minutos)


class Command(BaseCommand):
    help = "Corre las tareas periódicas (catálogo diario y reintentos). No termina nunca."

    def add_arguments(self, parser):
        parser.add_argument("--una-vez", action="store_true",
                            help="Corre cada tarea una vez y termina. Útil para probar.")

    def handle(self, *args, **op):
        inquilino = os.environ.get("TAE_INQUILINO", "scorpion")
        hh, mm = (int(x) for x in os.environ.get("TAE_HORA_CATALOGO", "04:00").split(":"))
        cada = int(os.environ.get("TAE_REINTENTOS_MINUTOS", "5"))

        tareas = {
            "catalogo": lambda: call_command("sincronizar_catalogo", inquilino=inquilino),
            "reintentos": lambda: call_command("reintentar_pendientes", inquilino=inquilino),
        }

        if op["una_vez"]:
            for nombre, tarea in tareas.items():
                self._correr(nombre, tarea)
            return

        log.warning("programador arrancó: catálogo diario a las %02d:%02d, reintentos cada %d min",
                    hh, mm, cada)
        ultima = {"catalogo": self._ultima_sincronizacion(), "reintentos": None}
        while True:
            ahora = timezone.localtime()
            if toca_catalogo(ahora, (hh, mm), ultima["catalogo"]):
                self._correr("catalogo", tareas["catalogo"])
                # Se marca aunque falle: el siguiente intento es mañana, y
                # mientras tanto la venta sigue con el catálogo de ayer.
                ultima["catalogo"] = ahora
            if toca_reintento(ahora, cada, ultima["reintentos"]):
                self._correr("reintentos", tareas["reintentos"])
                ultima["reintentos"] = ahora
            time.sleep(PULSO_SEGUNDOS)

    def _correr(self, nombre, tarea):
        inicio = time.monotonic()
        try:
            tarea()
            log.info("tarea %s terminó en %.1f s", nombre, time.monotonic() - inicio)
        except Exception as e:  # noqa: BLE001 — una tarea caída no tumba al resto
            log.error("tarea %s falló: %s", nombre, e)

    @staticmethod
    def _ultima_sincronizacion():
        """La hora de la última sincronización registrada, para no repetirla al reiniciar."""
        from apps.catalogo.models import ProductoDelProveedor

        visto = (
            ProductoDelProveedor.objects.order_by("-visto").values_list("visto", flat=True).first()
        )
        return timezone.localtime(visto) if visto else None
