"""
Quién es quién y qué puede hacer.

Un usuario pertenece a exactamente un lugar de la jerarquía, y ese lugar es
lo que determina sus permisos. No hay un sistema de permisos genérico con
casillas: los roles son cuatro, están cerrados, y cada regla de este archivo
corresponde a una línea del documento de alcance.

El permiso se verifica en el servidor. Que la pantalla no muestre el botón
es conveniencia para el usuario, no seguridad.
"""
import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import Administrador, Cliente, Inquilino, PuntoVenta


class Rol(models.TextChoices):
    """Los cuatro roles. Cerrados a propósito: no hay roles a la medida."""

    SUPER_ADMIN = "super_admin", "Super Admin"
    ADMINISTRADOR = "administrador", "Administrador"
    CLIENTE = "cliente", "Cliente"
    PUNTO_VENTA = "punto_venta", "Punto de venta"


class GestorUsuario(BaseUserManager):
    """Alta de usuarios. Siempre con `full_clean` para que el ancla sea coherente."""

    def crear(self, correo, rol, *, password=None, inquilino=None, **extra):
        if not correo:
            raise ValueError("El correo es obligatorio")
        usuario = self.model(
            correo=self.normalize_email(correo), rol=rol, inquilino=inquilino, **extra
        )
        usuario.set_password(password)
        usuario.full_clean()
        usuario.save(using=self._db)
        return usuario

    def create_superuser(self, correo, password=None, **extra):
        """
        El Super Admin de la plataforma. Siempre lleva segundo factor: está
        arriba de toda la cadena y es quien fondea a los administradores.
        """
        inquilino = extra.pop("inquilino", None) or Inquilino.objects.first()
        extra.setdefault("nombre", "Super Admin")
        usuario = self.crear(
            correo, Rol.SUPER_ADMIN, password=password, inquilino=inquilino, **extra
        )
        usuario.is_staff = True
        usuario.is_superuser = True
        usuario.save(using=self._db)
        return usuario


class Usuario(AbstractBaseUser, PermissionsMixin):
    """
    El correo es la identidad. No hay nombre de usuario aparte: uno menos que
    recordar y uno menos que recuperar.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    correo = models.EmailField(unique=True)
    nombre = models.CharField(max_length=160)
    rol = models.CharField(max_length=14, choices=Rol.choices)

    inquilino = models.ForeignKey(
        Inquilino, null=True, blank=True, on_delete=models.PROTECT, related_name="usuarios"
    )
    administrador = models.ForeignKey(
        Administrador, null=True, blank=True, on_delete=models.PROTECT, related_name="usuarios"
    )
    cliente = models.ForeignKey(
        Cliente, null=True, blank=True, on_delete=models.PROTECT, related_name="usuarios"
    )
    punto_venta = models.ForeignKey(
        PuntoVenta, null=True, blank=True, on_delete=models.PROTECT, related_name="usuarios"
    )

    # Mientras esté en True, el usuario solo puede cambiar su contraseña:
    # la API le niega todo lo demás. Se prende al darlo de alta y cada vez
    # que su superior le restablece la contraseña, porque en ambos casos
    # alguien más la vio. Ver docs/DECISIONES.md, decisión 15.
    debe_cambiar_contrasena = models.BooleanField(default=False)

    # Secreto del segundo factor. Vacío mientras no se haya activado.
    totp_secreto = models.CharField(max_length=64, blank=True)
    totp_activado = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    objects = GestorUsuario()

    USERNAME_FIELD = "correo"
    REQUIRED_FIELDS = ["nombre"]

    class Meta:
        db_table = "identidad_usuario"
        constraints = [
            models.CheckConstraint(
                name="usuario_anclado_a_su_nivel",
                condition=(
                    Q(rol="super_admin", administrador__isnull=True,
                      cliente__isnull=True, punto_venta__isnull=True)
                    | Q(rol="administrador", administrador__isnull=False,
                        cliente__isnull=True, punto_venta__isnull=True)
                    | Q(rol="cliente", cliente__isnull=False,
                        administrador__isnull=True, punto_venta__isnull=True)
                    | Q(rol="punto_venta", punto_venta__isnull=False,
                        administrador__isnull=True, cliente__isnull=True)
                ),
            )
        ]
        indexes = [models.Index(fields=["inquilino", "rol"])]

    def __str__(self):
        return f"{self.nombre} ({self.get_rol_display()})"

    def clean(self):
        """
        La base garantiza que el ancla exista; aquí se garantiza que sea
        coherente. Un usuario de cliente cuyo cliente pertenece a otro
        administrador es un error de alta, no un caso de negocio.
        """
        super().clean()
        if self.rol != Rol.SUPER_ADMIN and self.inquilino_id is None:
            raise ValidationError({"inquilino": "Todo usuario pertenece a un inquilino"})

        ancla = self.ancla
        if ancla is not None and ancla.inquilino_id != self.inquilino_id:
            raise ValidationError(
                {"inquilino": "El usuario y su entidad están en inquilinos distintos"}
            )

    @property
    def ancla(self):
        """La entidad de la jerarquía a la que cuelga este usuario."""
        return self.administrador or self.cliente or self.punto_venta

    # ------------------------------------------------------------------
    # Segundo factor
    # ------------------------------------------------------------------
    @property
    def exige_totp_al_entrar(self):
        """
        Solo el Super Admin. Al administrador se le pide al autorizar un
        traspaso sobre el umbral, no al entrar: si se le pide seis dígitos
        cada mañana, termina dejando la sesión abierta, que es peor.

        Clientes y cajeros nunca: la fricción los expulsa y su riesgo está
        acotado a su propia bolsa.
        """
        return self.rol == Rol.SUPER_ADMIN

    @property
    def totp_listo(self):
        return bool(self.totp_secreto and self.totp_activado)

    @property
    def puede_autoservicio(self):
        """
        Quién puede restablecer su contraseña solo, por correo.

        El cajero no. Muchos no tienen un correo que revisen, y el que
        tienen suele ser compartido o del dueño. Su contraseña la cambia el
        cliente desde su panel, que es el mismo que lo dio de alta.
        """
        return self.rol != Rol.PUNTO_VENTA

    # ------------------------------------------------------------------
    # Alcance: qué entidades ve este usuario
    # ------------------------------------------------------------------
    def alcanza_cliente(self, cliente):
        if self.rol == Rol.SUPER_ADMIN:
            return cliente.inquilino_id == self.inquilino_id
        if self.rol == Rol.ADMINISTRADOR:
            return cliente.administrador_id == self.administrador_id
        if self.rol == Rol.CLIENTE:
            return cliente.pk == self.cliente_id
        if self.rol == Rol.PUNTO_VENTA:
            return cliente.pk == self.punto_venta.cliente_id
        return False

    def alcanza_punto_venta(self, punto_venta):
        if self.rol == Rol.PUNTO_VENTA:
            return punto_venta.pk == self.punto_venta_id
        return self.alcanza_cliente(punto_venta.cliente)

    def alcanza_administrador(self, administrador):
        if self.rol == Rol.SUPER_ADMIN:
            return administrador.inquilino_id == self.inquilino_id
        if self.rol == Rol.ADMINISTRADOR:
            return administrador.pk == self.administrador_id
        return False


class SolicitudRestablecimiento(models.Model):
    """
    Un vale de un solo uso para cambiar la contraseña.

    De la credencial solo se guarda su huella. Si alguien se lleva esta tabla
    completa, no se lleva ningún vale utilizable: tendría huellas, no llaves.

    Hoy solo lo usa el autoservicio por correo. El restablecimiento por el
    superior ya no emite vales: asigna una contraseña temporal (ver
    `contrasenas.py`) y queda registrado en BitacoraAdministracion.
    `solicitado_por` se conserva para los vales emitidos antes de ese cambio
    y por si un flujo futuro lo vuelve a necesitar; en el autoservicio va vacío.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    usuario = models.ForeignKey(
        Usuario, on_delete=models.CASCADE, related_name="restablecimientos"
    )
    huella = models.CharField(max_length=64, unique=True)
    solicitado_por = models.ForeignKey(
        Usuario, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    origen = models.GenericIPAddressField(null=True, blank=True)
    creado = models.DateTimeField(default=timezone.now)
    expira = models.DateTimeField()
    usado = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identidad_restablecimiento"
        indexes = [models.Index(fields=["usuario", "usado"])]

    def __str__(self):
        return f"Restablecimiento de {self.usuario.correo}"

    @property
    def vigente(self):
        return self.usado is None and timezone.now() < self.expira


class BitacoraAcceso(models.Model):
    """
    Quién entró, desde dónde y si pasó el segundo factor.

    Separada de la bitácora de saldo a propósito: una responde "quién movió
    este dinero" y esta responde "quién entró a la plataforma". Mezclarlas
    hace que ninguna de las dos se pueda leer.
    """

    usuario = models.ForeignKey(
        Usuario, null=True, blank=True, on_delete=models.SET_NULL, related_name="accesos"
    )
    correo_intentado = models.EmailField(blank=True)
    exito = models.BooleanField(default=False)
    motivo = models.CharField(max_length=80, blank=True)
    origen = models.GenericIPAddressField(null=True, blank=True)
    cuando = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "identidad_bitacora_acceso"
        indexes = [
            models.Index(fields=["usuario", "cuando"]),
            models.Index(fields=["cuando"]),
        ]

    def __str__(self):
        estado = "ok" if self.exito else "fallo"
        return f"{self.correo_intentado or self.usuario} {estado} {self.cuando:%Y-%m-%d %H:%M}"


class AccionAdministrativa(models.TextChoices):
    """Lo que puede registrar BitacoraAdministracion."""
    ALTA = "alta", "Alta"
    APROBACION = "aprobacion", "Aprobación"
    RECHAZO = "rechazo", "Rechazo"
    SUSPENSION = "suspension", "Suspensión"
    REACTIVACION = "reactivacion", "Reactivación"
    RESTABLECIMIENTO = "restablecimiento", "Restablecimiento de contraseña"
    CAMBIO_CONTRASENA = "cambio_contrasena", "Cambio de contraseña"
    REINICIO_TOTP = "reinicio_totp", "Reinicio de segundo factor"
    DESACTIVACION = "desactivacion", "Desactivación de acceso"
    REACTIVACION_ACCESO = "reactiva_acceso", "Reactivación de acceso"
    RETIRO = "retiro", "Retiro de saldo a EPS"
    RETENCION = "retencion", "Retención de red"
    CANCELA_RETENCION = "cancela_retencion", "Cancelación de retención"


class BitacoraAdministracion(models.Model):
    """
    Quién hizo qué sobre quién: altas, aprobaciones, rechazos, suspensiones
    y restablecimientos.

    Es la tercera bitácora y responde una pregunta distinta a las otras dos:
    el ledger dice quién movió dinero y BitacoraAcceso dice quién entró. Esta
    dice quién cambió la red. Ver docs/DECISIONES.md, decisión 17.

    Solo inserción, igual que el ledger: la aplicación nunca edita ni borra
    estas filas. Guarda nombres además de llaves porque una entidad puede
    renombrarse y la bitácora tiene que decir lo que se vio en su momento.
    Nunca guarda contraseñas, ni temporales: solo que se generó una.
    """

    actor = models.ForeignKey(
        Usuario, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_nombre = models.CharField(max_length=160)
    inquilino = models.ForeignKey(
        Inquilino, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    accion = models.CharField(max_length=20, choices=AccionAdministrativa.choices)
    objetivo_tipo = models.CharField(max_length=20)  # administrador, cliente, punto_venta, usuario
    objetivo_uuid = models.UUIDField(null=True, blank=True)
    objetivo_nombre = models.CharField(max_length=160)
    detalle = models.CharField(max_length=240, blank=True)
    cuando = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "identidad_bitacora_administracion"
        indexes = [
            models.Index(fields=["inquilino", "cuando"]),
            models.Index(fields=["objetivo_uuid", "cuando"]),
        ]

    def __str__(self):
        return f"{self.actor_nombre}: {self.get_accion_display()} de {self.objetivo_nombre}"
