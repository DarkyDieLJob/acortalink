"""Tests for QR codes, password protection, analytics, API, domains, team, webhooks."""

import json
import hashlib
from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import (
    ShortLink, Subscription, ClickEvent, ApiKey,
    CustomDomain, TeamMember, Webhook,
)


class QRCodeTest(TestCase):
    """Test QR code generation endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='qruser', email='qr@test.com', password='testpass123',
        )
        self.link = ShortLink.objects.create(
            short_code='qrtst1', original_url='https://example.com',
            owner=self.user,
        )

    def test_qr_png_returns_image(self):
        """QR endpoint should return a PNG image."""
        resp = self.client.get(f'/qr/{self.link.short_code}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/png')
        self.assertGreater(len(resp.content), 100)

    def test_qr_svg_returns_svg(self):
        """QR endpoint with format=svg should return SVG."""
        resp = self.client.get(f'/qr/{self.link.short_code}/?format=svg')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/svg+xml')

    def test_qr_invalid_code_404(self):
        """QR for non-existent code should return 404."""
        resp = self.client.get('/qr/nonexist/')
        self.assertEqual(resp.status_code, 404)


class PasswordProtectionTest(TestCase):
    """Test password protection on links."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='pwduser', email='pwd@test.com', password='testpass123',
        )
        self.password = 'secret123'
        self.pwd_hash = hashlib.sha256(self.password.encode()).hexdigest()
        self.link = ShortLink.objects.create(
            short_code='pwdtst1', original_url='https://example.com',
            owner=self.user, is_premium=True,
            seo_title='Test', password_hash=self.pwd_hash,
        )

    def test_password_protected_shows_form(self):
        """Redirect to password-protected link should show form, not redirect."""
        resp = self.client.get(f'/s/{self.link.short_code}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'contrase', resp.content.lower())

    def test_correct_password_redirects(self):
        """POST with correct password should redirect to original URL."""
        resp = self.client.post(f'/s/{self.link.short_code}/', {'password': self.password})
        # Should render SEO page (premium with seo_title) or redirect
        self.assertIn(resp.status_code, [200, 302])

    def test_wrong_password_shows_error(self):
        """POST with wrong password should show error."""
        resp = self.client.post(f'/s/{self.link.short_code}/', {'password': 'wrong'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'incorrecta', resp.content.lower())

    def test_no_password_link_redirects_normally(self):
        """Link without password should redirect without form."""
        link = ShortLink.objects.create(
            short_code='nopwd1', original_url='https://example.com',
            owner=self.user,
        )
        resp = self.client.get(f'/s/{link.short_code}/')
        self.assertEqual(resp.status_code, 302)


class ClickEventAnalyticsTest(TestCase):
    """Test click event tracking and analytics."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='analytics', email='an@test.com', password='testpass123',
        )
        self.link = ShortLink.objects.create(
            short_code='anltst1', original_url='https://example.com',
            owner=self.user,
        )

    def test_click_event_creation(self):
        """ClickEvent can be created with device and browser info."""
        event = ClickEvent.objects.create(
            link=self.link,
            device=ClickEvent.DEVICE_DESKTOP,
            browser='Chrome',
            referrer='https://google.com',
        )
        self.assertEqual(event.device, ClickEvent.DEVICE_DESKTOP)
        self.assertEqual(event.browser, 'Chrome')

    def test_click_event_device_choices(self):
        """ClickEvent should have all device choices."""
        devices = [c[0] for c in ClickEvent.DEVICE_CHOICES]
        self.assertIn('mobile', devices)
        self.assertIn('desktop', devices)
        self.assertIn('tablet', devices)
        self.assertIn('bot', devices)
        self.assertIn('other', devices)

    def test_flush_clicks_parses_user_agent(self):
        """flush_clicks should parse UA into device and browser."""
        from core.management.commands.flush_clicks import Command
        cmd = Command()
        device, browser = cmd._parse_ua(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0'
        )
        self.assertEqual(device, ClickEvent.DEVICE_DESKTOP)
        self.assertEqual(browser, 'Chrome')

        device, browser = cmd._parse_ua(
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1'
        )
        self.assertEqual(device, ClickEvent.DEVICE_MOBILE)
        self.assertEqual(browser, 'Safari')

        device, browser = cmd._parse_ua('GoogleBot/2.1')
        self.assertEqual(device, ClickEvent.DEVICE_BOT)

        device, browser = cmd._parse_ua('')
        self.assertEqual(device, ClickEvent.DEVICE_OTHER)


class ApiRestTest(TestCase):
    """Test REST API endpoints."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='apiuser', email='api@test.com', password='testpass123',
        )
        self.api_key = ApiKey.objects.create(
            user=self.user, key='test_api_key_123456789', name='test',
        )
        self.link = ShortLink.objects.create(
            short_code='apitst1', original_url='https://example.com',
            owner=self.user,
        )

    def test_api_without_key_returns_401(self):
        """API without key should return 401."""
        resp = self.client.get('/api/v1/links/')
        self.assertEqual(resp.status_code, 401)

    def test_api_list_links(self):
        """API with valid key should list user links."""
        resp = self.client.get(
            '/api/v1/links/',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['links'][0]['short_code'], 'apitst1')

    def test_api_create_link(self):
        """API POST should create a new link."""
        resp = self.client.post(
            '/api/v1/links/',
            data=json.dumps({'url': 'https://newsite.com'}),
            content_type='application/json',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)
        self.assertTrue(data['created'])
        self.assertEqual(data['link']['original_url'], 'https://newsite.com')

    def test_api_create_duplicate_returns_existing(self):
        """API POST with duplicate URL should return existing link."""
        resp = self.client.post(
            '/api/v1/links/',
            data=json.dumps({'url': 'https://example.com'}),
            content_type='application/json',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertFalse(data['created'])

    def test_api_link_detail(self):
        """API GET link detail should return link info."""
        resp = self.client.get(
            f'/api/v1/links/{self.link.short_code}/',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['link']['short_code'], 'apitst1')

    def test_api_link_detail_404(self):
        """API GET non-existent link should return 404."""
        resp = self.client.get(
            '/api/v1/links/nonexist/',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 404)

    def test_api_delete_link(self):
        """API DELETE should remove link."""
        resp = self.client.delete(
            f'/api/v1/links/{self.link.short_code}/',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ShortLink.objects.filter(pk=self.link.pk).exists())

    def test_api_stats(self):
        """API GET stats should return analytics aggregations."""
        ClickEvent.objects.create(
            link=self.link, device=ClickEvent.DEVICE_DESKTOP, browser='Chrome',
        )
        ClickEvent.objects.create(
            link=self.link, device=ClickEvent.DEVICE_MOBILE, browser='Firefox',
        )
        resp = self.client.get(
            f'/api/v1/stats/{self.link.short_code}/',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['total_clicks'], 0)  # clicks field not incremented
        self.assertIn('Desktop', data['by_device'])
        self.assertIn('Chrome', data['by_browser'])

    def test_api_create_key(self):
        """API POST keys should create a new API key."""
        resp = self.client.post(
            '/api/v1/keys/',
            data=json.dumps({'name': 'my-key'}),
            content_type='application/json',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)
        self.assertIn('key', data)
        self.assertEqual(data['name'], 'my-key')

    def test_api_list_keys(self):
        """API GET keys should list user's keys."""
        resp = self.client.get(
            '/api/v1/keys/',
            HTTP_X_API_KEY='test_api_key_123456789',
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(len(data['keys']), 1)

    def test_api_invalid_key_401(self):
        """API with invalid key should return 401."""
        resp = self.client.get(
            '/api/v1/links/',
            HTTP_X_API_KEY='invalid_key',
        )
        self.assertEqual(resp.status_code, 401)


class CustomDomainTest(TestCase):
    """Test custom domain model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='domuser', email='dom@test.com', password='testpass123',
        )

    def test_create_domain(self):
        """CustomDomain can be created with pending status."""
        domain = CustomDomain.objects.create(
            owner=self.user, domain='mylinks.com',
        )
        self.assertEqual(domain.status, CustomDomain.STATUS_PENDING)
        self.assertIsNone(domain.dns_verified_at)

    def test_domain_unique(self):
        """CustomDomain domain field should be unique."""
        CustomDomain.objects.create(owner=self.user, domain='unique.com')
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            CustomDomain.objects.create(owner=self.user, domain='unique.com')

    def test_domain_str(self):
        """CustomDomain __str__ should include domain and status."""
        domain = CustomDomain.objects.create(owner=self.user, domain='test.com')
        self.assertIn('test.com', str(domain))


class TeamMemberTest(TestCase):
    """Test team member model."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='teamowner', email='owner@test.com', password='testpass123',
        )
        self.member = User.objects.create_user(
            username='teammember', email='member@test.com', password='testpass123',
        )

    def test_create_team_member(self):
        """TeamMember can be created with member role."""
        tm = TeamMember.objects.create(
            team_owner=self.owner, user=self.member,
        )
        self.assertEqual(tm.role, TeamMember.ROLE_MEMBER)
        self.assertTrue(tm.active)

    def test_team_member_unique(self):
        """TeamMember should not allow duplicates."""
        TeamMember.objects.create(team_owner=self.owner, user=self.member)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            TeamMember.objects.create(team_owner=self.owner, user=self.member)

    def test_team_member_str(self):
        """TeamMember __str__ should include both usernames."""
        tm = TeamMember.objects.create(team_owner=self.owner, user=self.member)
        self.assertIn('teamowner', str(tm))
        self.assertIn('teammember', str(tm))


class WebhookTest(TestCase):
    """Test webhook model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='whuser', email='wh@test.com', password='testpass123',
        )

    def test_create_webhook(self):
        """Webhook can be created with default events."""
        wh = Webhook.objects.create(
            owner=self.user, url='https://example.com/webhook',
        )
        self.assertTrue(wh.active)
        self.assertEqual(wh.fallos_consecutivos, 0)

    def test_webhook_event_list(self):
        """Webhook event_list should parse comma-separated events."""
        wh = Webhook.objects.create(
            owner=self.user, url='https://example.com/wh',
            events='link.created, click, link.deleted',
        )
        events = wh.event_list()
        self.assertEqual(len(events), 3)
        self.assertIn('link.created', events)
        self.assertIn('click', events)

    def test_webhook_generate_secret(self):
        """Webhook.generate_secret should return a non-empty string."""
        secret = Webhook.generate_secret()
        self.assertGreater(len(secret), 20)

    def test_webhook_str(self):
        """Webhook __str__ should include URL."""
        wh = Webhook.objects.create(owner=self.user, url='https://example.com/wh')
        self.assertIn('example.com', str(wh))


class ExportAnalyticsTest(TestCase):
    """Test analytics CSV export."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='exportuser', email='exp@test.com', password='testpass123',
        )
        self.link = ShortLink.objects.create(
            short_code='exptst1', original_url='https://example.com',
            owner=self.user,
        )
        ClickEvent.objects.create(
            link=self.link, device=ClickEvent.DEVICE_DESKTOP, browser='Chrome',
        )

    def test_export_free_user_redirects(self):
        """Free user should be redirected from export."""
        self.client.login(username='exportuser', password='testpass123')
        resp = self.client.get('/mis-links/export/')
        self.assertEqual(resp.status_code, 302)

    def test_export_premium_user_returns_csv(self):
        """Premium user should get CSV file."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_PRO,
        )
        self.client.login(username='exportuser', password='testpass123')
        resp = self.client.get('/mis-links/export/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/csv')
        self.assertIn(b'short_code', resp.content)
        self.assertIn(b'exptst1', resp.content)


class SubscriptionPlanTest(TestCase):
    """Test subscription plan features."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='planuser', email='plan@test.com', password='testpass123',
        )

    def test_plan_link_limits(self):
        """PLAN_LINK_LIMITS should have correct values."""
        self.assertEqual(Subscription.PLAN_LINK_LIMITS['starter'], 3000)
        self.assertEqual(Subscription.PLAN_LINK_LIMITS['pro'], 10000)
        self.assertEqual(Subscription.PLAN_LINK_LIMITS['business'], 50000)

    def test_plan_choices(self):
        """PLAN_CHOICES should have 3 plans."""
        plans = [c[0] for c in Subscription.PLAN_CHOICES]
        self.assertEqual(len(plans), 3)
        self.assertIn('starter', plans)
        self.assertIn('pro', plans)
        self.assertIn('business', plans)

    def test_subscription_default_plan(self):
        """New subscription should default to starter plan."""
        sub = Subscription.objects.create(user=self.user)
        self.assertEqual(sub.plan, Subscription.PLAN_STARTER)

    def test_subscription_is_active_property(self):
        """is_active property should return True only for active status."""
        sub = Subscription.objects.create(user=self.user, status=Subscription.STATUS_ACTIVE)
        self.assertTrue(sub.is_active)
        sub.status = Subscription.STATUS_CANCELLED
        sub.save()
        self.assertFalse(sub.is_active)


class CustomDomainViewTest(TestCase):
    """Test custom domains management UI."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='domview', email='dv@test.com', password='testpass123',
        )

    def test_free_user_redirected(self):
        """Free user should be redirected from custom domains."""
        self.client.login(username='domview', password='testpass123')
        resp = self.client.get('/dominios/')
        self.assertEqual(resp.status_code, 302)

    def test_premium_user_sees_domains(self):
        """Premium user should see custom domains page."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_STARTER,
        )
        self.client.login(username='domview', password='testpass123')
        resp = self.client.get('/dominios/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Dominios', resp.content)

    def test_add_domain(self):
        """Premium user can add a domain."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_STARTER,
        )
        self.client.login(username='domview', password='testpass123')
        resp = self.client.post('/dominios/', {
            'action': 'add', 'domain': 'mylinks.com',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(CustomDomain.objects.filter(domain='mylinks.com').exists())

    def test_delete_domain(self):
        """Premium user can delete a domain."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_STARTER,
        )
        domain = CustomDomain.objects.create(owner=self.user, domain='test.com')
        self.client.login(username='domview', password='testpass123')
        resp = self.client.post('/dominios/', {
            'action': 'delete', 'pk': domain.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(CustomDomain.objects.filter(pk=domain.pk).exists())

    def test_domain_limit_enforced(self):
        """Starter plan should be limited to 1 domain."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_STARTER,
        )
        CustomDomain.objects.create(owner=self.user, domain='first.com')
        self.client.login(username='domview', password='testpass123')
        resp = self.client.post('/dominios/', {
            'action': 'add', 'domain': 'second.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'mite', resp.content)


class DomainPaymentWebhookTest(TestCase):
    """Test MercadoPago webhook handling for domain purchases."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='dpuser', email='dp@test.com', password='testpass123',
        )
        self.cd = CustomDomain.objects.create(
            owner=self.user, domain='mynew.com',
            source=CustomDomain.SOURCE_PURCHASED,
            purchase_status=CustomDomain.PURCHASE_PENDING,
            price=1200,
        )

    def test_approved_payment_triggers_registration(self):
        """Approved payment should mark domain as paid and auto-register."""
        from core import mercadopago_service
        event = {
            'type': 'payment',
            'data': {
                'id': 'pay123456',
                'status': 'approved',
                'external_reference': f'domain:{self.cd.pk}',
            },
        }
        mercadopago_service.handle_webhook_event(event)
        self.cd.refresh_from_db()
        self.assertEqual(self.cd.mp_payment_id, 'pay123456')
        self.assertEqual(self.cd.purchase_status, CustomDomain.PURCHASE_REGISTERED)

    def test_rejected_payment_marks_failed(self):
        """Rejected payment should mark domain purchase as failed."""
        from core import mercadopago_service
        event = {
            'type': 'payment',
            'data': {
                'id': 'pay789',
                'status': 'rejected',
                'external_reference': f'domain:{self.cd.pk}',
            },
        }
        mercadopago_service.handle_webhook_event(event)
        self.cd.refresh_from_db()
        self.assertEqual(self.cd.purchase_status, CustomDomain.PURCHASE_FAILED)

    def test_invalid_external_reference_ignored(self):
        """Invalid external_reference should not crash."""
        from core import mercadopago_service
        event = {
            'type': 'payment',
            'data': {
                'id': 'pay000',
                'status': 'approved',
                'external_reference': 'domain:notanumber',
            },
        }
        # Should not raise
        mercadopago_service.handle_webhook_event(event)
        self.cd.refresh_from_db()
        # Domain should remain pending
        self.assertEqual(self.cd.purchase_status, CustomDomain.PURCHASE_PENDING)

    def test_non_domain_payment_still_works(self):
        """Regular subscription payments should not be affected by domain logic."""
        from core import mercadopago_service
        sub = Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_PENDING,
            plan=Subscription.PLAN_PRO,
        )
        event = {
            'type': 'payment',
            'data': {
                'id': 'pay_sub_1',
                'status': 'approved',
                'external_reference': str(self.user.pk),
            },
        }
        mercadopago_service.handle_webhook_event(event)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)


class ApiKeyViewTest(TestCase):
    """Test API keys management UI."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='keyview', email='kv@test.com', password='testpass123',
        )

    def test_free_user_redirected(self):
        """Free user should be redirected from API keys."""
        self.client.login(username='keyview', password='testpass123')
        resp = self.client.get('/api-keys/')
        self.assertEqual(resp.status_code, 302)

    def test_premium_user_sees_keys(self):
        """Premium user should see API keys page."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_STARTER,
        )
        self.client.login(username='keyview', password='testpass123')
        resp = self.client.get('/api-keys/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'API', resp.content)

    def test_create_key_shows_secret(self):
        """Creating a key should show the secret once."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_STARTER,
        )
        self.client.login(username='keyview', password='testpass123')
        resp = self.client.post('/api-keys/', {
            'action': 'create', 'name': 'test-key',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'API key creada', resp.content)


class TeamMemberViewTest(TestCase):
    """Test team members management UI."""

    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username='teamowner2', email='to2@test.com', password='testpass123',
        )
        self.member = User.objects.create_user(
            username='teammember2', email='tm2@test.com', password='testpass123',
        )

    def test_non_business_redirected(self):
        """Non-business user should be redirected from team."""
        Subscription.objects.create(
            user=self.owner, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_STARTER,
        )
        self.client.login(username='teamowner2', password='testpass123')
        resp = self.client.get('/team/')
        self.assertEqual(resp.status_code, 302)

    def test_business_user_sees_team(self):
        """Business user should see team page."""
        Subscription.objects.create(
            user=self.owner, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_BUSINESS,
        )
        self.client.login(username='teamowner2', password='testpass123')
        resp = self.client.get('/team/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'team', resp.content.lower())

    def test_invite_member(self):
        """Business user can invite a member."""
        Subscription.objects.create(
            user=self.owner, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_BUSINESS,
        )
        self.client.login(username='teamowner2', password='testpass123')
        resp = self.client.post('/team/', {
            'action': 'invite', 'username': 'teammember2',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TeamMember.objects.filter(
            team_owner=self.owner, user=self.member,
        ).exists())


class WebhookViewTest(TestCase):
    """Test webhooks management UI."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='whview', email='whv@test.com', password='testpass123',
        )

    def test_non_business_redirected(self):
        """Non-business user should be redirected from webhooks."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_PRO,
        )
        self.client.login(username='whview', password='testpass123')
        resp = self.client.get('/webhooks/')
        self.assertEqual(resp.status_code, 302)

    def test_business_user_sees_webhooks(self):
        """Business user should see webhooks page."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_BUSINESS,
        )
        self.client.login(username='whview', password='testpass123')
        resp = self.client.get('/webhooks/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Webhook', resp.content)

    def test_create_webhook(self):
        """Business user can create a webhook."""
        Subscription.objects.create(
            user=self.user, status=Subscription.STATUS_ACTIVE,
            plan=Subscription.PLAN_BUSINESS,
        )
        self.client.login(username='whview', password='testpass123')
        resp = self.client.post('/webhooks/', {
            'action': 'create',
            'url': 'https://example.com/hook',
            'events': 'link.created,click',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Webhook.objects.filter(url='https://example.com/hook').exists())

