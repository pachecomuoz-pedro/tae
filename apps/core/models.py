"""
Entidades de la jerarquía: inquilino, administrador, cliente, punto de venta.

Este archivo solo define quién es quién. El dinero vive en apps.saldo.
"""
import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Inquilino(models.Model):
    """
    Scorpion es el primero y hoy el único. La columna existe desde la primera
    migración porque agregarla después obliga a reescribir cada consulta de
    saldo, que es justo donde no se quiere tocar nada.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    clave = models.SlugField(max_length=40, unique=True)
    nombre = models.CharField(max_length=120)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_inquilino"

    def __str__(self):
        return self.nombre


class EntidadBase(models.Model):
    """
    Lo que comparten administrador, cliente y punto de venta.

    `uuid` es el identificador que sale a la API; la llave primaria numérica
    nunca se expone, para que no se puedan adivinar ni contar registros.
    `activo` es el apagado técnico; el estado de negocio (pendiente,
    aprobado, suspendido) vive en cada modelo porque cada uno tiene el suyo.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    nombre = models.CharField(max_length=160)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class Administrador(EntidadBase):
    """Recibe saldo del Super Admin y lo reparte entre sus clientes."""

    responsable = models.CharField(max_length=160, blank=True)

    class Meta:
        db_table = "core_administrador"
        indexes = [models.Index(fields=["inquilino", "activo"])]

    def __str__(self):
        return self.nombre


class EstadoCliente(models.TextChoices):
    """
    Pendiente: lo dio de alta alguien que no es quien lo aprueba (el Super
    Admin) y espera a su administrador. Rechazado: el administrador dijo que
    no, con motivo; es reversible, porque aprobarlo después es válido.
    Ver docs/DECISIONES.md, decisión 16.
    """

    PENDIENTE = "pendiente", "Pendiente"
    APROBADO = "aprobado", "Aprobado"
    SUSPENDIDO = "suspendido", "Suspendido"
    RECHAZADO = "rechazado", "Rechazado"


class ModoBolsa(models.TextChoices):
    """Compartida: todas las cajas venden de la bolsa del cliente. Asignada: cada caja con su límite (decisión 7)."""
    COMPARTIDA = "compartida", "Bolsa compartida"
    ASIGNADA = "asignada", "Bolsa por punto de venta"


class Cliente(EntidadBase):
    """
    Tendero afiliado o sucursal propia. No vende: vende su punto de venta.

    El margen se aplica desde la compra. Si el margen es 4% y el cliente paga
    1,000, su bolsa se acredita con 1,040. La diferencia sale de la cuenta de
    bonificación de margen, no del aire.
    """

    administrador = models.ForeignKey(
        Administrador, on_delete=models.PROTECT, related_name="clientes"
    )
    rfc = models.CharField(max_length=13, blank=True)
    margen_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    modo_bolsa = models.CharField(
        max_length=12, choices=ModoBolsa.choices, default=ModoBolsa.ASIGNADA
    )
    estado = models.CharField(
        max_length=12, choices=EstadoCliente.choices, default=EstadoCliente.PENDIENTE
    )
    # Por qué se rechazó. Solo tiene valor en estado RECHAZADO; al aprobar se
    # limpia. La historia completa queda en la bitácora de administración.
    motivo_rechazo = models.CharField(max_length=240, blank=True)

    class Meta:
        db_table = "core_cliente"
        indexes = [
            models.Index(fields=["inquilino", "estado"]),
            models.Index(fields=["administrador", "estado"]),
        ]

    def __str__(self):
        return self.nombre

    @property
    def puede_operar(self):
        """Puede recibir saldo y sus cajas pueden vender."""
        return self.activo and self.estado == EstadoCliente.APROBADO


class EstadoPuntoVenta(models.TextChoices):
    """Mismo ciclo que el cliente. Ver EstadoCliente."""

    PENDIENTE = "pendiente", "Pendiente"
    ACTIVO = "activo", "Activo"
    SUSPENDIDO = "suspendido", "Suspendido"
    RECHAZADO = "rechazado", "Rechazado"


class PuntoVenta(EntidadBase):
    """
    La caja que efectivamente vende.

    `estado_previo` guarda dónde estaba antes de una suspensión en cascada,
    para que al reactivar al cliente cada caja vuelva a donde estaba y no
    todas a activo. Una caja suspendida a mano no debe revivir por accidente.
    """

    cliente = models.ForeignKey(
        Cliente, on_delete=models.PROTECT, related_name="puntos_venta"
    )
    direccion = models.CharField(max_length=240, blank=True)
    estado = models.CharField(
        max_length=12, choices=EstadoPuntoVenta.choices, default=EstadoPuntoVenta.PENDIENTE
    )
    estado_previo = models.CharField(
        max_length=12, choices=EstadoPuntoVenta.choices, blank=True
    )
    motivo_rechazo = models.CharField(max_length=240, blank=True)

    class Meta:
        db_table = "core_punto_venta"
        indexes = [
            models.Index(fields=["inquilino", "estado"]),
            models.Index(fields=["cliente", "estado"]),
        ]

    def __str__(self):
        return self.nombre

    @property
    def puede_vender(self):
        """La caja, su cliente y ambos apagados técnicos tienen que estar bien."""
        return (
            self.activo
            and self.estado == EstadoPuntoVenta.ACTIVO
            and self.cliente.puede_operar
        )
