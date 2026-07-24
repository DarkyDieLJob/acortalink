"""Django settings for acortador standalone project."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-dev-key-change-in-production')

DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1', 'yes')

_allowed = os.environ.get('ALLOWED_HOSTS', '')
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(',') if h.strip()] or ['localhost', '127.0.0.1', '0.0.0.0']

csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = [o.strip() for o in csrf_origins.split(',') if o.strip()] if csrf_origins else []

USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

FORCE_SCRIPT_NAME = os.environ.get('FORCE_SCRIPT_NAME', '')

# Maintenance mode — blocks external traffic, allows internal IPs for testing
MAINTENANCE_MODE = os.environ.get('MAINTENANCE_MODE', 'False').lower() in ('true', '1', 'yes')
_maint_ips = os.environ.get('MAINTENANCE_ALLOWED_IPS', '')
MAINTENANCE_ALLOWED_IPS = [ip.strip() for ip in _maint_ips.split(',') if ip.strip()]

CSRF_COOKIE_SECURE = os.environ.get('CSRF_COOKIE_SECURE', 'False').lower() in ('true', '1', 'yes')
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1', 'yes')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_bootstrap5',
    'django_cryptography',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'acortador_project.maintenance.MaintenanceModeMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'acortador_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'acortador_project.context_processors.google_ads',
            ],
        },
    },
]

WSGI_APPLICATION = 'acortador_project.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': os.environ.get('DB_ENGINE', 'django.db.backends.sqlite3'),
        'NAME': os.environ.get('DB_NAME', str(BASE_DIR / 'db.sqlite3')),
        'HOST': os.environ.get('DB_HOST', ''),
        'PORT': os.environ.get('DB_PORT', ''),
        'USER': os.environ.get('DB_USER', ''),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
    }
}

# SQLite WAL mode
if 'sqlite' in DATABASES['default']['ENGINE']:
    import sqlite3
    _db_path = DATABASES['default']['NAME']
    if _db_path != ':memory:':
        try:
            _conn = sqlite3.connect(str(_db_path))
            _conn.execute('PRAGMA journal_mode=WAL')
            _conn.execute('PRAGMA busy_timeout=5000')
            _conn.close()
        except Exception:
            pass

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'
USE_I18N = True
USE_TZ = True
USE_X_FORWARDED_HOST = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = '/public/'

# Cache — Redis para multi-worker
REDIS_URL = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
        'TIMEOUT': 21600,
    }
}

# Fallback a LocMemCache si Redis no está disponible (dev)
if os.environ.get('CACHE_BACKEND', 'redis') == 'locmem':
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'acortador-cache',
            'TIMEOUT': 21600,
        }
    }

CLICK_TRACKING_SET_KEY = 'clicks:pending_pks'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/ingresar/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# Email / SMTP
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'acortador_project.email_backends.UnverifiedSMTPBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', 'False').lower() in ('true', '1', 'yes')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@localhost')

# Payment provider — 'mercadopago' or 'stripe'
PAYMENT_PROVIDER = os.environ.get('PAYMENT_PROVIDER', 'mercadopago')

# Stripe (optional)
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

# Mercado Pago
MERCADOPAGO_ACCESS_TOKEN = os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '')
MERCADOPAGO_PUBLIC_KEY = os.environ.get('MERCADOPAGO_PUBLIC_KEY', '')
MERCADOPAGO_WEBHOOK_SECRET = os.environ.get('MERCADOPAGO_WEBHOOK_SECRET', '')
MERCADOPAGO_CURRENCY = os.environ.get('MERCADOPAGO_CURRENCY', 'ARS')

# Pricing por plan (ARS/mes) — 30% por debajo de competidores
PLAN_PRICES = {
    'starter': int(os.environ.get('PLAN_PRICE_STARTER', '4900')),
    'pro': int(os.environ.get('PLAN_PRICE_PRO', '9800')),
    'business': int(os.environ.get('PLAN_PRICE_BUSINESS', '28000')),
}
# Alias legacy para compatibilidad
MERCADOPAGO_PRICE = PLAN_PRICES['starter']

SITE_URL = os.environ.get('SITE_URL', 'https://acortalink.com.ar')

# Custom domains
CNAME_TARGET = os.environ.get('CNAME_TARGET', 'app.acortalink.com.ar')
DOMAIN_PRICES = {
    '.com': int(os.environ.get('DOMAIN_PRICE_COM', '1200')),
    '.net': int(os.environ.get('DOMAIN_PRICE_NET', '1500')),
    '.org': int(os.environ.get('DOMAIN_PRICE_ORG', '1300')),
    '.io': int(os.environ.get('DOMAIN_PRICE_IO', '3500')),
    '.dev': int(os.environ.get('DOMAIN_PRICE_DEV', '3000')),
    '.app': int(os.environ.get('DOMAIN_PRICE_APP', '2500')),
    '.ar': int(os.environ.get('DOMAIN_PRICE_AR', '2000')),
}
# Registrar API (Namecheap, ResellerClub, etc.)
REGISTRAR_API_URL = os.environ.get('REGISTRAR_API_URL', '')
REGISTRAR_API_KEY = os.environ.get('REGISTRAR_API_KEY', '')
REGISTRAR_API_USER = os.environ.get('REGISTRAR_API_USER', '')

# DonWeb affiliate program — referral link for domain purchases
# Commission: up to USD 10 per referral · Referred user gets 20% OFF first purchase
# Set DONWEB_AFFILIATE_ENABLED=True to show the referral section in custom domains
DONWEB_AFFILIATE_LINK = os.environ.get('DONWEB_AFFILIATE_LINK', '')
DONWEB_AFFILIATE_ENABLED = os.environ.get('DONWEB_AFFILIATE_ENABLED', 'False').lower() in ('true', '1', 'yes')

# Google Ads (gtag.js) — shared across landing + app
GOOGLE_ADS_ID = os.environ.get('GOOGLE_ADS_ID', 'AW-18282918371')
# Google Ads conversion label for subscription purchases
GOOGLE_ADS_SUBSCRIPTION_LABEL = os.environ.get('GOOGLE_ADS_SUBSCRIPTION_LABEL', 'aCt1CNfg5dUcEOPj_I1E')
# Google Analytics 4 (GA4) — visit tracking + conversion funnels
GA4_ID = os.environ.get('GA4_ID', '')

# Field-level encryption (django-cryptography)
FIELD_ENCRYPTION_KEY = os.environ.get('FIELD_ENCRYPTION_KEY', '')
