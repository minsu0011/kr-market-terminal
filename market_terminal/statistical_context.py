from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StatisticalContextProvider(ABC):
    """Stable boundary for a future validated statistics/backtest service."""

    available: bool

    @abstractmethod
    async def get_feature_distribution(self, **query: Any) -> dict[str, Any]: ...

    @abstractmethod
    async def get_forward_return_stats(self, **query: Any) -> dict[str, Any]: ...

    @abstractmethod
    async def get_regime_thresholds(self, **query: Any) -> dict[str, Any]: ...

    @abstractmethod
    async def get_similar_case_stats(self, **query: Any) -> dict[str, Any]: ...

    @abstractmethod
    async def get_cost_adjusted_expectancy(self, **query: Any) -> dict[str, Any]: ...


class NullStatisticalContextProvider(StatisticalContextProvider):
    available = False
    _result = {"available": False, "reason": "통계·백테스트 엔진 미연결"}

    async def get_feature_distribution(self, **query: Any) -> dict[str, Any]:
        return dict(self._result)

    async def get_forward_return_stats(self, **query: Any) -> dict[str, Any]:
        return dict(self._result)

    async def get_regime_thresholds(self, **query: Any) -> dict[str, Any]:
        return dict(self._result)

    async def get_similar_case_stats(self, **query: Any) -> dict[str, Any]:
        return dict(self._result)

    async def get_cost_adjusted_expectancy(self, **query: Any) -> dict[str, Any]:
        return dict(self._result)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._result)
