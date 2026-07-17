"""Internal monitoring & analytics API for staff users.

Endpoints (all require is_staff, session auth):
  GET /api/internal/overview/       — global platform metrics
  GET /api/internal/users/          — user growth and active users
  GET /api/internal/links/          — link stats and top links
  GET /api/internal/clicks/         — click events breakdown (device, browser, referrer)
  GET /api/internal/subscriptions/  — subscription revenue and plan distribution
  GET /api/internal/health/         — system health (Redis, DB, cache hit rate)
"""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Sum, Q
from django.http import JsonResponse
from django.urls import path
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import (
    ShortLink, ClickEvent, Subscription, User,
    ApiKey, CustomDomain, LinkReport, Webhook, TeamMember,
)


def _staff_required(view_func):
    """Decorator: requires authenticated staff user."""
    @login_required(login_url='/ingresar/')
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            return JsonResponse({'error': 'Forbidden. Staff access only.'}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


@require_GET
@_staff_required
def overview(request):
    """GET /api/internal/overview/ — global platform snapshot."""
    now = timezone.now()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)
    last_30d = now - timedelta(days=30)

    total_users = User.objects.count()
    active_users_24h = User.objects.filter(last_login__gte=last_24h).count()
    new_users_7d = User.objects.filter(date_joined__gte=last_7d).count()
    new_users_30d = User.objects.filter(date_joined__gte=last_30d).count()

    total_links = ShortLink.objects.count()
    new_links_24h = ShortLink.objects.filter(creado__gte=last_24h).count()
    new_links_7d = ShortLink.objects.filter(creado__gte=last_7d).count()

    total_clicks = ShortLink.objects.aggregate(total=Sum('clicks'))['total'] or 0
    clicks_24h = ClickEvent.objects.filter(creado__gte=last_24h).count()
    clicks_7d = ClickEvent.objects.filter(creado__gte=last_7d).count()

    active_subs = Subscription.objects.filter(status=Subscription.STATUS_ACTIVE).count()
    pending_reports = LinkReport.objects.filter(reviewed=False).count()

    return JsonResponse({
        'users': {
            'total': total_users,
            'active_24h': active_users_24h,
            'new_7d': new_users_7d,
            'new_30d': new_users_30d,
        },
        'links': {
            'total': total_links,
            'new_24h': new_links_24h,
            'new_7d': new_links_7d,
        },
        'clicks': {
            'total': total_clicks,
            'events_24h': clicks_24h,
            'events_7d': clicks_7d,
        },
        'subscriptions': {
            'active': active_subs,
        },
        'reports': {
            'pending': pending_reports,
        },
    })


@require_GET
@_staff_required
def users(request):
    """GET /api/internal/users/ — user growth over last 30 days + recent signups."""
    now = timezone.now()
    start = now - timedelta(days=30)

    daily_signups = []
    for i in range(30):
        day = (start + timedelta(days=i)).date()
        next_day = day + timedelta(days=1)
        count = User.objects.filter(
            date_joined__gte=day,
            date_joined__lt=next_day,
        ).count()
        daily_signups.append({'date': day.isoformat(), 'count': count})

    recent_users = User.objects.order_by('-date_joined')[:20].values(
        'username', 'email', 'date_joined', 'is_active', 'is_staff',
    )

    plan_distribution = Subscription.objects.filter(
        status=Subscription.STATUS_ACTIVE,
    ).values('plan').annotate(count=Count('id')).order_by('-count')

    return JsonResponse({
        'daily_signups': daily_signups,
        'recent_users': list(recent_users),
        'plan_distribution': list(plan_distribution),
    })


@require_GET
@_staff_required
def links(request):
    """GET /api/internal/links/ — link creation over time + top links by clicks."""
    now = timezone.now()
    start = now - timedelta(days=30)

    daily_links = []
    for i in range(30):
        day = (start + timedelta(days=i)).date()
        next_day = day + timedelta(days=1)
        count = ShortLink.objects.filter(
            creado__gte=day,
            creado__lt=next_day,
        ).count()
        daily_links.append({'date': day.isoformat(), 'count': count})

    top_links = ShortLink.objects.order_by('-clicks')[:20].values(
        'short_code', 'clicks', 'is_premium', 'creado', 'ultimo_click',
    )

    premium_count = ShortLink.objects.filter(is_premium=True).count()
    free_count = ShortLink.objects.filter(is_premium=False).count()

    return JsonResponse({
        'daily_links': daily_links,
        'top_links': list(top_links),
        'premium_count': premium_count,
        'free_count': free_count,
    })


@require_GET
@_staff_required
def clicks(request):
    """GET /api/internal/clicks/ — click event breakdown by device, browser, referrer."""
    now = timezone.now()
    last_7d = now - timedelta(days=7)
    last_30d = now - timedelta(days=30)

    by_device = (
        ClickEvent.objects.filter(creado__gte=last_30d)
        .values('device')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    by_browser = (
        ClickEvent.objects.filter(creado__gte=last_30d)
        .values('browser')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    by_referrer = (
        ClickEvent.objects.filter(creado__gte=last_30d)
        .exclude(referrer='')
        .values('referrer')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    daily_clicks = []
    for i in range(30):
        day = (last_30d + timedelta(days=i)).date()
        next_day = day + timedelta(days=1)
        count = ClickEvent.objects.filter(
            creado__gte=day,
            creado__lt=next_day,
        ).count()
        daily_clicks.append({'date': day.isoformat(), 'count': count})

    return JsonResponse({
        'by_device': list(by_device),
        'by_browser': list(by_browser),
        'top_referrers': list(by_referrer),
        'daily_clicks': daily_clicks,
        'total_events_7d': ClickEvent.objects.filter(creado__gte=last_7d).count(),
        'total_events_30d': ClickEvent.objects.filter(creado__gte=last_30d).count(),
    })


@require_GET
@_staff_required
def subscriptions(request):
    """GET /api/internal/subscriptions/ — revenue and plan distribution."""
    status_distribution = Subscription.objects.values('status').annotate(
        count=Count('id'),
    ).order_by('-count')

    plan_distribution = Subscription.objects.filter(
        status=Subscription.STATUS_ACTIVE,
    ).values('plan').annotate(count=Count('id'))

    recent_subs = Subscription.objects.order_by('-creado')[:20].values(
        'user__username', 'plan', 'status', 'provider',
        'fecha_inicio', 'fecha_fin', 'creado',
    )

    from django.conf import settings
    prices = getattr(settings, 'PLAN_PRICES', {})
    active_by_plan = {
        p['plan']: p['count'] for p in plan_distribution
    }
    monthly_revenue = sum(
        prices.get(plan, 0) * count
        for plan, count in active_by_plan.items()
    )

    return JsonResponse({
        'status_distribution': list(status_distribution),
        'plan_distribution': list(plan_distribution),
        'recent_subscriptions': list(recent_subs),
        'estimated_monthly_revenue_ars': monthly_revenue,
        'plan_prices': prices,
    })


@require_GET
@_staff_required
def health(request):
    """GET /api/internal/health/ — system health check."""
    from django.conf import settings as django_settings

    redis_ok = True
    redis_info = {}
    try:
        cache.set('health:check', '1', timeout=10)
        redis_ok = cache.get('health:check') == '1'
        if hasattr(cache, '_cache'):
            info = cache._cache.info()
            redis_info = {
                'connected_clients': info.get('connected_clients'),
                'used_memory_human': info.get('used_memory_human'),
                'total_commands_processed': info.get('total_commands_processed'),
                'uptime_in_seconds': info.get('uptime_in_seconds'),
            }
    except Exception as e:
        redis_ok = False
        redis_info = {'error': str(e)}

    db_ok = True
    db_info = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM pg_stat_activity;')
            db_connections = cursor.fetchone()[0]
            cursor.execute('SELECT count(*) FROM pg_stat_activity WHERE state = %s;', ['active'])
            active_connections = cursor.fetchone()[0]
            db_info = {
                'total_connections': db_connections,
                'active_connections': active_connections,
                'max_connections': 200,
            }
    except Exception as e:
        db_ok = False
        db_info = {'error': str(e)}

    pending_clicks = 0
    try:
        set_key = getattr(django_settings, 'CLICK_TRACKING_SET_KEY', 'clicks:pending_pks')
        pending_clicks = len(cache.smembers(set_key))
    except Exception:
        pass

    return JsonResponse({
        'redis': {'ok': redis_ok, **redis_info},
        'database': {'ok': db_ok, **db_info},
        'pending_clicks_in_redis': pending_clicks,
        'maintenance_mode': getattr(django_settings, 'MAINTENANCE_MODE', False),
        'debug': django_settings.DEBUG,
    })


internal_urls = [
    path('overview/', overview, name='internal_overview'),
    path('users/', users, name='internal_users'),
    path('links/', links, name='internal_links'),
    path('clicks/', clicks, name='internal_clicks'),
    path('subscriptions/', subscriptions, name='internal_subscriptions'),
    path('health/', health, name='internal_health'),
]
