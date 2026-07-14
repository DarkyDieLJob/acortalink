import ssl
from django.core.mail.backends.smtp import EmailBackend


class UnverifiedSMTPBackend(EmailBackend):
    def open(self):
        if self.connection:
            return False

        connection_params = {'timeout': self.timeout}
        if self.use_ssl:
            connection_params['context'] = ssl._create_unverified_context()

        try:
            self.connection = self.connection_class(self.host, self.port, **connection_params)

            if self.use_tls:
                context = ssl._create_unverified_context()
                self.connection.starttls(context=context)

            if self.username and self.password:
                self.connection.login(self.username, self.password)
        except Exception:
            if not self.fail_silently:
                raise
            self.connection = None

        return True
