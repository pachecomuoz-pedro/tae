"""
Transacciones y movimientos por jerarquía.

Lo que importa probar es la frontera: cada nivel ve lo suyo y lo de abajo,
nunca lo de un vecino y nunca el saldo de arriba.
"""
from decimal import Decimal

from apps.identidad import acciones
from apps.saldo import services

from .test_saldo_red import VENTA, BaseSaldoRed

D = Decimal


class Reportes(BaseSaldoRed):
    def setUp(self):
        super().setUp()
        # Una venta aprobada de la Caja Mostrador.
        r = self.como("luis@laermita.mx").post("/api/venta", VENTA,
                                              content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.folio = r.json()["folio"]
        # Una venta del cliente de OTRO administrador, que nadie de La Ermita debe ver.
        services.traspasar_a_cliente(self.m.otro_admin, self.m.ajeno, D("500"))
        from apps.core.models import EstadoPuntoVenta, PuntoVenta
        caja_ajena = PuntoVenta.objects.create(
            inquilino=self.m.inquilino, cliente=self.m.ajeno, nombre="Caja Ajena",
            estado=EstadoPuntoVenta.ACTIVO,
        )
        services.asignar_limite(caja_ajena, D("500"))
        services.capturar(services.reservar(caja_ajena, D("100"), producto=self.m.producto))
        # El cliente le retira algo a su caja.
        acciones.retirar_de_caja(self.m.usuario_cliente, self.m.pdv, D("50"), motivo="captura")

    def folios(self, correo, ruta="/api/transacciones"):
        return [f["folio"] for f in self.como(correo).get(ruta).json()["filas"]]


class VisibilidadDeTransacciones(Reportes):
    def test_cada_nivel_ve_solo_su_rama(self):
        self.assertEqual(self.folios("luis@laermita.mx"), [self.folio])
        self.assertEqual(self.folios("jorge@laermita.mx"), [self.folio])
        self.assertEqual(self.folios("brenda@eps.mx"), [self.folio])
        self.assertEqual(len(self.folios("cesar@eps.mx")), 2)
        self.assertEqual(len(self.folios("raul@eps.mx")), 1)  # el otro administrador

    def test_la_caja_no_ve_el_saldo_de_su_cliente(self):
        fila = self.como("luis@laermita.mx").get("/api/transacciones").json()["filas"][0]
        self.assertIsNone(fila["saldo_despues"])
        self.assertIsNone(fila["administrador"])
        fila = self.como("jorge@laermita.mx").get("/api/transacciones").json()["filas"][0]
        self.assertIsNotNone(fila["saldo_despues"])
        self.assertIsNone(fila["administrador"])

    def test_el_detalle_trae_autorizaciones_y_respeta_el_alcance(self):
        d = self.como("jorge@laermita.mx").get(f"/api/transacciones/{self.folio}").json()
        self.assertEqual(d["estado"], "aprobada")
        self.assertTrue(d["autorizacion_emida"])
        self.assertEqual(d["response_code"], "0")

        r = self.como("raul@eps.mx").get(f"/api/transacciones/{self.folio}")
        self.assertEqual(r.status_code, 403)

    def test_un_filtro_no_amplia_el_alcance(self):
        r = self.como("luis@laermita.mx").get("/api/transacciones?q=Caja Ajena")
        self.assertEqual(r.json()["filas"], [])

    def test_exportar_respeta_el_alcance_y_las_columnas(self):
        r = self.como("luis@laermita.mx").get("/api/transacciones/exportar")
        texto = r.content.decode("utf-8-sig")
        self.assertIn(self.folio, texto)
        self.assertNotIn("Caja Ajena", texto)
        self.assertNotIn("Saldo cliente", texto)


class VisibilidadDeMovimientos(Reportes):
    def filas(self, correo):
        return self.como(correo).get("/api/movimientos").json()["filas"]

    def test_la_caja_solo_ve_los_cambios_a_su_limite(self):
        filas = self.filas("luis@laermita.mx")
        self.assertTrue(filas)
        self.assertTrue(all(f["tipo"] == "limite" for f in filas))
        self.assertTrue(all(f["afectado"] == "Caja Mostrador" for f in filas))
        retiro = next(f for f in filas if f["concepto"] == "Retiro de caja")
        self.assertEqual(D(retiro["monto"]), D("-50"))

    def test_el_cliente_ve_su_bolsa_pero_no_la_de_su_administrador(self):
        filas = self.filas("jorge@laermita.mx")
        afectados = {f["afectado"] for f in filas}
        self.assertNotIn("Scorpion Centro", afectados)
        traspaso = next(f for f in filas if f["tipo"] == "TRS")
        self.assertEqual(traspaso["concepto"], "Traspaso de Scorpion Centro")
        self.assertEqual(traspaso["movimiento"], "abono")

    def test_el_administrador_ve_su_bolsa_y_la_de_sus_clientes_pero_no_las_ajenas(self):
        afectados = {f["afectado"] for f in self.filas("brenda@eps.mx")}
        self.assertIn("Scorpion Centro", afectados)
        self.assertIn("Abarrotes La Ermita", afectados)
        self.assertNotIn("Mini Super El Roble", afectados)
        self.assertNotIn("Scorpion Sureste", afectados)

    def test_saldo_anterior_mas_monto_es_saldo_actual(self):
        for f in self.filas("cesar@eps.mx"):
            self.assertEqual(D(f["saldo_anterior"]) + D(f["monto"]), D(f["saldo_actual"]), f)
