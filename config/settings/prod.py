from .base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")  # noqa: F405
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]  # noqa: F405

SESSION_COOKIE_SECURE = True  # noqa: F405
CSRF_COOKIE_SECURE = True  # noqa: F405

# Off until TLS is terminated in front of this deploy (nginx/host proxy with a
# real cert) — with no HTTPS listener, SECURE_SSL_REDIRECT would redirect every
# request to a https:// that nothing serves. Flip DJANGO_SECURE_SSL_REDIRECT=1
# once a domain + cert are in place.
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"  # noqa: F405
SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
