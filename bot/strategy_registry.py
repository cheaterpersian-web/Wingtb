from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .demo_strategy import SimpleMAReversion


@dataclass
class StrategyInfo:
	key: str
	name: str
	description: str
	runner_factory: Callable[[], object]


def _ma_reversion_factory() -> SimpleMAReversion:
	return SimpleMAReversion(window=10, threshold=0.003, position_size_usdt=100.0)


STRATEGIES: Dict[str, StrategyInfo] = {
	"ma_reversion": StrategyInfo(
		key="ma_reversion",
		name="MA Reversion (Demo)",
		description="بازگشت به میانگین با SMA ساده. صرفا جهت دمو.",
		runner_factory=_ma_reversion_factory,
	),
}


def list_strategies() -> List[StrategyInfo]:
	return list(STRATEGIES.values())


def get_strategy(key: str) -> Optional[StrategyInfo]:
	return STRATEGIES.get(key)

