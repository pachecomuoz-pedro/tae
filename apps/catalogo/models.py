"""
Catálogo de productos, en dos capas.

La capa de arriba es lo que ve la caja: "Telcel Tiempo Aire $100". La de
abajo es a qué producto de qué proveedor se manda eso. Están separadas por
una razón concreta: el día que entre un segundo proveedor, o que Emida
cambie un identificador, se toca la capa de abajo y la pantalla de venta no
se entera. Si el `Product-Id` del proveedor viviera en el botón que aprieta
el cajero, cada cambio de proveedor sería un cambio de interfaz.

Sobre el código de barras: identifica la fila del catálogo, nunca el precio.
Si el precio viviera en el código, subir un producto obligaría a reimprimir
cada etiqueta, y una etiqueta vieja olvidada en un estante vendería al precio
anterior para siempre.
"""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import Inquilino

CERO = Decimal("0.0000")


class TipoProducto(models.TextChoices):
    """Lo que se vende: tiempo aire, pago de servicios o pines."""
    TIEMPO_AIRE = "tae", "Tiempo aire"
    SERVICIO = "servicio", "Pago de servicio"
    PIN = "pin", "Pin electrónico"


class TipoMonto(models.TextChoices):
    """
    De dónde sale el monto de la venta.

    FIJO      la denominación es el producto: Telcel $100 es un producto y
              Telcel $200 es otro, igual que en el catálogo del proveedor.
    RANGO     el cajero teclea cualquier monto dentro de un rango, a veces
              en múltiplos: recarga abierta de 10 a 500 de diez en diez.
    RECIBO    lo dice el recibo, no el cajero: la consulta del servicio
              devuelve el adeudo y ese es el monto.
    """

    FIJO = "fijo", "Denominación fija"
    RANGO = "rango", "Monto abierto en un rango"
    RECIBO = "recibo", "El del recibo"


class Carrier(models.Model):
    """Telcel, AT&T, CFE, Netflix. La marca que el cliente reconoce."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    clave = models.SlugField(max_length=40)
    nombre = models.CharField(max_length=80)
    tipo = models.CharField(max_length=10, choices=TipoProducto.choices)
    orden = models.PositiveSmallIntegerField(default=100)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "catalogo_carrier"
        constraints = [
            models.UniqueConstraint(fields=["inquilino", "clave"], name="carrier_clave_unica")
        ]
        ordering = ["orden", "nombre"]

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    """
    Emida hoy, y el lugar para el siguiente.

    La fila del segundo proveedor existe desde el principio aunque esté
    inactiva: deja probado el ruteo antes de que haya con quién probarlo.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    clave = models.SlugField(max_length=40)
    nombre = models.CharField(max_length=80)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "catalogo_proveedor"
        constraints = [
            models.UniqueConstraint(fields=["inquilino", "clave"], name="proveedor_clave_unica")
        ]

    def __str__(self):
        return self.nombre


class ProductoLogico(models.Model):
    """
    Lo que la caja ofrece. No sabe de proveedores.

    El `sku` es su identidad estable y es lo que carga el código de barras.
    No lleva el precio adentro a propósito.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inquilino = models.ForeignKey(Inquilino, on_delete=models.PROTECT, related_name="+")
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name="productos")
    sku = models.CharField(max_length=40)
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=10, choices=TipoProducto.choices)

    tipo_monto = models.CharField(max_length=8, choices=TipoMonto.choices)
    monto_fijo = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    monto_min = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    monto_max = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    multiplo = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    orden = models.PositiveSmallIntegerField(default=100)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "catalogo_producto_logico"
        constraints = [
            models.UniqueConstraint(fields=["inquilino", "sku"], name="producto_sku_unico")
        ]
        indexes = [models.Index(fields=["inquilino", "tipo", "activo"])]
        ordering = ["carrier__orden", "orden", "nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        """
        Cada tipo de monto exige sus campos y prohíbe los otros. Un producto
        fijo con rango, o un producto de rango sin mínimo, es una fila que
        rompe la validación de la venta mucho después de darse de alta.
        """
        super().clean()
        if self.tipo_monto == TipoMonto.FIJO:
            if self.monto_fijo is None or self.monto_fijo <= CERO:
                raise ValidationError({"monto_fijo": "Una denominación fija necesita su monto"})
            if self.monto_min or self.monto_max:
                raise ValidationError({"monto_min": "Una denominación fija no lleva rango"})
        elif self.tipo_monto == TipoMonto.RANGO:
            if self.monto_min is None or self.monto_max is None:
                raise ValidationError({"monto_min": "Un monto abierto necesita mínimo y máximo"})
            if self.monto_min <= CERO or self.monto_max < self.monto_min:
                raise ValidationError({"monto_max": "El rango está al revés o en cero"})
            if self.monto_fijo:
                raise ValidationError({"monto_fijo": "Un monto abierto no lleva denominación"})
        else:  # RECIBO
            if self.monto_fijo or self.monto_min or self.monto_max:
                raise ValidationError(
                    {"tipo_monto": "El monto del recibo lo fija la consulta, no el catálogo"}
                )

    @property
    def codigo_barras(self):
        """
        Lo que lleva impreso la etiqueta. Es el sku, sin el precio.

        Code 128 acepta letras y números, y los lectores USB lo entregan como
        si alguien lo hubiera tecleado, así que no hace falta nada numérico.
        """
        return self.sku

    @property
    def etiqueta_monto(self):
        if self.tipo_monto == TipoMonto.FIJO:
            return f"${self.monto_fijo:,.2f}"
        if self.tipo_monto == TipoMonto.RANGO:
            return f"${self.monto_min:,.2f} a ${self.monto_max:,.2f}"
        return "El del recibo"


class ProductoProveedor(models.Model):
    """
    La ruta: este producto lógico se surte con este identificador de este
    proveedor.

    `prioridad` ordena cuando hay más de una ruta viva. La más baja gana; el
    resto queda como respaldo para el día que el primero no conteste.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    producto = models.ForeignKey(
        ProductoLogico, on_delete=models.PROTECT, related_name="rutas"
    )
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, related_name="rutas")

    # El identificador tal como lo espera el proveedor. Lo único de este
    # archivo que cambia cuando cambia el proveedor.
    product_id = models.CharField(max_length=60)

    prioridad = models.PositiveSmallIntegerField(default=10)
    # `activo` es la decisión de una persona: "esta ruta se usa". La pone o
    # la quita alguien, nunca la sincronización.
    activo = models.BooleanField(default=True)
    # `pausada_por_proveedor` es lo que dice el proveedor: la última
    # sincronización no listó este product_id. Va separado de `activo` para
    # que la sincronización nunca reviva una ruta que alguien apagó a mano.
    # Una ruta se usa solo si activo=True Y pausada_por_proveedor=False.
    pausada_por_proveedor = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "catalogo_producto_proveedor"
        constraints = [
            models.UniqueConstraint(
                fields=["proveedor", "producto"], name="ruta_unica_por_proveedor"
            )
        ]
        indexes = [models.Index(fields=["producto", "activo", "prioridad"])]
        ordering = ["prioridad"]

    def __str__(self):
        return f"{self.producto.sku} → {self.proveedor.clave}:{self.product_id}"


class ProductoDelProveedor(models.Model):
    """
    Lo que el proveedor dice que este terminal puede vender, tal como lo dice.

    Es una copia de `GET /api/v1/terminal/products` (y de product-flow) que
    `sincronizar_catalogo` actualiza a diario, como piden las Reglas
    Generales de Emida. Vive aparte del catálogo (ProductoLogico y sus
    rutas) a propósito: lo que Emida ofrece no entra solo a la pantalla del
    cajero. Alguien decide qué se vende, con qué nombre y en qué orden
    (`vincular_producto`). La sincronización solo apaga o prende las rutas
    según lo que Emida tenga disponible.

    Los campos legibles (descripción, carrier, monto) se extraen "lo mejor
    posible" porque no tenemos el contrato exacto de la respuesta; la
    respuesta completa queda en `datos` y `flujo`.
    """

    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, related_name="ofertas")
    product_id = models.CharField(max_length=60)
    descripcion = models.CharField(max_length=200, blank=True)
    carrier = models.CharField(max_length=80, blank=True)
    monto = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    datos = models.JSONField(default=dict)
    # Lo de product-flow: textos y referencias por idioma para la pantalla.
    flujo = models.JSONField(null=True, blank=True)
    disponible = models.BooleanField(default=True)
    visto = models.DateTimeField(help_text="Última sincronización en que el proveedor lo listó")
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalogo_producto_del_proveedor"
        constraints = [
            models.UniqueConstraint(
                fields=["proveedor", "product_id"], name="oferta_unica_por_proveedor"
            )
        ]
        ordering = ["carrier", "monto", "descripcion"]

    def __str__(self):
        return f"{self.proveedor.clave}:{self.product_id} {self.descripcion}"
