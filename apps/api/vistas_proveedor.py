"""
Saldo de EPS en Emida, solo para el Super Admin.

GET  /api/proveedor/saldo  → lo último guardado y el total asignado en la red.
                             No llama a Emida: abrir el tablero no gasta
                             llamadas ni depende de que el proveedor conteste.
POST /api/proveedor/saldo  → consulta a Emida ahora, guarda el resultado y
                             devuelve lo mismo que el GET.

Ver apps/proveedor/saldo.py y docs/DECISIONES.md, decisión 23.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.proveedor import saldo as saldo_proveedor

from .permisos_http import Operativo
from .proveedor import obtener_cliente
from .vistas import manejar


@api_view(["GET", "POST"])
@permission_classes([Operativo])
@manejar
def saldo_emida(request):
    if request.method == "POST":
        saldo_proveedor.consultar(request.user, obtener_cliente())
    return Response(saldo_proveedor.resumen(request.user))
