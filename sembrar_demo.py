"""
Siembra un ambiente demostrable: un administrador, un cliente con saldo, una
caja con límite, el catálogo de tiempo aire y usuarios con contraseña conocida.

SOLO para la prueba de punta a punta (pruebas_e2e.py) y demostraciones.
Para instalar una tienda de verdad usa: python manage.py instalar

Esto NO es para producción: existe para recorrer la aplicación completa sin
depender del UAT. Úsalo junto con PROVEEDOR_MODO=simulado.

    docker compose exec api python sembrar_demo.py

Usuarios (todos con la contraseña de CLAVE, abajo):
    brenda@eps.mx       Administrador
    jorge@laermita.mx   Cliente
    luis@laermita.mx    Punto de venta

Es idempotente: si ya sembró, no vuelve a crear nada.
"""
import os
import sys
from pathlib import Path

import django

# La raíz del proyecto es la carpeta de este archivo, esté donde esté
# (tu Mac, el contenedor en /app o el servidor). Antes estaba fija a la
# ruta de la máquina donde se escribió.
RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# Dentro de Docker el .env ya trae DB_HOST=db; fuera, se asume MySQL local.
os.environ.setdefault("DB_HOST", "127.0.0.1")
django.setup()

from decimal import Decimal  # noqa: E402

from apps.api import proveedor as puente  # noqa: E402
from apps.catalogo.models import (  # noqa: E402
    Carrier, ProductoLogico, ProductoProveedor, Proveedor, TipoMonto, TipoProducto,
)
from apps.core.models import (  # noqa: E402
    Administrador, Cliente, EstadoCliente, EstadoPuntoVenta, Inquilino, PuntoVenta,
)
from apps.identidad import acciones  # noqa: E402
from apps.identidad.models import Rol, Usuario  # noqa: E402
from apps.proveedor.cliente import ClienteProveedor, Config  # noqa: E402
from apps.proveedor.emulador import Emulador  # noqa: E402
from apps.saldo import services  # noqa: E402

CLAVE = "demo-contrasena-larga"


def sembrar():
    """Crea el ambiente de demostración. Devuelve el inquilino."""
    inq, _ = Inquilino.objects.get_or_create(clave="scorpion", defaults={"nombre": "Scorpion"})
    if Usuario.objects.filter(correo="luis@laermita.mx").exists():
        return inq

    admin = Administrador.objects.create(
        inquilino=inq, nombre="Scorpion Centro", responsable="Brenda Lemus"
    )
    Usuario.objects.crear("brenda@eps.mx", Rol.ADMINISTRADOR, password=CLAVE,
                          inquilino=inq, administrador=admin, nombre="Brenda Lemus")
    cliente = Cliente.objects.create(
        inquilino=inq, administrador=admin, nombre="Abarrotes La Ermita",
        margen_pct=Decimal("4"), estado=EstadoCliente.APROBADO,
    )
    Usuario.objects.crear("jorge@laermita.mx", Rol.CLIENTE, password=CLAVE,
                          inquilino=inq, cliente=cliente, nombre="Jorge Rivas")
    pdv = PuntoVenta.objects.create(
        inquilino=inq, cliente=cliente, nombre="Caja Mostrador",
        direccion="Calle 60 #412, Centro", estado=EstadoPuntoVenta.ACTIVO,
    )
    Usuario.objects.crear("luis@laermita.mx", Rol.PUNTO_VENTA, password=CLAVE,
                          inquilino=inq, punto_venta=pdv, nombre="Luis Canul")

    prov = Proveedor.objects.create(inquilino=inq, clave="axexo", nombre="axexo")
    for clave, nombre, orden in [("telcel", "Telcel", 1), ("att", "AT&T", 2),
                                 ("movistar", "Movistar", 3)]:
        carrier = Carrier.objects.create(
            inquilino=inq, clave=clave, nombre=nombre,
            tipo=TipoProducto.TIEMPO_AIRE, orden=orden,
        )
        for monto in (50, 100, 200):
            p = ProductoLogico.objects.create(
                inquilino=inq, carrier=carrier, sku=f"{clave.upper()[:3]}-TAE-{monto}",
                nombre=f"{nombre} Tiempo Aire ${monto}", tipo=TipoProducto.TIEMPO_AIRE,
                tipo_monto=TipoMonto.FIJO, monto_fijo=Decimal(monto),
            )
            ProductoProveedor.objects.create(
                producto=p, proveedor=prov, product_id=f"{clave.upper()[:3]}{monto}"
            )

    services.fondear_administrador(admin, Decimal("500000"))
    usuario_admin = Usuario.objects.get(correo="brenda@eps.mx")
    acciones.traspasar(usuario_admin, cliente, Decimal("20000"))
    usuario_cliente = Usuario.objects.get(correo="jorge@laermita.mx")
    acciones.asignar_limite(usuario_cliente, pdv, Decimal("8000"))
    return inq


def emular(responde_en=0.2):
    """
    Pone al proveedor emulado detrás de la API, dentro de ESTE proceso.

    Lo usa pruebas_e2e.py. Para el servidor de desarrollo normal no hace
    falta: basta con PROVEEDOR_MODO=simulado en el .env.
    """
    emulador = Emulador(responde_en=responde_en)
    cfg = Config(base_url="https://emulado.local", usuario="u", contrasena="x",
                 site_id="9400822", clerk_id="CESAR45")
    puente.usar_fabrica(lambda: ClienteProveedor(cfg, sesion=emulador))
    return emulador


if __name__ == "__main__":
    sembrar()
    print("sembrado")
