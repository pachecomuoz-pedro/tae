"""
Rutas raíz. Solo la API: el frontend lo sirve nginx desde web/dist.

No hay /admin de Django a propósito: toda alta pasa por la API, donde se
verifican los permisos de la jerarquía y queda registro en la bitácora. El
panel de Django se saltaría las dos cosas.
"""
from django.urls import include, path

urlpatterns = [
    path("api/", include("apps.api.urls")),
]
