from typing import Protocol

from app.domain import Market, Model, Positive, Price, Side


class LiveExecutionDisabled(RuntimeError):
    pass


class OrderIntent(Model):
    idempotency_key: str
    market_id: str
    side: Side
    quantity: Positive
    limit_price: Price


class VenueExecution(Protocol):
    async def preflight_order(self, intent: OrderIntent, market: Market) -> None: ...
    async def place_limit_order(self, intent: OrderIntent) -> str: ...
    async def cancel_order(self, order_id: str) -> None: ...
    async def cancel_all_orders(self) -> None: ...
    async def get_order_status(self, order_id: str) -> str: ...


class DisabledExecution:
    async def preflight_order(self, intent: OrderIntent, market: Market) -> None:
        raise LiveExecutionDisabled("LIVE_EXECUTION_NOT_IMPLEMENTED")

    async def place_limit_order(self, intent: OrderIntent) -> str:
        raise LiveExecutionDisabled("LIVE_EXECUTION_NOT_IMPLEMENTED")

    async def cancel_order(self, order_id: str) -> None:
        raise LiveExecutionDisabled("LIVE_EXECUTION_NOT_IMPLEMENTED")

    async def cancel_all_orders(self) -> None:
        raise LiveExecutionDisabled("LIVE_EXECUTION_NOT_IMPLEMENTED")

    async def get_order_status(self, order_id: str) -> str:
        raise LiveExecutionDisabled("LIVE_EXECUTION_NOT_IMPLEMENTED")
