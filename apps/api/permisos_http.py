"""
Permisos a nivel HTTP. Solo deciden si la petición entra a la vista.

Las reglas de negocio (quién alcanza a quién, quién aprueba qué) NO viven
aquí: viven en apps/identidad/permisos.py y se verifican dentro de cada
acción. Esto es solo la puerta del edificio, no la de cada oficina.
"""
from rest_framework.permissions import BasePermission


class Operativo(BasePermission):
    """
    Sesión iniciada y contraseña al día.

    Quien entra con una contraseña temporal solo puede ver su perfil, cambiar
    la contraseña y salir. Todo lo demás responde 403 con clave
    `cambiar_contrasena`, y la pantalla lo manda directo al formulario de
    cambio. Así la temporal, que vio otra persona, no sirve para operar.
    Ver docs/DECISIONES.md, decisión 15.
    """

    message = {
        "error": "Antes de continuar tienes que cambiar tu contraseña temporal.",
        "clave": "cambiar_contrasena",
    }

    def has_permission(self, request, view):
        usuario = request.user
        if not (usuario and usuario.is_authenticated):
            # Sin sesión DRF responde "no autenticado" por su cuenta; el
            # mensaje de arriba solo aplica a quien sí entró.
            return False
        return not usuario.debe_cambiar_contrasena
