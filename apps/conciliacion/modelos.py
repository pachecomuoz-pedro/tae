"""
Conciliación contra el archivo del proveedor.

El archivo es la autoridad. Nuestro ledger dice lo que creemos que pasó; el
archivo dice lo que el proveedor cobró. Donde difieren, alguien perdió
dinero y hay que saber quién.

Una diferencia no se borra al resolverse: se marca resuelta y se guarda con
qué operación se corrigió. El historial de descuadres es lo que después
permite contestar si el problema fue un día malo o una falla sistemática.
"""
import uuid

from django.db import models

from apps.core.models import Inquilino
from apps.saldo.models import Operacion


class TipoDiferencia(models.TextChoices):
    """
    Las seis formas en que el archivo y el ledger pueden no coincidir.

    Las dos primeras son las que cuestan dinero y van en direcciones
    opuestas: en una pagamos nosotros, en la otra paga el tendero.
    """

    COBRO_NO_REGISTRADO = "cobro_no_registrado", "El proveedor cobró y nosotros no"
    REGISTRO_SIN_COBRO = "registro_sin_cobro", "Nosotros cobramos y el proveedor no"
    MONTO_DISTINTO = "monto_distinto", "El monto no coincide"
    FALTA_EN_LEDGER = "falta_en_ledger", "Está en el archivo y no en el ledger"
    FALTA_EN_ARCHIVO = "falta_en_archivo", "Está en el ledger y no en el archivo"
    CUENTA_DISTINTA = "cuenta_distinta", "La cuenta destino no coincide"


class EstadoDiferencia(models.TextChoices):
    """Abierta mientras nadie la revise; resuelta con autor y nota."""
    ABIERTA = "abierta", "Abierta"
    RESUELTA = "resuelta", "Resuelta"
    ACEPTADA = "aceptada", "Aceptada sin acción"


class ArchivoConciliacion(models.Model):
    """
    Un archivo del proveedor, con la ventana de fechas que cubre.

    La ventana importa más de lo que parece. El Invoice-No da la vuelta en
    99,999, así que el mismo número se repite cada pocos días. Cruzar por
    número a secas emparejaría una venta de hoy con una de la vuelta
    anterior. Por eso el cruce siempre va acotado a las fechas del archivo.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    nombre = models.CharField(max_length=200)
    huella = models.CharField(max_length=64, unique=True)

    desde = models.DateTimeField(null=True, blank=True)
    hasta = models.DateTimeField(null=True, blank=True)

    renglones = models.PositiveIntegerField(default=0)
    importe_proveedor = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    conciliados = models.PositiveIntegerField(default=0)
    con_diferencia = models.PositiveIntegerField(default=0)

    importado = models.DateTimeField(auto_now_add=True)
    conciliado = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "conciliacion_archivo"
        ordering = ["-importado"]

    def __str__(self):
        return self.nombre

    @property
    def cuadra(self):
        return self.con_diferencia == 0 and self.conciliado is not None


class RenglonArchivo(models.Model):
    """
    Un renglón tal como venía, sin interpretar.

    Se guarda crudo además de parseado para que una aclaración se pueda
    contestar con lo que el proveedor mandó, no con nuestra lectura de lo
    que mandó.
    """

    archivo = models.ForeignKey(
        ArchivoConciliacion, on_delete=models.CASCADE, related_name="renglones_detalle"
    )
    fecha = models.DateTimeField(null=True, blank=True)
    terminal_id = models.CharField(max_length=40, blank=True)
    transaction_id = models.CharField(max_length=60, blank=True)
    invoice_no = models.PositiveIntegerField(null=True, blank=True)
    product_id = models.CharField(max_length=60, blank=True)
    monto = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    cuenta = models.CharField(max_length=60, blank=True)
    response_code = models.CharField(max_length=10, blank=True)
    crudo = models.CharField(max_length=500, blank=True)

    operacion = models.ForeignKey(
        Operacion, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "conciliacion_renglon"
        indexes = [
            models.Index(fields=["archivo", "invoice_no"]),
            models.Index(fields=["transaction_id"]),
        ]

    def __str__(self):
        return f"{self.invoice_no} {self.monto} rc={self.response_code}"

    @property
    def aprobada_por_proveedor(self):
        """
        En el archivo, 0 es éxito. Cualquier otro código es un rechazo que
        el proveedor no cobró.
        """
        return str(self.response_code).strip() in ("0", "00")


class Diferencia(models.Model):
    """Una discrepancia entre el archivo del proveedor y nuestro registro. Ver TipoDiferencia."""
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    archivo = models.ForeignKey(
        ArchivoConciliacion, on_delete=models.CASCADE, related_name="diferencias"
    )
    tipo = models.CharField(max_length=24, choices=TipoDiferencia.choices)
    estado = models.CharField(
        max_length=10, choices=EstadoDiferencia.choices, default=EstadoDiferencia.ABIERTA
    )

    renglon = models.ForeignKey(
        RenglonArchivo, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    operacion = models.ForeignKey(
        Operacion, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    invoice_no = models.PositiveIntegerField(null=True, blank=True)
    monto_proveedor = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    monto_nuestro = models.DecimalField(max_digits=18, decimal_places=4, default=0)

    detalle = models.CharField(max_length=500, blank=True)
    # Con qué se corrigió, cuando se corrigió.
    operacion_correccion = models.ForeignKey(
        Operacion, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resuelto_por = models.CharField(max_length=160, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    resuelto = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "conciliacion_diferencia"
        indexes = [
            models.Index(fields=["archivo", "estado"]),
            models.Index(fields=["tipo", "estado"]),
        ]
        ordering = ["tipo", "invoice_no"]

    def __str__(self):
        return f"{self.get_tipo_display()} invoice {self.invoice_no}"

    @property
    def impacto(self):
        """
        Cuánto y para quién. Positivo nos deben, negativo debemos.
        """
        return self.monto_proveedor - self.monto_nuestro
