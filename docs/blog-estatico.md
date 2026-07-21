# Blog estático — Guía para agregar posts

## Estructura

```
landing/
├── index.html              # Homepage
├── robots.txt              # Generado por comando (no editar a mano)
├── sitemap.xml             # Generado por comando (no editar a mano)
├── llms.txt                # Generado por comando (no editar a mano)
├── favicon.svg
├── Dockerfile              # nginx:alpine, copia todo a /usr/share/nginx/html/
└── blog/
    ├── index.html          # Blog index
    ├── style.css           # Estilos del blog
    ├── acortador-argentina.html
    ├── alternativa-a-bitly.html
    └── ...                 # 30 posts actualmente
```

La landing se sirve con **nginx** (Docker). Los HTML son estáticos, no pasan por Django.

## Cómo agregar un blog post

1. **Crear el archivo HTML** en `landing/blog/{slug}.html`
   - Usar `slug` descriptivo con keywords (ej: `acortador-chile.html`, no `post-31.html`)
   - Copiar la estructura de un post existente (meta tags, nav, footer)
   - Incluir: `<meta name="robots" content="index, follow">`, Open Graph, Twitter Cards, canonical

2. **Agregar el post al blog index** en `landing/blog/index.html`
   - Buscar la sección de posts y agregar un `<a href="{slug}.html">` con título y descripción

3. **Regenerar sitemap, robots.txt y llms.txt**:
   ```bash
   cd /path/al/acortador
   python manage.py generate_landing_sitemap
   ```
   Esto escanea `landing/blog/*.html` y regenera:
   - `sitemap.xml` con todas las URLs y `lastmod`
   - `robots.txt` con permisos para AI crawlers (GPTBot, PerplexityBot, Claude, etc.)
   - `llms.txt` con resumen del sitio para LLMs (GEO/AI search optimization)

4. **Deploy**:
   ```bash
   # Rebuild y deploy del container nginx
   docker compose up -d --build landing
   # o el comando de deploy que uses
   ```

5. **Acelerar indexación** (opcional):
   - Google Search Console → URL Inspection → pegar la URL nueva → "Request Indexing"
   - Solo para posts importantes (límite ~10/día)

## Comando generate_landing_sitemap

Ubicación: `core/management/commands/generate_landing_sitemap.py`

Qué hace:
- Escanea `landing/blog/*.html` (excluye `index.html`, ya está hardcoded)
- Genera `landing/sitemap.xml` con: homepage, blog index, y un `<url>` por cada post
- Genera `landing/robots.txt` con `Allow: /` + reglas explícitas para AI crawlers + referencia al sitemap
- Genera `landing/llms.txt` con resumen del producto, pricing, diferenciadores y links a todos los blog posts
- `lastmod` = fecha de modificación del archivo HTML

No necesita Django corriendo ni DB — solo acceso al filesystem. Se puede correr desde el VPS o desde local antes de hacer push.

## SEO on-page por post

Cada post debe tener:

```html
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{Título} — Acortalink</title>
<meta name="description" content="{155-160 chars con keyword}">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://acortalink.com.ar/blog/{slug}.html">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="stylesheet" href="/blog/style.css">
<meta property="og:type" content="article">
<meta property="og:title" content="{Título}">
<meta property="og:description" content="{descripción}">
<meta property="og:url" content="https://acortalink.com.ar/blog/{slug}.html">
<meta property="og:site_name" content="Acortalink">
<meta property="og:locale" content="es_AR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{Título}">
<meta name="twitter:description" content="{descripción}">
```

## Sitemap de la app Django (app.acortalink.com.ar)

La app Django tiene su propio sitemap **dinámico** que se genera en runtime:

- URL: `https://app.acortalink.com.ar/sitemap.xml`
- View: `core.views.sitemap_xml`
- Lista: páginas públicas estáticas + links premium con `has_seo=True` (hasta 500)
- No requiere regeneración manual — se actualiza solo en cada request

## Google Search Console

Dominios registrados (o a registrar):
- `acortalink.com.ar` — sitemap: `https://acortalink.com.ar/sitemap.xml`
- `app.acortalink.com.ar` — sitemap: `https://app.acortalink.com.ar/sitemap.xml`

Enviar sitemaps desde Search Console después del primer deploy.

## Bing Webmaster Tools

**Obligatorio para ChatGPT Search** — el 87% de las citas de ChatGPT Search vienen del top 10 de Bing.

- Registrar en [bing.com/webmasters](https://www.bing.com/webmasters)
- Añadir `acortalink.com.ar` y `app.acortalink.com.ar`
- Verificar via DNS TXT o meta tag
- Enviar sitemaps (ambos dominios)

## GEO — Generative Engine Optimization

Optimización para que IA (ChatGPT, Perplexity, Gemini, Claude) cite Acortalink en sus respuestas.

### Lo que YA está implementado

- **robots.txt** con reglas explícitas para AI crawlers (GPTBot, OAI-SearchBot, PerplexityBot, ClaudeBot, Claude-SearchBot, etc.)
- **llms.txt** en la raíz del sitio con resumen del producto, pricing, diferenciadores y links a todos los blog posts
- **FAQPage schema** en los 30 blog posts + landing
- **Article schema** con `datePublished` y `dateModified` en los 30 blog posts
- **SoftwareApplication schema** en la landing
- **Organization schema** en la landing
- **WebSite schema** en la landing
- **robots.txt** de la app Django también permite AI crawlers

### Lo que hay que hacer al agregar un blog post nuevo

Además de los pasos de arriba, asegurar:

1. **TL;DR answer block** — 40-60 palabras después del H1 que respondan directamente la pregunta del título. Es lo que las IA citan textualmente.
2. **H2 como preguntas** — los H2 deben ser preguntas reales que la gente buscaría ("¿Cuál es el acortador más barato?" no "Análisis de precios")
3. **Párrafos cortos** — máximo 120 palabras, una idea por párrafo
4. **FAQPage schema** — con 3-4 Q&A relevantes al final del post
5. **Article schema** — con `datePublished` y `dateModified`
6. **Tablas de comparación** — los posts con 3+ tablas reciben 25.7% más citas en AI Overviews

### Verificación manual (post-deploy)

Cada 1-2 semanas, buscar en:
- **ChatGPT**: "mejor acortador de links argentina" / "alternativa a bitly barato"
- **Perplexity**: "acortador de links con mercadopago" / "acortador con SEO"
- **Google**: "acortador de links argentina" — verificar si aparece AI Overview

Si no aparecemos en 4-6 semanas, revisar:
1. ¿Bing nos indexó? (Bing Webmaster Tools → URL Inspection)
2. ¿Google nos indexó? (Search Console → Coverage)
3. ¿El post tiene answer block + FAQ schema?
4. ¿Hay menciones en third-party sites (Reddit, AlternativeTo)?
