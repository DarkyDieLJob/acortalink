"""Ping search engines for links with needs_ping=True.

Runs via cron. Sends ping requests to Google and Bing indexing endpoints
for each link that was edited and marked as needing a ping. Marks
needs_ping=False after attempting, regardless of success (search engines
will crawl on their own schedule).
"""

import logging
import urllib.request
import urllib.parse
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from core.models import ShortLink

logger = logging.getLogger(__name__)

GOOGLE_PING_URL = 'https://www.google.com/ping'
BING_PING_URL = 'https://www.bing.com/ping'


class Command(BaseCommand):
    help = 'Ping search engines for links with pending SEO indexation.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be pinged without making requests.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        pending = ShortLink.objects.filter(needs_ping=True)

        if not pending:
            self.stdout.write('No links pending ping.')
            return

        site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
        parsed = urlparse(site_url.rstrip('/'))
        base_url = f'{parsed.scheme}://{parsed.netloc}'
        pinged = 0
        failed = 0

        for link in pending:
            sitemap_url = f"{base_url}/s/{link.short_code}/"
            self.stdout.write(f'Pinging: {sitemap_url}')

            if dry_run:
                pinged += 1
                continue

            ok = self._ping_google(sitemap_url) and self._ping_bing(sitemap_url)

            if ok:
                pinged += 1
            else:
                failed += 1

            link.needs_ping = False
            link.seo_updated_at = timezone.now()
            link.save(update_fields=['needs_ping', 'seo_updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'Done: {pinged} pinged, {failed} failed.'
        ))

    def _ping_google(self, url):
        try:
            params = urllib.parse.urlencode({'sitemap': url})
            full_url = f'{GOOGLE_PING_URL}?{params}'
            req = urllib.request.Request(full_url, method='GET')
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            logger.warning('Google ping failed for %s: %s', url, e)
            return False

    def _ping_bing(self, url):
        try:
            params = urllib.parse.urlencode({'sitemap': url})
            full_url = f'{BING_PING_URL}?{params}'
            req = urllib.request.Request(full_url, method='GET')
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            logger.warning('Bing ping failed for %s: %s', url, e)
            return False
