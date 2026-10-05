"""Broker adapter interface (docs/design.md §2.3). Only adapters know broker
specifics; the Order Manager sees this interface and nothing else."""

from __future__ import annotations

from typing import Protocol

from algotrade.core.types import BrokerAck, BrokerPosition, Fill, Funds, Order


class BrokerError(RuntimeError):
    """The outcome of a broker call is unknown. Never retry blindly: query."""


class Broker(Protocol):
    name: str

    async def place(self, order: Order) -> BrokerAck: ...

    async def cancel(self, broker_order_id: str) -> bool: ...

    async def modify_trigger(self, broker_order_id: str, trigger_price: object) -> bool: ...

    async def positions(self) -> list[BrokerPosition]: ...

    async def open_order_ids(self) -> dict[str, str]:
        """client order id -> broker order id for working orders."""
        ...

    async def find_order(self, client_order_id: str) -> str | None:
        """Broker order id for a client order id, if the broker has it."""
        ...

    async def funds(self) -> Funds: ...

    async def poll_fills(self) -> list[Fill]:
        """Fills since the last poll."""
        ...
