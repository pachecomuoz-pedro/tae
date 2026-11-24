"""
Espera a que MySQL acepte conexiones antes de arrancar.

En la primera inicialización del volumen, MySQL levanta un servidor temporal
que responde al healthcheck pero no acepta conexiones por red. Si runserver
arranca en ese momento, su revisión de migraciones truena, el hilo del
servidor muere y el contenedor sigue "Up" respondiendo 500 a todo.

Uso: python manage.py esperar_bd [--segundos 120]
"""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Espera a que la base de datos acepte conexiones."

    def add_arguments(self, parser):
        parser.add_argument("--segundos", type=int, default=120)

    def handle(self, *args, **opciones):
        limite = time.monotonic() + opciones["segundos"]
        while True:
            try:
                connections["default"].ensure_connection()
                self.stdout.write("Base de datos lista.")
                return
            except OperationalError:
                if time.monotonic() > limite:
                    raise CommandError("La base de datos no respondió a tiempo.")
                self.stdout.write("Esperando a la base de datos...")
                time.sleep(2)
            finally:
                connections["default"].close()
