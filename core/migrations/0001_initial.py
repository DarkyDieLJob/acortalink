# Squashed migration: 0001-0006 → 0001_initial
# Created for standalone acortador project extraction

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django_cryptography.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Subscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pendiente"), ("active", "Activa"), ("cancelled", "Cancelada"), ("expired", "Expirada")], default="pending", max_length=20)),
                ("provider", models.CharField(default="mercadopago", max_length=30)),
                ("provider_id", models.CharField(blank=True, default="", max_length=100)),
                ("stripe_customer_id", models.CharField(blank=True, default="", max_length=100)),
                ("fecha_inicio", models.DateTimeField(blank=True, null=True)),
                ("fecha_fin", models.DateTimeField(blank=True, null=True)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("actualizado", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="subscription", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="EmailVerification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("verification_type", models.CharField(choices=[("activation", "Activación de cuenta"), ("action", "Verificación de acción (2FA)")], max_length=20)),
                ("token", models.CharField(db_index=True, max_length=64, unique=True)),
                ("code", models.CharField(blank=True, default="", max_length=6)),
                ("expires_at", models.DateTimeField()),
                ("used", models.BooleanField(default=False)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="verifications", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Verificación de email",
                "verbose_name_plural": "Verificaciones de email",
                "ordering": ["-creado"],
            },
        ),
        migrations.CreateModel(
            name="ShortLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("short_code", models.CharField(db_index=True, max_length=8, unique=True)),
                ("original_url", django_cryptography.fields.encrypt(models.URLField(max_length=2048))),
                ("url_hash", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("is_premium", models.BooleanField(default=False)),
                ("seo_title", django_cryptography.fields.encrypt(models.CharField(blank=True, default="", max_length=120))),
                ("seo_description", django_cryptography.fields.encrypt(models.CharField(blank=True, default="", max_length=300))),
                ("seo_image", django_cryptography.fields.encrypt(models.URLField(blank=True, default=""))),
                ("redirect_seconds", models.PositiveIntegerField(default=5)),
                ("seo_updated_at", models.DateTimeField(blank=True, null=True)),
                ("has_seo", models.BooleanField(db_index=True, default=False)),
                ("needs_ping", models.BooleanField(db_index=True, default=False)),
                ("clicks", models.PositiveIntegerField(default=0)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("actualizado", models.DateTimeField(auto_now=True)),
                ("ultimo_click", models.DateTimeField(blank=True, null=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="short_links", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Link acortado",
                "verbose_name_plural": "Links acortados",
                "ordering": ["-creado"],
                "indexes": [models.Index(fields=["owner", "url_hash"], name="idx_owner_urlhash")],
            },
        ),
        migrations.CreateModel(
            name="LinkReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.CharField(choices=[("phishing", "Phishing / Suplantación"), ("malware", "Malware / Virus"), ("spam", "Spam"), ("fraud", "Fraude / Estafa"), ("other", "Otro")], max_length=20)),
                ("detail", models.CharField(blank=True, default="", max_length=500)),
                ("reporter_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("reviewed", models.BooleanField(default=False)),
                ("link", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reports", to="core.shortlink")),
                ("reporter", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="link_reports", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Reporte de link",
                "verbose_name_plural": "Reportes de links",
                "ordering": ["-creado"],
            },
        ),
    ]
