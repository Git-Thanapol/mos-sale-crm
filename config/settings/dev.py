from .base import *  # noqa: F403

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"  # noqa: F405
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")  # noqa: F405
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-secret")  # noqa: F405

SESSION_COOKIE_SECURE = False  # noqa: F405
CSRF_COOKIE_SECURE = False  # noqa: F405
