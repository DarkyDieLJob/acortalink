"""ASGI config for acortador project."""

import os

import acortador_project.compat  # noqa: F401 — baseconv shim for Django 5.x

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'acortador_project.settings')

application = get_asgi_application()
