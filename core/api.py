"""API REST v1 para el acortador de links.

Endpoints:
  POST   /api/v1/links/          — crear link acortado
  GET    /api/v1/links/          — listar links del usuario
  GET    /api/v1/links/<code>/   — detalle de un link
  DELETE /api/v1/links/<code>/   — eliminar link
  GET    /api/v1/stats/<code>/   — analytics de un link
  POST   /api/v1/keys/           — crear API key
  GET    /api/v1/keys/           — listar API keys

Auth: header X-API-Key
Rate limit: según plan (starter=1000, pro=5000, business=25000 req/mes)
"""

import hashlib
import json
import logging
import secrets
import string

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.urls import path, include
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import ShortLink, ApiKey, ClickEvent, Subscription
from acortador_project.rate_limit import rate_limit, client_ip

logger = logging.getLogger(__name__)

PLAN_API_LIMITS = {
    'starter': 1000,
    'pro': 5000,
    'business': 25000,
}


def _json_error(msg, status=400):
    return JsonResponse({'error': msg}, status=status)


def _get_user_from_api_key(request):
    """Autentica via X-API-Key header. Returns (user, api_key) or (None, None)."""
    key = request.headers.get('X-API-Key', '').strip()
    if not key:
        return None, None
    try:
        api_key = ApiKey.objects.select_related('user').get(key=key, active=True)
    except ApiKey.DoesNotExist:
        return None, None
    api_key.ultimo_uso = timezone.now()
    api_key.save(update_fields=['ultimo_uso'])
    return api_key.user, api_key


def _check_api_rate_limit(user):
    """Rate limit mensual por plan. Usa Redis counter con TTL de 30 días."""
    plan = 'starter'
    try:
        sub = user.subscription
        if sub.status == Subscription.STATUS_ACTIVE:
            plan = sub.plan
    except Subscription.DoesNotExist:
        pass

    limit = PLAN_API_LIMITS.get(plan, 1000)
    key = f'api_rl:{user.pk}:{timezone.now().strftime("%Y%m")}'
    try:
        current = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=2592000)  # 30 días
        current = 1

    return current <= limit, limit, current


def _api_auth(view_func):
    """Decorator: autentica via API key y aplica rate limit."""
    def wrapper(request, *args, **kwargs):
        user, api_key = _get_user_from_api_key(request)
        if not user:
            return _json_error('API key inválida o no proporcionada.', 401)

        allowed, limit, used = _check_api_rate_limit(user)
        if not allowed:
            resp = _json_error(
                f'Límite de API alcanzado ({used}/{limit} req/mes). '
                f'Considerá upgradear tu plan.',
                429,
            )
            resp['X-RateLimit-Limit'] = str(limit)
            resp['X-RateLimit-Used'] = str(used)
            return resp

        request.api_user = user
        return view_func(request, *args, **kwargs)
    return wrapper


def _generate_short_code(length=6):
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _link_to_dict(link, request):
    return {
        'short_code': link.short_code,
        'short_url': request.build_absolute_uri(f'/s/{link.short_code}/'),
        'original_url': link.original_url,
        'is_premium': link.is_premium,
        'clicks': link.clicks,
        'has_seo': link.has_seo,
        'has_password': bool(link.password_hash),
        'created_at': link.creado.isoformat() if link.creado else None,
        'qr_url': request.build_absolute_uri(f'/qr/{link.short_code}/'),
    }


@csrf_exempt
@require_http_methods(['GET', 'POST'])
@_api_auth
def api_links(request):
    """GET/POST /api/v1/links/ — listar o crear links."""
    if request.method == 'POST':
        return _api_create_link_impl(request)
    return _api_list_links_impl(request)


def _api_create_link_impl(request):
    """POST /api/v1/links/ — crear link acortado."""
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error('Body debe ser JSON válido.')

    url = (body.get('url') or '').strip()
    if not url:
        return _json_error('Campo "url" es requerido.')
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    user = request.api_user
    is_premium = _is_premium(user)

    # Check link limit
    from .views import _check_link_limit
    allowed, limit = _check_link_limit(user)
    if not allowed:
        return _json_error(
            f'Límite de links alcanzado ({limit}). '
            f'Upgradear plan para más links.',
            429,
        )

    # Check duplicate
    url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
    existing = ShortLink.objects.filter(owner=user, url_hash=url_hash).first()
    if existing:
        return JsonResponse({'link': _link_to_dict(existing, request), 'created': False})

    # Generate unique code
    for _ in range(10):
        code = _generate_short_code()
        if not ShortLink.objects.filter(short_code=code).exists():
            break
    else:
        return _json_error('No se pudo generar un código único.', 500)

    link = ShortLink.objects.create(
        short_code=code,
        original_url=url,
        url_hash=url_hash,
        owner=user,
        is_premium=is_premium,
    )

    return JsonResponse({'link': _link_to_dict(link, request), 'created': True}, status=201)


def _api_list_links_impl(request):
    """GET /api/v1/links/ — listar links del usuario."""
    user = request.api_user
    links = ShortLink.objects.filter(owner=user).order_by('-creado')[:100]
    return JsonResponse({
        'links': [_link_to_dict(l, request) for l in links],
        'count': len(links),
    })


@csrf_exempt
@require_http_methods(['GET', 'DELETE'])
@_api_auth
def api_link_detail(request, code):
    """GET/DELETE /api/v1/links/<code>/ — detalle o eliminar link."""
    user = request.api_user
    link = ShortLink.objects.filter(owner=user, short_code=code).first()
    if not link:
        return _json_error('Link no encontrado.', 404)

    if request.method == 'DELETE':
        link.delete()
        return JsonResponse({'deleted': True})

    return JsonResponse({'link': _link_to_dict(link, request)})


@csrf_exempt
@require_http_methods(['GET'])
@_api_auth
def api_link_stats(request, code):
    """GET /api/v1/stats/<code>/ — analytics de un link."""
    user = request.api_user
    link = ShortLink.objects.filter(owner=user, short_code=code).first()
    if not link:
        return _json_error('Link no encontrado.', 404)

    # Aggregate click events
    events = link.click_events.all()
    by_device = {}
    by_browser = {}
    by_country = {}
    by_referrer = {}
    for ev in events:
        by_device[ev.get_device_display() or 'other'] = by_device.get(ev.get_device_display() or 'other', 0) + 1
        by_browser[ev.browser] = by_browser.get(ev.browser, 0) + 1
        by_country[ev.country] = by_country.get(ev.country, 0) + 1
        ref = ev.referrer or '(direct)'
        by_referrer[ref] = by_referrer.get(ref, 0) + 1

    return JsonResponse({
        'short_code': link.short_code,
        'total_clicks': link.clicks,
        'by_device': by_device,
        'by_browser': by_browser,
        'by_country': by_country,
        'by_referrer': by_referrer,
    })


@csrf_exempt
@require_http_methods(['GET', 'POST'])
@_api_auth
def api_keys(request):
    """GET/POST /api/v1/keys/ — listar o crear API keys."""
    if request.method == 'POST':
        user = request.api_user
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            body = {}
        name = (body.get('name') or 'Default').strip()[:60]

        key = ApiKey.generate_key()
        api_key = ApiKey.objects.create(user=user, key=key, name=name)
        return JsonResponse({
            'key': key,
            'name': api_key.name,
            'created_at': api_key.creado.isoformat(),
        }, status=201)

    user = request.api_user
    keys = user.api_keys.filter(active=True).values('name', 'creado', 'ultimo_uso')
    return JsonResponse({
        'keys': [
            {
                'name': k['name'],
                'created_at': k['creado'].isoformat() if k['creado'] else None,
                'last_used': k['ultimo_uso'].isoformat() if k['ultimo_uso'] else None,
            }
            for k in keys
        ],
    })


def _is_premium(user):
    """Check if user has active subscription."""
    try:
        sub = user.subscription
        return sub.status == Subscription.STATUS_ACTIVE
    except Subscription.DoesNotExist:
        return False


api_urls = [
    path('links/', api_links, name='api_links'),
    path('links/<str:code>/', api_link_detail, name='api_link_detail'),
    path('stats/<str:code>/', api_link_stats, name='api_link_stats'),
    path('keys/', api_keys, name='api_keys'),
]
