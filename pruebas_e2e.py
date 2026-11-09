"""
Punta a punta con navegador real: Django + frontend construido + emulador.

Es la única prueba donde todas las capas corren juntas. Requisitos:
    pip install playwright && playwright install chromium
    cd web && npm run build          # genera web/dist
    python3 pruebas_e2e.py

Usa la base de datos configurada en el entorno (siembra datos de demo): no
la corras contra producción.
"""
import os, subprocess, sys, threading, time, json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ["DJANGO_DEBUG"] = "true"

import django
django.setup()
from django.core.management import call_command
import sembrar_demo

sembrar_demo.sembrar()
emulador = sembrar_demo.emular(responde_en=0.2)

# Django sirve el frontend construido en la raíz, igual que hará Nginx.
from django.urls import re_path
from django.views.generic import TemplateView
from django.views.static import serve
from django.conf import settings

DIST = str(RAIZ / "web" / "dist")
settings.TEMPLATES[0]["DIRS"] = [DIST]
import config.urls as u
# Vite emite las rutas de sus archivos como /assets/..., así que hay que
# servir esa carpeta tal cual. En producción esto lo hace Nginx.
u.urlpatterns = u.urlpatterns + [
    re_path(r"^assets/(?P<path>.*)$", serve, {"document_root": DIST + "/assets"}),
    re_path(r"^$", TemplateView.as_view(template_name="index.html")),
]

from django.core.servers.basehttp import run, WSGIServer
from django.core.handlers.wsgi import WSGIHandler

hilo = threading.Thread(
    target=lambda: run("127.0.0.1", 8077, WSGIHandler(), server_cls=WSGIServer),
    daemon=True,
)
hilo.start()
time.sleep(2)

from playwright.sync_api import sync_playwright

fallos = []
def revisar(condicion, que):
    print(("  ok   " if condicion else "  FALLA ") + que)
    if not condicion:
        fallos.append(que)

with sync_playwright() as p:
    navegador = p.chromium.launch()
    pagina = navegador.new_page(viewport={"width": 1280, "height": 860})
    errores_consola = []
    pagina.on("pageerror", lambda e: errores_consola.append(str(e)))

    pagina.goto("http://127.0.0.1:8077/", wait_until="networkidle")

    print("1. entrar")
    pagina.fill("[data-prueba=correo]", "luis@laermita.mx")
    pagina.fill("[data-prueba=password]", sembrar_demo.CLAVE)
    pagina.click("button[type=submit]")
    pagina.wait_for_selector("[data-sku]", timeout=15000)
    revisar("Luis Canul" in pagina.content(), "la sesión reconoce al cajero")
    revisar("Caja Mostrador" in pagina.content(), "ve su caja")

    print("2. catálogo")
    skus = pagina.eval_on_selector_all("[data-sku]", "ns => ns.map(n => n.dataset.sku)")
    revisar(len(skus) == 9, f"nueve productos de tiempo aire (vinieron {len(skus)})")

    print("3. venta con número mal confirmado")
    pagina.click("[data-sku='TEL-TAE-100']")
    pagina.fill("[data-prueba=numero]", "9991234567")
    pagina.fill("[data-prueba=confirmacion]", "9991234500")
    revisar(pagina.is_disabled("[data-prueba=vender]"), "no deja vender si no coinciden")

    print("4. venta buena")
    pagina.fill("[data-prueba=confirmacion]", "9991234567")
    revisar(pagina.is_enabled("[data-prueba=vender]"), "ya deja vender")
    pagina.click("[data-prueba=vender]")
    pagina.wait_for_selector("[data-prueba=cierre]", timeout=20000)
    desenlace = pagina.get_attribute("[data-prueba=cierre]", "data-desenlace")
    folio = pagina.inner_text("[data-prueba=folio]")
    revisar(desenlace == "aprobada", f"desenlace aprobada (fue {desenlace})")
    revisar(folio.startswith("VTA-2026-"), f"folio con formato ({folio})")
    revisar(emulador.cobros() == 1, f"el proveedor cobró una vez ({emulador.cobros()})")

    print("5. el saldo bajó en la pantalla")
    pagina.click("[data-prueba=otra-venta]")
    pagina.wait_for_selector("[data-sku]")
    texto = pagina.inner_text("aside")
    revisar("7,900" in texto.replace(" ", ""), f"la caja muestra 7,900 ({texto[:120]!r})")

    print("6. sin resolver se ve distinto")
    import apps.api.proveedor as puente
    from apps.proveedor.cliente import ClienteProveedor, Config
    from apps.proveedor.emulador import emulador_de_certificacion
    import apps.proveedor.venta as vmod
    mudo = emulador_de_certificacion("TX-LK-10")
    cfg = Config(base_url="https://emulado.local", usuario="u", contrasena="x",
                 site_id="9400822", clerk_id="CESAR45", timeout_venta=1,
                 timeout_lookup=1, lookup_desde_envio=0, espera_entre_lookups=0,
                 max_lookups=2)
    puente.usar_fabrica(lambda: ClienteProveedor(cfg, sesion=mudo))
    pagina.click("[data-sku='TEL-TAE-50']")
    pagina.fill("[data-prueba=numero]", "9995550000")
    pagina.fill("[data-prueba=confirmacion]", "9995550000")
    pagina.click("[data-prueba=vender]")
    pagina.wait_for_selector("[data-prueba=cierre]", timeout=30000)
    d2 = pagina.get_attribute("[data-prueba=cierre]", "data-desenlace")
    cuerpo = pagina.inner_text("[data-prueba=cierre]")
    revisar(d2 == "sin_resolver", f"desenlace sin_resolver (fue {d2})")
    revisar("NO vuelvas a vender" in cuerpo, "le dice al cajero que no reintente")
    revisar("aplicada" not in cuerpo.lower(), "no dice que se aplicó")

    print("7. la jerarquía completa, de arriba abajo")
    # Volvemos a poner el proveedor que sí responde.
    puente.usar_fabrica(lambda: ClienteProveedor(
        Config(base_url="https://emulado.local", usuario="u", contrasena="x",
               site_id="9400822", clerk_id="CESAR45"),
        sesion=sembrar_demo.Emulador(responde_en=0.2)))

    def entrar(correo):
        pagina.click("[data-prueba=salir]")
        pagina.wait_for_selector("[data-prueba=correo]", timeout=10000)
        pagina.fill("[data-prueba=correo]", correo)
        pagina.fill("[data-prueba=password]", sembrar_demo.CLAVE)
        pagina.click("button[type=submit]")

    # 7a. El administrador le traspasa saldo al cliente.
    entrar("brenda@eps.mx")
    pagina.wait_for_selector("[data-cliente]", timeout=15000)
    revisar(pagina.is_visible("[data-pantalla=traspaso]"), "el admin ve sus dos pantallas")
    pagina.click("[data-cliente='Abarrotes La Ermita']")
    pagina.fill("[data-prueba=monto-traspaso]", "1000")
    cuerpo = pagina.inner_text("aside")
    revisar("1,040" in cuerpo.replace(" ", ""), f"enseña el margen aplicado ({cuerpo[:90]!r})")
    pagina.click("[data-prueba=traspasar]")
    pagina.wait_for_selector("[data-prueba=folio-traspaso]", timeout=15000)
    folio_trs = pagina.inner_text("[data-prueba=folio-traspaso]")
    revisar("TRS-2026-" in folio_trs, f"folio de traspaso ({folio_trs})")

    # 7b. El retiro muestra que el saldo no es lo retirable.
    pagina.click("[data-pantalla=retiro]")
    pagina.wait_for_selector("[data-cliente]", timeout=10000)
    pagina.click("[data-cliente='Abarrotes La Ermita']")
    desglose = pagina.inner_text("aside")
    revisar("Retirable" in desglose, "el retiro desglosa de dónde sale el dinero")
    pagina.fill("[data-prueba=monto-retiro]", "999999")
    revisar(pagina.is_disabled("[data-prueba=retirar]"), "no deja retirar de más")

    # 7c. El cliente reparte a su caja.
    entrar("jorge@laermita.mx")
    pagina.wait_for_selector("[data-caja]", timeout=15000)
    pagina.click("[data-caja='Caja Mostrador']")
    pagina.fill("[data-prueba=monto-limite]", "999999")
    revisar(pagina.is_disabled("[data-prueba=asignar]"), "no deja asignar por encima de su bolsa")
    pagina.fill("[data-prueba=monto-limite]", "9000")
    pagina.click("[data-prueba=asignar]")
    pagina.wait_for_selector("[data-prueba=asignado]", timeout=15000)
    revisar("9,000" in pagina.inner_text("[data-prueba=asignado]").replace(" ", ""),
            "la caja quedó en 9,000")

    # 7d. El cajero vende con ese saldo.
    entrar("luis@laermita.mx")
    pagina.wait_for_selector("[data-sku]", timeout=15000)
    pagina.click("[data-sku='TEL-TAE-200']")
    pagina.fill("[data-prueba=numero]", "9991112233")
    pagina.fill("[data-prueba=confirmacion]", "9991112233")
    pagina.click("[data-prueba=vender]")
    pagina.wait_for_selector("[data-prueba=cierre]", timeout=20000)
    revisar(pagina.get_attribute("[data-prueba=cierre]", "data-desenlace") == "aprobada",
            "la venta con el saldo recién repartido cierra bien")

    revisar(not errores_consola, f"sin errores de consola ({errores_consola[:1]})")
    pagina.click("[data-prueba=otra-venta]")
    pagina.wait_for_selector("[data-sku]")
    pagina.screenshot(path=str(RAIZ / "web" / "pantalla-venta.png"), full_page=True)
    entrar("jorge@laermita.mx")
    pagina.wait_for_selector("[data-caja]", timeout=15000)
    pagina.click("[data-caja='Caja Mostrador']")
    pagina.screenshot(path=str(RAIZ / "web" / "pantalla-cliente.png"), full_page=True)
    entrar("brenda@eps.mx")
    pagina.wait_for_selector("[data-cliente]", timeout=15000)
    pagina.click("[data-cliente='Abarrotes La Ermita']")
    pagina.fill("[data-prueba=monto-traspaso]", "5000")
    pagina.screenshot(path=str(RAIZ / "web" / "pantalla-admin.png"), full_page=True)
    navegador.close()

print()
if fallos:
    print(f"FALLARON {len(fallos)}:"); [print("  -", f) for f in fallos]; sys.exit(1)
print("Punta a punta: todo pasó")
