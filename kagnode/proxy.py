"""Accept forwarding headers only from explicitly trusted proxy peers."""

import ipaddress

from werkzeug.middleware.proxy_fix import ProxyFix


class TrustedProxyFix:
    def __init__(self, app, trusted_proxies):
        if not isinstance(trusted_proxies, list):
            raise ValueError("TRUSTED_PROXIES must be a list of exact IP addresses.")
        addresses = set()
        for value in trusted_proxies:
            if not isinstance(value, str) or "%" in value:
                raise ValueError("TRUSTED_PROXIES must contain exact IP addresses.")
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError("TRUSTED_PROXIES must contain exact IP addresses, without CIDR ranges.") from exc
            if address.is_unspecified or address.is_multicast:
                raise ValueError("TRUSTED_PROXIES must identify individual proxy peers.")
            addresses.add(address)
        self.trusted_proxies = frozenset(addresses)
        self.app = ProxyFix(app, x_for=1, x_proto=1, x_host=0)

    def __call__(self, environ, start_response):
        try:
            trusted = ipaddress.ip_address(environ.get("REMOTE_ADDR", "")) in self.trusted_proxies
        except ValueError:
            trusted = False
        if not trusted:
            # Preserve the WSGI server's actual peer and transport. Header values
            # supplied by a direct client must not change either one.
            for name in list(environ):
                if name.startswith("HTTP_X_FORWARDED_") or name in (
                    "HTTP_FORWARDED", "HTTP_X_REAL_IP",
                ):
                    del environ[name]
        return self.app(environ, start_response)
