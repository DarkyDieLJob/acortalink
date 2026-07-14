"""Tests for Stripe webhook handler and subscription lifecycle."""

import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User

from core.models import Subscription
from core import stripe_service


class StripeWebhookTest(TestCase):
    """Test the Stripe webhook endpoint and event handlers."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
        )
        self.sub = Subscription.objects.create(
            user=self.user,
            status=Subscription.STATUS_PENDING,
            stripe_customer_id='cus_test123',
        )

    def _build_event(self, event_type, data):
        return {
            'id': 'evt_test123',
            'type': event_type,
            'data': {'object': data},
        }

    def _post_webhook(self, event):
        """Post a mock webhook event to the endpoint."""
        payload = json.dumps(event)
        with patch('core.stripe_service.verify_webhook_signature') as mock_verify:
            mock_verify.return_value = event
            response = self.client.post(
                '/stripe/webhook/',
                data=payload,
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='t=123,v1=fake',
            )
        return response

    @override_settings(STRIPE_SECRET_KEY='sk_test_fake')
    def test_webhook_returns_200_on_valid_event(self):
        """Webhook should return 200 for a valid event."""
        event = self._build_event('invoice.created', {'id': 'in_test123'})
        response = self._post_webhook(event)
        self.assertEqual(response.status_code, 200)

    @override_settings(STRIPE_SECRET_KEY='sk_test_fake')
    def test_webhook_returns_400_on_invalid_signature(self):
        """Webhook should return 400 when signature verification fails."""
        import stripe
        with patch('core.stripe_service.verify_webhook_signature') as mock_verify:
            mock_verify.side_effect = stripe.error.SignatureVerificationError(
                'Invalid signature', 't=123'
            )
            response = self.client.post(
                '/stripe/webhook/',
                data=json.dumps({'type': 'test'}),
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='invalid',
            )
        self.assertEqual(response.status_code, 400)

    @override_settings(STRIPE_SECRET_KEY='sk_test_fake')
    def test_webhook_returns_405_on_get(self):
        """Webhook should return 405 for GET requests."""
        response = self.client.get('/stripe/webhook/')
        self.assertEqual(response.status_code, 405)

    def test_subscription_created_sets_pending(self):
        """customer.subscription.created should set provider_id and status=pending."""
        event = self._build_event('customer.subscription.created', {
            'id': 'sub_test123',
            'customer': 'cus_test123',
            'status': 'incomplete',
        })
        stripe_service.handle_webhook_event(event)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.provider_id, 'sub_test123')
        self.assertEqual(self.sub.status, Subscription.STATUS_PENDING)

    def test_subscription_updated_sets_active(self):
        """customer.subscription.updated with status=active should activate."""
        self.sub.provider_id = 'sub_test123'
        self.sub.save()

        event = self._build_event('customer.subscription.updated', {
            'id': 'sub_test123',
            'customer': 'cus_test123',
            'status': 'active',
            'start_date': 1700000000,
            'cancel_at_period_end': False,
        })
        stripe_service.handle_webhook_event(event)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_ACTIVE)
        self.assertIsNotNone(self.sub.fecha_inicio)

    def test_subscription_updated_sets_cancelled(self):
        """customer.subscription.updated with cancel_at_period_end should cancel."""
        self.sub.provider_id = 'sub_test123'
        self.sub.status = Subscription.STATUS_ACTIVE
        self.sub.save()

        event = self._build_event('customer.subscription.updated', {
            'id': 'sub_test123',
            'customer': 'cus_test123',
            'status': 'active',
            'cancel_at_period_end': True,
            'current_period_end': 1701000000,
        })
        stripe_service.handle_webhook_event(event)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_CANCELLED)

    def test_subscription_deleted_sets_cancelled(self):
        """customer.subscription.deleted should mark as cancelled."""
        self.sub.provider_id = 'sub_test123'
        self.sub.status = Subscription.STATUS_ACTIVE
        self.sub.save()

        event = self._build_event('customer.subscription.deleted', {
            'id': 'sub_test123',
            'customer': 'cus_test123',
            'status': 'canceled',
            'ended_at': 1700500000,
        })
        stripe_service.handle_webhook_event(event)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_CANCELLED)
        self.assertIsNotNone(self.sub.fecha_fin)

    def test_subscription_updated_by_customer_id(self):
        """Should find subscription by customer_id when provider_id not set."""
        event = self._build_event('customer.subscription.updated', {
            'id': 'sub_new456',
            'customer': 'cus_test123',
            'status': 'active',
            'start_date': 1700000000,
            'cancel_at_period_end': False,
        })
        stripe_service.handle_webhook_event(event)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.provider_id, 'sub_new456')
        self.assertEqual(self.sub.status, Subscription.STATUS_ACTIVE)

    def test_unknown_event_type_does_not_crash(self):
        """Unknown event types should be handled gracefully."""
        event = self._build_event('unknown.event.type', {'id': 'test'})
        stripe_service.handle_webhook_event(event)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_PENDING)

    def test_no_subscription_for_customer_is_safe(self):
        """Webhook for unknown customer should not crash."""
        event = self._build_event('customer.subscription.created', {
            'id': 'sub_unknown',
            'customer': 'cus_unknown',
            'status': 'incomplete',
        })
        stripe_service.handle_webhook_event(event)

    def test_timezone_from_stripe_none(self):
        """timezone_from_stripe should return None for None input."""
        self.assertIsNone(stripe_service.timezone_from_stripe(None))

    def test_timezone_from_stripe_valid(self):
        """timezone_from_stripe should convert Unix timestamp to datetime."""
        from datetime import datetime
        result = stripe_service.timezone_from_stripe(1700000000)
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2023)
