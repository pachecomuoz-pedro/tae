"""
Rutas de la API. Una línea por endpoint, sin router mágico.

Todo cuelga de /api/ (ver config/urls.py). Las vistas de operación están en
vistas.py; las de la red (altas, aprobaciones, restablecimiento) en
vistas_red.py.
"""
from django.urls import path

from . import vistas_reportes, vistas, vistas_proveedor, vistas_red

urlpatterns = [
    # Sesión y contraseña
    path("auth/entrar", vistas.entrar),
    path("auth/salir", vistas.salir),
    path("auth/cambiar", vistas.cambiar_contrasena),
    path("auth/restablecer", vistas.pedir_restablecimiento),
    path("auth/restablecer/confirmar", vistas.confirmar_restablecimiento),
    path("yo", vistas.yo),

    # Catálogo
    path("catalogo", vistas.listar_catalogo),
    path("catalogo/codigo/<str:codigo>", vistas.buscar_por_codigo),

    # Venta y saldo
    path("venta", vistas.vender),
    path("saldo", vistas.mi_saldo),

    # Cajas
    path("puntos-venta", vistas.listar_puntos_venta),
    path("puntos-venta/alta", vistas_red.alta_punto_venta),
    path("puntos-venta/limite", vistas.asignar_limite),

    # Clientes y dinero
    path("clientes", vistas.listar_clientes),
    path("clientes/alta", vistas_red.alta_cliente),
    path("clientes/fondeo", vistas_red.fondear_cliente),
    path("clientes/suspension", vistas.suspender),
    path("traspasos", vistas.traspasar),
    path("retiros", vistas.retirar),
    path("retiros/eps", vistas_red.retirar_a_eps),
    path("retenciones", vistas_red.retenciones),
    path("retenciones/cancelar", vistas_red.cancelar_retencion),

    # La red: administradores, aprobaciones y restablecimiento por el superior
    path("administradores/alta", vistas_red.alta_administrador),
    path("administradores/fondeo", vistas_red.fondear_administrador),
    path("red", vistas_red.red),
    path("aprobaciones", vistas_red.aprobaciones),
    path("aprobaciones/aprobar", vistas_red.aprobar),
    path("aprobaciones/rechazar", vistas_red.rechazar),
    path("usuarios/restablecer", vistas_red.restablecer_usuario),
    path("usuarios/acceso", vistas_red.acceso),
    path("red/suspension", vistas_red.suspension),
    path("red/margen", vistas_red.cambiar_margen),
    path("transacciones", vistas_reportes.transacciones),
    path("transacciones/exportar", vistas_reportes.exportar_transacciones),
    path("transacciones/<str:folio>", vistas_reportes.transaccion),
    path("movimientos", vistas_reportes.movimientos),
    path("movimientos/exportar", vistas_reportes.exportar_movimientos),

    # Saldo de EPS en Emida (solo Super Admin)
    path("proveedor/saldo", vistas_proveedor.saldo_emida),
]
