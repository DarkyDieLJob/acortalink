# Arquitectura técnica

## Stack

```
Internet → Caddy (reverse proxy + auto-SSL)
  → Gunicorn (5 workers × 4 threads = 20 concurrent)
    → Django 5.x
      → PostgreSQL 16 (multi-writer)
      → Redis 7 (cache, rate limit, click batching)
```

## Servicios (docker-compose.yml)

| Servicio | Imagen | Puerto | Función |
|---|---|---|---|
| web | Dockerfile | 8000 | Gunicorn + Django |
| db | postgres:16-alpine | 5432 | PostgreSQL |
| redis | redis:7-alpine | 6379 | Cache + click tracking |
| cron | Dockerfile | — | flush_clicks, cleanup, ping_seo |

## Performance

### Redirect (hot path)

```
Request: GET /s/abc123/
  → Rate limit check (Redis, 60/min per IP)
  → Redis cache lookup (rdr:abc123)
    → HIT: return 302 redirect (0 DB queries)
    → MISS: DB lookup → populate cache → return 302
    → 404: cache negativo 60s
  → Click tracking: Redis INCR + SADD (async flush cada 5 min)
```

### Bulk CSV (500 filas)

```
Fase 1: Parse + validate (0 DB queries)
Fase 2: Pre-fetch existentes con in_bulk (1 query)
Fase 3: Preparar objetos en memoria (0 DB queries)
Fase 4: bulk_create + save en transaction.atomic (2-3 queries)
Total: ~3 queries vs ~1500 antes
```

### Click tracking

```
Redirect → Redis INCR(clicks:{pk}) + SADD(clicks:pending_pks, pk)
Cron 5min → SMEMBERS(clicks:pending_pks) → batch UPDATE DB → DELETE SET
```

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `SECRET_KEY` | dev key | Django secret |
| `DEBUG` | True | Debug mode |
| `DB_ENGINE` | sqlite3 | PostgreSQL en prod |
| `REDIS_URL` | redis://127.0.0.1:6379/0 | Redis connection |
| `CACHE_BACKEND` | redis | `locmem` para dev sin Redis |
| `MERCADOPAGO_ACCESS_TOKEN` | — | MP production token |
| `MERCADOPAGO_PRICE` | 2500 | ARS/mes |
| `FIELD_ENCRYPTION_KEY` | — | Fernet key para cifrado |
| `EMAIL_HOST_USER` | — | SMTP credentials |
| `SITE_URL` | localhost:8000 | URL del sitio |

## Cron jobs

| Comando | Frecuencia | Función |
|---|---|---|
| `flush_clicks` | 5 min | Persistir clicks de Redis a DB |
| `cleanup_expired_links` | diario 3am | Borrar links free sin clicks (90 días) |
| `ping_seo` | 30 min | Ping search engines para SEO pages |

## Compatibilidad

### django-cryptography + Django 5.x

`django-cryptography 1.1` importa `django.utils.baseconv` que fue removido en Django 5.
`acortador_project/compat.py` inyecta un shim base62 antes de Django setup.
Importado en `manage.py`, `wsgi.py` y `asgi.py`.

Cuando `django-cryptography 2.0` se publique estable, se puede remover el shim.
