import csv
import hashlib
import io
import logging
import re
import secrets
import string
from urllib.parse import urlparse

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.core.cache import cache
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from django.core.mail import send_mail
from django.conf import settings

import stripe

from acortador_project.rate_limit import rate_limit, burst_limit, client_ip

from .decorators import acortador_login_required, subscription_required
from .forms import ContactoAcortalinkForm
from .models import (
    ShortLink, Subscription, LinkReport, EmailVerification,
    CustomDomain, TeamMember, Webhook, ApiKey,
)
from . import stripe_service
from . import mercadopago_service
from . import verification_service

logger = logging.getLogger(__name__)


def _payment_provider():
    """Return the active payment provider ('mercadopago' or 'stripe')."""
    return getattr(settings, 'PAYMENT_PROVIDER', 'mercadopago')


def _stripe_enabled():
    return bool(getattr(settings, 'STRIPE_SECRET_KEY', ''))


def _mp_enabled():
    return bool(getattr(settings, 'MERCADOPAGO_ACCESS_TOKEN', ''))


# --- 2FA Action Handlers ---

def _handle_password_change(request, data):
    """Execute password change after 2FA verification."""
    from django.contrib.auth import update_session_auth_hash
    user = request.user
    user.set_password(data['new_password'])
    user.save()
    update_session_auth_hash(request, user)
    return True, None, reverse('core:perfil') + '?password_changed=1'


verification_service.register_action(
    'password_change', _handle_password_change, 'Cambio de contraseña',
)


# --- Helpers ---

# Dominios bloqueados (phishing, malware, spam)
_BLOCKED_DOMAINS = {
    'bit.ly', 'tinyurl.com', 't.co',  # otros acortadores (evitar chains)
    'ngrok.io', 'ngrok.app',  # tunnels temporales
    '000webhostapp.com',
}

# Límites de links por usuario
FREE_LINK_LIMIT = 30
FREE_EXPIRY_DAYS = 30


def _normalize_url(url):
    """Normaliza una URL para deduplicacion consistente.

    - Lowercase en esquema y dominio
    - Quita www. del dominio
    - Quita puerto default (:80 http, :443 https)
    - Quita trailing slash del path (excepto raiz)
    - Quita fragment (#...)
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    # Quitar www.
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    # Quitar puerto default
    if scheme == 'http' and netloc.endswith(':80'):
        netloc = netloc[:-3]
    elif scheme == 'https' and netloc.endswith(':443'):
        netloc = netloc[:-4]
    # Path: quitar trailing slash (excepto raiz)
    path = parsed.path
    if path == '/':
        path = ''
    elif len(path) > 1 and path.endswith('/'):
        path = path.rstrip('/')
    # Reconstruir sin fragment
    return f'{scheme}://{netloc}{path}' + (
        f'?{parsed.query}' if parsed.query else ''
    )


# Regex para dominio válido: debe tener al menos un punto y un TLD no numérico
_DOMAIN_RE = re.compile(
    r'^(?!\d+\.\d+$)'  # no solo numeros (ej: 321.654)
    r'[a-z0-9]([a-z0-9-]*[a-z0-9])?'  # etiqueta
    r'(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'  # .tld (al menos uno)
)


def _validate_url(url, request=None):
    """Valida una URL: formato, dominio, blocklist y self-referencia.

    Retorna (is_valid, error_msg).
    """
    # 1. Validación básica con Django
    validator = URLValidator()
    try:
        validator(url)
    except ValidationError:
        return False, 'URL inválida: formato incorrecto.'

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().lstrip('www.')

        # 2. Debe tener dominio con TLD válido (no "3216546")
        if not domain or not _DOMAIN_RE.match(domain):
            return False, 'URL inválida: el dominio no es válido.'

        # 3. Rechazar HTTP (sin SSL) — todos los usuarios
        if parsed.scheme == 'http':
            return False, (
                'Solo se aceptan URLs con HTTPS (certificado SSL). '
                'Las URLs http:// son inseguras y no serán aceptadas.'
            )

        # 4. Blocklist
        for blocked in _BLOCKED_DOMAINS:
            if domain == blocked or domain.endswith('.' + blocked):
                return False, f'Dominio bloqueado: {domain}'

        # 5. No permitir acortar links del propio sitio (evitar chains)
        if request is not None:
            own_host = request.get_host().lower().lstrip('www.')
            if domain == own_host or domain.endswith('.' + own_host):
                # Verificar si es un link acortado nuestro
                path = parsed.path.strip('/')
                if path.startswith('s/'):
                    return False, 'No podés acortar un link que ya está acortado en esta plataforma.'
                return False, 'No podés acortar URLs de este sitio.'

        return True, None
    except Exception:
        return False, 'URL inválida.'


def _check_link_limit(user):
    """Verifica si el usuario puede crear más links."""
    sub = getattr(user, 'subscription', None)
    if sub and sub.is_active:
        limit = Subscription.PLAN_LINK_LIMITS.get(sub.plan, 3000)
    else:
        limit = FREE_LINK_LIMIT
    count = ShortLink.objects.filter(owner=user).count()
    return count < limit, limit


def _generate_short_code(length=6):
    """Genera un código corto único alfanumérico.

    Usa secrets.choice con 62 caracteres (a-z, A-Z, 0-9).
    62^6 = 56.8B combinaciones — probabilidad de colisión despreciable.
    Sin DB query por código (vs implementación anterior).
    """
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _generate_short_codes(count, length=6):
    """Genera múltiples códigos únicos en batch (para bulk_create).

    Retorna una lista de códigos únicos sin DB queries.
    """
    alphabet = string.ascii_letters + string.digits
    codes = set()
    while len(codes) < count:
        code = ''.join(secrets.choice(alphabet) for _ in range(length))
        codes.add(code)
    return list(codes)


def _parse_redirect_seconds(value):
    """Parsea redirect_seconds desde CSV/string, con clamp 0-30."""
    try:
        return max(0, min(30, int(value or 5)))
    except (ValueError, TypeError):
        return 5


def _is_premium(user):
    sub = getattr(user, 'subscription', None)
    return sub is not None and sub.is_active


def _check_rate_limit(user, request=None):
    """Rate limit: 50 links/hora para free, 500/hora para premium."""
    limit = 500 if _is_premium(user) else 50
    allowed, _ = rate_limit(f'link_quota:{user.pk}', limit=limit, ttl=3600, request=request)
    return allowed


# --- Views ---

def index(request):
    """Landing + form de acortar."""
    created_link = None
    short_url = None
    error = None

    is_premium = request.user.is_authenticated and _is_premium(request.user)
    user_domains = []
    if is_premium:
        user_domains = list(CustomDomain.objects.filter(
            owner=request.user, status=CustomDomain.STATUS_ACTIVE,
        ))

    if request.method == 'POST':
        if not request.user.is_authenticated:
            return redirect('core:login')
        original_url = request.POST.get('url', '').strip()
        if not original_url:
            error = 'Ingresá una URL válida.'
        elif not original_url.startswith(('http://', 'https://')):
            original_url = 'https://' + original_url
        if original_url and not error:
            original_url = _normalize_url(original_url)
            is_valid, url_error = _validate_url(original_url, request)
            if not is_valid:
                error = url_error
            elif not _check_rate_limit(request.user, request):
                error = 'Límite de links por hora alcanzado. Intentá más tarde.'
            else:
                # Deduplicacion: si el usuario ya acorto esta URL, devolver el existente
                url_hash = hashlib.sha256(original_url.encode('utf-8')).hexdigest()
                existing = ShortLink.objects.filter(
                    owner=request.user, url_hash=url_hash
                ).first()
                if existing:
                    created_link = existing
                    if existing.custom_domain:
                        short_url = f'https://{existing.custom_domain.domain}/s/{existing.short_code}/'
                    else:
                        short_url = request.build_absolute_uri(
                            f'/s/{existing.short_code}/'
                        )
                else:
                    can_create, limit = _check_link_limit(request.user)
                    if not can_create:
                        error = f'Alcanzaste el límite de {limit} links. {"Subscribite premium para más." if not _is_premium(request.user) else ""}'
                    else:
                        # Custom domain selection
                        selected_domain = None
                        if is_premium:
                            domain_pk = request.POST.get('custom_domain', '')
                            if domain_pk:
                                selected_domain = next(
                                    (d for d in user_domains if str(d.pk) == domain_pk), None
                                )

                        link = ShortLink.objects.create(
                            short_code=_generate_short_code(),
                            original_url=original_url,
                            owner=request.user,
                            is_premium=_is_premium(request.user),
                            custom_domain=selected_domain,
                        )
                        created_link = link
                        cache.delete(_redirect_cache_key(link.short_code))
                        if link.custom_domain:
                            short_url = f'https://{link.custom_domain.domain}/s/{link.short_code}/'
                        else:
                            short_url = request.build_absolute_uri(
                                f'/s/{link.short_code}/'
                            )

    return render(request, 'core/index.html', {
        'created_link': created_link,
        'short_url': short_url,
        'error': error,
        'is_premium': is_premium,
        'user_domains': user_domains,
    })


def _is_link_expired(link):
    """Verifica si un link free expiró (90 días sin clicks)."""
    if link.is_premium:
        return False
    if not link.ultimo_click:
        # Nunca fue clickeado: comparar con fecha de creación
        expiry = link.creado + timezone.timedelta(days=FREE_EXPIRY_DAYS)
    else:
        expiry = link.ultimo_click + timezone.timedelta(days=FREE_EXPIRY_DAYS)
    return timezone.now() > expiry


REDIRECT_CACHE_TTL = 300  # 5 min — cache de URLs de redirect
REDIRECT_CACHE_PREFIX = 'rdr:'


def _redirect_cache_key(code):
    return f'{REDIRECT_CACHE_PREFIX}{code}'


def redirect_view(request, code):
    """Maneja el redirect del link acortado.

    Free: 302 instantáneo, no indexable.
    Premium con SEO: página HTML indexable con countdown.
    Premium sin SEO: 302 instantáneo.
    Links free expirados (30 días sin clicks): 404.
    Password protection: si el link tiene password, muestra form.
    """
    # Rate limit por IP: 60 redirects/minuto (prevenir DoS) — antes de DB lookup
    ip = client_ip(request)
    allowed, _ = rate_limit(f'redir:{ip}', limit=60, ttl=60, request=request)
    if not allowed:
        return HttpResponse(status=429)

    # Fast path: cache hit en Redis (sub-ms, 0 DB queries)
    cache_key = _redirect_cache_key(code)
    cached = cache.get(cache_key)
    if cached is not None:
        # cached = {'url': str, 'pk': int, 'is_premium': bool, 'seo_title': str, 'has_password': bool}
        _track_click(cached['pk'])
        _track_click_event(cached['pk'], request)
        if cached.get('has_password'):
            link = ShortLink.objects.only(
                'pk', 'short_code', 'original_url', 'is_premium', 'password_hash',
                'seo_title', 'seo_description', 'seo_image', 'redirect_seconds',
            ).filter(short_code=code).first()
            if link:
                return _handle_password_redirect(request, link)
        if cached.get('is_premium') and cached.get('seo_title'):
            # SEO pages necesitan render completo — query DB solo en este caso
            link = ShortLink.objects.only(
                'pk', 'short_code', 'original_url', 'is_premium',
                'seo_title', 'seo_description', 'seo_image', 'redirect_seconds',
            ).filter(short_code=code).first()
            if link:
                return render(request, 'core/redirect_page.html', {'link': link})
        return HttpResponseRedirect(cached['url'])

    # Cache miss → DB lookup con campos mínimos
    link = ShortLink.objects.only(
        'pk', 'short_code', 'original_url', 'is_premium',
        'seo_title', 'seo_description', 'seo_image', 'redirect_seconds',
        'password_hash', 'creado', 'ultimo_click', 'clicks',
    ).filter(short_code=code).first()

    if not link:
        # Cache negativo por 60s para evitar queries repetidos a códigos inválidos
        cache.set(cache_key, None, timeout=60)
        from django.http import Http404
        raise Http404('Link no encontrado.')

    # Verificar expiración de links free
    if _is_link_expired(link):
        link.delete()
        cache.delete(cache_key)
        from django.http import Http404
        raise Http404('Este link expiró por inactividad.')

    # Poblar cache
    cache_data = {
        'url': link.original_url,
        'pk': link.pk,
        'is_premium': link.is_premium,
        'seo_title': link.seo_title or '',
        'has_password': bool(link.password_hash),
    }
    cache.set(cache_key, cache_data, timeout=REDIRECT_CACHE_TTL)

    # Click tracking: INCR + SADD para flush eficiente
    _track_click(link.pk)
    _track_click_event(link.pk, request)

    # Password protection
    if link.password_hash:
        return _handle_password_redirect(request, link)

    if link.is_premium and link.seo_title:
        return render(request, 'core/redirect_page.html', {
            'link': link,
        })

    # Free o premium sin SEO: redirect instantáneo
    return HttpResponseRedirect(link.original_url)


def _track_click(link_pk):
    """Acumula click en Redis: INCR contador + SADD al set de PKs pendientes.

    flush_clicks solo itera los PKs del SET, no todos los links.
    """
    click_key = f'clicks:{link_pk}'
    try:
        cache.incr(click_key)
    except ValueError:
        cache.set(click_key, 1, timeout=900)
    # Registrar PK en SET para flush_clicks (best-effort, no falla si no soporta)
    try:
        client = cache._cache.get_client()
        client.sadd(getattr(settings, 'CLICK_TRACKING_SET_KEY', 'clicks:pending_pks'), link_pk)
    except (AttributeError, NotImplementedError, Exception):
        pass  # Fallback: flush_clicks usa legacy mode iterando todos los links


def _track_click_event(link_pk, request):
    """Encola evento de click para analytics geo/device/referrer en Redis.

    Se persiste en batch desde flush_clicks para evitar 1 DB write por click.
    """
    import json
    event_key = f'click_events:{link_pk}'
    event = {
        'pk': link_pk,
        'ip': client_ip(request),
        'ua': request.META.get('HTTP_USER_AGENT', ''),
        'ref': request.META.get('HTTP_REFERER', '')[:500],
    }
    try:
        client = cache._cache.get_client()
        client.rpush(event_key, json.dumps(event))
    except (AttributeError, NotImplementedError, Exception):
        pass


def _handle_password_redirect(request, link):
    """Muestra form de password y valida antes de redirigir."""
    if request.method == 'POST' and request.POST.get('password'):
        import hashlib as _hl
        entered = _hl.sha256(request.POST.get('password', '').encode()).hexdigest()
        if entered == link.password_hash:
            if link.is_premium and link.seo_title:
                return render(request, 'core/redirect_page.html', {'link': link})
            return HttpResponseRedirect(link.original_url)
        return render(request, 'core/password_protected.html', {
            'link': link, 'error': 'Contraseña incorrecta.',
        })
    return render(request, 'core/password_protected.html', {'link': link})


def qr_code(request, code):
    """Genera QR code PNG o SVG para un link acortado."""
    from django.http import Http404

    link = ShortLink.objects.only('pk', 'short_code', 'owner').filter(short_code=code).first()
    if not link:
        raise Http404('Link no encontrado.')

    fmt = request.GET.get('format', 'png')
    short_url = request.build_absolute_uri(f'/s/{link.short_code}/')

    import qrcode
    import io as _io

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(short_url)
    qr.make(fit=True)

    if fmt == 'svg':
        import qrcode.image.svg as svg_image
        factory = svg_image.SvgImage
        img = qrcode.make(short_url, image_factory=factory)
        buf = _io.BytesIO()
        img.save(buf)
        return HttpResponse(buf.getvalue(), content_type='image/svg+xml')

    img = qr.make_image(fill_color='black', back_color='white')
    buf = _io.BytesIO()
    img.save(buf, format='PNG')
    return HttpResponse(buf.getvalue(), content_type='image/png')


@acortador_login_required
def mis_links(request):
    """Dashboard de links del usuario con filtros por tier y paginación."""
    links = ShortLink.objects.filter(owner=request.user)
    is_premium = _is_premium(request.user)

    # --- Filtros comunes (free + premium) ---
    q = request.GET.get('q', '').strip()
    if q:
        links = links.filter(short_code__icontains=q)

    seo_filter = request.GET.get('seo', '')
    if seo_filter == 'yes':
        links = links.filter(has_seo=True)
    elif seo_filter == 'no':
        links = links.filter(has_seo=False)

    if is_premium:
        # --- Filtros avanzados (premium only) ---
        link_type = request.GET.get('type', '')
        if link_type == 'free':
            links = links.filter(is_premium=False)
        elif link_type == 'premium':
            links = links.filter(is_premium=True)

        sort = request.GET.get('sort', '-creado')
        valid_sorts = ['-creado', 'creado', '-clicks', 'clicks']
        if sort not in valid_sorts:
            sort = '-creado'
        links = links.order_by(sort)

        date_from = request.GET.get('from', '').strip()
        date_to = request.GET.get('to', '').strip()
        if date_from:
            try:
                from datetime import datetime
                dt_from = datetime.strptime(date_from, '%Y-%m-%d')
                links = links.filter(creado__date__gte=dt_from.date())
            except ValueError:
                pass
        if date_to:
            try:
                from datetime import datetime
                dt_to = datetime.strptime(date_to, '%Y-%m-%d')
                links = links.filter(creado__date__lte=dt_to.date())
            except ValueError:
                pass
    else:
        links = links.order_by('-creado')

    # --- Paginación: 50 links por página ---
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    page = request.GET.get('page', 1)
    paginator = Paginator(links, 50)
    try:
        links_page = paginator.page(page)
    except (EmptyPage, PageNotAnInteger):
        links_page = paginator.page(1)

    return render(request, 'core/mis_links.html', {
        'links': links_page,
        'is_premium': is_premium,
        'q': q,
        'page_obj': links_page,
    })


@acortador_login_required
def export_analytics(request):
    """Exporta analytics de todos los links del usuario como CSV (premium only)."""
    if not _is_premium(request.user):
        return redirect('core:subscribir')

    links = ShortLink.objects.filter(owner=request.user).order_by('-creado')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="analytics.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'short_code', 'short_url', 'original_url', 'clicks',
        'mobile', 'desktop', 'tablet', 'bot', 'other',
        'chrome', 'firefox', 'safari', 'edge', 'opera', 'other_browser',
        'created_at', 'last_click',
    ])

    for link in links:
        events = link.click_events.all()
        device_counts = {'mobile': 0, 'desktop': 0, 'tablet': 0, 'bot': 0, 'other': 0}
        browser_counts = {'Chrome': 0, 'Firefox': 0, 'Safari': 0, 'Edge': 0, 'Opera': 0, 'Other': 0}
        for ev in events:
            device_counts[ev.device] = device_counts.get(ev.device, 0) + 1
            browser_counts[ev.browser] = browser_counts.get(ev.browser, 0) + 1

        short_url = request.build_absolute_uri(f'/s/{link.short_code}/')
        writer.writerow([
            link.short_code, short_url, link.original_url, link.clicks,
            device_counts['mobile'], device_counts['desktop'],
            device_counts['tablet'], device_counts['bot'], device_counts['other'],
            browser_counts['Chrome'], browser_counts['Firefox'],
            browser_counts['Safari'], browser_counts['Edge'],
            browser_counts['Opera'], browser_counts['Other'],
            link.creado.isoformat() if link.creado else '',
            link.ultimo_click.isoformat() if link.ultimo_click else '',
        ])

    return response


@acortador_login_required
def eliminar_link(request, pk):
    """Elimina un link del usuario."""
    link = get_object_or_404(ShortLink, pk=pk, owner=request.user)
    cache.delete(_redirect_cache_key(link.short_code))
    link.delete()
    return redirect('core:mis_links')


@subscription_required
def bulk(request):
    """Bulk: subir CSV de URLs y descargar tabla con links acortados.

    Free: CSV con columna url.
    Premium: CSV con url, seo_title, seo_description, seo_image, redirect_seconds.

    Optimizado para 750 usuarios concurrentes:
    - Pre-fetch de url_hashes existentes (1 query en vez de N)
    - bulk_create para links nuevos (1 query en vez de N)
    - Batch generation de short codes (0 DB queries)
    - transaction.atomic para consistencia
    - Reducción de ~1500 queries a ~3 queries para 500 filas
    """
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        is_premium = _is_premium(request.user)

        if not csv_file:
            return render(request, 'core/bulk.html', {
                'error': 'Seleccioná un archivo CSV.',
                'is_premium': is_premium,
            })

        # --- Validaciones ---
        MAX_SIZE = 2 * 1024 * 1024  # 2 MB
        if csv_file.size > MAX_SIZE:
            return render(request, 'core/bulk.html', {
                'error': f'El archivo pesa {csv_file.size / 1024 / 1024:.1f} MB. El máximo es 2 MB.',
                'is_premium': is_premium,
            })

        if not csv_file.name.lower().endswith('.csv'):
            return render(request, 'core/bulk.html', {
                'error': 'El archivo debe tener extensión .csv',
                'is_premium': is_premium,
            })

        try:
            decoded = csv_file.read().decode('utf-8-sig')
        except (UnicodeDecodeError, UnicodeError):
            return render(request, 'core/bulk.html', {
                'error': 'No se pudo leer el archivo. Asegurate de que esté en UTF-8.',
                'is_premium': is_premium,
            })

        reader = csv.DictReader(io.StringIO(decoded))

        # Validar header
        fieldnames = reader.fieldnames or []
        url_field = None
        for f in fieldnames:
            if f.strip().lower() == 'url':
                url_field = f
                break
        if not url_field:
            expected = 'url, seo_title, seo_description, seo_image, redirect_seconds' if is_premium else 'url'
            return render(request, 'core/bulk.html', {
                'error': f'El CSV no tiene la columna "url". Columnas esperadas: {expected}',
                'is_premium': is_premium,
            })

        # --- Fase 1: Parsear y validar todas las filas (0 DB queries) ---
        MAX_ROWS = 500
        parsed_rows = []
        errors = []

        for i, row in enumerate(reader, start=2):
            if i > MAX_ROWS + 1:
                errors.append(f'Se procesaron las primeras {MAX_ROWS} filas. El resto fue ignorado.')
                break
            url = (row.get('url') or row.get('URL') or '').strip()
            if not url:
                continue
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            url = _normalize_url(url)
            is_valid, url_error = _validate_url(url, request)
            if not is_valid:
                errors.append(f'Fila {i}: {url_error}')
                continue

            url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
            seo_data = {}
            if is_premium:
                seo_data = {
                    'seo_title': (row.get('seo_title') or '').strip()[:120],
                    'seo_description': (row.get('seo_description') or '').strip()[:300],
                    'seo_image': (row.get('seo_image') or '').strip(),
                    'redirect_seconds': _parse_redirect_seconds(row.get('redirect_seconds')),
                }

            parsed_rows.append({
                'row_num': i,
                'url': url,
                'url_hash': url_hash,
                'seo_data': seo_data,
            })

        if not parsed_rows:
            return render(request, 'core/bulk.html', {
                'error': 'No se pudieron procesar URLs del archivo.',
                'is_premium': is_premium,
            })

        # --- Fase 2: Pre-fetch links existentes (1 query) ---
        url_hashes = [r['url_hash'] for r in parsed_rows]
        existing_links = ShortLink.objects.filter(
            owner=request.user, url_hash__in=url_hashes
        ).in_bulk(field_name='url_hash')

        # --- Fase 3: Separar nuevos de existentes y preparar objetos ---
        from django.db import transaction

        new_links_to_create = []
        existing_to_update = []
        results = []

        # Necesitamos códigos solo para los nuevos
        new_count = sum(1 for r in parsed_rows if r['url_hash'] not in existing_links)
        new_codes = _generate_short_codes(new_count) if new_count else []
        code_idx = 0

        for r in parsed_rows:
            existing = existing_links.get(r['url_hash'])
            if existing:
                # Link ya existe — actualizar SEO si es premium
                if is_premium and r['seo_data']['seo_title']:
                    existing.is_premium = True
                    existing.seo_title = r['seo_data']['seo_title']
                    existing.seo_description = r['seo_data']['seo_description']
                    existing.seo_image = r['seo_data']['seo_image']
                    existing.redirect_seconds = r['seo_data']['redirect_seconds']
                    existing.needs_ping = True
                    existing_to_update.append(existing)
                results.append({
                    'original': r['url'],
                    'short': request.build_absolute_uri(f'/s/{existing.short_code}/'),
                    'code': existing.short_code,
                    'seo_title': existing.seo_title if is_premium else '',
                    'seo_description': existing.seo_description if is_premium else '',
                    'seo_image': existing.seo_image if is_premium else '',
                    'redirect_seconds': existing.redirect_seconds if is_premium else '',
                })
            else:
                # Link nuevo — crear objeto (sin save aún)
                code = new_codes[code_idx]
                code_idx += 1
                seo = r['seo_data']
                link = ShortLink(
                    short_code=code,
                    original_url=r['url'],
                    owner=request.user,
                    is_premium=is_premium,
                    url_hash=r['url_hash'],
                    has_seo=bool(seo.get('seo_title') or seo.get('seo_description')),
                    seo_title=seo.get('seo_title', '') if is_premium else '',
                    seo_description=seo.get('seo_description', '') if is_premium else '',
                    seo_image=seo.get('seo_image', '') if is_premium else '',
                    redirect_seconds=seo.get('redirect_seconds', 5) if is_premium else 5,
                    needs_ping=bool(seo.get('seo_title')) if is_premium else False,
                )
                new_links_to_create.append(link)
                results.append({
                    'original': r['url'],
                    'short': request.build_absolute_uri(f'/s/{code}/'),
                    'code': code,
                    'seo_title': seo.get('seo_title', '') if is_premium else '',
                    'seo_description': seo.get('seo_description', '') if is_premium else '',
                    'seo_image': seo.get('seo_image', '') if is_premium else '',
                    'redirect_seconds': seo.get('redirect_seconds', '') if is_premium else '',
                })

        # --- Fase 4: Persistir en DB (2-3 queries total) ---
        with transaction.atomic():
            if new_links_to_create:
                ShortLink.objects.bulk_create(new_links_to_create, batch_size=100)
            if existing_to_update:
                # bulk_update de campos SEO
                from django.db.models import F
                for link in existing_to_update:
                    link.save(update_fields=[
                        'is_premium', 'seo_title', 'seo_description',
                        'seo_image', 'redirect_seconds', 'needs_ping',
                        'has_seo', 'actualizado',
                    ])

        # Invalidar cache de redirects para links actualizados
        for link in existing_to_update:
            cache.delete(_redirect_cache_key(link.short_code))

        if not results:
            return render(request, 'core/bulk.html', {
                'error': 'No se pudieron procesar URLs del archivo.',
                'is_premium': is_premium,
            })

        fmt = request.POST.get('format', 'csv')
        if fmt == 'xlsx':
            return _generate_xlsx(results, is_premium)
        return _generate_csv(results, is_premium)

    return render(request, 'core/bulk.html', {
        'is_premium': _is_premium(request.user),
    })


@acortador_login_required
def plantilla_csv(request):
    """Descarga plantilla CSV según tier.

    Free: url
    Premium: url, seo_title, seo_description, seo_image, redirect_seconds
    """
    is_premium = _is_premium(request.user)
    response = HttpResponse(content_type='text/csv')
    filename = 'plantilla_premium.csv' if is_premium else 'plantilla_free.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    if is_premium:
        writer.writerow(['url', 'seo_title', 'seo_description', 'seo_image', 'redirect_seconds'])
        writer.writerow(['https://ejemplo.com/pagina1', 'Título SEO de ejemplo', 'Descripción SEO de ejemplo', 'https://ejemplo.com/imagen.jpg', '5'])
        writer.writerow(['https://ejemplo.com/pagina2', 'Otro título', 'Otra descripción', '', '0'])
    else:
        writer.writerow(['url'])
        writer.writerow(['https://ejemplo.com/pagina1'])
        writer.writerow(['https://ejemplo.com/pagina2'])

    return response


@acortador_login_required
def editar_link(request, pk):
    """Edita un link del usuario.

    - Free: edita solo la URL original.
    - Premium: edita URL + metadata SEO completa + password + redirect.
    """
    link = get_object_or_404(ShortLink, pk=pk, owner=request.user)
    is_premium = _is_premium(request.user)
    error = ''
    success = False

    # User's active custom domains
    user_domains = CustomDomain.objects.filter(
        owner=request.user, status=CustomDomain.STATUS_ACTIVE,
    ) if is_premium else []

    if request.method == 'POST':
        new_url = request.POST.get('original_url', '').strip()
        if not new_url:
            error = 'La URL no puede estar vacía.'
        else:
            if not new_url.startswith(('http://', 'https://')):
                new_url = 'https://' + new_url
            new_url = _normalize_url(new_url)
            is_valid, url_error = _validate_url(new_url, request)
            if not is_valid:
                error = url_error
            else:
                link.original_url = new_url

                # Custom domain assignment (premium only)
                if is_premium:
                    domain_pk = request.POST.get('custom_domain', '')
                    if domain_pk:
                        domain = user_domains.filter(pk=domain_pk).first()
                        if domain:
                            link.custom_domain = domain
                        else:
                            link.custom_domain = None
                    else:
                        link.custom_domain = None

                if is_premium:
                    link.seo_title = request.POST.get('seo_title', '')[:120]
                    link.seo_description = request.POST.get('seo_description', '')[:300]

                    # OG image: upload file or use URL
                    uploaded_image = request.FILES.get('seo_image_file')
                    if uploaded_image:
                        try:
                            from PIL import Image as PILImage
                            import os as _os

                            img = PILImage.open(uploaded_image)
                            if img.mode in ('RGBA', 'P'):
                                img = img.convert('RGB')

                            # Resize to max 1200x630 maintaining aspect ratio
                            max_size = (1200, 630)
                            img.thumbnail(max_size, PILImage.LANCZOS)

                            # Save as WebP
                            media_dir = settings.MEDIA_ROOT / 'og_images'
                            media_dir.mkdir(parents=True, exist_ok=True)
                            filename = f'og_{link.short_code}.webp'
                            filepath = media_dir / filename
                            img.save(filepath, 'WEBP', quality=82, method=6)

                            link.seo_image = f'{settings.MEDIA_URL}og_images/{filename}'
                        except Exception as e:
                            logger.warning('OG image upload failed: %s', e)
                            error = 'No se pudo procesar la imagen. Verificá que sea un archivo válido (JPG, PNG, WebP).'
                    else:
                        link.seo_image = request.POST.get('seo_image', '')

                    try:
                        link.redirect_seconds = max(0, min(30, int(request.POST.get('redirect_seconds', 5))))
                    except (ValueError, TypeError):
                        link.redirect_seconds = 5
                    link.needs_ping = True

                    # Password protection
                    password = request.POST.get('password', '').strip()
                    if password:
                        import hashlib as _hl
                        link.password_hash = _hl.sha256(password.encode()).hexdigest()
                    else:
                        link.password_hash = ''

                if not error:
                    link.save()
                    cache.delete(_redirect_cache_key(link.short_code))
                    success = True

    # Build short URL with custom domain if assigned
    if link.custom_domain:
        short_url = f'https://{link.custom_domain.domain}/s/{link.short_code}/'
    else:
        short_url = request.build_absolute_uri(f'/s/{link.short_code}/')

    return render(request, 'core/editar_link.html', {
        'link': link,
        'short_url': short_url,
        'is_premium': is_premium,
        'error': error,
        'success': success,
        'user_domains': user_domains,
    })


def registro(request):
    """Registro de nuevo usuario con verificación por email."""
    if request.user.is_authenticated:
        return redirect('core:index')

    if request.method == 'POST':
        ip = client_ip(request)

        # Anti-flooding: 3 registros por IP por hora
        allowed, _ = rate_limit(f'register:{ip}', limit=3, ttl=3600, request=request)
        if not allowed:
            return render(request, 'core/registro.html', {
                'form': UserCreationForm(),
                'email': '',
                'email_error': 'Demasiados registros desde esta IP. Esperá una hora.',
                'is_premium': False,
            })

        form = UserCreationForm(request.POST)
        email = request.POST.get('email', '').strip()

        # Validar email
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        email_error = ''
        if not email:
            email_error = 'El email es obligatorio.'
        else:
            try:
                validate_email(email)
            except ValidationError:
                email_error = 'Email inválido.'

        if form.is_valid() and not email_error:
            from django.contrib.auth.models import User
            if User.objects.filter(email=email).exists():
                return render(request, 'core/registro.html', {
                    'form': form,
                    'email': email,
                    'email_error': 'Ya existe una cuenta con ese email.',
                    'is_premium': False,
                })

            user = form.save(commit=False)
            user.email = email
            user.is_active = False
            user.save()

            verification_service.create_activation_verification(user)

            return render(request, 'core/registro_pendiente.html', {
                'email': email,
                'is_premium': False,
            })
        else:
            return render(request, 'core/registro.html', {
                'form': form,
                'email': email,
                'email_error': email_error,
                'is_premium': False,
            })
    else:
        form = UserCreationForm()

    return render(request, 'core/registro.html', {
        'form': form,
        'is_premium': False,
    })


def activar_cuenta(request, token):
    """Activa una cuenta usando el token enviado por email."""
    user, error = verification_service.verify_activation_token(token)

    if error:
        return render(request, 'core/activar_cuenta.html', {
            'error': error,
            'is_premium': False,
        })

    return render(request, 'core/activar_cuenta.html', {
        'user': user,
        'is_premium': False,
    })


def login_acortador(request):
    """Login propio del acortador, redirige a ?next= o / tras autenticar."""
    next_url = request.GET.get('next', '') or request.POST.get('next', '')

    if request.user.is_authenticated:
        return redirect(next_url or 'core:index')

    error = False
    error_msg = ''
    if request.method == 'POST':
        ip = client_ip(request)

        # Brute-force protection: 10 intentos por IP cada 15 min
        allowed, remaining = rate_limit(f'login:{ip}', limit=10, ttl=900, request=request)
        if not allowed:
            error = True
            error_msg = 'Demasiados intentos. Esperá 15 minutos antes de reintentar.'
        else:
            from django.contrib.auth import authenticate
            username = request.POST.get('username')
            password = request.POST.get('password')
            user = authenticate(request, username=username, password=password)
            if user is not None:
                if not user.is_active:
                    error_msg = 'Tu cuenta no está activada. Revisá tu email para el enlace de activación.'
                else:
                    login(request, user)
                    return redirect(next_url or 'core:index')
            error = True

    return render(request, 'core/login.html', {
        'error': error,
        'error_msg': error_msg,
        'is_premium': False,
        'next': next_url,
    })


@acortador_login_required
def subscribir(request):
    """Página de subscripción — muestra planes y estado."""
    has_sub = hasattr(request.user, 'subscription')
    sub_status = request.user.subscription.status if has_sub else None
    sub_plan = request.user.subscription.plan if has_sub else None
    checkout_status = request.GET.get('checkout', '')
    provider = _payment_provider()
    plan_prices = getattr(settings, 'PLAN_PRICES', {})
    return render(request, 'core/subscribir.html', {
        'has_sub': has_sub,
        'sub_status': sub_status,
        'sub_plan': sub_plan,
        'is_premium': _is_premium(request.user),
        'checkout_status': checkout_status,
        'payment_provider': provider,
        'plan_prices': {
            'starter': f'{plan_prices.get("starter", 4900):,}',
            'pro': f'{plan_prices.get("pro", 9800):,}',
            'business': f'{plan_prices.get("business", 28000):,}',
        },
        'mp_public_key': getattr(settings, 'MERCADOPAGO_PUBLIC_KEY', ''),
        'stripe_publishable_key': getattr(settings, 'STRIPE_PUBLISHABLE_KEY', ''),
    })


@acortador_login_required
def checkout(request):
    """Crea una sesión de checkout según el provider activo (MP o Stripe)."""
    if request.method != 'POST':
        return redirect('core:subscribir')

    plan = request.POST.get('plan', 'starter')
    if plan not in ('starter', 'pro', 'business'):
        plan = 'starter'

    provider = _payment_provider()

    try:
        if provider == 'stripe' and _stripe_enabled():
            product, price_id = stripe_service.get_or_create_product()
            checkout_url = stripe_service.create_checkout_session(request.user, price_id)
            return redirect(checkout_url)

        elif provider == 'mercadopago' and _mp_enabled():
            checkout_url = mercadopago_service.create_preapproval(request.user, plan=plan)
            if checkout_url:
                return redirect(checkout_url)
            logger.error('MP checkout: URL vacía para user %s plan %s', request.user.pk, plan)
            return redirect(f'{reverse("core:subscribir")}?checkout=error')

        else:
            logger.error('Checkout: provider %s no configurado (MP=%s, Stripe=%s)',
                         provider, _mp_enabled(), _stripe_enabled())
            return redirect(f'{reverse("core:subscribir")}?checkout=error')

    except Exception as e:
        logger.exception('Checkout error para user %s: %s', request.user.pk, e)
        return redirect(f'{reverse("core:subscribir")}?checkout=error')


@acortador_login_required
def cancelar_subscripcion(request):
    """Cancela la subscripción premium según el provider."""
    if request.method != 'POST':
        return redirect('core:subscribir')

    sub = getattr(request.user, 'subscription', None)
    if sub and sub.status == Subscription.STATUS_ACTIVE and sub.provider_id:
        try:
            if sub.provider == 'mercadopago':
                mercadopago_service.cancel_preapproval(sub.provider_id)
            elif sub.provider == 'stripe' and _stripe_enabled():
                stripe_service.cancel_stripe_subscription(sub.provider_id)
        except Exception:
            pass
        sub.status = Subscription.STATUS_CANCELLED
        sub.fecha_fin = timezone.now()
        sub.save()
    elif sub and sub.status == Subscription.STATUS_ACTIVE:
        sub.status = Subscription.STATUS_CANCELLED
        sub.fecha_fin = timezone.now()
        sub.save()

    return redirect('core:subscribir')


def privacidad(request):
    """Página de política de privacidad (requisito AdSense)."""
    return render(request, 'core/privacidad.html', {
        'is_premium': request.user.is_authenticated and _is_premium(request.user),
    })


def contacto_acortador(request):
    """Formulario de contacto propio del acortador para compra premium."""
    enviado = False
    if request.method == 'POST':
        ip = client_ip(request)

        # Rate limit: 3 mensajes por IP cada hora
        allowed, _ = rate_limit(f'contact:{ip}', limit=3, ttl=3600, request=request)
        if not allowed:
            return render(request, 'core/contacto.html', {
                'form': ContactoAcortalinkForm(),
                'enviado': False,
                'rate_limited': True,
                'is_premium': request.user.is_authenticated and _is_premium(request.user),
            })

        form = ContactoAcortalinkForm(request.POST)
        if form.is_valid():
            nombre = form.cleaned_data['nombre']
            email = form.cleaned_data['email']
            tipo = form.cleaned_data['tipo']
            mensaje = form.cleaned_data['mensaje']
            tipo_label = dict(ContactoAcortalinkForm.TIPO_CHOICES).get(tipo, tipo)
            if settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD:
                send_mail(
                    subject=f'[Acortalink] {tipo_label} — {nombre}',
                    message=(
                        f'Tipo: {tipo_label}\n'
                        f'Nombre: {nombre}\n'
                        f'Email: {email}\n\n'
                        f'Mensaje:\n{mensaje}'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[settings.DEFAULT_FROM_EMAIL],
                    fail_silently=True,
                )
            enviado = True
            form = ContactoAcortalinkForm()
    else:
        form = ContactoAcortalinkForm()

    return render(request, 'core/contacto.html', {
        'form': form,
        'enviado': enviado,
        'is_premium': request.user.is_authenticated and _is_premium(request.user),
    })


@csrf_exempt
def stripe_webhook(request):
    """Webhook de Stripe — procesa eventos de subscripción.

    Solo activo si STRIPE_SECRET_KEY está configurado.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    if not _stripe_enabled():
        return HttpResponse(status=404)

    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

    try:
        event = stripe_service.verify_webhook_signature(payload, sig_header)
    except stripe.error.SignatureVerificationError:
        return HttpResponse(status=400)
    except Exception:
        return HttpResponse(status=400)

    try:
        stripe_service.handle_webhook_event(event)
    except Exception:
        pass

    return HttpResponse(status=200)


@csrf_exempt
def mercadopago_webhook(request):
    """Webhook de Mercado Pago — procesa notificaciones de preapproval/pago.

    MP envía POST con JSON: {"type": "preapproval", "data": {...}}
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    if not _mp_enabled():
        return HttpResponse(status=404)

    import json
    try:
        event_data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return HttpResponse(status=400)

    try:
        mercadopago_service.handle_webhook_event(event_data)
    except Exception:
        pass

    return HttpResponse(status=200)


def reportar_link(request, code):
    """Formulario público para reportar abuso de un link acortado."""
    link = get_object_or_404(ShortLink, short_code=code)

    if request.method == 'POST':
        reason = request.POST.get('reason', '')
        detail = request.POST.get('detail', '')[:500]

        valid_reasons = [r[0] for r in LinkReport.REASON_CHOICES]
        if reason not in valid_reasons:
            return render(request, 'core/reportar.html', {
                'link': link,
                'error': 'Motivo inválido.',
                'is_premium': request.user.is_authenticated and _is_premium(request.user),
            })

        # Rate limit: 1 reporte por IP cada 10 min
        ip = client_ip(request)
        allowed, _ = rate_limit(f'report_quota:{ip}', limit=1, ttl=600, request=request)
        if not allowed:
            return render(request, 'core/reportar.html', {
                'link': link,
                'error': 'Ya enviaste un reporte recientemente. Esperá unos minutos.',
                'is_premium': request.user.is_authenticated and _is_premium(request.user),
            })

        LinkReport.objects.create(
            link=link,
            reason=reason,
            detail=detail,
            reporter=request.user if request.user.is_authenticated else None,
            reporter_ip=ip or None,
        )

        return render(request, 'core/reportar.html', {
            'link': link,
            'success': True,
            'is_premium': request.user.is_authenticated and _is_premium(request.user),
        })

    return render(request, 'core/reportar.html', {
        'link': link,
        'is_premium': request.user.is_authenticated and _is_premium(request.user),
    })


# --- Perfil / Configuración ---

@acortador_login_required
def perfil(request):
    """Configuración del usuario: email, contraseña y estado de subscripción."""
    user = request.user
    has_sub = hasattr(user, 'subscription')
    sub_status = user.subscription.status if has_sub else None
    sub_plan = user.subscription.plan if has_sub else None
    email_updated = False
    password_changed = bool(request.GET.get('password_changed'))
    email_error = ''
    password_error = ''

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'update_email':
            new_email = request.POST.get('email', '').strip()
            if new_email and new_email != user.email:
                from django.core.validators import validate_email
                from django.core.exceptions import ValidationError
                try:
                    validate_email(new_email)
                    from django.contrib.auth.models import User
                    if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
                        email_error = 'Ya existe una cuenta con ese email.'
                    else:
                        user.email = new_email
                        user.save()
                        email_updated = True
                except ValidationError:
                    email_error = 'Email inválido.'

        elif action == 'request_password_change':
            current = request.POST.get('current_password', '')
            new1 = request.POST.get('new_password1', '')
            new2 = request.POST.get('new_password2', '')

            if not user.check_password(current):
                password_error = 'Contraseña actual incorrecta.'
            elif len(new1) < 8:
                password_error = 'La nueva contraseña debe tener al menos 8 caracteres.'
            elif new1 != new2:
                password_error = 'Las contraseñas no coinciden.'
            elif not user.email:
                password_error = 'No tenés email configurado. Agregá uno primero.'
            else:
                verification_service.store_pending_action(
                    request, 'password_change', {'new_password': new1},
                )
                return redirect('core:verificar')

    return render(request, 'core/perfil.html', {
        'has_sub': has_sub,
        'sub_status': sub_status,
        'sub_plan': sub_plan,
        'is_premium': _is_premium(user),
        'email_updated': email_updated,
        'password_changed': password_changed,
        'email_error': email_error,
        'password_error': password_error,
    })


@acortador_login_required
def verificar_accion(request):
    """Vista genérica de verificación 2FA.

    Muestra el input de código, permite reenviar, y ejecuta la acción
    pendiente cuando el código es correcto.
    """
    pending = verification_service.get_pending_action(request)

    if not pending:
        return render(request, 'core/verificar_accion.html', {
            'no_pending': True,
            'is_premium': _is_premium(request.user),
        })

    action_description = verification_service.get_action_description(pending['type'])
    error = ''
    resent = False

    if request.method == 'POST':
        sub_action = request.POST.get('sub_action', '')

        if sub_action == 'resend':
            verification_service.create_action_verification(request.user, pending['type'])
            resent = True

        elif sub_action == 'verify':
            code = request.POST.get('code', '').strip()
            if not code:
                error = 'Ingresá el código de verificación.'
            else:
                redirect_url, v_error = verification_service.execute_pending_action(request, code)
                if v_error:
                    error = v_error
                else:
                    return redirect(redirect_url or 'core:perfil')

        elif sub_action == 'cancel':
            verification_service.clear_pending_action(request)
            return redirect('core:perfil')

    return render(request, 'core/verificar_accion.html', {
        'action_description': action_description,
        'error': error,
        'resent': resent,
        'is_premium': _is_premium(request.user),
    })


# --- Custom Domains ---

PLAN_DOMAIN_LIMITS = {
    'starter': 1,
    'pro': 10,
    'business': 25,
}


@acortador_login_required
def custom_domains(request):
    """Gestión de dominios personalizados (premium only).

    Two paths:
    - BYOD: user brings their own domain, we verify CNAME
    - Purchase: user searches, pays via MP, we register via registrar API
    """
    is_premium = _is_premium(request.user)
    if not is_premium:
        return redirect('core:subscribir')

    from . import domain_service

    sub = getattr(request.user, 'subscription', None)
    plan = sub.plan if sub and sub.is_active else 'starter'
    domain_limit = PLAN_DOMAIN_LIMITS.get(plan, 1)
    domains = CustomDomain.objects.filter(owner=request.user).order_by('-creado')
    for d in domains:
        d.link_count = d.links.count()
    error = ''
    search_result = None
    mp_checkout_url = None
    verify_result = None

    # Handle checkout redirect status
    checkout_status = request.GET.get('checkout', '')

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'add':
            # BYOD: add domain and set to pending
            domain = request.POST.get('domain', '').strip().lower().lstrip('www.')
            if not domain or '.' not in domain:
                error = 'Ingresá un dominio válido (ej: mislinks.com).'
            elif CustomDomain.objects.filter(owner=request.user, domain=domain).exists():
                error = 'Ya tenés ese dominio configurado.'
            elif domains.count() >= domain_limit:
                error = f'Alcanzaste el límite de {domain_limit} dominios para tu plan.'
            else:
                CustomDomain.objects.create(
                    owner=request.user, domain=domain,
                    source=CustomDomain.SOURCE_BYOD,
                )
                return redirect('core:custom_domains')

        elif action == 'verify':
            # Verify DNS CNAME for a BYOD domain
            pk = request.POST.get('pk', '')
            cd = CustomDomain.objects.filter(pk=pk, owner=request.user).first()
            if cd:
                result = domain_service.verify_cname(cd.domain)
                verify_result = result
                if result['verified']:
                    cd.status = CustomDomain.STATUS_ACTIVE
                    cd.dns_verified_at = timezone.now()
                    cd.save(update_fields=['status', 'dns_verified_at', 'actualizado'])
                else:
                    cd.status = CustomDomain.STATUS_FAILED
                    cd.save(update_fields=['status', 'actualizado'])
                return redirect('core:custom_domains')

        elif action == 'search':
            # Search domain availability for purchase
            domain = request.POST.get('domain', '').strip().lower().lstrip('www.')
            if not domain or '.' not in domain:
                error = 'Ingresá un dominio válido (ej: mislinks.com).'
            else:
                search_result = domain_service.check_domain_availability(domain)
                search_result['domain'] = domain

        elif action == 'purchase':
            # Initiate domain purchase via MP
            domain = request.POST.get('domain', '').strip().lower().lstrip('www.')
            if not domain:
                error = 'Ingresá un dominio.'
            elif domains.count() >= domain_limit:
                error = f'Alcanzaste el límite de {domain_limit} dominios para tu plan.'
            else:
                try:
                    mp_checkout_url = domain_service.create_domain_payment(request.user, domain)
                    if mp_checkout_url:
                        return HttpResponseRedirect(mp_checkout_url)
                except RuntimeError as e:
                    error = str(e)

        elif action == 'delete':
            pk = request.POST.get('pk', '')
            CustomDomain.objects.filter(pk=pk, owner=request.user).delete()
            return redirect('core:custom_domains')

    return render(request, 'core/custom_domains.html', {
        'domains': domains,
        'domain_limit': domain_limit,
        'domain_count': domains.count(),
        'plan': plan,
        'error': error,
        'is_premium': is_premium,
        'search_result': search_result,
        'checkout_status': checkout_status,
        'cname_target': getattr(settings, 'CNAME_TARGET', 'app.acortalink.com.ar'),
        'domain_prices': getattr(settings, 'DOMAIN_PRICES', {}),
        'donweb_affiliate_link': getattr(settings, 'DONWEB_AFFILIATE_LINK', ''),
        'donweb_affiliate_enabled': getattr(settings, 'DONWEB_AFFILIATE_ENABLED', False),
    })


# --- Team Members ---

PLAN_TEAM_LIMITS = {
    'business': 5,
}


@acortador_login_required
def team_members(request):
    """Gestión de miembros del team (Business only)."""
    is_premium = _is_premium(request.user)
    sub = getattr(request.user, 'subscription', None)
    plan = sub.plan if sub and sub.is_active else 'starter'

    if plan != 'business':
        return redirect('core:subscribir')

    team_limit = PLAN_TEAM_LIMITS.get(plan, 0)
    members = TeamMember.objects.filter(team_owner=request.user).order_by('-invited_at')
    error = ''

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'invite':
            username = request.POST.get('username', '').strip()
            if not username:
                error = 'Ingresá un nombre de usuario.'
            elif username == request.user.username:
                error = 'No podés invitarte a vos mismo.'
            elif members.count() >= team_limit:
                error = f'Alcanzaste el límite de {team_limit} miembros.'
            else:
                from django.contrib.auth.models import User
                try:
                    invitee = User.objects.get(username=username)
                except User.DoesNotExist:
                    error = 'Usuario no encontrado.'
                else:
                    if TeamMember.objects.filter(team_owner=request.user, user=invitee).exists():
                        error = 'Ese usuario ya es miembro del team.'
                    else:
                        import secrets as _s
                        token = _s.token_urlsafe(32)
                        TeamMember.objects.create(
                            team_owner=request.user,
                            user=invitee,
                            invite_token=token,
                            accepted_at=timezone.now(),
                        )
                        return redirect('core:team_members')
        elif action == 'remove':
            pk = request.POST.get('pk', '')
            TeamMember.objects.filter(pk=pk, team_owner=request.user).delete()
            return redirect('core:team_members')

    return render(request, 'core/team_members.html', {
        'members': members,
        'team_limit': team_limit,
        'team_count': members.count(),
        'error': error,
        'is_premium': is_premium,
    })


# --- Webhooks ---

@acortador_login_required
def webhooks(request):
    """Gestión de webhooks (Business only)."""
    is_premium = _is_premium(request.user)
    sub = getattr(request.user, 'subscription', None)
    plan = sub.plan if sub and sub.is_active else 'starter'

    if plan != 'business':
        return redirect('core:subscribir')

    hooks = Webhook.objects.filter(owner=request.user).order_by('-creado')
    error = ''

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'create':
            url = request.POST.get('url', '').strip()
            events = request.POST.get('events', 'link.created,link.deleted').strip()
            if not url:
                error = 'Ingresá una URL válida.'
            elif not url.startswith(('http://', 'https://')):
                error = 'La URL debe empezar con http:// o https://'
            else:
                secret = Webhook.generate_secret()
                Webhook.objects.create(
                    owner=request.user,
                    url=url,
                    events=events,
                    secret=secret,
                )
                return redirect('core:webhooks')
        elif action == 'delete':
            pk = request.POST.get('pk', '')
            Webhook.objects.filter(pk=pk, owner=request.user).delete()
            return redirect('core:webhooks')

    return render(request, 'core/webhooks.html', {
        'webhooks': hooks,
        'error': error,
        'is_premium': is_premium,
    })


# --- API Keys UI ---

@acortador_login_required
def api_keys(request):
    """Gestión de API keys (premium only)."""
    is_premium = _is_premium(request.user)
    if not is_premium:
        return redirect('core:subscribir')

    keys = ApiKey.objects.filter(user=request.user).order_by('-creado')
    error = ''
    new_key = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'create':
            name = request.POST.get('name', 'Default').strip()[:60]
            key = ApiKey.generate_key()
            ApiKey.objects.create(user=request.user, key=key, name=name)
            new_key = key
        elif action == 'deactivate':
            pk = request.POST.get('pk', '')
            ApiKey.objects.filter(pk=pk, user=request.user).update(active=False)
            return redirect('core:api_keys')

    return render(request, 'core/api_keys.html', {
        'keys': keys,
        'new_key': new_key,
        'error': error,
        'is_premium': is_premium,
    })


# --- Export helpers ---

def _generate_csv(results, is_premium=False):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="links_acortados.csv"'
    writer = csv.writer(response)
    if is_premium:
        writer.writerow(['URL original', 'URL acortada', 'Código', 'Título SEO', 'Descripción SEO', 'Imagen OG', 'Redirect (s)'])
        for r in results:
            writer.writerow([r['original'], r['short'], r['code'], r.get('seo_title', ''), r.get('seo_description', ''), r.get('seo_image', ''), r.get('redirect_seconds', '')])
    else:
        writer.writerow(['URL original', 'URL acortada', 'Código'])
        for r in results:
            writer.writerow([r['original'], r['short'], r['code']])
    return response


def _generate_xlsx(results, is_premium=False):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = 'Links acortados'
    if is_premium:
        ws.append(['URL original', 'URL acortada', 'Código', 'Título SEO', 'Descripción SEO', 'Imagen OG', 'Redirect (s)'])
        for r in results:
            ws.append([r['original'], r['short'], r['code'], r.get('seo_title', ''), r.get('seo_description', ''), r.get('seo_image', ''), r.get('redirect_seconds', '')])
        ws.column_dimensions['D'].width = 40
        ws.column_dimensions['E'].width = 50
        ws.column_dimensions['F'].width = 40
        ws.column_dimensions['G'].width = 12
    else:
        ws.append(['URL original', 'URL acortada', 'Código'])
        for r in results:
            ws.append([r['original'], r['short'], r['code']])

    ws.column_dimensions['A'].width = 60
    ws.column_dimensions['B'].width = 50
    ws.column_dimensions['C'].width = 12

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="links_acortados.xlsx"'
    return response


@login_required
def staff_dashboard(request):
    """Dashboard interno de monitoring para staff."""
    if not request.user.is_staff:
        return redirect('core:index')
    return render(request, 'core/staff_dashboard.html', {
        'is_premium': _is_premium(request.user),
    })


def robots_txt(request):
    """robots.txt para la app Django (app.acortalink.com.ar)."""
    lines = [
        'User-agent: *',
        'Disallow: /admin/',
        'Disallow: /api/',
        'Disallow: /staff/',
        'Disallow: /mis-links/',
        'Disallow: /bulk/',
        'Disallow: /editar/',
        'Disallow: /eliminar/',
        'Disallow: /perfil/',
        'Disallow: /verificar/',
        'Disallow: /subscribir/',
        'Disallow: /dominios/',
        'Disallow: /team/',
        'Disallow: /webhooks/',
        'Disallow: /api-keys/',
        'Disallow: /qr/',
        'Disallow: /*/reportar/',
        '',
        '# AI retrieval crawlers (drive citation traffic)',
        'User-agent: GPTBot',
        'Allow: /',
        '',
        'User-agent: OAI-SearchBot',
        'Allow: /',
        '',
        'User-agent: PerplexityBot',
        'Allow: /',
        '',
        'User-agent: Claude-SearchBot',
        'Allow: /',
        '',
        'User-agent: ClaudeBot',
        'Allow: /',
        '',
        'User-agent: Claude-Web',
        'Allow: /',
        '',
        'User-agent: anthropic-ai',
        'Allow: /',
        '',
        'User-agent: Bytespider',
        'Allow: /',
        '',
        '# Opt out of generative training only',
        'User-agent: Google-Extended',
        'Disallow: /',
        '',
        'User-agent: Applebot-Extended',
        'Disallow: /',
        '',
        'User-agent: CCBot',
        'Disallow: /',
        '',
        'Sitemap: https://app.acortalink.com.ar/sitemap.xml',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain')


def sitemap_xml(request):
    """sitemap.xml dinamico para la app Django.

    Lista paginas publicas estaticas + links premium con SEO habilitado.
    """
    from django.conf import settings as dj_settings
    from datetime import datetime

    site_url = getattr(dj_settings, 'SITE_URL', 'https://app.acortalink.com.ar').rstrip('/')
    now = datetime.now().strftime('%Y-%m-%d')

    urls = [
        {'loc': f'{site_url}/', 'priority': '1.0', 'changefreq': 'weekly'},
        {'loc': f'{site_url}/registrar/', 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f'{site_url}/ingresar/', 'priority': '0.6', 'changefreq': 'monthly'},
        {'loc': f'{site_url}/privacidad/', 'priority': '0.3', 'changefreq': 'yearly'},
        {'loc': f'{site_url}/contacto/', 'priority': '0.5', 'changefreq': 'monthly'},
    ]

    seo_links = ShortLink.objects.filter(
        is_premium=True, has_seo=True,
    ).only('short_code', 'seo_updated_at').order_by('-seo_updated_at')[:500]

    for link in seo_links:
        lastmod = link.seo_updated_at.strftime('%Y-%m-%d') if link.seo_updated_at else now
        urls.append({
            'loc': f'{site_url}/s/{link.short_code}/',
            'priority': '0.7',
            'changefreq': 'weekly',
            'lastmod': lastmod,
        })

    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        xml_parts.append('  <url>')
        xml_parts.append(f'    <loc>{u["loc"]}</loc>')
        if 'lastmod' in u:
            xml_parts.append(f'    <lastmod>{u["lastmod"]}</lastmod>')
        xml_parts.append(f'    <changefreq>{u["changefreq"]}</changefreq>')
        xml_parts.append(f'    <priority>{u["priority"]}</priority>')
        xml_parts.append('  </url>')
    xml_parts.append('</urlset>')

    return HttpResponse('\n'.join(xml_parts), content_type='application/xml')
