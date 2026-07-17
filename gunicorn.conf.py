"""Gunicorn config for high-concurrency acortador.

VPS: 4 vCPU / 3.8GB RAM (shared with other containers)
- workers = 15 (override via GUNICORN_WORKERS env)
- threads = 4 → 60 concurrent requests max
- worker_class = gthread (async I/O within sync Django)

Con Redis cache, los redirects son cache hits (sub-millisecond),
así que 60 conexiones concurrentes manejan ~3000+ req/s.
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
