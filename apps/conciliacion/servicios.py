"""
Importar el archivo del proveedor y cruzarlo contra el ledger.

**Qué se arregla solo y qué no.** La línea es ésta: una reserva que ya
existe se puede capturar o liberar automáticamente, porque reservar es
justamente apartar dinero a la espera de una de esas dos cosas, y el archivo
es la respuesta que faltaba. Cualquier otra corrección mueve dinero que ya
estaba quieto, y eso lleva motivo, autor y una persona que decida.

Por eso la conciliación cierra sola los SIN_RESOLVER y deja marcadas, sin
tocarlas, las diferencias donde alguien ya cobró o ya pagó de más.

Decisión que gobierna este archivo: 13 (la conciliación solo completa lo empezado).
"""
import csv
import hashlib
import io
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.saldo import services
from apps.saldo.models import EstadoReserva, Operacion, Reserva, TipoOperacion

from .modelos import (
    ArchivoConciliacion,
    Diferencia,
    EstadoDiferencia,
    RenglonArchivo,
    TipoDiferencia,
)

log = logging.getLogger("tae.conciliacion")

CERO = Decimal("0.0000")

# El archivo trae la fecha en día/mes/año. Si alguien la leyera como
# mes/día/año, el 09/11 sería septiembre y la ventana del cruce quedaría
# corrida tres meses: todo se vería como falta en el archivo.
FORMATOS_FECHA = ["%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"]

# Nombres de columna tolerantes: el proveedor cambió encabezados antes.
COLUMNAS = {
    "fecha": ["date_time", "datetime", "fecha"],
    "terminal": ["terminal_id", "terminalid", "site_id"],
    "transaccion": ["responsetransaction_id", "transaction_id", "transactionid"],
    "invoice": ["invoice_id", "invoice_no", "invoiceno"],
    "producto": ["product_id", "productid"],
    "monto": ["amount_id", "amount", "monto"],
    "cuenta": ["account id", "account_id", "accountid"],
    "codigo": ["responsecode", "response_code"],
}


class ErrorArchivo(Exception):
    pass


def _norm(texto):
    return str(texto or "").strip().lower().replace("﻿", "")


def _mapa_columnas(encabezados):
    normalizados = {_norm(h): i for i, h in enumerate(encabezados)}
    mapa = {}
    for campo, alias in COLUMNAS.items():
        for a in alias:
            if a in normalizados:
                mapa[campo] = normalizados[a]
                break
    faltan = [c for c in ("invoice", "monto", "codigo") if c not in mapa]
    if faltan:
        raise ErrorArchivo(
            f"Al archivo le faltan columnas imprescindibles: {', '.join(faltan)}"
        )
    return mapa


def _fecha(texto):
    texto = str(texto or "").strip()
    for formato in FORMATOS_FECHA:
        try:
            return timezone.make_aware(datetime.strptime(texto, formato))
        except ValueError:
            continue
    return None


def _decimal(texto):
    try:
        return Decimal(str(texto).strip().replace(",", "")).quantize(Decimal("0.0001"))
    except Exception:
        return CERO


@transaction.atomic
def importar(inquilino, contenido, *, nombre="archivo.csv"):
    """
    Lee el archivo y guarda sus renglones. No concilia todavía.

    Importar dos veces el mismo archivo se rechaza por huella: un archivo
    procesado dos veces duplicaría las diferencias y haría ver descuadres
    donde no los hay.
    """
    if isinstance(contenido, bytes):
        contenido = contenido.decode("utf-8-sig", errors="replace")

    huella = hashlib.sha256(contenido.encode()).hexdigest()
    if ArchivoConciliacion.objects.filter(huella=huella).exists():
        raise ErrorArchivo(f"Ese archivo ya se había importado ({nombre})")

    lector = csv.reader(io.StringIO(contenido))
    try:
        encabezados = next(lector)
    except StopIteration:
        raise ErrorArchivo("El archivo viene vacío")

    mapa = _mapa_columnas(encabezados)
    archivo = ArchivoConciliacion.objects.create(
        inquilino=inquilino, nombre=nombre, huella=huella
    )

    renglones, fechas, total = [], [], CERO
    for fila in lector:
        if not any(str(c).strip() for c in fila):
            continue

        def col(campo):
            i = mapa.get(campo)
            return fila[i] if i is not None and i < len(fila) else ""

        fecha = _fecha(col("fecha"))
        monto = _decimal(col("monto"))
        codigo = str(col("codigo")).strip()
        try:
            invoice = int(str(col("invoice")).strip())
        except (TypeError, ValueError):
            invoice = None

        if fecha:
            fechas.append(fecha)
        if codigo in ("0", "00"):
            total += monto

        renglones.append(
            RenglonArchivo(
                archivo=archivo, fecha=fecha,
                terminal_id=str(col("terminal")).strip(),
                transaction_id=str(col("transaccion")).strip(),
                invoice_no=invoice,
                product_id=str(col("producto")).strip(),
                monto=monto,
                cuenta=str(col("cuenta")).strip(),
                response_code=codigo,
                crudo=",".join(str(c) for c in fila)[:500],
            )
        )

    if not renglones:
        raise ErrorArchivo("El archivo no trae renglones")

    RenglonArchivo.objects.bulk_create(renglones)
    archivo.renglones = len(renglones)
    archivo.importe_proveedor = total
    if fechas:
        # Un día de margen a cada lado: una venta de las 23:59 puede
        # aparecer en el archivo del día siguiente.
        archivo.desde = min(fechas) - timedelta(days=1)
        archivo.hasta = max(fechas) + timedelta(days=1)
    archivo.save()
    return archivo


def _ventana(archivo):
    if archivo.desde and archivo.hasta:
        return {"creado__gte": archivo.desde, "creado__lte": archivo.hasta}
    return {}


@transaction.atomic
def conciliar(archivo, *, resolver=True, autor="conciliación automática"):
    """
    Cruza el archivo contra el ledger y clasifica cada diferencia.

    El cruce va por Invoice-No **dentro de la ventana de fechas del
    archivo**, nunca por número a secas: el contador da la vuelta en 99,999
    y el mismo número vuelve a salir a los pocos días.
    """
    inquilino = archivo.inquilino
    ventana = _ventana(archivo)

    ventas = {
        op.invoice_no: op
        for op in Operacion.objects.filter(
            inquilino=inquilino, tipo=TipoOperacion.VENTA,
            invoice_no__isnull=False, **ventana,
        ).select_related("reserva")
    }

    diferencias, vistos, conciliados = [], set(), 0

    # --- lo que viene en el archivo --------------------------------------
    for renglon in archivo.renglones_detalle.all():
        operacion = ventas.get(renglon.invoice_no)
        if operacion is not None:
            renglon.operacion = operacion
            renglon.save(update_fields=["operacion"])
            vistos.add(operacion.invoice_no)

        if operacion is None:
            if renglon.aprobada_por_proveedor:
                # Nos cobraron algo de lo que no tenemos registro. Es lo más
                # alarmante del archivo: no es un descuadre, es un hueco.
                diferencias.append(
                    Diferencia(
                        archivo=archivo, tipo=TipoDiferencia.FALTA_EN_LEDGER,
                        renglon=renglon, invoice_no=renglon.invoice_no,
                        monto_proveedor=renglon.monto,
                        detalle="El proveedor cobró una venta que no existe en el ledger",
                    )
                )
            continue

        reserva = getattr(operacion, "reserva", None)
        cobrada_por_nosotros = reserva is not None and reserva.estado == EstadoReserva.CAPTURADA
        sin_resolver = reserva is not None and reserva.estado == EstadoReserva.ACTIVA
        monto_nuestro = reserva.monto if reserva else CERO

        if renglon.aprobada_por_proveedor:
            if cobrada_por_nosotros:
                if renglon.monto != monto_nuestro:
                    diferencias.append(
                        Diferencia(
                            archivo=archivo, tipo=TipoDiferencia.MONTO_DISTINTO,
                            renglon=renglon, operacion=operacion,
                            invoice_no=renglon.invoice_no,
                            monto_proveedor=renglon.monto, monto_nuestro=monto_nuestro,
                            detalle="Cobrado por los dos, con montos distintos",
                        )
                    )
                else:
                    conciliados += 1
                continue

            # El proveedor cobró y nosotros no. Si la venta quedó sin
            # resolver, el archivo es exactamente la respuesta que faltaba:
            # se captura, que es completar una operación ya empezada.
            if sin_resolver and resolver:
                services.capturar(reserva)
                conciliados += 1
                log.info("conciliación capturó %s", operacion.folio)
                continue

            diferencias.append(
                Diferencia(
                    archivo=archivo, tipo=TipoDiferencia.COBRO_NO_REGISTRADO,
                    renglon=renglon, operacion=operacion,
                    invoice_no=renglon.invoice_no,
                    monto_proveedor=renglon.monto, monto_nuestro=monto_nuestro,
                    detalle="El proveedor cobró y nosotros no: lo pagó EPS",
                )
            )
            continue

        # El proveedor dice que la rechazó.
        if cobrada_por_nosotros:
            # Le cobramos a un tendero tiempo aire que nunca llegó. Esto NO
            # se arregla solo: devolverle mueve dinero que ya estaba quieto
            # y eso lleva motivo, autor y una persona que decida.
            diferencias.append(
                Diferencia(
                    archivo=archivo, tipo=TipoDiferencia.REGISTRO_SIN_COBRO,
                    renglon=renglon, operacion=operacion,
                    invoice_no=renglon.invoice_no,
                    monto_proveedor=CERO, monto_nuestro=monto_nuestro,
                    detalle="Le cobramos al cliente una venta que el proveedor rechazó",
                )
            )
            continue

        if sin_resolver and resolver:
            # El proveedor la rechazó: liberar es seguro porque él lo dijo.
            services.liberar(reserva)
            conciliados += 1
            continue

        conciliados += 1

    # --- lo que está en el ledger y no en el archivo ---------------------
    for invoice, operacion in ventas.items():
        if invoice in vistos:
            continue
        reserva = getattr(operacion, "reserva", None)
        if reserva is None:
            continue

        if reserva.estado == EstadoReserva.ACTIVA and resolver:
            # Sin resolver y sin renglón en un archivo que cubre su fecha:
            # el proveedor nunca la tuvo. Ahora sí se puede liberar, y no
            # por vieja sino porque el archivo lo dice.
            services.liberar(reserva)
            conciliados += 1
            continue

        if reserva.estado == EstadoReserva.CAPTURADA:
            diferencias.append(
                Diferencia(
                    archivo=archivo, tipo=TipoDiferencia.FALTA_EN_ARCHIVO,
                    operacion=operacion, invoice_no=invoice,
                    monto_proveedor=CERO, monto_nuestro=reserva.monto,
                    detalle="Cobramos una venta que el proveedor no reporta",
                )
            )

    Diferencia.objects.bulk_create(diferencias)
    archivo.conciliados = conciliados
    archivo.con_diferencia = len(diferencias)
    archivo.conciliado = timezone.now()
    archivo.save(update_fields=["conciliados", "con_diferencia", "conciliado"])
    return archivo


@transaction.atomic
def aplicar_devolucion(diferencia, usuario):
    """
    Corrige una diferencia del tipo "le cobramos y el proveedor no".

    Se hace a mano y con autor porque mueve dinero que ya estaba quieto. El
    folio de la devolución queda ligado a la diferencia, para que el día de
    la aclaración se pueda seguir el hilo completo.
    """
    from apps.identidad import acciones
    from apps.saldo.models import Motivo

    if diferencia.tipo != TipoDiferencia.REGISTRO_SIN_COBRO:
        raise ErrorArchivo("Ese tipo de diferencia no se corrige con una devolución")
    if diferencia.estado != EstadoDiferencia.ABIERTA:
        raise ErrorArchivo("Esa diferencia ya se había cerrado")

    cliente = diferencia.operacion.punto_venta.cliente
    operacion = acciones.ajustar(
        usuario, cliente, diferencia.monto_nuestro,
        motivo=Motivo.CONCILIACION, a_favor=True,
        nota=f"Conciliación {diferencia.archivo.nombre}, invoice {diferencia.invoice_no}",
    )
    diferencia.operacion_correccion = operacion
    diferencia.estado = EstadoDiferencia.RESUELTA
    diferencia.resuelto_por = usuario.nombre
    diferencia.resuelto = timezone.now()
    diferencia.save()
    return operacion


def resumen(archivo):
    """Lo que se le enseña a quien revisa el cierre del día."""
    abiertas = archivo.diferencias.filter(estado=EstadoDiferencia.ABIERTA)
    por_tipo = {}
    for d in abiertas:
        entrada = por_tipo.setdefault(
            d.tipo, {"nombre": d.get_tipo_display(), "cuantas": 0, "impacto": CERO}
        )
        entrada["cuantas"] += 1
        entrada["impacto"] += d.impacto
    return {
        "archivo": archivo.nombre,
        "renglones": archivo.renglones,
        "importe_proveedor": archivo.importe_proveedor,
        "conciliados": archivo.conciliados,
        "abiertas": abiertas.count(),
        "cuadra": archivo.cuadra,
        "por_tipo": por_tipo,
    }
