"""
Evidencia de transacciones On-Ramp (pesos → cripto).

Dos modelos por las mismas razones que RegistroEnvio/IntentoProveedor:
certificación y aclaración. La diferencia operativa es que no hay endpoint
de lookup por invoice_no; la conciliación nocturna usa el transactionId de
la respuesta.

phone_number se guarda solo para auditoría. Nunca aparece en logs ni en
respuestas de la API.
"""
import uuid as _uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.saldo.models import Operacion


class DesenlaceOnRamp(models.TextChoices):
    PENDIENTE                = "pendiente",                "Pendiente"
    APROBADA                 = "aprobada",                 "Aprobada"
    RECHAZADA                = "rechazada",                "Rechazada"
    SIN_RESOLVER             = "sin_resolver",             "Sin resolver"
    REQUIERE_REVISION_MANUAL = "requiere_revision_manual", "Requiere revisión manual"


class RegistroOnRamp(models.Model):
    """Una transacción on-ramp con su resultado final."""

    uuid         = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False)
    operacion    = models.OneToOneField(
        Operacion, on_delete=models.PROTECT, related_name="envio_on_ramp"
    )
    invoice_no   = models.CharField(max_length=100)
    product_id   = models.CharField(max_length=100)
    # Solo auditoría; nunca en logs ni respuestas de API (Requirement 9.4).
    phone_number = models.CharField(max_length=20)
    monto        = models.DecimalField(max_digits=18, decimal_places=4)

    desenlace = models.CharField(
        max_length=25,
        choices=DesenlaceOnRamp.choices,
        default=DesenlaceOnRamp.PENDIENTE,
    )
    intentos = models.PositiveSmallIntegerField(default=0)
    detalle  = models.CharField(max_length=1000, blank=True)

    transaction_id   = models.CharField(max_length=100, blank=True)
    target_amount    = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    target_asset     = models.CharField(max_length=20, blank=True)
    fee              = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    # Texto crudo del proveedor; truncado a 4 000 chars en la capa de app.
    response_message = models.TextField(blank=True)

    creado  = models.DateTimeField(auto_now_add=True)
    cerrado = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "proveedor_registro_on_ramp"

    def clean(self):
        """
        Garantiza que invoice_no coincida con operacion.invoice_no
        (Requirement 9.5). Se llama desde save() para que la restricción
        se aplique en toda inserción o actualización, no solo desde formularios.
        """
        try:
            op_invoice = str(self.operacion.invoice_no)
        except Operacion.DoesNotExist:
            # La FK no está resuelta aún; Django validará la FK por separado.
            return
        if str(self.invoice_no) != op_invoice:
            raise ValidationError(
                {
                    "invoice_no": (
                        f"invoice_no '{self.invoice_no}' no coincide con "
                        f"operacion.invoice_no '{op_invoice}'."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_no} {self.get_desenlace_display()}"


class IntentoOnRamp(models.Model):
    """
    Cada llamada al proveedor: el envío inicial y cada intento de
    conciliación posterior.

    Una fila por intento; nada se sobreescribe. La secuencia completa es
    lo que permite demostrar qué pasó y cuándo.
    """

    registro = models.ForeignKey(
        RegistroOnRamp, on_delete=models.CASCADE, related_name="intentos_detalle"
    )
    # "envio" | "conciliacion"
    tipo     = models.CharField(max_length=20)
    # Nulo cuando no hubo respuesta HTTP (timeout de red).
    http     = models.PositiveSmallIntegerField(null=True, blank=True)
    aprobada = models.BooleanField(default=False)
    # Cuerpo crudo de la respuesta; truncado a 4 000 chars en la capa de app.
    cuerpo   = models.TextField(blank=True)
    error    = models.CharField(max_length=500, blank=True)
    # Para conciliación: "sin_identificador" | "error_proveedor" |
    # "error_captura" | "error_liberacion"
    resultado = models.CharField(max_length=40, blank=True)
    cuando    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "proveedor_intento_on_ramp"
        ordering = ["cuando", "id"]

    def __str__(self):
        return f"{self.tipo} HTTP {self.http} {'ok' if self.aprobada else ''}"
