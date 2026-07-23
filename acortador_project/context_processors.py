"""Context processors for Acortalink."""


def google_ads(request):
    """Exposes GOOGLE_ADS_ID setting to all templates."""
    from django.conf import settings
    return {
        'GOOGLE_ADS_ID': getattr(settings, 'GOOGLE_ADS_ID', ''),
    }
