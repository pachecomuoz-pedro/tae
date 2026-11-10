"""
Diagnóstico de combinaciones contra Emida UAT: qué login, qué prefijo y qué
encabezados funcionan.

Prueba solo, en un minuto, lo que a mano en Postman toma una tarde:
  - login en la raíz y con /integratorapi, mandando "email" y "username";
  - con cada token que se obtenga, carriers y saldo, con y sin prefijo;
  - el saldo con y sin Merchant-Id.
Al final dice qué poner en el .env.

Solo consulta: no vende nada. Lee las credenciales del .env (el contenedor
ya las tiene) y nunca imprime el token ni la contraseña.

En Windows (PowerShell), con Docker corriendo:

    docker compose exec api python diagnostico_emida.py

Copia TODA la salida y compártela: no lleva datos secretos.
"""
import json
import os
import sys

import requests

TIMEOUT = 15
PREFIJO = "/integratorapi"


def env(nombre):
    return (os.environ.get(nombre) or "").strip()


def corto(respuesta, n=220):
    texto = respuesta.text.strip().replace("\n", " ")
    return (texto[:n] + "…") if len(texto) > n else texto or "(vacío)"


def desenvolver(datos):
    if isinstance(datos, dict) and "data" in datos and ("success" in datos or "code" in datos):
        return datos["data"]
    return datos


def buscar_token(datos):
    datos = desenvolver(datos)
    if isinstance(datos, dict):
        for campo in ("token", "accessToken", "access_token", "jwt"):
            if datos.get(campo):
                return str(datos[campo])
    return None


def linea(etiqueta, r=None, error=None):
    if error is not None:
        print(f"  {etiqueta:<58} ERROR  {error}")
        return
    extra = []
    for h in ("Server", "WWW-Authenticate", "Content-Type"):
        if r.headers.get(h):
            extra.append(f"{h}={r.headers[h][:60]}")
    print(f"  {etiqueta:<58} HTTP {r.status_code}  {corto(r)}")
    if extra:
        print(f"  {'':<58}   {'  '.join(extra)}")


def main():
    base = env("PROVEEDOR_BASE_URL").rstrip("/")
    usuario, contrasena = env("PROVEEDOR_USUARIO"), env("PROVEEDOR_CONTRASENA")
    cab_base = {
        "Site-Id": env("PROVEEDOR_SITE_ID"),
        "Clerk-Id": env("PROVEEDOR_CLERK_ID"),
        "Language": env("PROVEEDOR_LANGUAGE") or "2",
        "Content-Type": "application/json",
    }
    merchant = env("PROVEEDOR_MERCHANT_ID")

    print("Configuración leída del .env")
    print(f"  Base:        {base or '(VACÍA)'}")
    print(f"  Usuario:     {'configurado' if usuario else 'FALTA'}   Contraseña: {'configurada' if contrasena else 'FALTA'}")
    print(f"  Site-Id:     {cab_base['Site-Id'] or 'FALTA'}   Clerk-Id: {cab_base['Clerk-Id'] or 'FALTA'}   "
          f"Merchant-Id: {merchant or 'FALTA'}")
    if not (base and usuario and contrasena):
        print("\nFaltan datos en el .env: no se puede seguir.")
        return 1

    bases = [base]
    if ":10002" in base:
        bases.append(base.replace(":10002", ""))  # el 443 respondió en las pruebas

    # 1. Login: dónde y con qué nombre de campo
    print("\n1. Login")
    tokens = {}  # etiqueta -> (base, token)
    for b in bases:
        for pref in ("", PREFIJO):
            url = f"{b}{pref}/api/v1/auth/login"
            for campo in ("email", "username"):
                etiqueta = f"POST {url.replace(b, '') or '/'} [{campo}]  ({b})"
                try:
                    r = requests.post(url, json={campo: usuario, "password": contrasena}, timeout=TIMEOUT)
                except requests.RequestException as e:
                    linea(etiqueta, error=type(e).__name__)
                    break  # sin red no tiene caso probar el otro campo
                token = buscar_token(r.json() if "json" in r.headers.get("Content-Type", "") else {}) \
                    if r.status_code == 200 else None
                # No se imprime el cuerpo de un login exitoso: trae el token.
                if token:
                    print(f"  {etiqueta:<58} HTTP 200  token obtenido")
                    tokens[f"login {pref or 'raíz'} [{campo}] {b}"] = (b, token, pref, campo)
                    break
                linea(etiqueta, r)

    if not tokens:
        print("\nNingún login dio token. Revisa usuario y contraseña, o si la red llega (Test-NetConnection).")
        return 1

    # 2. Operaciones con cada token
    print("\n2. Operaciones (cada token, con y sin prefijo)")
    exitos = []
    for nombre, (b, token, pref_login, campo) in tokens.items():
        print(f"\n  Con el token de: {nombre}")
        for pref in ("", PREFIJO):
            pruebas = [
                ("carriers", "/api/v1/terminal/carriers", {}),
                ("saldo + Merchant-Id", "/api/v1/merchants/account/balance", {"Merchant-Id": merchant} if merchant else {}),
                ("saldo sin Merchant-Id", "/api/v1/merchants/account/balance", {}),
            ]
            for etiqueta, ruta, extra in pruebas:
                cab = dict(cab_base, Authorization=f"Bearer {token}", **extra)
                url = f"{b}{pref}{ruta}"
                try:
                    r = requests.get(url, headers=cab, timeout=TIMEOUT)
                except requests.RequestException as e:
                    linea(f"GET {pref}{ruta} ({etiqueta})", error=type(e).__name__)
                    continue
                linea(f"GET {pref}{ruta} ({etiqueta})", r)
                if r.status_code == 200:
                    exitos.append((etiqueta, b, pref_login, campo, pref, r))

    # 3. Conclusión
    print("\n3. Conclusión")
    if not exitos:
        print("  Ninguna operación respondió 200. Comparte esta salida: el código HTTP y las")
        print("  cabeceras (WWW-Authenticate, Server) dicen si es el token, un permiso o la red.")
        return 1
    for etiqueta, b, pref_login, campo, pref, r in exitos:
        print(f"  OK  {etiqueta}: base {b}, login {pref_login or 'raíz'} con '{campo}', operaciones con prefijo '{pref or 'ninguno'}'")
    _, b, pref_login, campo, pref, _ = exitos[0]
    print("\n  Valores para el .env:")
    print(f"    PROVEEDOR_BASE_URL={b}")
    print(f"    PROVEEDOR_PREFIJO_OPERACIONES={pref}")
    print(f"    PROVEEDOR_PREFIJO_LOGIN={pref_login}")
    print(f"    PROVEEDOR_CAMPO_USUARIO={campo}")
    saldo = [x for x in exitos if x[0].startswith("saldo")]
    if saldo:
        try:
            print("\n  Respuesta del saldo (para confirmar el nombre del campo):")
            print("   ", json.dumps(saldo[0][5].json(), ensure_ascii=False)[:400])
        except ValueError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
