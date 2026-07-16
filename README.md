# Acortalink de Links

Acortalink de URLs standalone en Django. Optimizado para 750 usuarios concurrentes.

## Features

- Redirect instantáneo (302) con cache Redis
- Páginas SEO indexables (premium) con Open Graph metadata
- Bulk CSV (hasta 500 URLs, optimizado con bulk_create)
- Cifrado de URLs originales (django-cryptography)
- Anti-phishing (blocklist, reportes de abuso)
- 2FA por email para acciones sensibles
- Verificación de cuenta por email
- MercadoPago (ARS) + Stripe (opcional)
- Rate limiting por IP

## Setup con UV

```bash
uv sync
cp .env.example .env  # editar credenciales

# Dev con SQLite + LocMemCache (sin Redis/Postgres)
CACHE_BACKEND=locmem uv run python manage.py migrate
CACHE_BACKEND=locmem uv run python manage.py createsuperuser
CACHE_BACKEND=locmem uv run python manage.py runserver
```

## Setup con Docker Compose

```bash
cp .env.example .env  # editar credenciales
docker compose up --build
```

Servicios:
- **web** — Gunicorn en :8000 (PostgreSQL + Redis)
- **db** — PostgreSQL 16 en :5432
- **redis** — Redis 7 en :6379
- **cron** — flush_clicks cada 5min, cleanup diario, ping_seo cada 30min

## Tests

```bash
CACHE_BACKEND=locmem uv run python manage.py test core
```

## URLs

| Path | Descripción |
|---|---|
| `/` | Landing + form de acortar |
| `/s/<code>/` | Redirect del link acortado |
| `/mis-links/` | Dashboard del usuario |
| `/bulk/` | Bulk CSV (premium) |
| `/ingresar/` | Login |
| `/registrar/` | Registro |
| `/subscribir/` | Planes premium |
| `/admin/` | Admin Django |

## Producción (VPS)

```bash
# Sin Docker — VPS directo
uv sync
redis-server --daemonize yes
gunicorn -c gunicorn.conf.py acortador_project.wsgi:application

# Cron
*/5 * * * * cd /path && uv run python manage.py flush_clicks
0 3 * * * cd /path && uv run python manage.py cleanup_expired_links
*/30 * * * * cd /path && uv run python manage.py ping_seo
```

