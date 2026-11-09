"""
Altas por nivel, aprobación con rechazo, contraseña temporal y bitácora.

Cada prueba corresponde a una regla de docs/DECISIONES.md (15, 16 y 17). Si
alguna falla, el documento y el código dejaron de decir lo mismo.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import EstadoCliente, EstadoPuntoVenta
from apps.identidad import acciones, contrasenas, permisos
from apps.identidad.models import AccionAdministrativa, BitacoraAdministracion
from apps.saldo import services

from .factories import CLAVE, Mundo

D = Decimal
NUEVA = "otra-contrasena-larga-2026"


class EstadoInicialSegunQuienCrea(TestCase):
    """Decisión 16: lo que crea quien aprueba nace aprobado; lo del Super Admin espera."""

    def setUp(self):
        self.m = Mundo()

    def test_el_cliente_que_crea_su_administrador_nace_aprobado(self):
        cliente = acciones.crear_cliente(
            self.m.usuario_admin, administrador=self.m.admin, nombre="Abarrotes El Sol"
        )
        self.assertEqual(cliente.estado, EstadoCliente.APROBADO)

    def test_el_cliente_que_crea_el_super_admin_queda_pendiente(self):
        cliente = acciones.crear_cliente(
            self.m.super_admin, administrador=self.m.admin, nombre="Tienda Doña Lupe"
        )
        self.assertEqual(cliente.estado, EstadoCliente.PENDIENTE)

    def test_la_caja_que_crea_su_cliente_nace_activa(self):
        pdv = acciones.crear_punto_venta(
            self.m.usuario_cliente, cliente=self.m.cliente, nombre="Caja Terraza"
        )
        self.assertEqual(pdv.estado, EstadoPuntoVenta.ACTIVO)

    def test_la_caja_que_crea_el_super_admin_queda_pendiente(self):
        pdv = acciones.crear_punto_venta(
            self.m.super_admin, cliente=self.m.cliente, nombre="Caja Móvil Feria"
        )
        self.assertEqual(pdv.estado, EstadoPuntoVenta.PENDIENTE)

    def test_una_cuenta_pendiente_no_entra_hasta_que_la_aprueban(self):
        cliente = acciones.crear_cliente(
            self.m.super_admin, administrador=self.m.admin, nombre="Tienda Doña Lupe",
            correo="lupe@tienda.mx", password=CLAVE,
        )
        with self.assertRaises(permisos.PermisoDenegado) as e:
            acciones.autenticar("lupe@tienda.mx", CLAVE)
        self.assertIn("aprobada", str(e.exception))

        acciones.aprobar_cliente(self.m.usuario_admin, cliente)
        acciones.autenticar("lupe@tienda.mx", CLAVE)


class Rechazo(TestCase):
    def setUp(self):
        self.m = Mundo()
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("10000"))

    def test_rechazar_exige_motivo(self):
        cliente = acciones.crear_cliente(
            self.m.super_admin, administrador=self.m.admin, nombre="Sin papeles"
        )
        with self.assertRaises(services.MotivoRequerido):
            acciones.rechazar_cliente(self.m.usuario_admin, cliente, motivo="  ")

    def test_solo_se_rechaza_lo_pendiente(self):
        """Un cliente que ya operó tiene historia de dinero: se suspende, no se rechaza."""
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.rechazar_cliente(
                self.m.usuario_admin, self.m.cliente, motivo="Ya no queremos"
            )

    def test_un_cliente_no_rechaza_a_otro_cliente(self):
        cliente = acciones.crear_cliente(
            self.m.super_admin, administrador=self.m.admin, nombre="Pendiente"
        )
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.rechazar_cliente(self.m.usuario_cliente, cliente, motivo="No")

    def test_rechazar_es_reversible(self):
        cliente = acciones.crear_cliente(
            self.m.super_admin, administrador=self.m.admin, nombre="Corregible"
        )
        acciones.rechazar_cliente(self.m.usuario_admin, cliente, motivo="RFC incorrecto")
        self.assertEqual(cliente.estado, EstadoCliente.RECHAZADO)

        acciones.aprobar_cliente(self.m.usuario_admin, cliente)
        cliente.refresh_from_db()
        self.assertEqual(cliente.estado, EstadoCliente.APROBADO)
        self.assertEqual(cliente.motivo_rechazo, "")

    def test_rechazar_una_caja_libera_su_limite(self):
        """
        Decisión 7: el límite se reserva desde el alta, aunque la caja quede
        pendiente. Si se rechaza, ese dinero vuelve a estar libre.
        """
        pdv = acciones.crear_punto_venta(
            self.m.super_admin, cliente=self.m.cliente, nombre="Caja Feria",
            limite=D("1000"),
        )
        self.assertEqual(services.limites_de(self.m.cliente), D("1000"))

        acciones.rechazar_punto_venta(self.m.usuario_cliente, pdv, motivo="Duplicada")
        self.assertEqual(pdv.estado, EstadoPuntoVenta.RECHAZADO)
        self.assertEqual(services.limites_de(self.m.cliente), D("0"))


class ContrasenaTemporal(TestCase):
    """Decisión 15: nadie elige ni conoce la contraseña definitiva de otro."""

    def setUp(self):
        self.m = Mundo()

    def alta_con_temporal(self):
        pdv = acciones.crear_punto_venta(
            self.m.usuario_cliente, cliente=self.m.cliente, nombre="Caja 3",
            correo="caja3@laermita.mx",
        )
        return pdv, pdv.usuarios.get()

    def test_el_alta_sin_contrasena_genera_una_temporal_de_doce(self):
        pdv, usuario = self.alta_con_temporal()
        self.assertEqual(len(pdv.credencial_temporal), contrasenas.LONGITUD)
        self.assertTrue(usuario.debe_cambiar_contrasena)
        acciones.autenticar("caja3@laermita.mx", pdv.credencial_temporal)

    def test_la_temporal_no_trae_caracteres_ambiguos(self):
        for _ in range(50):
            temporal = contrasenas.generar_temporal()
            self.assertFalse(set(temporal) & set("0O1lI"))

    def test_cambiar_exige_la_actual(self):
        pdv, usuario = self.alta_con_temporal()
        with self.assertRaises(permisos.PermisoDenegado):
            contrasenas.cambiar(usuario, "no-es-la-actual", NUEVA)

    def test_la_nueva_no_puede_ser_la_temporal(self):
        pdv, usuario = self.alta_con_temporal()
        with self.assertRaises(contrasenas.ContrasenaNoValida):
            contrasenas.cambiar(usuario, pdv.credencial_temporal, pdv.credencial_temporal)

    def test_la_nueva_pasa_por_los_validadores(self):
        pdv, usuario = self.alta_con_temporal()
        with self.assertRaises(contrasenas.ContrasenaNoValida):
            contrasenas.cambiar(usuario, pdv.credencial_temporal, "corta")

    def test_al_cambiarla_se_apaga_el_cambio_obligatorio(self):
        pdv, usuario = self.alta_con_temporal()
        contrasenas.cambiar(usuario, pdv.credencial_temporal, NUEVA)
        usuario.refresh_from_db()
        self.assertFalse(usuario.debe_cambiar_contrasena)
        acciones.autenticar("caja3@laermita.mx", NUEVA)


class Bitacora(TestCase):
    """Decisión 17: cada cambio en la red deja quién, qué y a quién."""

    def setUp(self):
        self.m = Mundo()

    def test_alta_aprobacion_y_rechazo_quedan_registrados(self):
        uno = acciones.crear_cliente(
            self.m.super_admin, administrador=self.m.admin, nombre="Uno"
        )
        dos = acciones.crear_cliente(
            self.m.super_admin, administrador=self.m.admin, nombre="Dos"
        )
        acciones.aprobar_cliente(self.m.usuario_admin, uno)
        acciones.rechazar_cliente(self.m.usuario_admin, dos, motivo="Duplicado")

        acciones_hechas = list(
            BitacoraAdministracion.objects.order_by("pk").values_list(
                "accion", "objetivo_nombre", "actor_nombre"
            )
        )
        self.assertIn((AccionAdministrativa.ALTA, "Uno", "César González"), acciones_hechas)
        self.assertIn((AccionAdministrativa.APROBACION, "Uno", "Brenda Lemus"), acciones_hechas)
        rechazo = BitacoraAdministracion.objects.get(accion=AccionAdministrativa.RECHAZO)
        self.assertEqual(rechazo.detalle, "Duplicado")

    def test_suspender_y_reactivar_quedan_registrados(self):
        acciones.suspender_cliente(self.m.usuario_admin, self.m.cliente)
        acciones.reactivar_cliente(self.m.usuario_admin, self.m.cliente)
        tipos = set(BitacoraAdministracion.objects.values_list("accion", flat=True))
        self.assertTrue(
            {AccionAdministrativa.SUSPENSION, AccionAdministrativa.REACTIVACION} <= tipos
        )

    def test_si_el_alta_falla_no_queda_bitacora(self):
        """La fila va en la misma transacción: la bitácora nunca dice algo que no pasó."""
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.crear_cliente(
                self.m.usuario_admin, administrador=self.m.otro_admin, nombre="Intruso"
            )
        self.assertFalse(BitacoraAdministracion.objects.exists())
