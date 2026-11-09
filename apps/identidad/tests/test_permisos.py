"""
Una prueba por combinación prohibida.

Lo que se verifica aquí no es que el botón no se vea: es que el servidor
rechaza aunque alguien llame a la función directamente. Esa distinción es
toda la diferencia entre un permiso y una sugerencia.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import EstadoPuntoVenta, ModoBolsa
from apps.identidad import acciones, permisos
from apps.saldo import services
from apps.saldo.models import Motivo

from .factories import CLAVE, Mundo

D = Decimal


class QuienCreaAQuien(TestCase):
    def setUp(self):
        self.m = Mundo()

    def test_solo_el_super_admin_crea_administradores(self):
        for usuario in (self.m.usuario_admin, self.m.usuario_cliente, self.m.usuario_pdv):
            with self.assertRaises(permisos.PermisoDenegado):
                acciones.crear_administrador(usuario, nombre="Scorpion Norte")

        nuevo = acciones.crear_administrador(self.m.super_admin, nombre="Scorpion Norte")
        self.assertEqual(services.bolsa_de(nuevo).saldo, D("0.0000"))

    def test_un_administrador_no_crea_clientes_en_otro_administrador(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.crear_cliente(
                self.m.usuario_admin, administrador=self.m.otro_admin, nombre="Intruso"
            )

        cliente = acciones.crear_cliente(
            self.m.usuario_admin, administrador=self.m.admin, nombre="Tienda Doña Lupe"
        )
        self.assertEqual(cliente.administrador, self.m.admin)

    def test_un_cliente_no_crea_clientes(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.crear_cliente(
                self.m.usuario_cliente, administrador=self.m.admin, nombre="Otro"
            )

    def test_un_cliente_crea_y_aprueba_sus_propias_cajas(self):
        pdv = acciones.crear_punto_venta(
            self.m.usuario_cliente, cliente=self.m.cliente, nombre="Caja Nueva"
        )
        self.assertEqual(pdv.estado, EstadoPuntoVenta.ACTIVO)
        self.assertEqual(pdv.cliente, self.m.cliente)

    def test_un_cliente_no_crea_cajas_en_otro_cliente(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.crear_punto_venta(
                self.m.usuario_cliente, cliente=self.m.ajeno, nombre="Caja Ajena"
            )

    def test_una_caja_no_crea_ni_aprueba_cajas(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.crear_punto_venta(
                self.m.usuario_pdv, cliente=self.m.cliente, nombre="Caja Nueva"
            )
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.aprobar_punto_venta(self.m.usuario_pdv, self.m.otra_caja)

    def test_un_cliente_no_se_aprueba_a_si_mismo(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.aprobar_cliente(self.m.usuario_cliente, self.m.cliente)

    def test_un_cliente_suspendido_se_reactiva_no_se_aprueba(self):
        acciones.suspender_cliente(self.m.usuario_admin, self.m.cliente)
        self.m.cliente.refresh_from_db()

        with self.assertRaises(permisos.PermisoDenegado):
            acciones.aprobar_cliente(self.m.usuario_admin, self.m.cliente)

        acciones.reactivar_cliente(self.m.usuario_admin, self.m.cliente)
        self.m.cliente.refresh_from_db()
        self.assertTrue(self.m.cliente.puede_operar)


class AlcanceDelSaldo(TestCase):
    def setUp(self):
        self.m = Mundo()

    def test_un_administrador_no_traspasa_al_cliente_de_otro(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.traspasar(self.m.usuario_admin, self.m.ajeno, D("1000"))

    def test_un_cliente_no_se_traspasa_saldo_a_si_mismo(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.traspasar(self.m.usuario_cliente, self.m.cliente, D("1000"))

    def test_una_caja_no_retira_saldo(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.devolver(
                self.m.usuario_pdv, self.m.cliente, D("100"), motivo=Motivo.SOLICITADA
            )

    def test_solo_el_super_admin_fondea_administradores(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.fondear_administrador(
                self.m.usuario_admin, self.m.admin, D("1000")
            )

        operacion = acciones.fondear_administrador(
            self.m.super_admin, self.m.admin, D("1000")
        )
        self.assertTrue(operacion.folio.startswith("CMP-"))

    def test_una_caja_no_se_asigna_saldo_a_si_misma(self):
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("1000"))
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.asignar_limite(self.m.usuario_pdv, self.m.pdv, D("500"))

        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("500"))
        self.assertEqual(services.limites_de(self.m.cliente), D("500.0000"))

    def test_el_nombre_de_quien_autoriza_queda_en_la_operacion(self):
        operacion = acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("1000"))
        self.assertEqual(operacion.autorizado_por, "Brenda Lemus")


class QuienVende(TestCase):
    def setUp(self):
        self.m = Mundo()
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("1000"))
        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("500"))

    def test_el_cliente_no_vende(self):
        """Regla de negocio: siempre vende una caja, nunca el cliente."""
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.vender(self.m.usuario_cliente, self.m.pdv, self.m.producto)

    def test_el_administrador_no_vende(self):
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.vender(self.m.usuario_admin, self.m.pdv, self.m.producto)

    def test_un_cajero_no_vende_en_una_caja_que_no_es_la_suya(self):
        """
        La compuerta que más se olvida. Credenciales válidas no bastan:
        el usuario tiene que estar asignado a ESA caja.

        La otra caja lleva saldo asignado a propósito: así lo único que
        detiene la venta es el permiso, y no que la caja esté sin fondos.
        Sin ese saldo la prueba pasaría por la razón equivocada.
        """
        acciones.asignar_limite(self.m.usuario_cliente, self.m.otra_caja, D("400"))

        with self.assertRaises(permisos.PermisoDenegado):
            acciones.vender(self.m.usuario_pdv, self.m.otra_caja, self.m.producto)

    def test_el_cajero_vende_en_la_suya(self):
        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)
        self.assertEqual(reserva.monto, D("100.0000"))

    def test_no_vende_si_su_caja_esta_suspendida(self):
        acciones.suspender_cliente(self.m.usuario_admin, self.m.cliente)
        self.m.pdv.refresh_from_db()

        with self.assertRaises(permisos.PermisoDenegado):
            acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)


class SegundoFactor(TestCase):
    def setUp(self):
        self.m = Mundo()

    def _activar(self, usuario):
        import pyotp

        acciones.preparar_totp(usuario)
        usuario.refresh_from_db()
        codigo = pyotp.TOTP(usuario.totp_secreto).now()
        self.assertTrue(acciones.activar_totp(usuario, codigo))
        usuario.refresh_from_db()
        return usuario

    def test_el_qr_no_activa_por_si_solo(self):
        uri = acciones.preparar_totp(self.m.super_admin)
        self.m.super_admin.refresh_from_db()

        self.assertIn("otpauth://totp/", uri)
        self.assertTrue(self.m.super_admin.totp_secreto)
        # Mostrar el código no basta: hace falta teclear uno correcto.
        self.assertFalse(self.m.super_admin.totp_listo)

    def test_el_super_admin_no_entra_sin_codigo(self):
        self._activar(self.m.super_admin)

        with self.assertRaises(permisos.SegundoFactorRequerido):
            acciones.autenticar("cesar@eps.mx", CLAVE)

        with self.assertRaises(permisos.PermisoDenegado):
            acciones.autenticar("cesar@eps.mx", CLAVE, codigo_totp="000000")

    def test_el_super_admin_entra_con_codigo(self):
        import pyotp

        self._activar(self.m.super_admin)
        codigo = pyotp.TOTP(self.m.super_admin.totp_secreto).now()

        usuario = acciones.autenticar("cesar@eps.mx", CLAVE, codigo_totp=codigo)
        self.assertEqual(usuario.pk, self.m.super_admin.pk)

    def test_al_tendero_no_se_le_pide_codigo(self):
        usuario = acciones.autenticar("jorge@laermita.mx", CLAVE)
        self.assertEqual(usuario.pk, self.m.usuario_cliente.pk)
        self.assertFalse(usuario.exige_totp_al_entrar)

    def test_al_cajero_tampoco(self):
        usuario = acciones.autenticar("luis@laermita.mx", CLAVE)
        self.assertEqual(usuario.pk, self.m.usuario_pdv.pk)

    def test_el_administrador_entra_sin_codigo_pero_lo_necesita_para_el_traspaso_grande(self):
        import pyotp

        acciones.autenticar("brenda@eps.mx", CLAVE)  # entra sin código

        # Debajo del umbral pasa.
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("10000"))

        # Encima del umbral de 50,000 no.
        with self.assertRaises(permisos.SegundoFactorRequerido):
            acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("60000"))

        self._activar(self.m.usuario_admin)
        codigo = pyotp.TOTP(self.m.usuario_admin.totp_secreto).now()
        operacion = acciones.traspasar(
            self.m.usuario_admin, self.m.cliente, D("60000"), codigo_totp=codigo
        )
        self.assertTrue(operacion.folio.startswith("TRS-"))

    def test_un_codigo_equivocado_no_destraba_el_traspaso_grande(self):
        self._activar(self.m.usuario_admin)
        with self.assertRaises(permisos.SegundoFactorRequerido):
            acciones.traspasar(
                self.m.usuario_admin, self.m.cliente, D("60000"), codigo_totp="000000"
            )

    def test_solo_el_super_admin_reinicia_el_segundo_factor(self):
        self._activar(self.m.usuario_admin)

        with self.assertRaises(permisos.PermisoDenegado):
            acciones.reiniciar_totp(self.m.usuario_otro_admin, self.m.usuario_admin)

        acciones.reiniciar_totp(self.m.super_admin, self.m.usuario_admin)
        self.m.usuario_admin.refresh_from_db()
        self.assertFalse(self.m.usuario_admin.totp_listo)

    def test_nadie_reinicia_su_propio_segundo_factor(self):
        self._activar(self.m.super_admin)
        with self.assertRaises(permisos.PermisoDenegado):
            acciones.reiniciar_totp(self.m.super_admin, self.m.super_admin)


class TopeDiario(TestCase):
    def setUp(self):
        self.m = Mundo()

    def test_el_tope_diario_detiene_al_administrador(self):
        import pyotp

        self.m.usuario_admin = self._con_totp(self.m.usuario_admin)
        codigo = lambda: pyotp.TOTP(self.m.usuario_admin.totp_secreto).now()

        # El tope por omisión es 500,000. Cinco traspasos de 100,000 lo agotan.
        for _ in range(5):
            acciones.traspasar(
                self.m.usuario_admin, self.m.cliente, D("100000"), codigo_totp=codigo()
            )

        with self.assertRaises(permisos.TopeDiarioExcedido):
            acciones.traspasar(
                self.m.usuario_admin, self.m.cliente, D("1"), codigo_totp=codigo()
            )

    def test_el_tope_se_cuenta_por_administrador_y_no_por_plataforma(self):
        """Que uno llegue a su tope no detiene al otro."""
        import pyotp

        self.m.usuario_admin = self._con_totp(self.m.usuario_admin)
        for _ in range(5):
            acciones.traspasar(
                self.m.usuario_admin, self.m.cliente, D("100000"),
                codigo_totp=pyotp.TOTP(self.m.usuario_admin.totp_secreto).now(),
            )

        operacion = acciones.traspasar(
            self.m.usuario_otro_admin, self.m.ajeno, D("1000")
        )
        self.assertTrue(operacion.folio.startswith("TRS-"))

    def _con_totp(self, usuario):
        import pyotp

        acciones.preparar_totp(usuario)
        usuario.refresh_from_db()
        acciones.activar_totp(usuario, pyotp.TOTP(usuario.totp_secreto).now())
        usuario.refresh_from_db()
        return usuario


class Bitacora(TestCase):
    def setUp(self):
        self.m = Mundo()

    def test_los_intentos_fallidos_quedan_registrados(self):
        from apps.identidad.models import BitacoraAcceso

        with self.assertRaises(permisos.PermisoDenegado):
            acciones.autenticar("jorge@laermita.mx", "incorrecta")

        fallo = BitacoraAcceso.objects.filter(exito=False).latest("cuando")
        self.assertEqual(fallo.motivo, "contraseña incorrecta")
        self.assertEqual(fallo.usuario, self.m.usuario_cliente)

    def test_un_correo_inexistente_responde_lo_mismo_que_una_clave_mala(self):
        from apps.identidad.models import BitacoraAcceso

        with self.assertRaises(permisos.PermisoDenegado) as desconocido:
            acciones.autenticar("nadie@ejemplo.mx", "lo-que-sea")
        with self.assertRaises(permisos.PermisoDenegado) as mala_clave:
            acciones.autenticar("jorge@laermita.mx", "incorrecta")

        # Mismo mensaje: no se le confirma a nadie qué correos existen.
        self.assertEqual(str(desconocido.exception), str(mala_clave.exception))
        # Pero por dentro sí se distingue, para quien lea la bitácora.
        motivos = set(
            BitacoraAcceso.objects.filter(exito=False).values_list("motivo", flat=True)
        )
        self.assertEqual(motivos, {"correo desconocido", "contraseña incorrecta"})


class AnclajeDelUsuario(TestCase):
    def setUp(self):
        self.m = Mundo()

    def test_un_usuario_de_cliente_no_puede_colgar_de_un_administrador(self):
        from django.core.exceptions import ValidationError

        from apps.identidad.models import Rol, Usuario

        with self.assertRaises(ValidationError):
            Usuario.objects.crear(
                "mixto@eps.mx", Rol.CLIENTE, password=CLAVE,
                inquilino=self.m.inquilino, nombre="Mixto",
            )

    def test_el_alcance_de_un_cajero_llega_hasta_su_cliente(self):
        self.assertTrue(self.m.usuario_pdv.alcanza_cliente(self.m.cliente))
        self.assertFalse(self.m.usuario_pdv.alcanza_cliente(self.m.ajeno))
        self.assertTrue(self.m.usuario_pdv.alcanza_punto_venta(self.m.pdv))
