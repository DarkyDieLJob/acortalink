"""Mercado Pago service layer for the acortador premium subscription.

Handles preapproval plan creation, checkout, and webhook event processing.
All Mercado Pago API calls go through here.

Mercado Pago uses "preapproval" (recurring payments) for subscriptions.
"""

import logging

import mercadopago
from django.conf import settings
from django.utils import timezone
from urllib.parse import urlparse

from .models import Subscription

logger = logging.getLogger(__name__)


def _base_url():
    """Return SITE_URL without any path — just scheme + domain."""
    url = settings.SITE_URL.rstrip('/')
    parsed = urlparse(url)
    return f'{parsed.scheme}://{parsed.netloc}'

# --- Constants ---

# MP exige reason <= 40 caracteres
PLAN_REASONS = {
    'starter': 'Acortalink Starter Mensual',
    'pro': 'Acortalink Pro Mensual',
    'business': 'Acortalink Business Mensual',
}
PREAPPROVAL_FREQUENCY = 1  # every 1 month
PREAPPROVAL_FREQUENCY_TYPE = 'months'

MP_LOGO_URL = 'https://acortalink.com.ar/mp-logo.png'


def _get_sdk():
    """Return a configured Mercado Pago SDK instance."""
    token = settings.MERCADOPAGO_ACCESS_TOKEN
    if not token:
        raise RuntimeError('MERCADOPAGO_ACCESS_TOKEN no configurado')
    return mercadopago.SDK(token)


def _mp_enabled():
    return bool(settings.MERCADOPAGO_ACCESS_TOKEN)


# --- Checkout / Preapproval (sin plan asociado, pago pendiente) ---

def create_preapproval(user, plan='starter'):
    """Create a pending preapproval for a user (suscripción sin plan asociado).

    Args:
        user: Django User instance.
        plan: One of 'starter', 'pro', 'business'.

    Flujo: status='pending' sin card_token_id ni preapproval_plan_id.
    MP devuelve init_point para que el usuario complete el pago en su checkout.
    Ver: https://mercadopago.com/developers/es/docs/subscriptions/integration-configuration/subscription-no-associated-plan/pending-payments

    Returns the checkout/init URL.
    """
    if not _mp_enabled():
        raise RuntimeError('MERCADOPAGO_ACCESS_TOKEN no configurado')

    plan_prices = getattr(settings, 'PLAN_PRICES', {})
    amount = int(plan_prices.get(plan, settings.MERCADOPAGO_PRICE))
    reason = PLAN_REASONS.get(plan, PLAN_REASONS['starter'])

    sdk = _get_sdk()

    # Create or get subscription record
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.provider = 'mercadopago'
    sub.status = Subscription.STATUS_PENDING
    sub.plan = plan
    sub.save()

    preapproval_response = sdk.preapproval().create({
        'reason': reason,
        'external_reference': str(user.pk),
        'payer_email': user.email or f'{user.username}@example.com',
        'auto_recurring': {
            'frequency': PREAPPROVAL_FREQUENCY,
            'frequency_type': PREAPPROVAL_FREQUENCY_TYPE,
            'transaction_amount': amount,
            'currency_id': settings.MERCADOPAGO_CURRENCY,
        },
        'back_url': f'{_base_url()}/subscribir/?checkout=success',
        'notification_url': f'{_base_url()}/mercadopago/webhook/',
        'status': 'pending',
    })

    if preapproval_response.get('status') >= 400:
        raise RuntimeError(f'MP error: {preapproval_response.get("response", {})}')

    response_data = preapproval_response['response']
    sub.provider_id = response_data.get('id', '')
    sub.save()

    # MP returns init_point for redirect
    init_point = response_data.get('init_point', '')
    logger.info('MP preapproval created: %s, init_point: %s',
                response_data.get('id', ''), bool(init_point))
    return init_point


def cancel_preapproval(preapproval_id):
    """Cancel a Mercado Pago preapproval (stop recurring billing)."""
    if not _mp_enabled():
        raise RuntimeError('MERCADOPAGO_ACCESS_TOKEN no configurado')

    sdk = _get_sdk()
    sdk.preapproval().update(preapproval_id, {
        'status': 'cancelled',
    })


# --- Webhook ---

def handle_webhook_event(event_data):
    """Process a Mercado Pago webhook notification.

    MP sends notifications for preapproval events. We sync the subscription
    status based on the event type.
    """
    event_type = event_data.get('type', '')
    data = event_data.get('data', {})

    if event_type == 'preapproval':
        _handle_preapproval_event(data)
    elif event_type == 'payment':
        _handle_payment_event(data)


def _find_subscription_by_mp_id(mp_id):
    """Find a Subscription by Mercado Pago preapproval ID."""
    return Subscription.objects.filter(provider_id=mp_id).first()


def _find_subscription_by_user_id(user_id):
    """Find a Subscription by external_reference (user ID)."""
    try:
        return Subscription.objects.filter(user_id=int(user_id)).first()
    except (ValueError, TypeError):
        return None


def _handle_preapproval_event(data):
    """Handle preapproval status changes."""
    mp_id = data.get('id', '')
    status = data.get('status', '')
    external_ref = data.get('external_reference', '')

    sub = _find_subscription_by_mp_id(mp_id)
    if not sub and external_ref:
        sub = _find_subscription_by_user_id(external_ref)
    if not sub:
        return

    sub.provider_id = mp_id

    if status == 'authorized':
        sub.status = Subscription.STATUS_ACTIVE
        sub.fecha_inicio = timezone.now()
    elif status == 'cancelled':
        sub.status = Subscription.STATUS_CANCELLED
        sub.fecha_fin = timezone.now()
    elif status == 'pending':
        sub.status = Subscription.STATUS_PENDING
    elif status == 'expired':
        sub.status = Subscription.STATUS_EXPIRED
        sub.fecha_fin = timezone.now()

    sub.save()


def _handle_payment_event(data):
    """Handle payment events — sync subscription status or domain purchase.

    For recurring payments, a successful payment means the subscription
    is active. A failed payment may mean it's expired.

    For domain purchases (external_reference starts with 'domain:'),
    a successful payment triggers auto-registration of the domain.
    """
    status = data.get('status', '')
    external_ref = data.get('external_reference', '')
    payment_id = str(data.get('id', ''))

    # Domain purchase payment
    if external_ref and external_ref.startswith('domain:'):
        _handle_domain_payment_event(external_ref, status, payment_id, data)
        return

    # Subscription payment
    sub = _find_subscription_by_user_id(external_ref)
    if not sub:
        return

    if status == 'approved':
        sub.status = Subscription.STATUS_ACTIVE
        sub.fecha_inicio = timezone.now()
    elif status in ('rejected', 'cancelled'):
        if sub.status == Subscription.STATUS_ACTIVE:
            sub.status = Subscription.STATUS_EXPIRED
            sub.fecha_fin = timezone.now()

    sub.save()


def _handle_domain_payment_event(external_ref, status, payment_id, data):
    """Handle a domain purchase payment event.

    external_ref format: 'domain:<custom_domain_pk>'
    On approved payment, marks the domain as paid and triggers registration.
    """
    from .models import CustomDomain
    from . import domain_service

    try:
        pk = int(external_ref.split(':', 1)[1])
    except (ValueError, IndexError):
        return

    cd = CustomDomain.objects.filter(pk=pk).first()
    if not cd:
        return

    cd.mp_payment_id = payment_id

    if status == 'approved':
        cd.purchase_status = CustomDomain.PURCHASE_PAID
        cd.save(update_fields=['mp_payment_id', 'purchase_status'])

        # Trigger domain registration
        try:
            domain_service.register_domain(cd)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                'Auto-registration failed for domain %s: %s', cd.domain, e,
            )
            cd.purchase_status = CustomDomain.PURCHASE_FAILED
            cd.save(update_fields=['purchase_status'])

    elif status in ('rejected', 'cancelled'):
        cd.purchase_status = CustomDomain.PURCHASE_FAILED
        cd.save(update_fields=['mp_payment_id', 'purchase_status'])
