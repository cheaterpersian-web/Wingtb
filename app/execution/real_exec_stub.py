from __future__ import annotations

"""
Placeholder for real execution. Keep the same interface as PaperExecutionGateway
but implement authenticated requests to CoinEx private endpoints in v2.
"""

class IExecutionGateway:
    async def place_order(self, pair: str, side: str, qty: float, price: float):
        raise NotImplementedError

    async def cancel_order(self, order_id: str):
        raise NotImplementedError

    async def balances(self):
        raise NotImplementedError

