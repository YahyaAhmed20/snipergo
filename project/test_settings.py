
import os
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .settings import *

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    raise ImproperlyConfigured(
        "TEST_DATABASE_URL must point to the dedicated test database."
    )

parsed_url = urlsplit(TEST_DATABASE_URL)

if parsed_url.path.lstrip("/") != "snipergo_test":
    raise ImproperlyConfigured(
        "Tests may only use the snipergo_test database."
    )

DATABASES = {
    "default": dj_database_url.parse(
        TEST_DATABASE_URL,
        conn_max_age=0,
        ssl_require=False,
    )
}

DATABASES["default"]["TEST"] = {
    "NAME": "test_snipergo_test",
}
