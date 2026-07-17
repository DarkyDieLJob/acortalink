"""Flush accumulated click counts and events from cache to database.

Runs via cron every 5 minutes. Reads pending click PKs from Redis SET
and batch-updates the ShortLink records, avoiding a DB write per redirect
request. Also flushes click events (geo/device/referrer) to ClickEvent model.

Usage:
    python manage.py flush_clicks
"""

import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from core.models import ShortLink, ClickEvent

logger = logging.getLogger(__name__)

CLICK_KEY_PREFIX = 'clicks:'
CLICK_KEY_TTL = 900  # 15 min — must be >= cron interval
EVENT_KEY_PREFIX = 'click_events:'


class Command(BaseCommand):
    help = 'Flush accumulated click counts and events from cache to database.'

    def handle(self, *args, **options):
        set_key = getattr(settings, 'CLICK_TRACKING_SET_KEY', 'clicks:pending_pks')

        pending_pks = self._get_pending_pks(set_key)

        if pending_pks is None:
            self._flush_legacy()
            return

        if not pending_pks:
            self.stdout.write('No pending clicks to flush.')
            return

        updated = 0
        flushed = 0
        events_created = 0

        for pk in pending_pks:
            click_key = f'{CLICK_KEY_PREFIX}{pk}'
            count = cache.get(click_key)
            if count is None:
                continue

            ShortLink.objects.filter(pk=pk).update(
                clicks=F('clicks') + count,
                ultimo_click=timezone.now(),
            )
            cache.delete(click_key)
            updated += 1
            flushed += count

            # Flush click events for analytics
            events_created += self._flush_events(pk)

        try:
            cache.delete(set_key)
        except Exception:
            pass

        if updated:
            msg = f'Flushed {flushed} clicks across {updated} links.'
            if events_created:
                msg += f' {events_created} analytics events persisted.'
            self.stdout.write(self.style.SUCCESS(msg))
        else:
            self.stdout.write('No pending clicks to flush.')

    def _flush_events(self, pk):
        """Flush click events from Redis list to ClickEvent model."""
        event_key = f'{EVENT_KEY_PREFIX}{pk}'
        events_to_create = []

        while True:
            try:
                client = cache._cache.get_client()
                raw = client.lpop(event_key)
            except (AttributeError, NotImplementedError):
                break
            if not raw:
                break
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue

            ua = data.get('ua', '')
            device, browser = self._parse_ua(ua)

            events_to_create.append(ClickEvent(
                link_id=pk,
                ip=data.get('ip') or None,
                device=device,
                browser=browser,
                referrer=data.get('ref', '')[:500],
            ))

        if events_to_create:
            try:
                ClickEvent.objects.bulk_create(events_to_create, batch_size=100)
            except Exception as e:
                logger.warning('Failed to create click events for pk %s: %s', pk, e)
                return 0

        return len(events_to_create)

    def _parse_ua(self, ua_string):
        """Parse user agent into (device, browser) tuple."""
        if not ua_string:
            return ClickEvent.DEVICE_OTHER, ''

        ua_lower = ua_string.lower()

        # Device detection
        if 'bot' in ua_lower or 'crawler' in ua_lower or 'spider' in ua_lower:
            device = ClickEvent.DEVICE_BOT
        elif 'ipad' in ua_lower or 'tablet' in ua_lower:
            device = ClickEvent.DEVICE_TABLET
        elif 'mobile' in ua_lower or 'iphone' in ua_lower or 'android' in ua_lower:
            device = ClickEvent.DEVICE_MOBILE
        else:
            device = ClickEvent.DEVICE_DESKTOP

        # Browser detection (simplified)
        if 'edg/' in ua_lower:
            browser = 'Edge'
        elif 'chrome/' in ua_lower and 'chromium' not in ua_lower:
            browser = 'Chrome'
        elif 'firefox/' in ua_lower:
            browser = 'Firefox'
        elif 'safari/' in ua_lower and 'chrome' not in ua_lower:
            browser = 'Safari'
        elif 'opera' in ua_lower or 'opr/' in ua_lower:
            browser = 'Opera'
        else:
            browser = 'Other'

        return device, browser

    def _get_pending_pks(self, set_key):
        """Obtiene PKs del Redis SET. Retorna None si no soporta SMEMBERS."""
        try:
            client = cache._cache.get_client()
            pks = client.smembers(set_key)
            return [int(pk) for pk in pks]
        except (AttributeError, NotImplementedError):
            return None
        except Exception:
            return None

    def _flush_legacy(self):
        """Fallback para LocMemCache — iterar todos los links."""
        updated = 0
        flushed = 0

        for link in ShortLink.objects.all().only('pk'):
            click_key = f'{CLICK_KEY_PREFIX}{link.pk}'
            count = cache.get(click_key)
            if count is None:
                continue

            ShortLink.objects.filter(pk=link.pk).update(
                clicks=F('clicks') + count,
                ultimo_click=timezone.now(),
            )
            cache.delete(click_key)
            updated += 1
            flushed += count

        if updated:
            self.stdout.write(self.style.SUCCESS(
                f'Flushed {flushed} clicks across {updated} links (legacy mode).'
            ))
        else:
            self.stdout.write('No pending clicks to flush.')
