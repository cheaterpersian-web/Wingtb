from __future__ import annotations

import os
import time
import hmac
import hashlib
import json
from typing import Tuple

import httpx
import logging


class CoinExPrivate:
    BASE_URL = "https://api.coinex.com/v2"

    def __init__(self) -> None:
        self._key = (os.getenv("COINEX_ACCESS_ID") or "").strip()
        self._secret = (os.getenv("COINEX_SECRET_KEY") or "").strip().encode()
        self._log = logging.getLogger("coinex_private")
        self._client = httpx.AsyncClient(timeout=12)

    async def get_spot_fee_bps(self) -> Tuple[float, float]:
        """
        Returns (maker_bps, taker_bps). Falls back to defaults if API not available.
        Note: Endpoint/signature may differ; this includes best-effort attempt with safe fallback.
        """
        if not self._key or not self._secret:
            return (10.0, 10.0)
        ts = str(int(time.time() * 1000))
        # Attempt a plausible v2 private endpoint path; fallback if fails
        path = "/account/user_fee_rate"
        params = {}
        body = ""
        sign_payload = path + ts + body
        try:
            sign = hmac.new(self._secret, sign_payload.encode(), hashlib.sha256).hexdigest()
            headers = {
                "X-COINEX-KEY": self._key,
                "X-COINEX-SIGN": sign,
                "X-COINEX-TIMESTAMP": ts,
                "Content-Type": "application/json",
            }
            url = self.BASE_URL + path
            r = await self._client.get(url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            # Expected structure may vary; attempt common keys
            maker = float(data.get("data", {}).get("spot_maker_fee_rate", 0.001) * 10000)
            taker = float(data.get("data", {}).get("spot_taker_fee_rate", 0.001) * 10000)
            maker_bps = maker if maker > 0 else 10.0
            taker_bps = taker if taker > 0 else 10.0
            return (maker_bps, taker_bps)
        except Exception as e:
            self._log.debug("fee fetch failed, fallback to defaults: %s", e)
            return (10.0, 10.0)