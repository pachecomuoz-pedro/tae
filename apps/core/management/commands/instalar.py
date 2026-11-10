"""
Instalación de primera vez: usuarios iniciales y datos reales de Emida.

Deja una tienda lista para operar con datos reales, en un solo comando:

1. Crea el inquilino y cuatro usuarios: el Super Admin, un administrador,
   un cliente y una caja. Todos con contraseña temporal y cambio
   obligatorio al primer acceso. NO crea saldo, ni catálogo de demo, ni
   nada más.
2. Activa el segundo factor del Super Admin (pide escanear un QR).
3. Llama a Emida: descarga el catálogo real (productos y product-flow), lo
   publica en la pantalla de la caja y consulta el saldo real de EPS.

Los saldos de la red NO salen de Emida: empiezan en cero y el Super Admin
los asigna a mano desde "Usuarios de la red" (decisión 23).

En Windows (PowerShell), después de `docker compose up -d --build` y
`docker compose exec api python manage.py migrate`:

    docker compose exec api python manage.py instalar

Las contraseñas temporales se muestran UNA vez en esta ventana. Por eso la
instalación no corre sola al levantar Docker: quedarían en los logs.

Es seguro volver a correrlo: un usuario que ya existe no se toca, y la
parte de Emida vuelve a traer el catálogo y el saldo.

Contraseñas: con TAE_CLAVE_INICIAL en el .env, los cuatro usuarios nacen
con esa contraseña; sin ella, cada uno recibe una aleatoria. En ambos casos
deben cambiarla al entrar.

Los nombres y correos salen del .env (TAE_SUPERADMIN_CORREO,
TAE_ADMIN_CORREO, etc.; ver .env.example). Sin ellos se usan los de la
demostración.
"""
import os
import sys

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.api.proveedor import obtener_cliente
from apps.catalogo.sincronizacion import sincronizar, vincular_todo
from apps.core.models import Inquilino
from apps.identidad import acciones, contrasenas
from apps.identidad.management.commands.preparar_super_admin import Command as PrepararSuperAdmin
from apps.identidad.models import AccionAdministrativa, Rol, Usuario
from apps.proveedor import saldo as saldo_proveedor


def _env(nombre, omision):
    return (os.environ.get(nombre) or omision).strip()


class Command(BaseCommand):
    help = "Primera instalación: inquilino, 4 usuarios iniciales y datos reales de Emida."

    def add_arguments(self, parser):
        parser.add_argument("--sin-emida", action="store_true",
                            help="Solo crea los usuarios; no llama a Emida.")
        parser.add_argument("--sin-totp", action="store_true",
                            help="No activa ahora el segundo factor del Super Admin.")
        parser.add_argument("--proveedor", default="axexo",
                            help="Clave del proveedor en el catálogo.")

    def handle(self, *args, **op):
        clave = os.environ.get("TAE_CLAVE_INICIAL", "").strip()
        inquilino, sa, credenciales = self._usuarios(clave)

        self._mostrar(credenciales, clave)

        if not op["sin_totp"] and not sa.totp_listo:
            if sys.stdin.isatty():
                self.stdout.write(self.style.MIGRATE_HEADING("\nSegundo factor del Super Admin"))
                activador = PrepararSuperAdmin(stdout=self.stdout, stderr=self.stderr)
                try:
                    activador._activar_totp(sa)
                except Exception as e:  # noqa: BLE001 — se puede repetir después
                    self.stderr.write(f"No se activó: {e}")
        if not Usuario.objects.get(pk=sa.pk).totp_listo:
            self.stdout.write(self.style.WARNING(
                "\nEl Super Admin aún no tiene segundo factor y sin él no puede entrar. Actívalo con:\n"
                f"    docker compose exec api python manage.py preparar_super_admin --correo {sa.correo} --solo-totp"
            ))

        if op["sin_emida"]:
            self.stdout.write("\nSin llamar a Emida (--sin-emida).")
            return
        self._emida(inquilino, sa, op["proveedor"])

    # -- 1. Usuarios -------------------------------------------------------
    @transaction.atomic
    def _usuarios(self, clave):
        inquilino, _ = Inquilino.objects.get_or_create(
            clave=_env("TAE_INQUILINO", "scorpion"),
            defaults={"nombre": _env("TAE_NOMBRE_INQUILINO", "Scorpion")},
        )
        credenciales = []

        def temporal_o_clave(usuario, temporal=None):
            """
            Contraseña inicial con cambio obligatorio: TAE_CLAVE_INICIAL si
            existe; si no, la temporal que ya generó el alta (o una nueva).
            """
            if clave:
                usuario.set_password(clave)
                usuario.debe_cambiar_contrasena = True
                usuario.save(update_fields=["password", "debe_cambiar_contrasena"])
                return clave
            return temporal or contrasenas.asignar_temporal(usuario)

        # Super Admin
        correo = _env("TAE_SUPERADMIN_CORREO", "superadmin@eps.mx").lower()
        sa = Usuario.objects.filter(correo__iexact=correo).first()
        if sa is None:
            sa = Usuario.objects.create_superuser(
                correo, password=None, inquilino=inquilino,
                nombre=_env("TAE_SUPERADMIN_NOMBRE", "Super Admin"),
            )
            credenciales.append(("Super Admin", correo, temporal_o_clave(sa)))
            acciones.registrar(None, AccionAdministrativa.ALTA, sa, detalle="instalación")
        else:
            credenciales.append(("Super Admin", correo, None))

        # Administrador
        correo = _env("TAE_ADMIN_CORREO", "brenda@eps.mx").lower()
        if Usuario.objects.filter(correo__iexact=correo).exists():
            credenciales.append(("Administrador", correo, None))
            admin = Usuario.objects.get(correo__iexact=correo).administrador
        else:
            admin = acciones.crear_administrador(
                sa, nombre=_env("TAE_ADMIN_ENTIDAD", "Scorpion"),
                responsable=_env("TAE_ADMIN_NOMBRE", "Brenda Lemus"),
                correo_responsable=correo,
            )
            credenciales.append(("Administrador", correo,
                                 temporal_o_clave(admin.usuarios.get(), admin.credencial_temporal)))

        # Cliente (aprobado: lo da de alta la instalación, no hay a quién esperar)
        correo = _env("TAE_CLIENTE_CORREO", "jorge@laermita.mx").lower()
        if Usuario.objects.filter(correo__iexact=correo).exists():
            credenciales.append(("Cliente", correo, None))
            cliente = Usuario.objects.get(correo__iexact=correo).cliente
        else:
            cliente = acciones.crear_cliente(
                sa, administrador=admin, nombre=_env("TAE_CLIENTE_ENTIDAD", "Abarrotes La Ermita"),
                margen_pct=_env("TAE_CLIENTE_MARGEN", "4"), correo=correo, aprobar=True,
            )
            usuario = cliente.usuarios.get()
            usuario.nombre = _env("TAE_CLIENTE_NOMBRE", "Jorge Rivas")
            usuario.save(update_fields=["nombre"])
            credenciales.append(("Cliente", correo,
                                 temporal_o_clave(usuario, cliente.credencial_temporal)))

        # Caja
        correo = _env("TAE_CAJA_CORREO", "luis@laermita.mx").lower()
        if Usuario.objects.filter(correo__iexact=correo).exists():
            credenciales.append(("Caja", correo, None))
        else:
            caja = acciones.crear_punto_venta(
                sa, cliente=cliente, nombre=_env("TAE_CAJA_ENTIDAD", "Caja Mostrador"),
                correo=correo, aprobar=True,
            )
            usuario = caja.usuarios.get()
            usuario.nombre = _env("TAE_CAJA_NOMBRE", "Luis Canul")
            usuario.save(update_fields=["nombre"])
            credenciales.append(("Caja", correo,
                                 temporal_o_clave(usuario, caja.credencial_temporal)))

        return inquilino, sa, credenciales

    def _mostrar(self, credenciales, clave):
        self.stdout.write(self.style.MIGRATE_HEADING("Usuarios iniciales"))
        for rol, correo, contrasena in credenciales:
            if contrasena is None:
                self.stdout.write(f"  {rol:<14} {correo:<28} ya existía, no se tocó")
            else:
                self.stdout.write(f"  {rol:<14} {correo:<28} {contrasena}")
        if any(c for _, _, c in credenciales):
            origen = "TAE_CLAVE_INICIAL" if clave else "generadas al azar"
            self.stdout.write(self.style.WARNING(
                f"  Contraseñas {origen}. Se muestran solo esta vez; cada usuario "
                "debe cambiarla al entrar."
            ))

    # -- 3. Datos reales de Emida -----------------------------------------
    def _emida(self, inquilino, sa, proveedor_clave):
        self.stdout.write(self.style.MIGRATE_HEADING("\nDatos reales de Emida"))
        cliente = obtener_cliente()

        try:
            r = sincronizar(inquilino, cliente, proveedor_clave=proveedor_clave)
            self.stdout.write(self.style.SUCCESS("  OK     ") +
                              f"Catálogo: {r['productos']} productos ({r['con_flujo']} con product-flow)")
            v = vincular_todo(inquilino, proveedor_clave=proveedor_clave)
            self.stdout.write(self.style.SUCCESS("  OK     ") +
                              f"Publicados en la pantalla de la caja: {len(v['vinculados'])}")
            if v["sin_monto"]:
                self.stdout.write(
                    "         Sin monto fijo (darlos de alta a mano con vincular_producto): "
                    + ", ".join(v["sin_monto"][:20])
                )
        except Exception as e:  # noqa: BLE001 — se reporta y se sigue con el saldo
            self.stdout.write(self.style.ERROR("  FALLA  ") + f"Catálogo: {e}")
            self.stdout.write("         Revisa la conexión con: python manage.py probar_proveedor")

        registro = saldo_proveedor.consultar(sa, cliente)
        if registro.saldo is not None:
            self.stdout.write(self.style.SUCCESS("  OK     ") +
                              f"Saldo de EPS en Emida: {registro.saldo:,.2f} {registro.moneda}".rstrip())
        else:
            self.stdout.write(self.style.ERROR("  FALLA  ") + f"Saldo: {registro.error}")

        self.stdout.write(
            "\nLos saldos de la red empiezan en cero. Asígnalos como Super Admin en "
            "Usuarios de la red → Asignar saldo."
        )
