"""
La API, vista desde fuera.

El punto de estas pruebas no es que los endpoints respondan: es que **la API
no sea una puerta trasera**. Cada regla que ya se probó a nivel de función se
vuelve a probar aquí entrando por HTTP, porque una vista que llame directo a
los servicios se saltaría las tres compuertas sin que ninguna prueba anterior
se entere.
"""
from decimal import Decimal

from django.test import Client, TestCase

from apps.api import proveedor as puente
from apps.identidad import acciones
from apps.identidad.tests.factories import CLAVE, Mundo
from apps.proveedor.cliente import ClienteProveedor, Config
from apps.proveedor.emulador import Emulador, emulador_de_certificacion
from apps.saldo import services

D = Decimal


def cliente_con(emulador):
    cfg = Config(
        base_url="https://gw.ejemplo.com:10002", usuario="u", contrasena="x",
        site_id="9400822", clerk_id="CESAR45",
        timeout_venta=30, timeout_lookup=15,
        lookup_desde_envio=40, espera_entre_lookups=10, max_lookups=3,
    )
    return ClienteProveedor(cfg, sesion=emulador)


class BaseAPI(TestCase):
    def setUp(self):
        self.m = Mundo()
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("10000"))
        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("5000"))
        self.emulador = Emulador(responde_en=0.2)
        self.anterior = puente.usar_fabrica(lambda: cliente_con(self.emulador))

        # El emulador avanza su reloj en lugar de dormir de verdad.
        # Se parchea el nombre EN apps.api.proveedor y no en el módulo de
        # origen: el puente lo importó con `from ... import`, así que su
        # referencia es propia y parchear el origen no la alcanza.
        self._cerrar = puente.cerrar_venta
        puente.cerrar_venta = lambda cl, r, **kw: self._cerrar(
            cl, r, dormir=self.emulador.avanzar, **kw
        )

    def tearDown(self):
        puente.usar_fabrica(self.anterior)
        puente.cerrar_venta = self._cerrar

    def como(self, correo):
        c = Client()
        r = c.post(
            "/api/auth/entrar",
            {"correo": correo, "password": CLAVE},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        return c


class SesionYPerfil(BaseAPI):
    def test_sin_sesion_no_se_ve_nada(self):
        for ruta in ["/api/yo", "/api/catalogo", "/api/saldo", "/api/puntos-venta"]:
            r = Client().get(ruta)
            self.assertIn(r.status_code, (401, 403), f"{ruta} dejó pasar")

    def test_credenciales_malas_no_dicen_cual_falló(self):
        c = Client()
        uno = c.post("/api/auth/entrar",
                     {"correo": "nadie@ejemplo.mx", "password": "x"},
                     content_type="application/json")
        dos = c.post("/api/auth/entrar",
                     {"correo": "jorge@laermita.mx", "password": "incorrecta"},
                     content_type="application/json")
        self.assertEqual(uno.status_code, 403)
        self.assertEqual(dos.status_code, 403)
        self.assertEqual(uno.json()["error"], dos.json()["error"])

    def test_el_perfil_dice_el_rol_y_las_capacidades(self):
        datos = self.como("luis@laermita.mx").get("/api/yo").json()
        self.assertEqual(datos["rol"], "punto_venta")
        self.assertTrue(datos["capacidades"]["vender"])
        self.assertFalse(datos["capacidades"]["traspasar"])

    def test_restablecer_siempre_responde_igual(self):
        c = Client()
        for correo in ["jorge@laermita.mx", "nadie@ejemplo.mx"]:
            r = c.post("/api/auth/restablecer", {"correo": correo},
                       content_type="application/json")
            self.assertEqual(r.status_code, 202)


class CSRFDesdeElNavegador(BaseAPI):
    """
    Lo que un navegador hace y el cliente de pruebas no: mandar `Origin`.

    Por eso esto no se detectó antes, y por partida doble: el cliente de
    Django no manda `Origin`, y además **desactiva la comprobación de CSRF
    por omisión**. Hay que pedirla con `enforce_csrf_checks=True` o las
    pruebas pasan sin probar nada, que fue exactamente lo que me ocurrió al
    escribir esta clase.

    En el navegador, en cambio, fallaba todo POST posterior a entrar. El
    síntoma era de los peores: el usuario quedaba encerrado, porque no podía
    salir —falla el CSRF— ni volver a entrar, porque la sesión vieja activa
    la comprobación.
    """

    ORIGEN = "http://localhost:5173"

    def entrar_como(self, correo):
        c = Client(enforce_csrf_checks=True)
        r = c.post(
            "/api/auth/entrar",
            {"correo": correo, "password": CLAVE},
            content_type="application/json",
            HTTP_ORIGIN=self.ORIGEN,
        )
        self.assertEqual(r.status_code, 200, r.content)
        return c

    def test_se_puede_cerrar_sesion_desde_un_navegador(self):
        c = self.entrar_como("brenda@eps.mx")
        r = c.post(
            "/api/auth/salir",
            HTTP_ORIGIN=self.ORIGEN,
            HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value,
        )
        self.assertEqual(r.status_code, 204, r.content)

    def test_se_puede_vender_desde_un_navegador(self):
        c = self.entrar_como("luis@laermita.mx")
        r = c.post(
            "/api/venta",
            {"sku": "TEL-TAE-100", "cuenta": "9991234567"},
            content_type="application/json",
            HTTP_ORIGIN=self.ORIGEN,
            HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value,
        )
        self.assertEqual(r.status_code, 201, r.content)

    def test_un_origen_ajeno_si_se_rechaza(self):
        """La protección sigue haciendo su trabajo: no la quitamos, la configuramos."""
        c = self.entrar_como("brenda@eps.mx")
        r = c.post(
            "/api/auth/salir",
            HTTP_ORIGIN="http://sitio-malicioso.example",
            HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value,
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("CSRF", r.json().get("detail", ""))


class LaAPINoEsPuertaTrasera(BaseAPI):
    """Cada regla probada a nivel de función, otra vez por HTTP."""

    def test_un_cajero_no_traspasa(self):
        r = self.como("luis@laermita.mx").post(
            "/api/traspasos",
            {"cliente": str(self.m.cliente.uuid), "monto": "1000"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["clave"], "permiso")

    def test_un_cliente_no_se_traspasa_a_si_mismo(self):
        r = self.como("jorge@laermita.mx").post(
            "/api/traspasos",
            {"cliente": str(self.m.cliente.uuid), "monto": "1000"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_un_administrador_no_alcanza_al_cliente_de_otro(self):
        r = self.como("brenda@eps.mx").post(
            "/api/traspasos",
            {"cliente": str(self.m.ajeno.uuid), "monto": "1000"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_un_cajero_no_vende_en_caja_ajena(self):
        acciones.asignar_limite(self.m.usuario_cliente, self.m.otra_caja, D("1000"))
        r = self.como("luis@laermita.mx").post(
            "/api/venta",
            {"punto_venta": str(self.m.otra_caja.uuid), "sku": "TEL-TAE-100",
             "cuenta": "9991234567"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.emulador.cobros(), 0)

    def test_un_cliente_no_vende(self):
        r = self.como("jorge@laermita.mx").post(
            "/api/venta",
            {"punto_venta": str(self.m.pdv.uuid), "sku": "TEL-TAE-100",
             "cuenta": "9991234567"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_un_cajero_no_asigna_limites(self):
        r = self.como("luis@laermita.mx").post(
            "/api/puntos-venta/limite",
            {"punto_venta": str(self.m.pdv.uuid), "monto": "100"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_un_cajero_solo_ve_su_propia_caja(self):
        datos = self.como("luis@laermita.mx").get("/api/puntos-venta").json()
        self.assertEqual([p["nombre"] for p in datos], ["Caja Mostrador"])

    def test_el_cliente_ve_todas_las_suyas_y_ninguna_ajena(self):
        datos = self.como("jorge@laermita.mx").get("/api/puntos-venta").json()
        nombres = sorted(p["nombre"] for p in datos)
        self.assertEqual(nombres, ["Caja Fondo", "Caja Mostrador"])


class VentaPorHTTP(BaseAPI):
    def test_venta_aprobada_devuelve_201_con_folio_y_autorizacion(self):
        antes = services.bolsa_de(self.m.cliente).saldo
        r = self.como("luis@laermita.mx").post(
            "/api/venta",
            {"sku": "TEL-TAE-100", "cuenta": "9991234567"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        datos = r.json()
        self.assertEqual(datos["desenlace"], "aprobada")
        self.assertTrue(datos["folio"].startswith("VTA-"))
        self.assertTrue(datos["autorizacion"])
        self.assertEqual(self.emulador.cobros(), 1)
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, antes - D("100"))

    def test_sin_resolver_devuelve_202_y_no_dice_aprobada(self):
        """
        Los tres desenlaces son distintos y la pantalla tiene que verlos
        distintos. Traducir `sin_resolver` a cualquiera de los otros dos es
        exactamente lo que no debe pasar.
        """
        self.emulador = emulador_de_certificacion("TX-LK-10")
        puente.usar_fabrica(lambda: cliente_con(self.emulador))

        r = self.como("luis@laermita.mx").post(
            "/api/venta",
            {"sku": "TEL-TAE-100", "cuenta": "9991234567"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["desenlace"], "sin_resolver")
        self.assertEqual(services.bolsa_de(self.m.cliente).reservado, D("100.0000"))

    def test_rechazo_del_proveedor_devuelve_200_y_libera(self):
        self.emulador = Emulador(aprobar=False, response_code="51")
        puente.usar_fabrica(lambda: cliente_con(self.emulador))
        antes = services.bolsa_de(self.m.cliente).saldo

        r = self.como("luis@laermita.mx").post(
            "/api/venta",
            {"sku": "TEL-TAE-100", "cuenta": "9991234567"},
            content_type="application/json",
        )
        self.assertEqual(r.json()["desenlace"], "rechazada")
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, antes)

    def test_saldo_insuficiente_devuelve_402(self):
        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("50"))
        r = self.como("luis@laermita.mx").post(
            "/api/venta",
            {"sku": "TEL-TAE-100", "cuenta": "9991234567"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.json()["clave"], "saldo")
        self.assertEqual(self.emulador.cobros(), 0)

    def test_producto_desconocido_devuelve_404(self):
        r = self.como("luis@laermita.mx").post(
            "/api/venta", {"sku": "NO-EXISTE", "cuenta": "9991234567"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 404)


class SaldoYCatalogo(BaseAPI):
    def test_el_cajero_ve_su_caja_y_la_bolsa_de_su_cliente(self):
        datos = self.como("luis@laermita.mx").get("/api/saldo").json()
        self.assertEqual(datos["titular"], "Abarrotes La Ermita")
        self.assertEqual(datos["mi_caja"]["nombre"], "Caja Mostrador")
        self.assertEqual(datos["mi_caja"]["limite"], "5000.0000")

    def test_el_retirable_descuenta_limites_y_reservas(self):
        datos = self.como("jorge@laermita.mx").get("/api/saldo").json()
        self.assertEqual(datos["limites_asignados"], "5000.0000")
        self.assertEqual(datos["retirable"], "5400.0000")  # 10,400 − 5,000

    def test_el_catalogo_trae_el_codigo_de_barras(self):
        datos = self.como("luis@laermita.mx").get("/api/catalogo").json()
        self.assertEqual(datos[0]["codigo_barras"], "TEL-TAE-100")
        self.assertEqual(datos[0]["etiqueta_monto"], "$100.00")

    def test_escanear_resuelve_el_producto(self):
        r = self.como("luis@laermita.mx").get("/api/catalogo/codigo/TEL-TAE-100")
        self.assertEqual(r.json()["sku"], "TEL-TAE-100")


class AdministracionPorHTTP(BaseAPI):
    def test_traspaso_sobre_el_umbral_pide_segundo_factor(self):
        r = self.como("brenda@eps.mx").post(
            "/api/traspasos",
            {"cliente": str(self.m.cliente.uuid), "monto": "60000"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["clave"], "segundo_factor")

    def test_retiro_sin_motivo_del_catalogo_se_rechaza(self):
        r = self.como("brenda@eps.mx").post(
            "/api/retiros",
            {"cliente": str(self.m.cliente.uuid), "monto": "100", "motivo": "porque si"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["clave"], "motivo")

    def test_devolucion_sobre_saldo_comprometido_devuelve_402(self):
        r = self.como("brenda@eps.mx").post(
            "/api/retiros",
            {"cliente": str(self.m.cliente.uuid), "monto": "9000",
             "motivo": "solicitada"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 402)

    def test_asignar_de_mas_devuelve_409_con_la_razon(self):
        r = self.como("jorge@laermita.mx").post(
            "/api/puntos-venta/limite",
            {"punto_venta": str(self.m.otra_caja.uuid), "monto": "9000"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["clave"], "limite")

    def test_suspender_arrastra_las_cajas(self):
        r = self.como("brenda@eps.mx").post(
            "/api/clientes/suspension",
            {"cliente": str(self.m.cliente.uuid)},
            content_type="application/json",
        )
        datos = r.json()
        self.assertEqual(datos["estado"], "suspendido")
        self.assertTrue(all(p["estado"] == "suspendido" for p in datos["puntos_venta"]))

    def test_suspendido_el_cliente_la_venta_se_rechaza_por_http(self):
        self.como("brenda@eps.mx").post(
            "/api/clientes/suspension", {"cliente": str(self.m.cliente.uuid)},
            content_type="application/json",
        )
        r = self.como("luis@laermita.mx").post(
            "/api/venta", {"sku": "TEL-TAE-100", "cuenta": "9991234567"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.emulador.cobros(), 0)
