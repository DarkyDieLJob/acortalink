from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ShortLink


class Command(BaseCommand):
    help = 'Elimina links free expirados (90 días sin clicks).'

    def handle(self, *args, **options):
        from core.views import FREE_EXPIRY_DAYS

        cutoff = timezone.now() - timezone.timedelta(days=FREE_EXPIRY_DAYS)
        expired = ShortLink.objects.filter(
            is_premium=False,
            ultimo_click__lt=cutoff,
        )
        # Links nunca clickeados: comparar por fecha de creación
        expired_no_clicks = ShortLink.objects.filter(
            is_premium=False,
            ultimo_click__isnull=True,
            creado__lt=cutoff,
        )

        count = expired.count() + expired_no_clicks.count()
        expired.delete()
        expired_no_clicks.delete()

        self.stdout.write(
            self.style.SUCCESS(f'Eliminados {count} links free expirados.')
        )
