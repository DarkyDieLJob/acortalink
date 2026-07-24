import hashlib

from django.contrib.auth.models import User
from django.db import models
from django_cryptography.fields import encrypt


class EmailVerification(models.Model):
    """Token/código de verificación por email.

    - TYPE_ACTIVATION: link de activación de cuenta (registro)
    - TYPE_ACTION: código de 6 dígitos para acciones sensibles (2FA genérico)
    """

    TYPE_ACTIVATION = 'activation'
    TYPE_ACTION = 'action'

    TYPE_CHOICES = [
        (TYPE_ACTIVATION, 'Activación de cuenta'),
        (TYPE_ACTION, 'Verificación de acción (2FA)'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='verifications')
    verification_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    code = models.CharField(max_length=6, blank=True, default='')
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'Verificación de email'
        verbose_name_plural = 'Verificaciones de email'

    def __str__(self):
        return f'{self.user.username} — {self.get_verification_type_display()}'

    @property
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at


class Subscription(models.Model):
    """Subscripción del usuario.

    Integrada con MercadoPago o Stripe. ``provider_id`` guarda el ID de
    la subscripción en el provider, ``stripe_customer_id`` guarda el customer ID.
    """

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_CANCELLED = 'cancelled'
    STATUS_EXPIRED = 'expired'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente'),
        (STATUS_ACTIVE, 'Activa'),
        (STATUS_CANCELLED, 'Cancelada'),
        (STATUS_EXPIRED, 'Expirada'),
    ]

    PLAN_STARTER = 'starter'
    PLAN_PRO = 'pro'
    PLAN_BUSINESS = 'business'

    PLAN_CHOICES = [
        (PLAN_STARTER, 'Starter'),
        (PLAN_PRO, 'Pro'),
        (PLAN_BUSINESS, 'Business'),
    ]

    PLAN_LINK_LIMITS = {
        PLAN_STARTER: 3000,
        PLAN_PRO: 10000,
        PLAN_BUSINESS: 50000,
    }

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_STARTER)
    provider = models.CharField(max_length=30, default='mercadopago')
    provider_id = models.CharField(max_length=100, blank=True, default='')
    last_payment_id = models.CharField(max_length=100, blank=True, default='')
    stripe_customer_id = models.CharField(max_length=100, blank=True, default='')
    fecha_inicio = models.DateTimeField(null=True, blank=True)
    fecha_fin = models.DateTimeField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.username} — {self.get_status_display()}'

    @property
    def is_active(self):
        return self.status == self.STATUS_ACTIVE


class ShortLink(models.Model):
    """Link acortado.

    - Free: redirect 302 instantáneo, no indexable.
    - Premium: página de redirección con SEO metadata, indexable, countdown opcional.
    """

    short_code = models.CharField(max_length=8, unique=True, db_index=True)
    original_url = encrypt(models.URLField(max_length=2048))
    url_hash = models.CharField(max_length=64, db_index=True, blank=True, default='')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='short_links')
    is_premium = models.BooleanField(default=False)

    # Campos SEO (solo premium) — cifrados
    seo_title = encrypt(models.CharField(max_length=120, blank=True, default=''))
    seo_description = encrypt(models.CharField(max_length=300, blank=True, default=''))
    seo_image = encrypt(models.URLField(blank=True, default=''))
    redirect_seconds = models.PositiveIntegerField(default=5)
    seo_updated_at = models.DateTimeField(null=True, blank=True)
    has_seo = models.BooleanField(default=False, db_index=True)
    needs_ping = models.BooleanField(default=False, db_index=True)

    # Password protection (premium)
    password_hash = models.CharField(max_length=128, blank=True, default='')

    # Custom domain (premium, nullable)
    custom_domain = models.ForeignKey(
        'CustomDomain', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='links', default=None,
    )

    # Stats
    clicks = models.PositiveIntegerField(default=0)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    ultimo_click = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'Link acortado'
        verbose_name_plural = 'Links acortados'
        indexes = [
            models.Index(fields=['owner', 'url_hash'], name='idx_owner_urlhash'),
        ]

    def __str__(self):
        return f'{self.short_code} → {self.original_url[:60]}'

    def _compute_url_hash(self):
        if self.original_url:
            return hashlib.sha256(self.original_url.encode('utf-8')).hexdigest()
        return ''

    def save(self, *args, **kwargs):
        if self.original_url:
            self.url_hash = self._compute_url_hash()
        self.has_seo = bool(self.seo_title or self.seo_description)
        super().save(*args, **kwargs)


class LinkReport(models.Model):
    """Reporte de abuso/phishing sobre un link acortado."""

    REASON_PHISHING = 'phishing'
    REASON_MALWARE = 'malware'
    REASON_SPAM = 'spam'
    REASON_FRAUD = 'fraud'
    REASON_OTHER = 'other'

    REASON_CHOICES = [
        (REASON_PHISHING, 'Phishing / Suplantación'),
        (REASON_MALWARE, 'Malware / Virus'),
        (REASON_SPAM, 'Spam'),
        (REASON_FRAUD, 'Fraude / Estafa'),
        (REASON_OTHER, 'Otro'),
    ]

    link = models.ForeignKey(ShortLink, on_delete=models.CASCADE, related_name='reports')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    detail = models.CharField(max_length=500, blank=True, default='')
    reporter = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='link_reports'
    )
    reporter_ip = models.GenericIPAddressField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    reviewed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'Reporte de link'
        verbose_name_plural = 'Reportes de links'

    def __str__(self):
        return f'Reporte de {self.link.short_code} — {self.get_reason_display()}'


class ClickEvent(models.Model):
    """Evento individual de click para analytics geo/device/referrer.

    Se crea de forma asíncrona (batch) desde Redis en flush_clicks.
    """

    DEVICE_MOBILE = 'mobile'
    DEVICE_DESKTOP = 'desktop'
    DEVICE_TABLET = 'tablet'
    DEVICE_BOT = 'bot'
    DEVICE_OTHER = 'other'

    DEVICE_CHOICES = [
        (DEVICE_MOBILE, 'Mobile'),
        (DEVICE_DESKTOP, 'Desktop'),
        (DEVICE_TABLET, 'Tablet'),
        (DEVICE_BOT, 'Bot'),
        (DEVICE_OTHER, 'Other'),
    ]

    link = models.ForeignKey(ShortLink, on_delete=models.CASCADE, related_name='click_events')
    ip = models.GenericIPAddressField(null=True, blank=True)
    country = models.CharField(max_length=2, blank=True, default='')
    device = models.CharField(max_length=10, choices=DEVICE_CHOICES, default=DEVICE_OTHER)
    browser = models.CharField(max_length=50, blank=True, default='')
    referrer = models.URLField(max_length=500, blank=True, default='')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado']
        indexes = [
            models.Index(fields=['link', '-creado'], name='idx_clickevent_link'),
        ]
        verbose_name = 'Evento de click'
        verbose_name_plural = 'Eventos de click'

    def __str__(self):
        return f'Click {self.link.short_code} — {self.get_device_display()}'


class ApiKey(models.Model):
    """API key para acceso REST API por usuario."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_keys')
    key = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=60, default='Default')
    active = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    ultimo_uso = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'API Key'
        verbose_name_plural = 'API Keys'

    def __str__(self):
        return f'{self.user.username} — {self.name}'

    @staticmethod
    def generate_key():
        import secrets as _secrets
        return _secrets.token_urlsafe(32)


class CustomDomain(models.Model):
    """Dominio personalizado configurado por el usuario (premium).

    El usuario apunta un CNAME a nuestro servidor y nosotros servimos
    los links desde ese dominio. Caddy se encarga del SSL automático.
    """

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente (DNS no verificado)'),
        (STATUS_ACTIVE, 'Activo'),
        (STATUS_FAILED, 'Fallido (DNS incorrecto)'),
    ]

    SOURCE_BYOD = 'byod'
    SOURCE_PURCHASED = 'purchased'

    SOURCE_CHOICES = [
        (SOURCE_BYOD, 'Dominio propio'),
        (SOURCE_PURCHASED, 'Comprado via plataforma'),
    ]

    PURCHASE_PENDING = 'purchase_pending'
    PURCHASE_PAID = 'purchase_paid'
    PURCHASE_REGISTERED = 'purchase_registered'
    PURCHASE_FAILED = 'purchase_failed'

    PURCHASE_CHOICES = [
        (PURCHASE_PENDING, 'Pago pendiente'),
        (PURCHASE_PAID, 'Pagado, registrando'),
        (PURCHASE_REGISTERED, 'Registrado'),
        (PURCHASE_FAILED, 'Fallo en registro'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='custom_domains')
    domain = models.CharField(max_length=253, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_BYOD)
    purchase_status = models.CharField(max_length=25, choices=PURCHASE_CHOICES, blank=True, default='')
    price = models.PositiveIntegerField(default=0)
    mp_payment_id = models.CharField(max_length=64, blank=True, default='')
    dns_verified_at = models.DateTimeField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'Dominio personalizado'
        verbose_name_plural = 'Dominios personalizados'

    def __str__(self):
        return f'{self.domain} ({self.get_status_display()})'


class TeamMember(models.Model):
    """Miembro de un team (Business plan).

    El owner del Business plan puede invitar hasta 5 usuarios para
    compartir links y analytics.
    """

    ROLE_ADMIN = 'admin'
    ROLE_MEMBER = 'member'

    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_MEMBER, 'Member'),
    ]

    team_owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_memberships')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    invite_token = models.CharField(max_length=64, blank=True, default='')
    invited_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-invited_at']
        unique_together = ['team_owner', 'user']
        verbose_name = 'Miembro del team'
        verbose_name_plural = 'Miembros del team'

    def __str__(self):
        return f'{self.team_owner.username} → {self.user.username} ({self.get_role_display()})'


class Webhook(models.Model):
    """Webhook endpoint para eventos de API (Business plan).

    Cuando un link recibe clicks o es creado, se envía un POST
    al URL configurado con el evento firmado.
    """

    EVENT_CLICK = 'click'
    EVENT_LINK_CREATED = 'link.created'
    EVENT_LINK_DELETED = 'link.deleted'

    EVENT_CHOICES = [
        (EVENT_CLICK, 'Click recibido'),
        (EVENT_LINK_CREATED, 'Link creado'),
        (EVENT_LINK_DELETED, 'Link eliminado'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='webhooks')
    url = models.URLField(max_length=500)
    events = models.CharField(max_length=200, default='link.created,link.deleted')
    secret = models.CharField(max_length=64, blank=True, default='')
    active = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    ultimo_envio = models.DateTimeField(null=True, blank=True)
    fallos_consecutivos = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'Webhook'
        verbose_name_plural = 'Webhooks'

    def __str__(self):
        return f'{self.owner.username} → {self.url}'

    @staticmethod
    def generate_secret():
        import secrets as _secrets
        return _secrets.token_urlsafe(32)

    def event_list(self):
        return [e.strip() for e in self.events.split(',') if e.strip()]
