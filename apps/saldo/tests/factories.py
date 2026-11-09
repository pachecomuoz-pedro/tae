"""Armado mínimo de una jerarquía para las pruebas."""
from decimal import Decimal

from apps.core.models import (
    Administrador,
    Cliente,
    EstadoCliente,
    EstadoPuntoVenta,
    Inquilino,
    ModoBolsa,
    PuntoVenta,
)
from apps.saldo import services


def escenario(*, margen="4.00", modo=ModoBolsa.ASIGNADA, fondeo="1000000.00"):
    inquilino = Inquilino.objects.create(clave="scorpion", nombre="Scorpion")
    admin = Administrador.objects.create(
        inquilino=inquilino, nombre="Scorpion Centro", responsable="Brenda Lemus"
    )
    cliente = Cliente.objects.create(
        inquilino=inquilino,
        administrador=admin,
        nombre="Abarrotes La Ermita",
        rfc="AER920415J21",
        margen_pct=Decimal(margen),
        modo_bolsa=modo,
        estado=EstadoCliente.APROBADO,
    )
    pdv = PuntoVenta.objects.create(
        inquilino=inquilino,
        cliente=cliente,
        nombre="Caja Mostrador",
        estado=EstadoPuntoVenta.ACTIVO,
    )
    services.fondear_administrador(admin, Decimal(fondeo), autorizado_por="pruebas")
    return inquilino, admin, cliente, pdv
