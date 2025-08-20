import time
from typing import Any, Dict, List, Optional

import requests


class CoinexClient:
	"""Minimal client for CoinEx v2 public endpoints we need in demo phase."""

	BASE_URL: str = "https://api.coinex.com"
	API_PREFIX: str = "/v2"

	def __init__(self, session: Optional[requests.Session] = None, timeout_seconds: int = 15) -> None:
		self.session = session or requests.Session()
		self.timeout_seconds = timeout_seconds

	def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
		url = f"{self.BASE_URL}{self.API_PREFIX}{path}"
		resp = self.session.get(url, params=params, timeout=self.timeout_seconds)
		resp.raise_for_status()
		return resp.json()

	def list_all_spot_tickers(self) -> List[Dict[str, Any]]:
		"""GET /v2/spot/ticker returns a list of tickers for all spot markets."""
		data = self._get("/spot/ticker")
		if isinstance(data, dict) and "data" in data:
			return data["data"]  # type: ignore[return-value]
		return []

	def get_spot_ticker(self, market: str) -> Optional[Dict[str, Any]]:
		"""Filter a single market from the all-spot tickers call (no single-ticker endpoint observed)."""
		markets = self.list_all_spot_tickers()
		for item in markets:
			if item.get("market") == market:
				return item
		return None

	def get_kline(self, market: str, period: str, limit: int = 100) -> List[Dict[str, Any]]:
		"""GET /v2/spot/kline with period like '1min','5min','15min','30min','1hour','4hour','1day'.

		Validated via curl test: period='1hour' works and returns fields: open, high, low, close, volume, value, created_at (ms).
		"""
		params = {"market": market, "period": period, "limit": limit}
		data = self._get("/spot/kline", params=params)
		if isinstance(data, dict) and "data" in data:
			return data["data"]  # type: ignore[return-value]
		return []

	def search_markets(self, query: str) -> List[str]:
		"""Very simple search over all tickers by substring on market symbol."""
		query_upper = query.upper()
		markets = self.list_all_spot_tickers()
		return [m.get("market") for m in markets if isinstance(m, dict) and query_upper in str(m.get("market", "")).upper()]

	@staticmethod
	def format_ticker_row(t: Dict[str, Any]) -> str:
		"""Human-readable compact ticker line."""
		market = t.get("market", "-")
		last = t.get("last", "-")
		open_price = t.get("open", "-")
		high = t.get("high", "-")
		low = t.get("low", "-")
		vol = t.get("volume", "-")
		return f"{market}: last={last} | open={open_price} | high={high} | low={low} | vol={vol}"

