"""Maintenance mode middleware.

When MAINTENANCE_MODE=True:
- External requests get 503 (service unavailable)
- Internal IPs (localhost, Docker network, private ranges) are allowed
- Rate limiting is bypassed for internal IPs

Usage:
    Set MAINTENANCE_MODE=True in .env to activate.
    Set MAINTENANCE_ALLOWED_IPS to add extra IPs (comma-separated).
"""

import os
import ipaddress

from django.conf import settings
from django.http import JsonResponse


def _is_internal_ip(ip_str):
    """Check if IP is internal (localhost, private, Docker networks)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except (ValueError, ipaddress.AddressValueError):
        return False

    if ip in ipaddress.ip_network('127.0.0.0/8'):
        return True
    if ip in ipaddress.ip_network('10.0.0.0/8'):
        return True
    if ip in ipaddress.ip_network('172.16.0.0/12'):
        return True
    if ip in ipaddress.ip_network('192.168.0.0/16'):
        return True
    if ip in ipaddress.ip_network('169.254.0.0/16'):
        return True
    if ip == ipaddress.ip_address('::1'):
        return True
    if ip in ipaddress.ip_network('fc00::/7'):
        return True
    if ip in ipaddress.ip_network('fe80::/10'):
        return True

    extra = getattr(settings, 'MAINTENANCE_ALLOWED_IPS', [])
    for allowed in extra:
        try:
            if ip in ipaddress.ip_network(allowed, strict=False):
                return True
        except (ValueError, ipaddress.AddressValueError):
            continue

    return False


def is_maintenance_internal(request):
    """Check if this request should bypass maintenance mode and rate limits.

    Returns True if:
    - Maintenance mode is ON AND
    - The request comes from an internal IP
    """
    if not getattr(settings, 'MAINTENANCE_MODE', False):
        return False

    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        ip = xff.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '0.0.0.0')

    return _is_internal_ip(ip)


class MaintenanceModeMiddleware:
    """Blocks external traffic when MAINTENANCE_MODE=True.

    Internal IPs bypass the block and also bypass rate limiting.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'MAINTENANCE_MODE', False):
            return self.get_response(request)

        if is_maintenance_internal(request):
            request._maintenance_bypass = True
            return self.get_response(request)

        return JsonResponse(
            {'error': 'Servicio en mantenimiento', 'status': 503},
            status=503,
        )
