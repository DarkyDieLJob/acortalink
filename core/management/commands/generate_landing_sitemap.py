"""Genera sitemap.xml, robots.txt y llms.txt para la landing (nginx estatico).

Escanea landing/blog/*.html y genera:
- landing/sitemap.xml con todas las URLs
- landing/robots.txt con permisos para AI crawlers
- landing/llms.txt con resumen del sitio para LLMs

Run: python manage.py generate_landing_sitemap
"""

import os
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings


LANDING_DIR = Path(settings.BASE_DIR).parent / 'landing'
SITE_URL = 'https://acortalink.com.ar'


class Command(BaseCommand):
    help = 'Genera sitemap.xml, robots.txt y llms.txt para la landing estatica.'

    def handle(self, *args, **options):
        blog_dir = LANDING_DIR / 'blog'
        if not blog_dir.exists():
            self.stderr.write(self.style.ERROR(f'No existe {blog_dir}'))
            return

        blog_posts = []
        for html_file in sorted(blog_dir.glob('*.html')):
            if html_file.name == 'index.html':
                continue
            blog_posts.append(html_file.stem)

        # --- sitemap.xml ---
        urls = [
            {'loc': f'{SITE_URL}/', 'priority': '1.0', 'changefreq': 'weekly'},
            {'loc': f'{SITE_URL}/blog/', 'priority': '0.9', 'changefreq': 'weekly'},
        ]
        for slug in blog_posts:
            html_file = blog_dir / f'{slug}.html'
            mtime = datetime.fromtimestamp(html_file.stat().st_mtime).strftime('%Y-%m-%d')
            urls.append({
                'loc': f'{SITE_URL}/blog/{slug}.html',
                'priority': '0.8',
                'changefreq': 'monthly',
                'lastmod': mtime,
            })

        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        for u in urls:
            xml_parts.append('  <url>')
            xml_parts.append(f'    <loc>{u["loc"]}</loc>')
            if 'lastmod' in u:
                xml_parts.append(f'    <lastmod>{u["lastmod"]}</lastmod>')
            xml_parts.append(f'    <changefreq>{u["changefreq"]}</changefreq>')
            xml_parts.append(f'    <priority>{u["priority"]}</priority>')
            xml_parts.append('  </url>')
        xml_parts.append('</urlset>')

        sitemap_path = LANDING_DIR / 'sitemap.xml'
        sitemap_path.write_text('\n'.join(xml_parts), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'sitemap.xml: {len(urls)} URLs'
        ))

        # --- robots.txt ---
        robots_content = f"""User-agent: *
Allow: /

# AI retrieval crawlers (drive citation traffic)
User-agent: GPTBot
Allow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Claude-SearchBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: Claude-Web
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: Bytespider
Allow: /

# Opt out of generative training (does not affect Google Search or Bing ranking)
User-agent: Google-Extended
Disallow: /

User-agent: Applebot-Extended
Disallow: /

User-agent: CCBot
Disallow: /

Sitemap: {SITE_URL}/sitemap.xml
"""
        robots_path = LANDING_DIR / 'robots.txt'
        robots_path.write_text(robots_content, encoding='utf-8')
        self.stdout.write(self.style.SUCCESS('robots.txt generado'))

        # --- llms.txt ---
        llms_parts = [
            '# Acortalink',
            '',
            '> Acortador de links con SEO indexable, dominios propios, QR codes y analytics.',
            '> Pago en pesos argentinos con MercadoPago. 5-15x mas barato que Bitly.',
            '> Hecho en Argentina, optimizado para LATAM. Plan free de 30 links.',
            '',
            '## Producto',
            f'- {SITE_URL}/ — Landing principal con features, pricing y comparativa',
            f'- https://app.acortalink.com.ar/registrar/ — Registro gratis (30 links, sin tarjeta)',
            f'- https://app.acortalink.com.ar/ingresar/ — Login',
            '',
            '## Blog',
        ]
        for slug in blog_posts:
            llms_parts.append(f'- {SITE_URL}/blog/{slug}.html')

        llms_parts.extend([
            '',
            '## Pricing',
            '- Free: $0 ARS — 30 links, redirect 302, 2FA, anti-phishing',
            '- Starter: $4.900 ARS/mes (~$5 USD) — 3.000 links, SEO, QR, bulk CSV, API, 1 dominio',
            '- Pro: $9.800 ARS/mes (~$7 USD) — 10.000 links, QR custom, 10 dominios, cifrado',
            '- Business: $28.000 ARS/mes (~$20 USD) — 50.000 links, team, webhooks, 25 dominios',
            '',
            '## Diferenciadores',
            '- Unico acortador con SEO indexable (meta tags + Open Graph por link)',
            '- Pago en pesos con MercadoPago (Bitly/Dub/Short.io solo USD)',
            '- 5-15x mas barato que Bitly',
            '- Cifrado de URLs (django-cryptography)',
            '- Anti-phishing con reportes',
            '- Bulk CSV desde plan Starter (Bitly solo en Enterprise $199/mes)',
        ])

        llms_path = LANDING_DIR / 'llms.txt'
        llms_path.write_text('\n'.join(llms_parts), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'llms.txt: {len(blog_posts)} blog posts listados'
        ))
