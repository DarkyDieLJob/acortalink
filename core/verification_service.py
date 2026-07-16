"""Email verification service for account activation and 2FA actions.

Two flows:
- Activation: link-based, sent on registration. 24h expiry.
- Action 2FA: code-based, for sensitive operations (password change,
  email change, account deletion, etc.). 10min expiry.

The action flow uses a session-based pending action store and a handler
registry so any view can request verification and plug in its own logic.
"""

import secrets
import string
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import EmailVerification


def _base_url():
    """Return SITE_URL without any path — just scheme + domain."""
    url = settings.SITE_URL.rstrip('/')
    parsed = urlparse(url)
    return f'{parsed.scheme}://{parsed.netloc}'

ACTIVATION_EXPIRY_HOURS = 24
ACTION_CODE_EXPIRY_MINUTES = 10

SESSION_KEY = 'pending_action'


# --- Token / code generators ---

def _generate_token(length=64):
    return secrets.token_urlsafe(length)[:length]


def _generate_code(length=6):
    return ''.join(secrets.choice(string.digits) for _ in range(length))


# --- Activation flow (link-based, registration) ---

def create_activation_verification(user):
    """Create an activation token for a newly registered user and send email."""
    EmailVerification.objects.filter(
        user=user,
        verification_type=EmailVerification.TYPE_ACTIVATION,
        used=False,
    ).update(used=True)

    token = _generate_token()
    verification = EmailVerification.objects.create(
        user=user,
        verification_type=EmailVerification.TYPE_ACTIVATION,
        token=token,
        expires_at=timezone.now() + timedelta(hours=ACTIVATION_EXPIRY_HOURS),
    )

    activation_url = f"{_base_url()}/activar/{token}/"

    send_mail(
        subject='Activá tu cuenta — Acortalink DieL',
        message=(
            f'Hola {user.username},\n\n'
            f'Activá tu cuenta en el acortador de links de DieL '
            f'haciendo click en el siguiente enlace:\n\n'
            f'{activation_url}\n\n'
            f'El enlace expira en {ACTIVATION_EXPIRY_HOURS} horas.\n\n'
            f'Si no creaste esta cuenta, ignorá este email.\n'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )

    return verification


def verify_activation_token(token):
    """Verify an activation token. Returns (user, error_message)."""
    verification = EmailVerification.objects.filter(
        token=token,
        verification_type=EmailVerification.TYPE_ACTIVATION,
        used=False,
    ).first()

    if not verification:
        return None, 'El enlace de activación no es válido o ya fue usado.'

    if verification.is_expired:
        verification.used = True
        verification.save()
        return None, 'El enlace de activación expiró. Solicitá uno nuevo.'

    user = verification.user
    user.is_active = True
    user.save()

    verification.used = True
    verification.save()

    return user, None


# --- Action 2FA flow (code-based, generic) ---

# Registry of action handlers.
# Each handler: (request, data) -> (success: bool, error: str|None, redirect_url: str|None)
_ACTION_HANDLERS = {}


def register_action(action_type, handler, description=''):
    """Register a handler for a 2FA action type.

    handler(request, data) -> (success, error_message, redirect_url)
    """
    _ACTION_HANDLERS[action_type] = {
        'handler': handler,
        'description': description,
    }


def get_action_description(action_type):
    """Get the human-readable description for an action type."""
    info = _ACTION_HANDLERS.get(action_type)
    return info['description'] if info else action_type


def create_action_verification(user, action_type):
    """Create a 6-digit code for a 2FA action and send email."""
    EmailVerification.objects.filter(
        user=user,
        verification_type=EmailVerification.TYPE_ACTION,
        used=False,
    ).update(used=True)

    code = _generate_code()
    verification = EmailVerification.objects.create(
        user=user,
        verification_type=EmailVerification.TYPE_ACTION,
        token=_generate_token(),
        code=code,
        expires_at=timezone.now() + timedelta(minutes=ACTION_CODE_EXPIRY_MINUTES),
    )

    description = get_action_description(action_type)

    send_mail(
        subject=f'Código de verificación — {description}',
        message=(
            f'Hola {user.username},\n\n'
            f'Tu código de verificación para {description.lower()} es:\n\n'
            f'    {code}\n\n'
            f'El código expira en {ACTION_CODE_EXPIRY_MINUTES} minutos.\n\n'
            f'Si no solicitaste esta acción, ignorá este email.\n'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )

    return verification


def verify_action_code(user, code):
    """Verify a 2FA action code. Returns (verification, error_message)."""
    verification = EmailVerification.objects.filter(
        user=user,
        verification_type=EmailVerification.TYPE_ACTION,
        used=False,
    ).order_by('-creado').first()

    if not verification:
        return None, 'No hay código pendiente. Solicitá uno nuevo.'

    if verification.is_expired:
        verification.used = True
        verification.save()
        return None, 'El código expiró. Solicitá uno nuevo.'

    if verification.code != code:
        return None, 'Código incorrecto.'

    return verification, None


# --- Session-based pending action store ---

def store_pending_action(request, action_type, data):
    """Store a pending action in the session and send verification code.

    Returns the EmailVerification instance or None on error.
    """
    if not request.user.email:
        return None

    request.session[SESSION_KEY] = {
        'type': action_type,
        'data': data,
    }
    request.session.modified = True

    return create_action_verification(request.user, action_type)


def get_pending_action(request):
    """Get the pending action dict from session, or None."""
    return request.session.get(SESSION_KEY)


def clear_pending_action(request):
    """Remove the pending action from session."""
    request.session.pop(SESSION_KEY, None)
    request.session.modified = True


def execute_pending_action(request, code):
    """Verify code and execute the pending action.

    Returns (redirect_url, error_message).
    On success, redirect_url is set and error is None.
    On failure, redirect_url is None and error describes the problem.
    """
    pending = get_pending_action(request)
    if not pending:
        return None, 'No hay acción pendiente. Volvé a intentarlo desde el inicio.'

    verification, v_error = verify_action_code(request.user, code)
    if v_error:
        return None, v_error

    handler_info = _ACTION_HANDLERS.get(pending['type'])
    if not handler_info:
        clear_pending_action(request)
        return None, f'Tipo de acción desconocido: {pending["type"]}'

    success, error, redirect_url = handler_info['handler'](request, pending['data'])

    if success:
        verification.used = True
        verification.save()
        clear_pending_action(request)

    return redirect_url, error
