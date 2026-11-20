"""ToolRunner: the one path every tool call takes (architecture §7).

Per call:
1. allow-list check for the calling node (deny-by-default);
2. input validated against the tool's input schema;
3. up to 1 + min(tool max_retries, budget max_retries_per_call) attempts. Each attempt:
   charges the budget, applies any injected fault, runs the tool on a worker thread under a
   real deadline, validates the output schema, writes a tool_calls row and a tool.attempt
   audit event with duration and outcome;
4. only timeouts and transient errors are retried, with bounded exponential backoff;
   permanent errors and invalid output fail immediately.

Failures come back as a ToolOutcome value (observable result, brief §3), so a control can
mark itself INDETERMINATE. Only BudgetExhausted and ToolNotAllowedError raise.
"""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from ap_agent.evidence.tools.base import (
    Tool,
    ToolError,
    ToolTimeoutError,
    TransientToolError,
)
from ap_agent.evidence.tools.faults import FaultInjector
from ap_agent.infra.observability.events import (
    BudgetExhausted as BudgetExhaustedEvent,
)
from ap_agent.infra.observability.events import ToolAttempt, ToolRetry, ToolTimeout
from ap_agent.infra.observability.logger import RunEventLogger, Stopwatch, utc_now_iso
from ap_agent.infra.persistence.protocols import Store
from ap_agent.infra.persistence.records import ToolCallRecord
from ap_agent.infra.settings import ToolSettings, ToolsSettings
from ap_agent.kernel.enums import FaultKind
from ap_agent.orchestration.budget import Budget, BudgetExhausted
from ap_agent.orchestration.tool_registry import check_allowed

ToolErrorKind = Literal["timeout", "transient", "permanent", "invalid_input", "invalid_output"]
RETRYABLE: frozenset[ToolErrorKind] = frozenset({"timeout", "transient"})
MAX_BACKOFF_S = 2.0


@dataclass(frozen=True)
class ToolOutcome[OutT: BaseModel]:
    tool: str
    output: OutT | None
    attempts: int
    tool_call_ids: tuple[str, ...]
    error: ToolErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.output is not None


def call_with_deadline[T](fn: Callable[[], T], timeout_s: float) -> T:
    """Run fn on a daemon thread; raise ToolTimeoutError if it does not finish in time.

    A timed-out thread cannot be killed and finishes in the background; its result is
    discarded. Acceptable here because only read-only tools run without approval.
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise ToolTimeoutError(f"exceeded {timeout_s}s deadline")
    if "error" in box:
        raise box["error"]
    value: T = box["value"]
    return value


def _apply_fault[T](fault: FaultKind, timeout_s: float, call: Callable[[], T]) -> T:
    """Simulated backend behaviour. A timeout fault really blocks past the deadline, so the
    runner's genuine timeout path is what gets exercised."""
    if fault is FaultKind.TIMEOUT:
        time.sleep(timeout_s * 2 + 0.05)
        raise ToolTimeoutError("injected timeout")  # never observed: deadline fires first
    if fault is FaultKind.TRANSIENT:
        raise TransientToolError("injected transient failure")
    return call()


def _error_text(exc: BaseException) -> str:
    """Our own ToolErrors carry id-only messages; anything else is recorded by class only."""
    if isinstance(exc, ToolError):
        return f"{type(exc).__name__}: {str(exc)[:200]}"
    return type(exc).__name__


class ToolRunner:
    def __init__(
        self,
        *,
        run_id: str,
        settings: ToolsSettings,
        budget: Budget,
        store: Store,
        events: RunEventLogger,
        faults: FaultInjector | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self._run_id = run_id
        self._settings = settings
        self._budget = budget
        self._store = store
        self._events = events
        self._faults = faults or FaultInjector()
        self._sleep = sleep
        self._clock = clock
        self._seq = 0

    def _tool_settings(self, name: str) -> ToolSettings:
        cfg = getattr(self._settings, name, None)
        if not isinstance(cfg, ToolSettings):
            raise KeyError(f"no settings for tool {name!r}")
        return cfg

    def _next_id(self) -> str:
        self._seq += 1
        return f"{self._run_id}-tc-{self._seq:03d}"

    def _record(
        self,
        *,
        tool_call_id: str,
        node: str,
        tool: str,
        attempt: int,
        args: dict[str, Any],
        response: dict[str, Any] | None,
        outcome: str,
        error: str | None,
        duration_ms: int,
    ) -> None:
        self._store.tool_calls.save(
            ToolCallRecord(
                tool_call_id=tool_call_id,
                run_id=self._run_id,
                node=node,
                tool=tool,
                attempt=attempt,
                args_redacted=args,
                response_redacted=response,
                outcome=outcome,
                error=error,
                duration_ms=duration_ms,
                ts=self._clock(),
            )
        )

    def run[InT: BaseModel, OutT: BaseModel](
        self, node: str, tool: Tool[InT, OutT], args: InT | Mapping[str, Any]
    ) -> ToolOutcome[OutT]:
        check_allowed(node, tool.name)
        cfg = self._tool_settings(tool.name)
        max_attempts = 1 + min(cfg.max_retries, self._budget.max_retries_per_call)

        try:
            parsed = (
                args
                if isinstance(args, tool.input_model)
                else tool.input_model.model_validate(args)
            )
        except ValidationError as exc:
            tc_id = self._next_id()
            self._record(
                tool_call_id=tc_id,
                node=node,
                tool=tool.name,
                attempt=1,
                args={},  # rejected input is not persisted
                response=None,
                outcome="invalid_input",
                error=f"ValidationError: {exc.error_count()} error(s)",
                duration_ms=0,
            )
            self._events.emit(
                ToolAttempt(tool=tool.name, tool_call_id=tc_id, attempt=1),
                node=node,
                outcome="invalid_input",
                duration_ms=0,
            )
            return ToolOutcome(tool.name, None, 0, (tc_id,), "invalid_input")

        args_json = parsed.model_dump(mode="json")
        ids: list[str] = []
        for attempt in range(1, max_attempts + 1):
            try:
                self._budget.charge("tool_calls")
            except BudgetExhausted as exc:
                self._events.emit(
                    BudgetExhaustedEvent(counter="tool_calls", limit=exc.limit),
                    node=node,
                    outcome="failed",
                )
                raise

            tc_id = self._next_id()
            ids.append(tc_id)
            fault = self._faults.next(tool.name)
            watch = Stopwatch()
            output: OutT | None = None
            error: ToolErrorKind | None = None
            error_text: str | None = None
            try:
                # partial binds this attempt's fault now; a closure would read the loop
                # variable later, from a thread that may outlive the attempt.
                raw = call_with_deadline(
                    partial(_apply_fault, fault, cfg.timeout_s, partial(tool, parsed)),
                    cfg.timeout_s,
                )
                # Always re-validate: an instance of the right class can still hold invalid
                # data (e.g. built with model_construct). Never trust it on type alone.
                output = tool.output_model.model_validate(
                    raw.model_dump(warnings=False) if isinstance(raw, BaseModel) else raw
                )
            except ToolTimeoutError as exc:
                error, error_text = "timeout", _error_text(exc)
            except TransientToolError as exc:
                error, error_text = "transient", _error_text(exc)
            except ValidationError as exc:
                error, error_text = "invalid_output", f"ValidationError: {exc.error_count()}"
            except Exception as exc:  # PermanentToolError or an unexpected bug in the tool
                error, error_text = "permanent", _error_text(exc)
            duration = watch.elapsed_ms()

            self._record(
                tool_call_id=tc_id,
                node=node,
                tool=tool.name,
                attempt=attempt,
                args=args_json,
                response=None if output is None else output.model_dump(mode="json"),
                outcome=error or "ok",
                error=error_text,
                duration_ms=duration,
            )
            self._events.emit(
                ToolAttempt(tool=tool.name, tool_call_id=tc_id, attempt=attempt),
                node=node,
                outcome=error or "ok",
                duration_ms=duration,
            )

            if error is None:
                return ToolOutcome(tool.name, output, attempt, tuple(ids))
            if error == "timeout":
                self._events.emit(
                    ToolTimeout(tool=tool.name, attempt=attempt, timeout_s=cfg.timeout_s),
                    node=node,
                    outcome="timeout",
                )
            if error not in RETRYABLE or attempt == max_attempts:
                return ToolOutcome(tool.name, None, attempt, tuple(ids), error)

            self._events.emit(
                ToolRetry(
                    tool=tool.name,
                    attempt=attempt,
                    fault=FaultKind.TIMEOUT if error == "timeout" else FaultKind.TRANSIENT,
                ),
                node=node,
                outcome="retry",
            )
            self._sleep(min(self._settings.backoff_s * 2 ** (attempt - 1), MAX_BACKOFF_S))

        raise AssertionError("unreachable")  # pragma: no cover
