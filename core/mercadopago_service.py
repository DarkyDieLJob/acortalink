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
PLAN_REASON = 'Acortador Premium Mensual'
PREAPPROVAL_FREQUENCY = 1  # every 1 month
PREAPPROVAL_FREQUENCY_TYPE = 'months'


def _get_sdk():
    """Return a configured Mercado Pago SDK instance."""
    token = settings.MERCADOPAGO_ACCESS_TOKEN
    if not token:
        raise RuntimeError('MERCADOPAGO_ACCESS_TOKEN no configurado')
    return mercadopago.SDK(token)


def _mp_enabled():
    return bool(settings.MERCADOPAGO_ACCESS_TOKEN)


# --- Checkout / Preapproval (sin plan asociado, pago pendiente) ---

def create_preapproval(user):
    """Create a pending preapproval for a user (suscripción sin plan asociado).

    Flujo: status='pending' sin card_token_id ni preapproval_plan_id.
    MP devuelve init_point para que el usuario complete el pago en su checkout.
    Ver: https://mercadopago.com/developers/es/docs/subscriptions/integration-configuration/subscription-no-associated-plan/pending-payments

    Returns the checkout/init URL.
    """
    if not _mp_enabled():
        raise RuntimeError('MERCADOPAGO_ACCESS_TOKEN no configurado')

    sdk = _get_sdk()

    # Create or get subscription record
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.provider = 'mercadopago'
    sub.status = Subscription.STATUS_PENDING
    sub.save()

    preapproval_response = sdk.preapproval().create({
        'reason': PLAN_REASON,
        'external_reference': str(user.pk),
        'payer_email': user.email or f'{user.username}@example.com',
        'auto_recurring': {
            'frequency': PREAPPROVAL_FREQUENCY,
            'frequency_type': PREAPPROVAL_FREQUENCY_TYPE,
            'transaction_amount': int(settings.MERCADOPAGO_PRICE),
            'currency_id': settings.MERCADOPAGO_CURRENCY,
        },
        'back_url': f'{_base_url()}/subscribir/?checkout=success',
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
    """Handle payment events — sync subscription status.

    For recurring payments, a successful payment means the subscription
    is active. A failed payment may mean it's expired.
    """
    status = data.get('status', '')
    external_ref = data.get('external_reference', '')

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
