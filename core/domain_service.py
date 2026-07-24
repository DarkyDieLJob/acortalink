"""Domain service layer.

Handles:
- Domain availability checking (registrar API or stub)
- DNS CNAME verification for BYOD
- Domain purchase via MercadoPago (one-time payment, not subscription)
"""

import logging
import urllib.request
import urllib.parse
import json

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_domain_tld(domain):
    """Extract the TLD from a domain (e.g. 'mylinks.com' -> '.com')."""
    parts = domain.rsplit('.', 2)
    if len(parts) >= 2:
        tld = '.' + parts[-1]
        # Check for compound TLD like .com.ar
        if len(parts) >= 3 and parts[-2] in ('com', 'net', 'org', 'gov', 'edu') and parts[-1] in ('ar', 'br', 'mx', 'cl', 'co', 'uy', 'pe', 'bo', 'py', 'ec', 've'):
            tld = '.' + parts[-2] + '.' + parts[-1]
        return tld
    return ''


def get_domain_price(domain):
    """Get the price for a domain based on its TLD."""
    tld = _get_domain_tld(domain)
    prices = getattr(settings, 'DOMAIN_PRICES', {})
    return prices.get(tld, 0)


def check_domain_availability(domain):
    """Check if a domain is available for registration.

    Uses registrar API if configured, otherwise uses a simple DNS-based check:
    if the domain doesn't resolve, we consider it "available" (rough heuristic).

    Returns dict: {'available': bool, 'price': int, 'tld': str}
    """
    tld = _get_domain_tld(domain)
    price = get_domain_price(domain)

    # If registrar API is configured, use it
    api_url = getattr(settings, 'REGISTRAR_API_URL', '')
    api_key = getattr(settings, 'REGISTRAR_API_KEY', '')

    if api_url and api_key:
        try:
            params = urllib.parse.urlencode({
                'api_user': getattr(settings, 'REGISTRAR_API_USER', ''),
                'api_key': api_key,
                'domain': domain,
            })
            url = f'{api_url}/domains:check?{params}'
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                available = data.get('available', False)
                return {'available': available, 'price': price, 'tld': tld}
        except Exception as e:
            logger.warning('Registrar API check failed for %s: %s', domain, e)

    # Fallback: DNS-based heuristic
    # If the domain has no A or CNAME records, it's likely available
    import socket
    try:
        socket.getaddrinfo(domain, None)
        # Domain resolves — might be taken
        return {'available': False, 'price': price, 'tld': tld}
    except socket.gaierror:
        # Domain doesn't resolve — likely available
        return {'available': True, 'price': price, 'tld': tld}


def verify_cname(domain):
    """Verify that a domain's CNAME points to our CNAME_TARGET.

    Returns dict: {'verified': bool, 'cname_value': str, 'target': str, 'error': str}
    """
    target = getattr(settings, 'CNAME_TARGET', 'app.acortalink.com.ar')

    # Try DNS resolution
    import socket
    try:
        # Try to resolve the domain
        result = socket.getaddrinfo(domain, None)
        if result:
            # Domain resolves — check if it's a CNAME to our target
            # We can't easily check CNAME with just socket, so we use a heuristic:
            # if the domain resolves, we check if it resolves to the same IP as our target
            domain_ips = {r[4][0] for r in result}

            try:
                target_result = socket.getaddrinfo(target, None)
                target_ips = {r[4][0] for r in target_result}
            except socket.gaierror:
                target_ips = set()

            if domain_ips & target_ips:
                return {
                    'verified': True,
                    'cname_value': target,
                    'target': target,
                    'error': '',
                }
            else:
                return {
                    'verified': False,
                    'cname_value': '',
                    'target': target,
                    'error': f'El dominio responde pero no apunta a {target}. '
                             f'Configurá un registro CNAME apuntando a {target}.',
                }
    except socket.gaierror:
        return {
            'verified': False,
            'cname_value': '',
            'target': target,
            'error': f'El dominio {domain} no resuelve todavía. '
                     f'Configurá un registro CNAME apuntando a {target} y esperá '
                     f'a que se propague (puede tardar hasta 48hs).',
        }

    return {
        'verified': False,
        'cname_value': '',
        'target': target,
        'error': 'No se pudo verificar el DNS.',
    }


def create_domain_payment(user, domain):
    """Create a one-time MercadoPago payment for a domain purchase.

    Unlike subscriptions, this is a single payment (not recurring).

    Returns the checkout URL.
    """
    import mercadopago
    from .models import CustomDomain

    token = settings.MERCADOPAGO_ACCESS_TOKEN
    if not token:
        raise RuntimeError('MERCADOPAGO_ACCESS_TOKEN no configurado')

    price = get_domain_price(domain)
    if not price:
        raise RuntimeError(f'No hay precio configurado para el TLD de {domain}')

    sdk = mercadopago.SDK(token)

    # Create or update the CustomDomain record
    cd, _ = CustomDomain.objects.get_or_create(
        owner=user, domain=domain,
        defaults={
            'source': CustomDomain.SOURCE_PURCHASED,
            'purchase_status': CustomDomain.PURCHASE_PENDING,
            'price': price,
        },
    )
    if cd.source != CustomDomain.SOURCE_PURCHASED:
        cd.source = CustomDomain.SOURCE_PURCHASED
        cd.purchase_status = CustomDomain.PURCHASE_PENDING
        cd.price = price
        cd.save(update_fields=['source', 'purchase_status', 'price'])

    # Create a preference (one-time payment)
    from urllib.parse import urlparse
    base_url = settings.SITE_URL.rstrip('/')
    parsed = urlparse(base_url)
    base = f'{parsed.scheme}://{parsed.netloc}'

    preference_data = {
        'items': [{
            'id': f'domain-{cd.pk}',
            'title': f'Dominio {domain} (1 año)',
            'quantity': 1,
            'unit_price': price,
            'currency_id': settings.MERCADOPAGO_CURRENCY,
            'picture_url': 'https://acortalink.com.ar/mp-logo.png',
        }],
        'external_reference': f'domain:{cd.pk}',
        'back_urls': {
            'success': f'{base}/dominios/?checkout=success',
            'failure': f'{base}/dominios/?checkout=failure',
            'pending': f'{base}/dominios/?checkout=pending',
        },
        'notification_url': f'{base}/mercadopago/webhook/',
        'auto_return': 'approved',
        'statement_descriptor': 'ACORTALINK',
    }

    response = sdk.preference().create(preference_data)

    if response.get('status') >= 400:
        raise RuntimeError(f'MP error: {response.get("response", {})}')

    init_point = response['response'].get('init_point', '')
    return init_point


def register_domain(custom_domain):
    """Register a domain via the registrar API after payment is confirmed.

    Args:
        custom_domain: CustomDomain instance with source=purchased and
                       purchase_status=purchase_paid.

    Returns:
        dict: {'success': bool, 'error': str}
    """
    api_url = getattr(settings, 'REGISTRAR_API_URL', '')
    api_key = getattr(settings, 'REGISTRAR_API_KEY', '')
    api_user = getattr(settings, 'REGISTRAR_API_USER', '')

    if not api_url or not api_key:
        logger.warning(
            'register_domain: no registrar API configured for %s',
            custom_domain.domain,
        )
        # No registrar configured — mark as registered (stub mode)
        custom_domain.purchase_status = custom_domain.PURCHASE_REGISTERED
        custom_domain.status = custom_domain.STATUS_PENDING
        custom_domain.save(update_fields=['purchase_status', 'status'])
        return {'success': True, 'error': ''}

    try:
        params = urllib.parse.urlencode({
            'api_user': api_user,
            'api_key': api_key,
            'domain': custom_domain.domain,
            'years': 1,
        })
        url = f'{api_url}/domains:register?{params}'
        req = urllib.request.Request(url, method='POST', data=b'')
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            if resp.status < 400:
                custom_domain.purchase_status = custom_domain.PURCHASE_REGISTERED
                custom_domain.status = custom_domain.STATUS_PENDING
                custom_domain.save(update_fields=['purchase_status', 'status'])
                logger.info('Domain %s registered successfully', custom_domain.domain)
                return {'success': True, 'error': ''}
            else:
                err = data.get('message', 'Unknown registrar error')
                custom_domain.purchase_status = custom_domain.PURCHASE_FAILED
                custom_domain.save(update_fields=['purchase_status'])
                logger.error('Domain registration failed for %s: %s', custom_domain.domain, err)
                return {'success': False, 'error': err}

    except Exception as e:
        custom_domain.purchase_status = custom_domain.PURCHASE_FAILED
        custom_domain.save(update_fields=['purchase_status'])
        logger.error('Domain registration exception for %s: %s', custom_domain.domain, e)
        return {'success': False, 'error': str(e)}
