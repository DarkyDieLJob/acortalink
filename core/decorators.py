from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

ACORTADOR_LOGIN_URL = '/ingresar/'


def acortador_login_required(view_func):
    """login_required que redirige al login del acortador, no al global."""
    return login_required(view_func, login_url=ACORTADOR_LOGIN_URL)


def subscription_required(view_func):
    """Requiere que el usuario tenga una subscripción activa."""

    @login_required(login_url=ACORTADOR_LOGIN_URL)
    def wrapper(request, *args, **kwargs):
        sub = getattr(request.user, 'subscription', None)
        if not sub or not sub.is_active:
            return redirect('core:subscribir')
        return view_func(request, *args, **kwargs)

    return wrapper
