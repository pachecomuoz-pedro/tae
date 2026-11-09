"""Una jerarquía completa con un usuario en cada nivel."""
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
from apps.catalogo.models import (
    Carrier,
    ProductoLogico,
    ProductoProveedor,
    Proveedor,
    TipoMonto,
    TipoProducto,
)
from apps.identidad.models import Rol, Usuario
from apps.saldo import services

CLAVE = "contrasena-larga-de-prueba"


class Mundo:
    """
    Dos administradores con un cliente cada uno. El segundo existe para poder
    probar lo que de verdad importa: que nadie alcance lo que no es suyo.
    """

    def __init__(self, *, margen="4.00", modo=ModoBolsa.ASIGNADA, fondeo="1000000.00"):
        self.inquilino = Inquilino.objects.create(clave="scorpion", nombre="Scorpion")

        self.super_admin = Usuario.objects.crear(
            "cesar@eps.mx", Rol.SUPER_ADMIN, password=CLAVE,
            inquilino=self.inquilino, nombre="César González",
        )

        self.admin = Administrador.objects.create(
            inquilino=self.inquilino, nombre="Scorpion Centro", responsable="Brenda Lemus"
        )
        self.usuario_admin = Usuario.objects.crear(
            "brenda@eps.mx", Rol.ADMINISTRADOR, password=CLAVE,
            inquilino=self.inquilino, administrador=self.admin, nombre="Brenda Lemus",
        )

        self.otro_admin = Administrador.objects.create(
            inquilino=self.inquilino, nombre="Scorpion Sureste", responsable="Raúl Pech"
        )
        self.usuario_otro_admin = Usuario.objects.crear(
            "raul@eps.mx", Rol.ADMINISTRADOR, password=CLAVE,
            inquilino=self.inquilino, administrador=self.otro_admin, nombre="Raúl Pech",
        )

        self.cliente = Cliente.objects.create(
            inquilino=self.inquilino, administrador=self.admin,
            nombre="Abarrotes La Ermita", rfc="AER920415J21",
            margen_pct=Decimal(margen), modo_bolsa=modo,
            estado=EstadoCliente.APROBADO,
        )
        self.usuario_cliente = Usuario.objects.crear(
            "jorge@laermita.mx", Rol.CLIENTE, password=CLAVE,
            inquilino=self.inquilino, cliente=self.cliente, nombre="Jorge Rivas",
        )

        self.ajeno = Cliente.objects.create(
            inquilino=self.inquilino, administrador=self.otro_admin,
            nombre="Mini Super El Roble", margen_pct=Decimal(margen),
            modo_bolsa=modo, estado=EstadoCliente.APROBADO,
        )

        self.pdv = PuntoVenta.objects.create(
            inquilino=self.inquilino, cliente=self.cliente,
            nombre="Caja Mostrador", estado=EstadoPuntoVenta.ACTIVO,
        )
        self.usuario_pdv = Usuario.objects.crear(
            "luis@laermita.mx", Rol.PUNTO_VENTA, password=CLAVE,
            inquilino=self.inquilino, punto_venta=self.pdv, nombre="Luis Canul",
        )

        self.otra_caja = PuntoVenta.objects.create(
            inquilino=self.inquilino, cliente=self.cliente,
            nombre="Caja Fondo", estado=EstadoPuntoVenta.ACTIVO,
        )

        self.proveedor = Proveedor.objects.create(
            inquilino=self.inquilino, clave="emida", nombre="Emida"
        )
        self.carrier = Carrier.objects.create(
            inquilino=self.inquilino, clave="telcel", nombre="Telcel",
            tipo=TipoProducto.TIEMPO_AIRE,
        )
        self.producto = ProductoLogico.objects.create(
            inquilino=self.inquilino, carrier=self.carrier, sku="TEL-TAE-100",
            nombre="Telcel Tiempo Aire $100", tipo=TipoProducto.TIEMPO_AIRE,
            tipo_monto=TipoMonto.FIJO, monto_fijo=Decimal("100"),
        )
        ProductoProveedor.objects.create(
            producto=self.producto, proveedor=self.proveedor, product_id="TEL100"
        )

        services.fondear_administrador(self.admin, Decimal(fondeo))
        services.fondear_administrador(self.otro_admin, Decimal(fondeo))
