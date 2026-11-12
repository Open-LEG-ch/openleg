# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded process-local public tool budgets, independent of MCP sessions."""

import ipaddress
import math
import os
import time
from collections import OrderedDict


def positive_setting(name, default):
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


class PublicLimits:
    def __init__(self):
        self.calls = positive_setting("OPENLEG_MCP_PUBLIC_CALLS", "60")
        self.window = positive_setting("OPENLEG_MCP_PUBLIC_WINDOW_SECONDS", "60")
        self.capacity = positive_setting("OPENLEG_MCP_PUBLIC_MAX_CLIENTS", "10000")
        trusted = os.environ.get("OPENLEG_MCP_TRUSTED_PROXY", "")
        self.trusted_proxy = str(ipaddress.ip_address(trusted)) if trusted else None
        self.clients = OrderedDict()

    def retry_after(self, request):
        peer = request.scope.get("client")
        key = peer[0] if peer else "unknown"
        try:
            key = str(ipaddress.ip_address(key))
        except ValueError:
            key = "unknown"
        if key == self.trusted_proxy:
            # One overwritten header, never an arbitrary forwarded chain.
            forwarded = request.headers.getlist("x-real-ip")
            if len(forwarded) == 1:
                try:
                    key = str(ipaddress.ip_address(forwarded[0]))
                except ValueError:
                    pass
        now = time.monotonic()
        # Entries stay ordered by expiry, not by last access. Never evict an
        # active budget: rotating addresses must not reset another peer's count.
        while self.clients and next(iter(self.clients.values()))[0] <= now:
            self.clients.popitem(last=False)
        if key not in self.clients:
            if len(self.clients) >= self.capacity:
                return math.ceil(next(iter(self.clients.values()))[0] - now)
            self.clients[key] = [now + self.window, 0]
        budget = self.clients[key]
        if budget[1] >= self.calls:
            return math.ceil(budget[0] - now)
        budget[1] += 1
        return 0
