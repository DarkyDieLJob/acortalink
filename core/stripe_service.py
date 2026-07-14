"""Stripe service layer for the acortador premium subscription.

Handles product/price/feature creation, checkout sessions, and webhook
event processing. All Stripe API calls go through here.
"""

import stripe
from django.conf import settings
from urllib.parse import urlparse

from .models import Subscription


def _base_url():
    """Return SITE_URL without any path — just scheme + domain."""
    url = settings.SITE_URL.rstrip('/')
    parsed = urlparse(url)
    return f'{parsed.scheme}://{parsed.netloc}'

stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '') or None

# --- Constants ---

PRODUCT_NAME = 'Acortador Premium'
PRODUCT_DESCRIPTION = 'Subscripción premium del acortador de links'
FEATURE_NAME = 'Premium Features'
FEATURE_LOOKUP_KEY = 'acortador_premium'
PRICE_AMOUNT = 200  # $2.00 USD in cents
PRICE_CURRENCY = 'usd'
PRICE_INTERVAL = 'month'
PRICE_INTERVAL_COUNT = 1


def _stripe_enabled():
    return bool(settings.STRIPE_SECRET_KEY)


# --- Product / Price / Feature setup ---

def get_or_create_product():
    """Create the premium product with a recurring monthly price.

    Idempotent: searches by name before creating.
    Returns (product, price_id).
    """
    if not _stripe_enabled():
        raise RuntimeError('STRIPE_SECRET_KEY no configurada')

    existing = stripe.Product.search(
        query=f'name:"{PRODUCT_NAME}"',
        limit=1,
    )
    if existing.data:
        product = existing.data[0]
        if product.default_price:
            return product, product.default_price

    product = stripe.Product.create(
        name=PRODUCT_NAME,
        description=PRODUCT_DESCRIPTION,
        default_price_data={
            'currency': PRICE_CURRENCY,
            'recurring': {
                'interval': PRICE_INTERVAL,
                'interval_count': PRICE_INTERVAL_COUNT,
            },
            'unit_amount': PRICE_AMOUNT,
        },
    )
    return product, product.default_price


def get_or_create_feature():
    """Create the entitlement feature for premium.

    Idempotent: searches by lookup_key before creating.
    Returns the feature ID.
    """
    if not _stripe_enabled():
        raise RuntimeError('STRIPE_SECRET_KEY no configurada')

    features = stripe.entitlements.Feature.list(limit=100)
    for f in features.data:
        if f.lookup_key == FEATURE_LOOKUP_KEY:
            return f.id

    feature = stripe.entitlements.Feature.create(
        name=FEATURE_NAME,
        lookup_key=FEATURE_LOOKUP_KEY,
    )
    return feature.id


def attach_feature_to_product(product_id, feature_id):
    """Attach an entitlement feature to a product."""
    if not _stripe_enabled():
        raise RuntimeError('STRIPE_SECRET_KEY no configurada')

    stripe.Product.create_feature(
        product=product_id,
        entitlement_feature=feature_id,
    )


def setup_products():
    """One-time setup: create product, price, and feature.

    Idempotent — safe to run multiple times.
    Returns dict with product_id, price_id, feature_id.
    """
    product, price_id = get_or_create_product()
    feature_id = get_or_create_feature()

    # Check if feature is already attached
    features = stripe.Product.list_features(product=product.id, limit=10)
    attached_ids = [f.entitlement_feature.id for f in features.data]
    if feature_id not in attached_ids:
        attach_feature_to_product(product.id, feature_id)

    return {
        'product_id': product.id,
        'price_id': price_id,
        'feature_id': feature_id,
    }


# --- Customer / Checkout ---

def get_or_create_customer(user):
    """Get or create a Stripe customer for the Django user.

    Returns the Stripe customer ID.
    """
    if not _stripe_enabled():
        raise RuntimeError('STRIPE_SECRET_KEY no configurada')

    sub = getattr(user, 'subscription', None)
    if sub and sub.stripe_customer_id:
        return sub.stripe_customer_id

    customer = stripe.Customer.create(
        email=user.email or f'{user.username}@example.com',
        name=user.get_full_name() or user.username,
        metadata={'django_user_id': user.id, 'username': user.username},
    )

    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.stripe_customer_id = customer.id
    sub.provider = 'stripe'
    sub.save()

    return customer.id


def create_checkout_session(user, price_id):
    """Create a Stripe Checkout session for a subscription.

    Returns the checkout URL.
    """
    if not _stripe_enabled():
        raise RuntimeError('STRIPE_SECRET_KEY no configurada')

    customer_id = get_or_create_customer(user)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        success_url=f'{_base_url()}/subscribir/?checkout=success',
        cancel_url=f'{_base_url()}/subscribir/?checkout=cancelled',
        metadata={'user_id': user.id, 'username': user.username},
    )
    return session.url


def cancel_stripe_subscription(subscription_id):
    """Cancel a Stripe subscription at period end."""
    if not _stripe_enabled():
        raise RuntimeError('STRIPE_SECRET_KEY no configurada')

    return stripe.Subscription.modify(
        subscription_id,
        cancel_at_period_end=True,
    )


# --- Webhook ---

def verify_webhook_signature(payload, sig_header):
    """Verify and construct a Stripe webhook event.

    Returns the Event object or raises stripe.error.SignatureVerificationError.
    """
    return stripe.Webhook.construct_event(
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET,
    )


def handle_webhook_event(event):
    """Process a Stripe webhook event.

    Updates the Subscription model based on the event type.
    """
    event_type = event['type']
    data = event['data']['object']

    if event_type == 'customer.subscription.created':
        _handle_subscription_created(data)
    elif event_type == 'customer.subscription.updated':
        _handle_subscription_updated(data)
    elif event_type == 'customer.subscription.deleted':
        _handle_subscription_deleted(data)
    elif event_type == 'invoice.created':
        _handle_invoice_created(data)
    elif event_type == 'entitlements.active_entitlement_summary.updated':
        _handle_entitlements_updated(data)


def _find_subscription_by_customer(customer_id):
    """Find a Subscription by Stripe customer ID."""
    return Subscription.objects.filter(stripe_customer_id=customer_id).first()


def _find_subscription_by_stripe_sub(subscription_id):
    """Find a Subscription by Stripe subscription ID (provider_id)."""
    return Subscription.objects.filter(provider_id=subscription_id).first()


def _handle_subscription_created(data):
    """Handle customer.subscription.created — set status to pending."""
    sub = _find_subscription_by_customer(data.get('customer', ''))
    if sub:
        sub.provider_id = data['id']
        sub.status = Subscription.STATUS_PENDING
        sub.save()


def _handle_subscription_updated(data):
    """Handle customer.subscription.updated — sync status."""
    sub = _find_subscription_by_stripe_sub(data['id'])
    if not sub:
        sub = _find_subscription_by_customer(data.get('customer', ''))
    if not sub:
        return

    sub.provider_id = data['id']

    stripe_status = data.get('status', '')
    if stripe_status == 'active':
        sub.status = Subscription.STATUS_ACTIVE
        sub.fecha_inicio = timezone_from_stripe(data.get('start_date'))
    elif stripe_status == 'canceled':
        sub.status = Subscription.STATUS_CANCELLED
        sub.fecha_fin = timezone_from_stripe(data.get('ended_at'))
    elif stripe_status == 'past_due' or stripe_status == 'unpaid':
        sub.status = Subscription.STATUS_EXPIRED
    elif stripe_status == 'trialing':
        sub.status = Subscription.STATUS_PENDING

    if data.get('cancel_at_period_end') and sub.status == Subscription.STATUS_ACTIVE:
        sub.status = Subscription.STATUS_CANCELLED
        sub.fecha_fin = timezone_from_stripe(data.get('current_period_end'))

    sub.save()


def _handle_subscription_deleted(data):
    """Handle customer.subscription.deleted — mark cancelled."""
    sub = _find_subscription_by_stripe_sub(data['id'])
    if not sub:
        sub = _find_subscription_by_customer(data.get('customer', ''))
    if not sub:
        return

    sub.status = Subscription.STATUS_CANCELLED
    sub.fecha_fin = timezone_from_stripe(data.get('ended_at'))
    sub.save()


def _handle_invoice_created(data):
    """Handle invoice.created — log for now, no action needed."""
    pass


def _handle_entitlements_updated(data):
    """Handle entitlements.active_entitlement_summary.updated — no action needed.

    Entitlements are managed by Stripe; we sync subscription status via
    the subscription events.
    """
    pass


def timezone_from_stripe(timestamp):
    """Convert a Stripe Unix timestamp to a Django timezone-aware datetime."""
    if not timestamp:
        return None
    from django.utils import timezone
    from datetime import datetime
    return timezone.make_aware(datetime.utcfromtimestamp(timestamp))
