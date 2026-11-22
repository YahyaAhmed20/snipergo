"""
ASGI config for project project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/

Daphne is the configured ASGI server for this project.

Development (in-memory channel layer, no Redis):
    $env:DJANGO_DEBUG="True"; $env:DJANGO_USE_SQLITE="True"; daphne -p 8000 project.asgi:application

Production (Redis channel layer, REDIS_URL required):
    $env:REDIS_URL="<redis-url>"; daphne -p 8000 project.asgi:application

The Procfile must reference daphne with project.asgi:application, not gunicorn
with project.wsgi:application, for WebSocket support.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

from project import routing  # noqa: E402

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(routing.websocket_urlpatterns)
    ),
})
