# Roadmap del producto

## Fase 0: Deploy VPS (semanas 1-2) — EN PROGRESO

- [x] Código standalone extraído del portfolio
- [x] UV + Docker Compose configurados
- [x] Optimización 750 usuarios concurrentes
- [x] Tests pasando (12/12)
- [x] Git repo inicializado
- [ ] Comprar dominio
- [ ] Contratar VPS
- [ ] Configurar MercadoPago productivo
- [ ] Configurar email SMTP
- [ ] Generar FIELD_ENCRYPTION_KEY
- [ ] Deploy con Caddy (auto-SSL)
- [ ] Probar flujo completo end-to-end

## Fase 1: Custom domains (semanas 3-5)

- [ ] Modelo `CustomDomain` en Django
- [ ] Dashboard: agregar dominio existente o subdomain
- [ ] Verificación DNS automática (polling cada 5 min)
- [ ] Caddy con `on_demand_tls` para SSL automático
- [ ] Multi-tenant routing (Host header → dominio → links)
- [ ] Redis cache para resoluciones multi-tenant

Ver `custom-domains-plan.md` para detalle técnico.

## Fase 2: API REST (semanas 5-6)

- [ ] `POST /api/v1/shorten` (long_url → short_url)
- [ ] `GET /api/v1/links` (listar links)
- [ ] API keys con rate limiting
- [ ] Documentación OpenAPI

## Fase 3: QR codes (semana 7)

- [ ] Generar QR code para cada short link
- [ ] Descarga PNG/SVG
- [ ] QR dinámicos (editables)

## Fase 4: Registro de dominios (semanas 8-10)

- [ ] Integración registrador (evaluar Namecheap vs Cloudflare)
- [ ] Búsqueda de dominios desde dashboard
- [ ] Checkout con MercadoPago
- [ ] Auto-configuración DNS post-registro

## Fase 5: Path prefix (semanas 10-11)

- [ ] Endpoint `/resolve/{slug}` con header `X-Custom-Domain`
- [ ] Generador de config snippets (nginx, Caddy, CF Workers)
- [ ] Wizard de configuración guiada

## Fase 6: Marketing LATAM (semanas 12+)

- [ ] SEO en español (keywords: "acortador de links", "acortar url")
- [ ] Posts Reddit (r/Argentina, r/devAr, r/mexico, r/brasil)
- [ ] Dev.to en español
- [ ] Telegram/Discord comunidades devs LATAM
- [ ] Google Ads LATAM ($50/mes)

Ver `arquitectura-750-difusion-latam.md` sección 3 para detalle.
