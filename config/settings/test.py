from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-only-not-secret"  # noqa: F405
ALLOWED_HOSTS = ["*"]  # noqa: F405

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # noqa: F405 -- fast tests only
SESSION_ENGINE = "django.contrib.sessions.backends.db"  # noqa: F405 -- no redis dependency in unit tests

CACHES = {  # noqa: F405
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}

RQ_QUEUES = {  # noqa: F405 -- run jobs inline in tests, no redis dependency
    "crm-default": {**RQ_QUEUES["crm-default"], "ASYNC": False},  # noqa: F405
}
