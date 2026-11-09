"""
Saldo de EPS en Emida, sincronización diaria del catálogo y el programador.

Las respuestas de Emida en estas pruebas son del emulador. Los formatos son
supuestos (no tenemos el contrato exacto); lo que se prueba es nuestro
manejo: que un saldo ilegible no se invente, que una lista vacía no apague
el catálogo y que la sincronización no reviva lo que una persona apagó.
"""
from datetime import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.conf import settings

from apps.api import proveedor as puente
from apps.catalogo import servicios as catalogo
from apps.catalogo.models import ProductoDelProveedor, ProductoProveedor
from apps.catalogo.sincronizacion import SincronizacionVacia, sincronizar
from apps.core.management.commands.programador import toca_catalogo, toca_reintento
from apps.identidad import acciones, permisos
from apps.identidad.tests.factories import Mundo
from apps.proveedor import saldo as saldo_proveedor
from apps.proveedor.cliente import ClienteProveedor, Config
from apps.proveedor.emulador import Emulador, _Resp
from apps.proveedor.modelos import ConsultaSaldoProveedor

D = Decimal


class EmidaEnMantenimiento(Emulador):
    """El login funciona, pero todo lo demás responde 503."""

    def request(self, metodo, url, headers=None, json=None, timeout=None, **kw):
        if url.endswith("/api/v1/auth/login"):
            return super().request(metodo, url, headers=headers, json=json, timeout=timeout)
        return _Resp(503, {"detail": "mantenimiento"})


def cliente_con(emulador):
    cfg = Config(base_url="https://emida.test", usuario="u", contrasena="p",
                 site_id="9400822", clerk_id="1", merchant_id="77")
    return ClienteProveedor(cfg, sesion=emulador)


class LeerSaldo(TestCase):
    def test_lee_el_campo_de_primer_nivel_y_la_moneda(self):
        self.assertEqual(saldo_proveedor.leer_saldo({"balance": "1,250.50", "currency": "MXN"}),
                         (D("1250.50"), "MXN"))

    def test_lee_un_saldo_anidado(self):
        saldo, _ = saldo_proveedor.leer_saldo({"account": {"availableBalance": 99}})
        self.assertEqual(saldo, D("99"))

    def test_sin_numero_legible_no_inventa_uno(self):
        self.assertEqual(saldo_proveedor.leer_saldo({"status": "ok"})[0], None)
        self.assertEqual(saldo_proveedor.leer_saldo(["no", "es", "objeto"])[0], None)


class SaldoEmida(TestCase):
    def setUp(self):
        self.m = Mundo()
        self.emulador = Emulador()

    def test_el_super_admin_consulta_y_queda_guardado(self):
        registro = saldo_proveedor.consultar(self.m.super_admin, cliente_con(self.emulador))
        self.assertEqual(registro.saldo, D("250000.00"))
        self.assertEqual(ConsultaSaldoProveedor.objects.count(), 1)

    def test_manda_merchant_id_del_env(self):
        saldo_proveedor.consultar(self.m.super_admin, cliente_con(self.emulador))
        llamada = [c for c in self.emulador.llamadas if c["ruta"].endswith("/account/balance")][0]
        self.assertEqual(llamada["headers"]["Merchant-Id"], "77")
        self.assertEqual(llamada["headers"]["Clerk-Id"], "1")

    def test_nadie_mas_ve_el_saldo_de_emida(self):
        for usuario in (self.m.usuario_admin, self.m.usuario_cliente, self.m.usuario_pdv):
            with self.assertRaises(permisos.PermisoDenegado):
                saldo_proveedor.consultar(usuario, cliente_con(self.emulador))
        self.assertFalse(ConsultaSaldoProveedor.objects.exists())

    def test_si_emida_falla_se_guarda_el_error_y_se_conserva_el_ultimo_saldo_bueno(self):
        saldo_proveedor.consultar(self.m.super_admin, cliente_con(self.emulador))
        saldo_proveedor.consultar(self.m.super_admin, cliente_con(EmidaEnMantenimiento()))

        resumen = saldo_proveedor.resumen(self.m.super_admin)
        self.assertEqual(resumen["saldo_emida"], "250000.0000")
        self.assertIn("mantenimiento", resumen["ultimo_error"])

    def test_el_asignado_en_la_red_es_la_suma_de_administradores_y_clientes(self):
        """
        Decisión 23: el saldo de la red lo asigna el Super Admin y no sale de
        Emida. Un traspaso mueve dinero dentro de la red; lo único que sube
        el total es el margen, que sale de la cuenta de bonificación
        (decisión 8), no de Emida ni del administrador.
        """
        antes = saldo_proveedor.asignado_en_red(self.m.inquilino)
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("1000"))
        margen = D("1000") * self.m.cliente.margen_pct / 100
        self.assertEqual(saldo_proveedor.asignado_en_red(self.m.inquilino), antes + margen)

    def test_api_solo_para_el_super_admin(self):
        puente.usar_fabrica(lambda: cliente_con(self.emulador))
        self.addCleanup(puente.usar_fabrica, None)

        sa = Client()
        sa.force_login(self.m.super_admin)
        r = sa.post("/api/proveedor/saldo")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["saldo_emida"], "250000.0000")
        # El GET no llama a Emida: devuelve lo guardado.
        llamadas = len(self.emulador.llamadas)
        self.assertEqual(sa.get("/api/proveedor/saldo").status_code, 200)
        self.assertEqual(len(self.emulador.llamadas), llamadas)

        admin = Client()
        admin.force_login(self.m.usuario_admin)
        self.assertEqual(admin.post("/api/proveedor/saldo").status_code, 403)


class SincronizacionDelCatalogo(TestCase):
    def setUp(self):
        self.m = Mundo()
        self.emulador = Emulador()  # lista TEL50…MOV200; el Mundo vende TEL100

    def sincronizar(self):
        return sincronizar(self.m.inquilino, cliente_con(self.emulador), proveedor_clave="emida")

    def test_copia_lo_que_lista_emida_con_su_product_flow(self):
        r = self.sincronizar()
        self.assertEqual(r["productos"], 9)
        oferta = ProductoDelProveedor.objects.get(product_id="TEL200")
        self.assertEqual(oferta.monto, D("200"))
        self.assertEqual(oferta.carrier, "Telcel")
        self.assertIsNotNone(oferta.flujo)
        self.assertIn("TEL200", r["sin_vincular"])
        self.assertNotIn("TEL100", r["sin_vincular"])

    def test_si_emida_deja_de_listarlo_la_ruta_se_pausa_y_vuelve_sola(self):
        self.sincronizar()
        self.emulador.productos_terminal = [
            p for p in self.emulador.productos_terminal if p["productId"] != "TEL100"
        ]
        r = self.sincronizar()
        self.assertEqual(r["rutas_pausadas"], ["TEL100"])
        with self.assertRaises(catalogo.SinRuta):
            catalogo.ruta_para(self.m.producto)
        self.assertFalse(catalogo.vendibles(self.m.inquilino).exists())

        self.emulador.productos_terminal.append(
            {"productId": "TEL100", "description": "Telcel $100", "amount": 100}
        )
        r = self.sincronizar()
        self.assertEqual(r["rutas_reanudadas"], ["TEL100"])
        catalogo.ruta_para(self.m.producto)

    def test_nunca_revive_una_ruta_que_una_persona_apago(self):
        ProductoProveedor.objects.filter(product_id="TEL100").update(activo=False)
        self.sincronizar()
        ruta = ProductoProveedor.objects.get(product_id="TEL100")
        self.assertFalse(ruta.activo)

    def test_una_lista_vacia_no_toca_nada(self):
        self.sincronizar()
        self.emulador.productos_terminal = []
        with self.assertRaises(SincronizacionVacia):
            self.sincronizar()
        self.assertFalse(ProductoProveedor.objects.filter(pausada_por_proveedor=True).exists())
        self.assertTrue(ProductoDelProveedor.objects.filter(disponible=True).exists())

    def test_vincular_producto_lo_pone_en_la_pantalla(self):
        self.sincronizar()
        call_command("vincular_producto", product_id="TEL200", sku="TEL-TAE-200",
                     nombre="Telcel Tiempo Aire $200", carrier="Telcel",
                     inquilino=self.m.inquilino.clave, proveedor="emida", stdout=StringIO())
        skus = set(catalogo.vendibles(self.m.inquilino).values_list("sku", flat=True))
        self.assertIn("TEL-TAE-200", skus)


class Programador(TestCase):
    def test_el_catalogo_toca_una_vez_al_dia_despues_de_la_hora(self):
        hoy_3 = datetime(2026, 11, 6, 3, 59)
        hoy_4 = datetime(2026, 11, 6, 4, 1)
        self.assertFalse(toca_catalogo(hoy_3, (4, 0), None))
        self.assertTrue(toca_catalogo(hoy_4, (4, 0), None))
        self.assertFalse(toca_catalogo(hoy_4, (4, 0), datetime(2026, 11, 6, 4, 0, 30)))
        # Un reinicio a mediodía sin sincronización de hoy: sí toca.
        self.assertTrue(toca_catalogo(datetime(2026, 11, 6, 12, 0), (4, 0),
                                      datetime(2026, 11, 5, 4, 0)))

    def test_los_reintentos_tocan_cada_n_minutos(self):
        t = datetime(2026, 11, 6, 10, 0)
        self.assertTrue(toca_reintento(t, 5, None))
        self.assertFalse(toca_reintento(t, 5, datetime(2026, 11, 6, 9, 57)))
        self.assertTrue(toca_reintento(t, 5, datetime(2026, 11, 6, 9, 55)))


class ProbarProveedor(TestCase):
    def test_el_diagnostico_corre_en_modo_simulado_y_no_vende(self):
        Mundo()
        config = dict(settings.TAE_PROVEEDOR, modo="simulado")
        with override_settings(TAE_PROVEEDOR=config):
            salida = StringIO()
            call_command("probar_proveedor", stdout=salida)
        texto = salida.getvalue()
        self.assertIn("token obtenido", texto)
        self.assertIn("productos", texto)
        self.assertIn("Saldo: 250,000.00", texto)
        self.assertNotIn("topup", texto)


class ExportarEvidencia(TestCase):
    def test_exporta_json_por_venta_y_el_reporte_en_formato_emida(self):
        import csv
        import json
        import tempfile
        from pathlib import Path

        from django.utils import timezone

        from apps.proveedor import venta as cierre_venta

        m = Mundo()
        acciones.traspasar(m.usuario_admin, m.cliente, D("1000"))
        acciones.asignar_limite(m.usuario_cliente, m.pdv, D("500"))
        reserva = acciones.vender(m.usuario_pdv, m.pdv, m.producto)
        cierre_venta.cerrar_venta(cliente_con(Emulador()), reserva, account_id="9991234567",
                                  product_id="TEL100", dormir=lambda s: None)

        with tempfile.TemporaryDirectory() as carpeta:
            hoy = timezone.localdate().isoformat()
            call_command("exportar_evidencia", desde=hoy, carpeta=carpeta, stdout=StringIO())
            archivos = sorted(p.name for p in Path(carpeta).iterdir())
            folio = reserva.operacion.folio
            self.assertIn(f"{folio}.json", archivos)

            evidencia = json.loads((Path(carpeta) / f"{folio}.json").read_text(encoding="utf-8"))
            self.assertEqual(evidencia["request"]["headers"]["Authorization"], "Bearer ***")
            self.assertEqual(evidencia["intentos"][0]["http"], 200)

            filas = list(csv.reader(open(Path(carpeta) / "reporte_emida.csv", encoding="utf-8")))
            self.assertEqual(filas[0][:3], ["Date_Time", "Terminal_Id", "ResponseTransaction_Id"])
            self.assertEqual(filas[1][6], "9991234567")
            self.assertEqual(filas[1][7], "0")
