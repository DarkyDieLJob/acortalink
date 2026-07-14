"""Flush accumulated click counts from cache to database.

Runs via cron every 5 minutes. Reads pending click PKs from Redis SET
and batch-updates the ShortLink records, avoiding a DB write per redirect
request.

Usage:
    python manage.py flush_clicks
"""

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from core.models import ShortLink

CLICK_KEY_PREFIX = 'clicks:'
CLICK_KEY_TTL = 900  # 15 min — must be >= cron interval


class Command(BaseCommand):
    help = 'Flush accumulated click counts from cache to database.'

    def handle(self, *args, **options):
        set_key = getattr(settings, 'CLICK_TRACKING_SET_KEY', 'clicks:pending_pks')

        # Intentar obtener PKs del Redis SET (eficiente — solo links con clicks)
        pending_pks = self._get_pending_pks(set_key)

        if pending_pks is None:
            # Fallback: LocMemCache no soporta SMEMBERS — iterar todos
            self._flush_legacy()
            return

        if not pending_pks:
            self.stdout.write('No pending clicks to flush.')
            return

        updated = 0
        flushed = 0

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

        # Limpiar el SET
        try:
            cache.delete(set_key)
        except Exception:
            pass

        if updated:
            self.stdout.write(self.style.SUCCESS(
                f'Flushed {flushed} clicks across {updated} links.'
            ))
        else:
            self.stdout.write('No pending clicks to flush.')

    def _get_pending_pks(self, set_key):
        """Obtiene PKs del Redis SET. Retorna None si no soporta SMEMBERS."""
        try:
            pks = cache.smembers(set_key)
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
