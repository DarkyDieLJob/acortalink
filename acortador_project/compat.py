"""Compatibility shim: re-add django.utils.baseconv for django-cryptography 1.1.

Django 5.x removed django.utils.baseconv (deprecated since 4.1).
django-cryptography 1.1 imports it in django_cryptography/core/signing.py.
This shim provides the minimal base62 implementation needed.

Must be imported before django.setup() — manage.py and wsgi.py do this.
"""

import django.utils as _django_utils

if not hasattr(_django_utils, 'baseconv'):
    class _BaseConv:
        """Minimal base62 encoder/decoder (Django's original implementation)."""
        BASE62 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'

        @classmethod
        def encode(cls, num):
            if num == 0:
                return cls.BASE62[0]
            chars = []
            while num:
                num, rem = divmod(num, 62)
                chars.append(cls.BASE62[rem])
            return ''.join(reversed(chars))

        @classmethod
        def decode(cls, s):
            num = 0
            for c in s:
                num = num * 62 + cls.BASE62.index(c)
            return num

    _django_utils.baseconv = _BaseConv
