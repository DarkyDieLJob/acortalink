from locust import HttpUser, task, between, constant

class AcortalinkUser(HttpUser):
    """Simula usuarios concurrentes en Acortalink.

    Escenarios:
    1. Visit landing page
    2. Visit app homepage (login form)
    3. Follow a short link redirect (the hot path — 99% Redis cache hit)
    4. Visit app and browse links
    """

    wait_time = between(1, 3)

    @task(50)
    def follow_short_link(self):
        """Hot path: redirect de link acortado. Este es el 95% del tráfico real."""
        # /app/s/loadtest/ -> Django redirect_view -> Redis cache -> 302 to example.com
        with self.client.get("/app/s/loadtest/", name="/app/s/{code} (redirect)", allow_redirects=False, catch_response=True) as resp:
            if resp.status_code in (302, 301, 200):
                resp.success()
            else:
                resp.failure(f"Unexpected status: {resp.status_code}")

    @task(20)
    def visit_landing(self):
        """Landing page SEO."""
        self.client.get("/landing/", name="/landing/ (SEO landing)")

    @task(15)
    def visit_app_home(self):
        """App homepage - formulario de acortar."""
        self.client.get("/app/", name="/app/ (home)")

    @task(5)
    def visit_admin(self):
        """Admin login page."""
        self.client.get("/app/admin/login/", name="/app/admin/login/")
