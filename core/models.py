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
    """Subscripción premium del usuario.

    Integrada con Stripe. ``provider_id`` guarda el Stripe subscription ID,
    ``stripe_customer_id`` guarda el Stripe customer ID.
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

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    provider = models.CharField(max_length=30, default='stripe')
    provider_id = models.CharField(max_length=100, blank=True, default='')
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
