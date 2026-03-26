"""
Gate registry — factory for exchange API gate singletons.
"""
from typing import Dict

from crawler.base_gate import BaseGate


_gates = {}  # type: Dict[str, BaseGate]


def get_gate(platform, domain="crypto"):
    # type: (str, str) -> BaseGate
    """Return the singleton gate for the given platform.
    The domain parameter is for future extensibility (e.g. us_stock gates).
    """
    key = "{}:{}".format(domain, platform)
    if key not in _gates:
        if domain == "crypto":
            if platform == "binance":
                from crawler.binance_gate import gate as bg
                _gates[key] = bg
            elif platform == "okx":
                from crawler.okx_gate import gate as og
                _gates[key] = og
            elif platform == "bybit":
                from crawler.bybit_gate import gate as bg2
                _gates[key] = bg2
            else:
                raise ValueError("Unknown crypto platform: {}".format(platform))
        else:
            raise NotImplementedError("Domain '{}' not yet supported".format(domain))
    return _gates[key]


async def close_all() -> None:
    """Close all open gate clients."""
    for g in _gates.values():
        await g.close()
    _gates.clear()
