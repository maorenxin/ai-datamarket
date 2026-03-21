"""
Gate registry — factory for exchange API gate singletons.
"""
from typing import Dict

from crawler.base_gate import BaseGate


_gates = {}  # type: Dict[str, BaseGate]


def get_gate(exchange: str) -> BaseGate:
    """Return the singleton gate for the given exchange."""
    if exchange not in _gates:
        if exchange == "binance":
            from crawler.binance_gate import gate as bg
            _gates["binance"] = bg
        elif exchange == "okx":
            from crawler.okx_gate import gate as og
            _gates["okx"] = og
        elif exchange == "bybit":
            from crawler.bybit_gate import gate as bg2
            _gates["bybit"] = bg2
        else:
            raise ValueError("Unknown exchange: {}".format(exchange))
    return _gates[exchange]


async def close_all() -> None:
    """Close all open gate clients."""
    for g in _gates.values():
        await g.close()
    _gates.clear()
