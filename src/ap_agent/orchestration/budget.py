"""Run budget (§4 bounded loop; architecture §2.2). Charged on every node transition and every
tool/model attempt, retries included. Exhaustion ends the run FAILED with no decision.

Limits come from config/app.yaml via Settings.budget; counters are persisted on the run row
and returned by Get run (P6 wires that).
"""

from dataclasses import dataclass, field
from typing import Literal

from ap_agent.infra.settings import BudgetSettings

Counter = Literal["steps", "tool_calls", "model_calls"]


class BudgetExhausted(Exception):
    def __init__(self, counter: Counter, limit: int) -> None:
        super().__init__(f"budget exhausted: {counter} limit {limit}")
        self.counter: Counter = counter
        self.limit = limit


@dataclass
class Budget:
    limits: BudgetSettings
    used: dict[Counter, int] = field(
        default_factory=lambda: {"steps": 0, "tool_calls": 0, "model_calls": 0}
    )

    def _limit(self, counter: Counter) -> int:
        return {
            "steps": self.limits.max_steps,
            "tool_calls": self.limits.max_tool_calls,
            "model_calls": self.limits.max_model_calls,
        }[counter]

    def charge(self, counter: Counter) -> None:
        """Charge one unit, or raise BudgetExhausted without charging."""
        limit = self._limit(counter)
        if self.used[counter] >= limit:
            raise BudgetExhausted(counter, limit)
        self.used[counter] += 1

    @property
    def max_retries_per_call(self) -> int:
        return self.limits.max_retries_per_call

    def snapshot(self) -> dict[str, int]:
        return {
            **{f"{k}_used": v for k, v in self.used.items()},
            **{f"{k}_limit": self._limit(k) for k in self.used},
        }
