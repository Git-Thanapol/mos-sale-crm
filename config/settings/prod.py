from .base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")  # noqa: F405
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]  # noqa: F405

# Content-hashed static filenames (base.css -> base.<hash>.css). nginx serves
# /static/ with a one-year immutable Cache-Control (docker/nginx/crm.conf), which
# is only safe because every change gets a new URL. Do not remove this without
# also removing that header.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Off until TLS is terminated in front of this deploy (nginx/host proxy with a
# real cert). With no HTTPS listener: SECURE_SSL_REDIRECT would redirect every
# request to a https:// that nothing serves, and *_COOKIE_SECURE cookies are
# dropped by browsers on plain http:// (breaks CSRF/login entirely). Flip
# DJANGO_SECURE_SSL_REDIRECT=1 once a domain + cert are in place.
_TLS = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"  # noqa: F405
SESSION_COOKIE_SECURE = _TLS
CSRF_COOKIE_SECURE = _TLS
SECURE_SSL_REDIRECT = _TLS
SECURE_HSTS_SECONDS = 31536000 if _TLS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
