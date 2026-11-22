import hashlib
import ipaddress
import logging
import os
import time

from django.core.cache import cache
from django.http import HttpResponse

logger = logging.getLogger(__name__)


def _load_trusted_proxies():
    """Load trusted proxy IPs/CIDRs from DJANGO_TRUSTED_PROXY_IPS env var."""
    raw = os.environ.get("DJANGO_TRUSTED_PROXY_IPS", "") or ""
    proxies = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            # ip_network with strict=False accepts both single IPs and CIDR
            proxies.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            pass
    return proxies


_TRUSTED_PROXIES = _load_trusted_proxies()


def _is_trusted_proxy(ip_str):
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_PROXIES)


def _get_client_ip(request):
    """
    Extract client IP safely behind a proxy.

    - If REMOTE_ADDR is a trusted proxy and X-Forwarded-For is present,
      use the leftmost (original client) IP from X-Forwarded-For.
    - Otherwise, use REMOTE_ADDR directly. This prevents a client from
      spoofing X-Forwarded-For to bypass rate limiting.
    """
    remote_addr = request.META.get("REMOTE_ADDR", "")
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if _is_trusted_proxy(remote_addr) and x_forwarded_for:
        client_ip = x_forwarded_for.split(",")[0].strip()
        try:
            ipaddress.ip_address(client_ip)
            return client_ip
        except ValueError:
            pass

    return remote_addr


class RateLimitMiddleware:
    """
    Rate limiting for Django classic views (LoginView, Registration).
    DRF throttling does not apply to these views, so a lightweight
    middleware is used instead. Keys are hashed to avoid storing
    exposed personal data (IP + username) directly in cache.
    """

    LOGIN_PATH = "/accounts/login/"
    REGISTER_PATHS = {"/accounts/register/customer/", "/accounts/register/captain/"}

    LOGIN_LIMIT = 5          # 5 attempts per minute
    REGISTER_LIMIT = 3       # 3 requests per minute per IP
    WINDOW = 60              # 60 seconds

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if request.method == "POST":
            if path == self.LOGIN_PATH:
                return self._handle_login(request)
            if path in self.REGISTER_PATHS:
                return self._handle_register(request)

        return self.get_response(request)

    def _handle_login(self, request):
        username = request.POST.get("username", "") or ""
        ip = _get_client_ip(request)
        key = hashlib.sha256(f"login:{ip}:{username}".encode()).hexdigest()
        cache_key = f"ratelimit:login:{key}"

        now = time.time()
        attempts = cache.get(cache_key, [])
        attempts = [t for t in attempts if now - t < self.WINDOW]

        if len(attempts) >= self.LOGIN_LIMIT:
            retry_after = int(self.WINDOW - (now - attempts[0])) + 1
            logger.warning("Rate limit exceeded for login endpoint.")
            response = HttpResponse(
                "Too many login attempts. Please try again later.",
                status=429,
            )
            response["Retry-After"] = str(max(retry_after, 1))
            return response

        attempts.append(now)
        cache.set(cache_key, attempts, self.WINDOW)

        return self.get_response(request)

    def _handle_register(self, request):
        ip = _get_client_ip(request)
        key = hashlib.sha256(f"register:{ip}".encode()).hexdigest()
        cache_key = f"ratelimit:register:{key}"

        now = time.time()
        attempts = cache.get(cache_key, [])
        attempts = [t for t in attempts if now - t < self.WINDOW]

        if len(attempts) >= self.REGISTER_LIMIT:
            retry_after = int(self.WINDOW - (now - attempts[0])) + 1
            logger.warning("Rate limit exceeded for registration endpoint.")
            response = HttpResponse(
                "Too many registration attempts. Please try again later.",
                status=429,
            )
            response["Retry-After"] = str(max(retry_after, 1))
            return response

        attempts.append(now)
        cache.set(cache_key, attempts, self.WINDOW)

        return self.get_response(request)
