from django.apps import AppConfig


class SaldoConfig(AppConfig):
    name = "apps.saldo"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import senales  # noqa: F401
