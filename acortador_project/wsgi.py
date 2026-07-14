"""WSGI config for acortador project."""

import os

import acortador_project.compat  # noqa: F401 — baseconv shim for Django 5.x

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'acortador_project.settings')

application = get_wsgi_application()
