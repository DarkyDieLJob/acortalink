"""Gunicorn config for 750 concurrent users.

VPS recomendado: 2 vCPU / 4GB RAM
- workers = (2 * CPU) + 1 = 5
- threads = 4 → 20 concurrent requests max
- worker_class = gthread (async I/O within sync Django)

Con Redis cache, los redirects son cache hits (sub-millisecond),
así que 20 conexiones concurrentes manejan ~2000 req/s.
"""

import multiprocessing
import os

bind = '0.0.0.0:8000'
workers = int(os.environ.get('GUNICORN_WORKERS', 2 * multiprocessing.cpu_count() + 1))
threads = int(os.environ.get('GUNICORN_THREADS', 4))
worker_class = 'gthread'
timeout = 30
graceful_timeout = 10
keepalive = 5
max_requests = 1000
max_requests_jitter = 50
preload_app = True
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
