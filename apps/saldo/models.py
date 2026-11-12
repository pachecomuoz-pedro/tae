"""
Motor de saldo: bolsas, ledger de doble partida, reservas y folios.

Dos decisiones que mandan sobre todo lo demás en este archivo:

1. El ledger es de SOLO INSERCIÓN. Nada se actualiza ni se borra. Una
   corrección agrega asientos contrarios bajo un folio nuevo, y el movimiento
   equivocado permanece visible. La auditoría tiene que poder ver el error.

2. Asignar un límite a un punto de venta NO mueve dinero. El dinero se queda
   en la bolsa del cliente y el límite es un techo de gasto para esa caja.
   Por eso existe la regla de que la suma de los límites no puede exceder la
   bolsa del cliente: si el dinero se moviera, la regla sobraría.

Todos los montos son DECIMAL(18,4). Nunca float: 0.1 + 0.2 no es 0.3 y aquí
eso significa un descuadre que alguien tiene que explicar.
"""
import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.core.models import Administrador, Cliente, Inquilino, PuntoVenta

CERO = Decimal("0.0000")


class ClaveSistema(models.TextChoices):
    """
    Cuentas que no pertenecen a nadie de la jerarquía pero que hacen que la
    partida cuadre.
    """

    FONDEO = "FONDEO_EPS", "Fondeo de EPS"
    BONIFICACION_MARGEN = "BONIFICACION_MARGEN", "Bonificación de margen"
    COSTO_VENTA = "COSTO_VENTA_PROVEEDOR", "Costo de venta del proveedor"


class TipoBolsa(models.TextChoices):
    """De quién es la bolsa: un administrador, un cliente o una cuenta del sistema."""
    ADMINISTRADOR = "administrador", "Administrador"
    CLIENTE = "cliente", "Cliente"
    SISTEMA = "sistema", "Cuenta de sistema"


class Bolsa(models.Model):
    """
    Saldo materializado de un titular.

    `saldo` es la suma de todos sus asientos. Se guarda calculado porque
    sumar el ledger en cada venta no escala, pero una prueba comprueba que
    los dos números coinciden al peso.

    `reservado` es dinero comprometido en ventas en vuelo. No está gastado
    todavía, pero tampoco está disponible.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    tipo = models.CharField(max_length=14, choices=TipoBolsa.choices)

    administrador = models.OneToOneField(
        Administrador, null=True, blank=True, on_delete=models.PROTECT, related_name="bolsa"
    )
    cliente = models.OneToOneField(
        Cliente, null=True, blank=True, on_delete=models.PROTECT, related_name="bolsa"
    )
    # NULL para las bolsas de administrador y de cliente. Va nulo y no vacío
    # porque MySQL permite muchos NULL en un índice único pero solo una cadena
    # vacía: es lo que deja exigir una sola cuenta de sistema por clave sin
    # recurrir a un índice condicional, que MySQL no soporta.
    clave_sistema = models.CharField(
        max_length=32, choices=ClaveSistema.choices, null=True, blank=True, default=None
    )

    saldo = models.DecimalField(max_digits=18, decimal_places=4, default=CERO)
    reservado = models.DecimalField(max_digits=18, decimal_places=4, default=CERO)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saldo_bolsa"
        constraints = [
            models.CheckConstraint(
                name="bolsa_un_solo_titular",
                condition=(
                    Q(tipo="administrador", administrador__isnull=False,
                      cliente__isnull=True, clave_sistema__isnull=True)
                    | Q(tipo="cliente", cliente__isnull=False,
                        administrador__isnull=True, clave_sistema__isnull=True)
                    | Q(tipo="sistema", administrador__isnull=True,
                        cliente__isnull=True, clave_sistema__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["inquilino", "clave_sistema"],
                name="bolsa_sistema_unica_por_inquilino",
            ),
            # Última línea de defensa: aunque un error de código se colara, la
            # base de datos rechaza una bolsa de la red en negativo o con más
            # apartado que saldo. Las cuentas de sistema de EPS sí pueden ir
            # en negativo: son la contrapartida de lo que EPS pone en la red.
            models.CheckConstraint(
                name="bolsa_red_nunca_negativa",
                condition=(
                    Q(tipo="sistema")
                    | Q(saldo__gte=0, reservado__gte=0, reservado__lte=F("saldo"))
                ),
            ),
        ]
        indexes = [models.Index(fields=["inquilino", "tipo"])]

    def __str__(self):
        return f"Bolsa {self.etiqueta}"

    @property
    def etiqueta(self):
        if self.tipo == TipoBolsa.ADMINISTRADOR:
            return self.administrador.nombre
        if self.tipo == TipoBolsa.CLIENTE:
            return self.cliente.nombre
        return self.get_clave_sistema_display()

    @property
    def es_sistema(self):
        return self.tipo == TipoBolsa.SISTEMA

    @property
    def disponible(self):
        """
        Lo que se puede gastar ahora mismo. Las cuentas de sistema no tienen
        tope: son la contrapartida contable, no una caja de dinero.
        """
        return self.saldo - self.reservado


class BolsaPuntoVenta(models.Model):
    """
    Techo de gasto de una caja, solo cuando el cliente opera en modo asignado.

    `limite` es lo que le queda por gastar, no lo que se le asignó alguna vez:
    cada venta lo baja. Asignar de nuevo reemplaza el valor.

    En modo compartido esta fila no existe y la caja debita directo de la
    bolsa del cliente sin tope individual.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    punto_venta = models.OneToOneField(
        PuntoVenta, on_delete=models.PROTECT, related_name="limite"
    )
    limite = models.DecimalField(
        max_digits=18, decimal_places=4, default=CERO, validators=[MinValueValidator(CERO)]
    )
    reservado = models.DecimalField(max_digits=18, decimal_places=4, default=CERO)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saldo_bolsa_punto_venta"
        constraints = [
            models.CheckConstraint(
                name="limite_caja_nunca_negativo",
                condition=Q(limite__gte=0, reservado__gte=0, reservado__lte=F("limite")),
            ),
        ]

    def __str__(self):
        return f"Límite {self.punto_venta.nombre}"

    @property
    def disponible(self):
        return self.limite - self.reservado


class TipoOperacion(models.TextChoices):
    """El prefijo del folio. La serie corre por tipo y por año."""

    COMPRA = "CMP", "Compra de bolsa"
    TRASPASO = "TRS", "Traspaso"
    DEVOLUCION = "DEV", "Devolución"
    VENTA = "VTA", "Venta"
    AJUSTE = "AJU", "Ajuste"
    # El Super Admin regresa saldo de la red a la cuenta de fondeo de EPS:
    # retiros directos y liquidaciones de una retención.
    RETIRO = "RET", "Retiro a EPS"


class Motivo(models.TextChoices):
    """
    Catálogo cerrado. Es catálogo y no texto libre para que dentro de seis
    meses se pueda contar cuántos movimientos fueron error de dedo, en lugar
    de leer trescientas notas.
    """

    CAPTURA = "captura", "Error de captura en el monto"
    CLIENTE_EQUIVOCADO = "cliente_equivocado", "Traspaso a cliente equivocado"
    SOLICITADA = "solicitada", "Devolución solicitada por el cliente"
    CONCILIACION = "conciliacion", "Ajuste por conciliación con el proveedor"
    SUSPENSION = "suspension", "Recuperación de saldo tras suspensión"
    FONDEO_EXCEDENTE = "fondeo_excedente", "Fondeo de más (corrección)"
    OTRO = "otro", "Otro"


class Secuencia(models.Model):
    """
    Contador del folio por inquilino, tipo y año. Se bloquea la fila para
    tomar el siguiente número: dos operaciones simultáneas no pueden sacar
    el mismo folio.
    """

    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    tipo = models.CharField(max_length=3, choices=TipoOperacion.choices)
    anio = models.PositiveSmallIntegerField()
    ultimo = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "saldo_secuencia"
        constraints = [
            models.UniqueConstraint(
                fields=["inquilino", "tipo", "anio"], name="secuencia_unica"
            )
        ]


class SecuenciaInvoice(models.Model):
    """
    El contador de Invoice-No hacia el proveedor.

    Va de 1 a 99,999 por Site-Id y da la vuelta. Como toda la plataforma
    comparte un solo Site-Id, este contador es uno para todos: no hay uno
    por punto de venta ni por cliente.

    `ciclo` sube cada vez que da la vuelta. Es lo que vuelve único al número
    para siempre: el proveedor solo ve el Invoice-No, pero nosotros sabemos
    de qué vuelta fue, y eso permite consultar un timeout sin ambigüedad.

    A 15,000 ventas diarias el ciclo dura casi siete días, y la ventana de
    consulta son segundos. El margen es de miles a uno.
    """

    TOPE = 99999

    inquilino = models.OneToOneField(
        Inquilino, on_delete=models.PROTECT, related_name="secuencia_invoice"
    )
    ultimo = models.PositiveIntegerField(default=0)
    ciclo = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "saldo_secuencia_invoice"


class Operacion(models.Model):
    """
    Una operación de negocio. Agrupa los asientos que la componen.

    El folio se asigna al crearla, aunque después termine rechazada: es lo que
    el tendero menciona cuando llama a reportar, y tiene que existir incluso
    para lo que salió mal.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    tipo = models.CharField(max_length=3, choices=TipoOperacion.choices)
    folio = models.CharField(max_length=24)

    motivo = models.CharField(max_length=20, choices=Motivo.choices, blank=True)
    nota = models.CharField(max_length=500, blank=True)

    # Quién autorizó. Texto y no FK a usuario para que el registro sobreviva
    # al borrado de una cuenta: la bitácora no debe perder al responsable.
    autorizado_por = models.CharField(max_length=160, blank=True)

    # De dónde salió la operación, cuando aplica.
    punto_venta = models.ForeignKey(
        PuntoVenta, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Qué se vendió. Vacío en traspasos, devoluciones y ajustes, que no
    # venden nada.
    producto = models.ForeignKey(
        "catalogo.ProductoLogico", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
    )
    operacion_origen = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="correcciones"
    )
    # La retención que esta operación creó o liquidó, cuando aplica.
    retencion = models.ForeignKey(
        "Retencion", null=True, blank=True, on_delete=models.PROTECT, related_name="operaciones"
    )

    # Llave de idempotencia que manda la pantalla. Si la misma petición
    # llega dos veces (doble clic, red que reintenta), la segunda encuentra
    # esta operación y no mueve dinero otra vez.
    clave_idempotencia = models.CharField(max_length=64, null=True, blank=True)

    # Lo que viaja al proveedor. Vacío en todo lo que no sea una venta.
    invoice_no = models.PositiveIntegerField(null=True, blank=True)
    invoice_ciclo = models.PositiveIntegerField(null=True, blank=True)

    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saldo_operacion"
        constraints = [
            models.UniqueConstraint(fields=["inquilino", "folio"], name="operacion_folio_unico"),
            models.UniqueConstraint(
                fields=["inquilino", "clave_idempotencia"], name="operacion_idempotente",
            ),
        ]
        indexes = [
            models.Index(fields=["inquilino", "tipo", "creado"]),
            models.Index(fields=["punto_venta", "creado"]),
        ]

    def __str__(self):
        return self.folio

    @property
    def requiere_nota(self):
        return self.motivo == Motivo.OTRO


class Asiento(models.Model):
    """
    Un movimiento sobre una bolsa, con signo.

    Positivo entra, negativo sale. La suma de los asientos de una operación
    siempre es cero: esa es la partida doble, comprobada en cada escritura.

    No tiene campo de actualización ni de borrado a propósito.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    operacion = models.ForeignKey(Operacion, on_delete=models.PROTECT, related_name="asientos")
    bolsa = models.ForeignKey(Bolsa, on_delete=models.PROTECT, related_name="asientos")
    monto = models.DecimalField(max_digits=18, decimal_places=4)
    saldo_resultante = models.DecimalField(max_digits=18, decimal_places=4)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saldo_asiento"
        indexes = [
            models.Index(fields=["bolsa", "creado"]),
            models.Index(fields=["operacion"]),
        ]

    def __str__(self):
        return f"{self.operacion.folio} {self.bolsa.etiqueta} {self.monto}"


class EstadoReserva(models.TextChoices):
    """Activa (dinero apartado), capturada (venta cobrada) o liberada (no se cobró)."""
    ACTIVA = "activa", "Activa"
    CAPTURADA = "capturada", "Capturada"
    LIBERADA = "liberada", "Liberada"


class Reserva(models.Model):
    """
    Dinero apartado mientras una venta está en vuelo.

    Se reserva antes de mandar la venta al proveedor y se resuelve con la
    respuesta: se captura si aprobó, se libera si rechazó, y se queda ACTIVA
    si no hubo respuesta. Esa tercera es la importante: mientras no se sepa
    si el proveedor entregó el tiempo aire, ese dinero no se puede volver a
    gastar ni se puede devolver.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    bolsa = models.ForeignKey(Bolsa, on_delete=models.PROTECT, related_name="reservas")
    bolsa_punto_venta = models.ForeignKey(
        BolsaPuntoVenta, null=True, blank=True, on_delete=models.PROTECT, related_name="reservas"
    )
    punto_venta = models.ForeignKey(PuntoVenta, on_delete=models.PROTECT, related_name="+")
    operacion = models.OneToOneField(
        Operacion, on_delete=models.PROTECT, related_name="reserva"
    )
    monto = models.DecimalField(max_digits=18, decimal_places=4)
    estado = models.CharField(
        max_length=10, choices=EstadoReserva.choices, default=EstadoReserva.ACTIVA
    )
    creado = models.DateTimeField(auto_now_add=True)
    resuelto = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "saldo_reserva"
        indexes = [
            models.Index(fields=["inquilino", "estado"]),
            models.Index(fields=["bolsa", "estado"]),
        ]

    def __str__(self):
        return f"Reserva {self.operacion.folio} {self.get_estado_display()}"


class EstadoRetencion(models.TextChoices):
    """Activa (frena la red), liquidada (ya se cobró completa) o cancelada (con motivo)."""
    ACTIVA = "activa", "Activa"
    LIQUIDADA = "liquidada", "Liquidada"
    CANCELADA = "cancelada", "Cancelada"


class Retencion(models.Model):
    """
    Dinero que una rama de la red le debe a EPS por un fondeo de más que ya
    bajó por la cadena (decisión 27).

    Ejemplo: se fondearon 110 a un administrador y debían ser 106. Si el
    administrador ya repartió los 110, no hay de dónde retirar los 4 sin
    elegir arbitrariamente a qué cliente o caja quitárselos. En lugar de
    eso se crea una retención de 4 sobre el administrador:

    - Cada venta de su red pasa solo si el saldo de red, después de la
      venta, queda en `pendiente` o más. Al llegar ahí la rama deja de
      vender: la red solo puede gastar lo que realmente debió recibir.
    - El siguiente fondeo que reciba cubre primero `pendiente` (y un
      traspaso, si la retención es de un cliente). Al llegar a cero queda
      LIQUIDADA y la rama vuelve a vender normal.
    - El Super Admin puede cancelarla con motivo.

    La fila también sirve de candado: toda venta de una rama con retención
    bloquea esta fila antes de leer el saldo de red. Eso pone en fila solo
    las ventas de ramas retenidas; las demás no pagan nada.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    administrador = models.ForeignKey(
        Administrador, null=True, blank=True, on_delete=models.PROTECT, related_name="retenciones"
    )
    cliente = models.ForeignKey(
        Cliente, null=True, blank=True, on_delete=models.PROTECT, related_name="retenciones"
    )
    monto = models.DecimalField(max_digits=18, decimal_places=4)
    pendiente = models.DecimalField(max_digits=18, decimal_places=4)
    estado = models.CharField(
        max_length=10, choices=EstadoRetencion.choices, default=EstadoRetencion.ACTIVA
    )
    motivo = models.CharField(max_length=20, choices=Motivo.choices)
    nota = models.CharField(max_length=500, blank=True)
    autorizado_por = models.CharField(max_length=160, blank=True)
    operacion_origen = models.ForeignKey(
        Operacion, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    creado = models.DateTimeField(auto_now_add=True)
    resuelto = models.DateTimeField(null=True, blank=True)
    resuelto_por = models.CharField(max_length=160, blank=True)
    nota_resolucion = models.CharField(max_length=500, blank=True)
    clave_idempotencia = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        db_table = "saldo_retencion"
        constraints = [
            models.UniqueConstraint(
                fields=["inquilino", "clave_idempotencia"], name="retencion_idempotente",
            ),
            models.CheckConstraint(
                name="retencion_un_solo_titular",
                condition=(
                    Q(administrador__isnull=False, cliente__isnull=True)
                    | Q(administrador__isnull=True, cliente__isnull=False)
                ),
            ),
        ]
        indexes = [
            models.Index(fields=["administrador", "estado"]),
            models.Index(fields=["cliente", "estado"]),
        ]

    def __str__(self):
        return f"Retención {self.titular.nombre} {self.pendiente}"

    @property
    def titular(self):
        return self.administrador or self.cliente


class MovimientoLimite(models.Model):
    """
    Cada cambio al límite de una caja: asignación, retiro o recuperación.

    El límite no mueve dinero (decisión 7), así que no deja asientos. Sin
    esta tabla no habría forma de responder "¿quién le quitó saldo a mi caja
    y cuándo?", que es justo lo que el cajero y el cliente necesitan ver en
    su historial de traspasos.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    punto_venta = models.ForeignKey(
        PuntoVenta, on_delete=models.PROTECT, related_name="movimientos_limite"
    )
    anterior = models.DecimalField(max_digits=18, decimal_places=4)
    nuevo = models.DecimalField(max_digits=18, decimal_places=4)
    motivo = models.CharField(max_length=20, blank=True)
    nota = models.CharField(max_length=500, blank=True)
    autorizado_por = models.CharField(max_length=160, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saldo_movimiento_limite"
        indexes = [models.Index(fields=["punto_venta", "creado"])]

    @property
    def diferencia(self):
        return self.nuevo - self.anterior
