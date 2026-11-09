"""
Configuración de la plataforma TAE.

Todo lo que cambia entre ambientes vive en variables de entorno.
Nunca hay credenciales en este archivo: ver .env.example.
"""
import os
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None, required=False):
    """Lee una variable de entorno; con required=True falla al arrancar si falta."""
    value = os.environ.get(name, default)
    if required and value in (None, ""):
        raise RuntimeError(f"Falta la variable de entorno obligatoria: {name}")
    return value


def env_bool(name, default=False):
    """Lee una variable de entorno como booleano: 1, true, yes, si, sí."""
    return str(env(name, str(default))).lower() in ("1", "true", "yes", "si", "sí")


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-inseguro-solo-para-local")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "*").split(",") if h]

# Django compara el encabezado Origin de toda petición que escribe contra
# esta lista. Sin ella, el navegador manda Origin del front (5173 en
# desarrollo, el dominio en producción), Django ve que no coincide con el
# host al que llegó la petición y la rechaza con 403.
#
# Esto NO se nota al entrar: la sesión todavía no existe, así que no hay
# comprobación de CSRF. Se nota en el primer POST de alguien ya autenticado
# —cerrar sesión, vender, traspasar— y deja al usuario encerrado: no puede
# salir porque falla el CSRF, y no puede volver a entrar porque la sesión
# vieja activa la comprobación.
CSRF_TRUSTED_ORIGINS = [
    o for o in env(
        "DJANGO_CSRF_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",") if o
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.api",
    "apps.catalogo",
    "apps.conciliacion",
    "apps.proveedor",
    "apps.core",
    "apps.identidad",
    "apps.saldo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# InnoDB no es opcional: el motor de saldo depende de SELECT ... FOR UPDATE
# con bloqueo de fila. MyISAM bloquea la tabla entera y MEMORY no transacciona.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME", "tae"),
        "USER": env("DB_USER", "tae"),
        "PASSWORD": env("DB_PASSWORD", "tae"),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET default_storage_engine=INNODB, "
            "sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'",
        },
        "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
    }
}

# Hash rápido solo al correr pruebas. PBKDF2 con sus iteraciones reales hace
# que una suite que da de alta usuarios tarde minutos, y una suite lenta es
# una suite que la gente deja de correr. En cualquier otro contexto se usa el
# hash normal de Django.
if "test" in sys.argv:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "identidad.Usuario"

LANGUAGE_CODE = "es-mx"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Reglas de negocio configurables.
# Viven aquí y no incrustadas en el código porque Emida exige que los
# parámetros del protocolo sean ajustables sin desplegar, y porque los
# umbrales de autorización los define Scorpion y van a cambiar.
# ---------------------------------------------------------------------------
# Correo saliente. En desarrollo se imprime en la consola para no depender
# de un servidor SMTP; en producción se configura por entorno.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-responder@eps.mx")

# Proveedor. Los valores reales van en .env y NUNCA en el repositorio.
# Los tiempos los indicó el proveedor: consultar a los 40 segundos del envío
# y luego cada 10. Son ajustables sin desplegar porque la certificación
# puede pedir otros.
REST_FRAMEWORK = {
    # Sesión y no token: la PWA vive en el mismo origen que la API, así que
    # la cookie basta y además muere sola al cambiar la contraseña, que es
    # justo lo que se quiere cuando alguien restablece por sospecha.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "UNAUTHENTICATED_USER": None,
}

TAE_PROVEEDOR = {
    "base_url": env("PROVEEDOR_BASE_URL", "https://gw-uat.ejemplo.com:10002"),
    "usuario": env("PROVEEDOR_USUARIO", ""),
    "contrasena": env("PROVEEDOR_CONTRASENA", ""),
    "site_id": env("PROVEEDOR_SITE_ID", ""),
    "clerk_id": env("PROVEEDOR_CLERK_ID", ""),
    "merchant_id": env("PROVEEDOR_MERCHANT_ID", ""),
    "language": env("PROVEEDOR_LANGUAGE", "2"),
    "prefijo_operaciones": env("PROVEEDOR_PREFIJO_OPERACIONES", ""),
    "prefijo_login": env("PROVEEDOR_PREFIJO_LOGIN", ""),
    "campo_usuario": env("PROVEEDOR_CAMPO_USUARIO", "email"),
    # "real" habla con axexo; "simulado" usa el emulador dentro del proceso
    # para desarrollar sin red ni credenciales. Ver decisión 18.
    "modo": env("PROVEEDOR_MODO", "real").strip().lower(),
    "timeout_venta": float(env("PROVEEDOR_TIMEOUT_VENTA", "30")),
    "timeout_lookup": float(env("PROVEEDOR_TIMEOUT_LOOKUP", "15")),
    "lookup_desde_envio": float(env("PROVEEDOR_LOOKUP_DESDE_ENVIO", "40")),
    "espera_entre_lookups": float(env("PROVEEDOR_ESPERA_ENTRE_LOOKUPS", "10")),
    "max_lookups": int(env("PROVEEDOR_MAX_LOOKUPS", "3")),
}

TAE = {
    # Dónde vive la plataforma, para armar la liga del correo.
    "URL_BASE": env("TAE_URL_BASE", "http://localhost:8000"),
    # Vida de una liga de restablecimiento. Corta a propósito: es una llave
    # que viaja por correo y el correo se queda guardado para siempre.
    "RESTABLECIMIENTO_VIGENCIA": timedelta(
        hours=int(env("TAE_RESTABLECIMIENTO_HORAS", "1"))
    ),
    # Tope por operación de un ajuste a favor del cliente. Un ajuste que
    # acredita saldo crea valor dentro de la plataforma: no puede ser ilimitado.
    "AJUSTE_FAVOR_TOPE": env("TAE_AJUSTE_FAVOR_TOPE", "5000.00"),
    # Arriba de este monto, un traspaso exige segundo factor del administrador.
    "TRASPASO_UMBRAL_2FA": env("TAE_TRASPASO_UMBRAL_2FA", "50000.00"),
    # Tope diario de traspaso por administrador. Al rebasarlo se avisa al Super Admin.
    "TRASPASO_TOPE_DIARIO": env("TAE_TRASPASO_TOPE_DIARIO", "500000.00"),
}

# Candado: el proveedor simulado nunca corre con DEBUG apagado. Si alguien
# copia un .env de desarrollo al servidor, la aplicación no arranca en lugar
# de "vender" tiempo aire que nunca sale. Mejor un error ruidoso al desplegar
# que ventas falsas en producción.
if TAE_PROVEEDOR["modo"] not in ("real", "simulado"):
    raise RuntimeError("PROVEEDOR_MODO debe ser 'real' o 'simulado'")
if TAE_PROVEEDOR["modo"] == "simulado" and not DEBUG:
    raise RuntimeError(
        "PROVEEDOR_MODO=simulado no se permite con DJANGO_DEBUG=false. "
        "En producción el proveedor siempre es el real."
    )

# ---------------------------------------------------------------------------
# Producción detrás de nginx con HTTPS. Ver docs/DESPLIEGUE.md.
# Con DEBUG encendido (desarrollo) nada de esto se activa, para que
# http://localhost siga funcionando.
# ---------------------------------------------------------------------------
STATIC_ROOT = BASE_DIR / "staticfiles"

if not DEBUG:
    # nginx termina el HTTPS y le avisa a Django con este encabezado.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # Empieza corto: un HSTS largo mal configurado deja el dominio
    # inaccesible por HTTP durante todo ese tiempo. Súbelo a 31536000 cuando
    # el HTTPS lleve unas semanas estable.
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SEGUNDOS", "3600"))
    if SECRET_KEY.startswith("dev-") or "cambiar" in SECRET_KEY:
        raise RuntimeError("DJANGO_SECRET_KEY de desarrollo con DEBUG apagado")

# Bitácoras a la salida estándar: Docker las recoge y `docker compose logs`
# las muestra. Los nombres "tae.*" son los que usa el código.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"consola": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {
        "tae": {"handlers": ["consola"], "level": env("TAE_LOG_NIVEL", "INFO")},
        "django": {"handlers": ["consola"], "level": "WARNING"},
    },
}

# En las pruebas, silencio: los avisos esperados (timeouts emulados) no deben
# enterrar el resultado de la suite.
if "test" in sys.argv:
    LOGGING["loggers"]["tae"]["level"] = "CRITICAL"
