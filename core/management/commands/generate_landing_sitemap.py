"""Genera sitemap.xml y robots.txt para la landing (nginx estatico).

Escanea landing/blog/*.html y genera landing/sitemap.xml con todas las URLs.
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
    help = 'Genera sitemap.xml y robots.txt para la landing estatica.'

    def handle(self, *args, **options):
        blog_dir = LANDING_DIR / 'blog'
        if not blog_dir.exists():
            self.stderr.write(self.style.ERROR(f'No existe {blog_dir}'))
            return

        urls = [
            {'loc': f'{SITE_URL}/', 'priority': '1.0', 'changefreq': 'weekly'},
            {'loc': f'{SITE_URL}/blog/', 'priority': '0.9', 'changefreq': 'weekly'},
        ]

        for html_file in sorted(blog_dir.glob('*.html')):
            if html_file.name == 'index.html':
                continue
            mtime = datetime.fromtimestamp(html_file.stat().st_mtime).strftime('%Y-%m-%d')
            slug = html_file.stem
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
            f'sitemap.xml generado: {len(urls)} URLs en {sitemap_path}'
        ))

        robots_path = LANDING_DIR / 'robots.txt'
        robots_content = f'User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n'
        robots_path.write_text(robots_content, encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'robots.txt generado en {robots_path}'
        ))
