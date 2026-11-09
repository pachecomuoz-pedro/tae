"""
Evidencia de lo que se mandó al proveedor y de lo que contestó.

Existe por dos razones. La primera es la certificación: cada caso de la
matriz se respalda con request, response, código HTTP y timestamp, y eso no
se reconstruye de memoria. La segunda es la aclaración de tres meses
después, cuando alguien pregunte por qué una venta quedó como quedó.

Se guarda el cuerpo crudo recortado. Entero es demasiado para la base y
vacío no sirve para nada.
"""
import uuid

from django.db import models

from apps.saldo.models import Operacion


class Desenlace(models.TextChoices):
    """
    Los cinco cierres posibles. No hay un sexto.

    PENDIENTE es el estado mientras se está hablando con el proveedor.
    SIN_RESOLVER es el que importa: salió, no sabemos qué pasó, y la reserva
    sigue viva.
    """

    PENDIENTE = "pendiente", "Pendiente"
    APROBADA = "aprobada", "Aprobada"
    RECHAZADA = "rechazada", "Rechazada"
    SIN_RESOLVER = "sin_resolver", "Sin resolver"
    DUPLICADA = "duplicada", "Duplicada"


class RegistroEnvio(models.Model):
    """Una venta hacia el proveedor, con su resultado final."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    operacion = models.OneToOneField(
        Operacion, on_delete=models.PROTECT, related_name="envio"
    )

    invoice_no = models.CharField(max_length=24)
    product_id = models.CharField(max_length=60)
    account_id = models.CharField(max_length=60)
    monto = models.DecimalField(max_digits=18, decimal_places=4)

    desenlace = models.CharField(
        max_length=14, choices=Desenlace.choices, default=Desenlace.PENDIENTE
    )
    intentos = models.PositiveSmallIntegerField(default=0)
    detalle = models.CharField(max_length=500, blank=True)

    transaction_id = models.CharField(max_length=60, blank=True)
    control_no = models.CharField(max_length=60, blank=True)
    response_code = models.CharField(max_length=10, blank=True)
    h2h_result_code = models.CharField(max_length=10, blank=True)

    creado = models.DateTimeField(auto_now_add=True)
    cerrado = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "proveedor_registro_envio"
        indexes = [
            models.Index(fields=["desenlace", "creado"]),
            models.Index(fields=["invoice_no"]),
        ]

    def __str__(self):
        return f"{self.invoice_no} {self.get_desenlace_display()}"


class IntentoProveedor(models.Model):
    """
    Cada llamada: el envío y cada consulta posterior.

    Una fila por intento y no un campo que se sobrescribe, porque lo que se
    necesita demostrar en una aclaración es la secuencia completa, no el
    último estado.
    """

    envio = models.ForeignKey(
        RegistroEnvio, on_delete=models.CASCADE, related_name="intentos_detalle"
    )
    tipo = models.CharField(max_length=10)  # envio | lookup
    http = models.PositiveSmallIntegerField(default=0)
    response_code = models.CharField(max_length=10, blank=True)
    h2h_result_code = models.CharField(max_length=10, blank=True)
    aprobada = models.BooleanField(default=False)
    cuerpo = models.TextField(blank=True)
    error = models.CharField(max_length=500, blank=True)
    cuando = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "proveedor_intento"
        ordering = ["cuando", "id"]
        indexes = [models.Index(fields=["envio", "cuando"])]

    def __str__(self):
        return f"{self.tipo} HTTP {self.http} {'ok' if self.aprobada else ''}"


class ConsultaSaldoProveedor(models.Model):
    """
    Cada vez que el Super Admin consulta el saldo de EPS en Emida.

    Se guarda la consulta completa, no solo el número, por tres razones:
    el tablero muestra la última sin volver a llamar al proveedor; queda la
    historia de cómo bajó el float; y la respuesta cruda permite revisar
    qué contestó Emida si el número no se pudo leer.

    Este saldo es el dinero de EPS en el proveedor. NO es la suma de la red:
    los saldos de administradores y clientes los asigna el Super Admin a
    mano (decisión 23). Solo inserción.
    """

    inquilino = models.ForeignKey(
        "core.Inquilino", on_delete=models.PROTECT, related_name="+"
    )
    # Nulo si la consulta falló o si la respuesta no traía un número legible.
    saldo = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    moneda = models.CharField(max_length=8, blank=True)
    http = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
    respuesta = models.JSONField(null=True, blank=True)
    consultado_por = models.ForeignKey(
        "identidad.Usuario", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cuando = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "proveedor_consulta_saldo"
        indexes = [models.Index(fields=["inquilino", "cuando"])]
        ordering = ["-cuando"]

    def __str__(self):
        return f"{self.cuando:%Y-%m-%d %H:%M} {self.saldo if self.saldo is not None else self.error}"
