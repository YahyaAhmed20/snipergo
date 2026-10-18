from .settings import *

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PAYMOB_INTEGRATION_ID = "5489313"
PAYMOB_HMAC_SECRET = "local-test-secret"
