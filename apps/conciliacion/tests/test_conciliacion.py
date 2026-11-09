"""
Conciliación contra el archivo del proveedor.

La mitad de estas pruebas verifican que algo se arregle solo. La otra mitad,
más importante, verifica que algo **no** se arregle solo. La línea entre las
dos es la que decide si el sistema es auditable o si mueve dinero por su
cuenta.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.conciliacion import reportes, servicios
from apps.conciliacion.modelos import (
    ArchivoConciliacion,
    Diferencia,
    EstadoDiferencia,
    TipoDiferencia,
)
from apps.identidad import acciones
from apps.identidad.tests.factories import Mundo
from apps.proveedor import venta as cierre_venta
from apps.proveedor.cliente import ClienteProveedor, Config
from apps.proveedor.emulador import Emulador, emulador_de_certificacion
from apps.saldo import services
from apps.saldo.models import EstadoReserva

D = Decimal

ENCABEZADO = (
    "Date_Time,Terminal_Id,ResponseTransaction_Id,Invoice_Id,"
    "Product_Id,Amount_Id,Account ID,ResponseCode"
)


def archivo_csv(filas):
    return "\n".join([ENCABEZADO] + filas) + "\n"


def renglon(invoice, monto, codigo="0", *, cuando=None, cuenta="9991234567"):
    fecha = (cuando or timezone.localtime()).strftime("%d/%m/%Y %H:%M")
    return f"{fecha},9400822,2262557{invoice:02d},{invoice},TEL100,{monto},{cuenta},{codigo}"


class Base(TestCase):
    def setUp(self):
        self.m = Mundo()
        acciones.traspasar(self.m.usuario_admin, self.m.cliente, D("50000"))
        acciones.asignar_limite(self.m.usuario_cliente, self.m.pdv, D("20000"))

    def cliente_proveedor(self, emulador):
        cfg = Config(
            base_url="https://gw.ejemplo.com:10002", usuario="u", contrasena="x",
            site_id="9400822", clerk_id="CESAR45", timeout_venta=30,
            timeout_lookup=15, lookup_desde_envio=40,
            espera_entre_lookups=10, max_lookups=3,
        )
        return ClienteProveedor(cfg, sesion=emulador)

    def vender(self, emulador=None):
        emulador = emulador or Emulador(responde_en=0.2)
        reserva = acciones.vender(self.m.usuario_pdv, self.m.pdv, self.m.producto)
        cierre = cierre_venta.cerrar_venta(
            self.cliente_proveedor(emulador), reserva,
            account_id="9991234567", product_id="TEL100",
            dormir=emulador.avanzar,
        )
        return reserva, cierre

    def conciliar(self, filas, **kw):
        archivo = servicios.importar(
            self.m.inquilino, archivo_csv(filas), nombre="dia.csv"
        )
        return servicios.conciliar(archivo, **kw)


class LecturaDelArchivo(Base):
    def test_lee_el_formato_real_del_proveedor(self):
        reserva, _ = self.vender()
        archivo = servicios.importar(
            self.m.inquilino,
            archivo_csv([renglon(reserva.operacion.invoice_no, "100")]),
            nombre="dia.csv",
        )
        fila = archivo.renglones_detalle.get()

        self.assertEqual(archivo.renglones, 1)
        self.assertEqual(fila.invoice_no, reserva.operacion.invoice_no)
        self.assertEqual(fila.monto, D("100.0000"))
        self.assertTrue(fila.aprobada_por_proveedor)
        self.assertEqual(archivo.importe_proveedor, D("100.0000"))

    def test_la_fecha_se_lee_dia_mes_y_no_mes_dia(self):
        """
        Si se leyera al revés, el 09/11 sería septiembre y la ventana del
        cruce quedaría corrida tres meses: todo se vería como faltante.
        """
        archivo = servicios.importar(
            self.m.inquilino,
            archivo_csv(["09/11/2026 00:10,9400822,226255771,7,TEL100,30,3313402330,0"]),
            nombre="x.csv",
        )
        fila = archivo.renglones_detalle.get()
        self.assertEqual(fila.fecha.month, 11)
        self.assertEqual(fila.fecha.day, 9)

    def test_no_se_importa_dos_veces_el_mismo_archivo(self):
        filas = [renglon(1, "100")]
        servicios.importar(self.m.inquilino, archivo_csv(filas), nombre="dia.csv")
        with self.assertRaises(servicios.ErrorArchivo):
            servicios.importar(self.m.inquilino, archivo_csv(filas), nombre="otro.csv")

    def test_un_archivo_sin_columnas_clave_se_rechaza(self):
        with self.assertRaises(servicios.ErrorArchivo):
            servicios.importar(
                self.m.inquilino, "a,b,c\n1,2,3\n", nombre="malo.csv"
            )


class LoQueSeArreglaSolo(Base):
    """
    Capturar o liberar una reserva que ya existe es completar una operación
    empezada, no mover dinero quieto. Por eso sí es automático.
    """

    def test_sin_resolver_que_el_proveedor_si_cobro_se_captura(self):
        reserva, cierre = self.vender(emulador_de_certificacion("TX-LK-10"))
        self.assertTrue(cierre.sin_resolver)
        antes = services.bolsa_de(self.m.cliente).saldo

        archivo = self.conciliar([renglon(reserva.operacion.invoice_no, "100")])

        reserva.refresh_from_db()
        self.assertEqual(reserva.estado, EstadoReserva.CAPTURADA)
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, antes - D("100"))
        self.assertEqual(archivo.con_diferencia, 0)
        self.assertTrue(archivo.cuadra)

    def test_sin_resolver_que_el_proveedor_rechazo_se_libera(self):
        reserva, _ = self.vender(emulador_de_certificacion("TX-LK-10"))
        antes = services.bolsa_de(self.m.cliente).saldo

        self.conciliar([renglon(reserva.operacion.invoice_no, "100", "51")])

        reserva.refresh_from_db()
        self.assertEqual(reserva.estado, EstadoReserva.LIBERADA)
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, antes)
        self.assertEqual(services.bolsa_de(self.m.cliente).reservado, D("0.0000"))

    def test_sin_resolver_que_no_aparece_en_el_archivo_se_libera(self):
        """
        Se libera porque el archivo que cubre su fecha no la trae, no porque
        la reserva sea vieja. El tiempo no es evidencia; el archivo sí.
        """
        reserva, _ = self.vender(emulador_de_certificacion("TX-LK-10"))
        otra, _ = self.vender()  # para que el archivo tenga ventana de fechas

        self.conciliar([renglon(otra.operacion.invoice_no, "100")])

        reserva.refresh_from_db()
        self.assertEqual(reserva.estado, EstadoReserva.LIBERADA)

    def test_con_sin_resolver_desactivado_no_toca_nada(self):
        reserva, _ = self.vender(emulador_de_certificacion("TX-LK-10"))

        archivo = self.conciliar(
            [renglon(reserva.operacion.invoice_no, "100")], resolver=False
        )

        reserva.refresh_from_db()
        self.assertEqual(reserva.estado, EstadoReserva.ACTIVA)
        self.assertEqual(
            archivo.diferencias.get().tipo, TipoDiferencia.COBRO_NO_REGISTRADO
        )


class LoQueNoSeArreglaSolo(Base):
    """
    Lo importante de este archivo. Mover dinero que ya estaba quieto lleva
    motivo, autor y una persona que decida.
    """

    def test_cobramos_y_el_proveedor_no_queda_marcado_sin_tocarse(self):
        reserva, cierre = self.vender()
        self.assertTrue(cierre.cobrada)
        saldo = services.bolsa_de(self.m.cliente).saldo

        archivo = self.conciliar([renglon(reserva.operacion.invoice_no, "100", "51")])

        diferencia = archivo.diferencias.get()
        self.assertEqual(diferencia.tipo, TipoDiferencia.REGISTRO_SIN_COBRO)
        self.assertEqual(diferencia.estado, EstadoDiferencia.ABIERTA)
        # El saldo del tendero no se movió solo.
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, saldo)

    def test_esa_diferencia_se_corrige_a_mano_con_autor_y_folio(self):
        reserva, _ = self.vender()
        archivo = self.conciliar([renglon(reserva.operacion.invoice_no, "100", "51")])
        diferencia = archivo.diferencias.get()
        saldo = services.bolsa_de(self.m.cliente).saldo

        operacion = servicios.aplicar_devolucion(diferencia, self.m.usuario_admin)

        diferencia.refresh_from_db()
        self.assertEqual(diferencia.estado, EstadoDiferencia.RESUELTA)
        self.assertEqual(diferencia.resuelto_por, "Brenda Lemus")
        self.assertEqual(diferencia.operacion_correccion, operacion)
        self.assertTrue(operacion.folio.startswith("AJU-"))
        self.assertEqual(operacion.motivo, "conciliacion")
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, saldo + D("100"))

    def test_no_se_puede_corregir_dos_veces(self):
        reserva, _ = self.vender()
        archivo = self.conciliar([renglon(reserva.operacion.invoice_no, "100", "51")])
        diferencia = archivo.diferencias.get()

        servicios.aplicar_devolucion(diferencia, self.m.usuario_admin)
        with self.assertRaises(servicios.ErrorArchivo):
            servicios.aplicar_devolucion(diferencia, self.m.usuario_admin)

    def test_un_monto_distinto_se_marca_y_no_se_ajusta(self):
        reserva, _ = self.vender()
        saldo = services.bolsa_de(self.m.cliente).saldo

        archivo = self.conciliar([renglon(reserva.operacion.invoice_no, "150")])

        diferencia = archivo.diferencias.get()
        self.assertEqual(diferencia.tipo, TipoDiferencia.MONTO_DISTINTO)
        self.assertEqual(diferencia.monto_proveedor, D("150.0000"))
        self.assertEqual(diferencia.monto_nuestro, D("100.0000"))
        self.assertEqual(services.bolsa_de(self.m.cliente).saldo, saldo)

    def test_un_cobro_sin_registro_es_la_alarma_mas_grande(self):
        """No es un descuadre: es un hueco. Nos cobraron algo que no existe."""
        self.vender()
        archivo = self.conciliar([renglon(77777, "100")])

        diferencia = archivo.diferencias.get(tipo=TipoDiferencia.FALTA_EN_LEDGER)
        self.assertEqual(diferencia.monto_proveedor, D("100.0000"))
        self.assertIn("no existe en el ledger", diferencia.detalle)

    def test_cobramos_algo_que_el_proveedor_no_reporta(self):
        reserva, _ = self.vender()
        otra, _ = self.vender()

        archivo = self.conciliar([renglon(otra.operacion.invoice_no, "100")])

        diferencia = archivo.diferencias.get(tipo=TipoDiferencia.FALTA_EN_ARCHIVO)
        self.assertEqual(diferencia.invoice_no, reserva.operacion.invoice_no)
        self.assertEqual(diferencia.monto_nuestro, D("100.0000"))


class VentanaDeFechas(Base):
    def test_el_cruce_respeta_la_ventana_del_archivo(self):
        """
        El Invoice-No da la vuelta en 99,999. Cruzar por número a secas
        emparejaría una venta de hoy con una de la vuelta anterior.
        """
        reserva, _ = self.vender()

        # Un archivo de hace dos meses con el mismo número de invoice.
        viejo = timezone.localtime() - timedelta(days=60)
        archivo = self.conciliar(
            [renglon(reserva.operacion.invoice_no, "100", cuando=viejo)]
        )

        # No lo emparejó con nuestra venta de hoy: lo reportó como faltante.
        self.assertEqual(
            archivo.diferencias.get().tipo, TipoDiferencia.FALTA_EN_LEDGER
        )


class CorteDelDia(Base):
    def test_cuenta_los_tres_desenlaces_por_separado(self):
        self.vender()
        self.vender()
        self.vender(Emulador(aprobar=False, response_code="51"))
        self.vender(emulador_de_certificacion("TX-LK-10"))

        datos = reportes.corte(self.m.inquilino)

        self.assertEqual(datos["intentos"], 4)
        self.assertEqual(datos["aprobadas"], 2)
        self.assertEqual(datos["rechazadas"], 1)
        self.assertEqual(datos["sin_resolver"], 1)
        self.assertEqual(datos["vendido"], D("200.0000"))
        self.assertEqual(datos["apartado_sin_resolver"], D("100.0000"))
        self.assertEqual(datos["tasa_aprobacion"], 50.0)

    def test_el_corte_coincide_con_el_ledger(self):
        """
        Si estos dos números no coinciden hay una captura sin asiento o un
        asiento huérfano, y eso se tiene que ver antes de firmar el cierre.
        """
        self.vender()
        self.vender()
        self.vender(Emulador(aprobar=False, response_code="51"))

        datos = reportes.corte(self.m.inquilino)
        self.assertEqual(
            datos["vendido"], reportes.ventas_del_ledger(self.m.inquilino)
        )

    def test_el_desglose_por_caja_ignora_las_que_no_vendieron(self):
        self.vender()
        filas = reportes.corte_por_punto_venta(self.m.inquilino)

        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["punto_venta"], "Caja Mostrador")
        self.assertEqual(filas[0]["cliente"], "Abarrotes La Ermita")


class ElLedgerSigueCuadrando(Base):
    def test_despues_de_conciliar_todo_cuadra(self):
        a, _ = self.vender()
        b, _ = self.vender(emulador_de_certificacion("TX-LK-10"))
        c, _ = self.vender(Emulador(aprobar=False, response_code="51"))

        self.conciliar([
            renglon(a.operacion.invoice_no, "100"),
            renglon(b.operacion.invoice_no, "100"),
            renglon(c.operacion.invoice_no, "100", "51"),
        ])

        ok, problemas = services.cuadra(self.m.inquilino)
        self.assertTrue(ok, problemas)
