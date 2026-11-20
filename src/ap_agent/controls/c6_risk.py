"""C6 untrusted-content risk (FIN-POL-005 §3-4).

Indicators come from two sources: deterministic patterns over the case's own free text, and
indicators the model found in untrusted text whose citations were verified (P5). Two or more
distinct indicators → ESCALATE_CONTROL_REVIEW. Text telling the reader to disable checks is
itself an indicator (FIN-POL-005 §4), never an instruction.

Applicability is a field check: with no case free text and no model indicators there is
nothing to assess. Text can never switch this control off.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType

from ap_agent.controls.common import ResultBuilder
from ap_agent.controls.context import ControlContext
from ap_agent.controls.result import ControlResult
from ap_agent.kernel.enums import ControlId, ExceptionCategory, IndicatorCode, RecommendationCode

RULE = "FIN-POL-005 §3-4 fraud indicators in untrusted content"

_I = re.IGNORECASE
PATTERNS: Mapping[IndicatorCode, tuple[re.Pattern[str], ...]] = MappingProxyType(
    {
        IndicatorCode.NEW_BANK_DETAILS: (
            re.compile(r"bank\s+(account|details)\s+(has|have)?\s*(changed|been\s+updated)", _I),
            re.compile(r"\bnew\s+(bank\s+)?account\b", _I),
        ),
        IndicatorCode.URGENT_OR_SECRET_LANGUAGE: (
            re.compile(r"\burgent(ly)?\b|\bimmediately\b|\btoday\b|\basap\b", _I),
            re.compile(r"\bconfidential(ly)?\b|\bsecret(ly)?\b", _I),
        ),
        IndicatorCode.BYPASS_APPROVAL_REQUEST: (
            re.compile(r"do\s+not\s+(ask|seek|wait\s+for)\b[^.]{0,40}approv", _I),
            re.compile(r"\b(skip|bypass|without)\b[^.]{0,20}approv", _I),
            re.compile(r"\balready\s+approved\b", _I),
        ),
        IndicatorCode.DISABLE_CHECKS_INSTRUCTION: (
            re.compile(r"\bignore\b[^.]{0,30}\b(polic(y|ies)|instructions|controls)\b", _I),
            re.compile(r"\b(skip|disable)\b[^.]{0,20}\b(check|detection|control)", _I),
            re.compile(r"\bmark\b[^.]{0,30}\bverified\b", _I),
        ),
    }
)


def detect_indicators(texts: list[str]) -> set[IndicatorCode]:
    return {
        code
        for code, patterns in PATTERNS.items()
        if any(p.search(t) for p in patterns for t in texts)
    }


def check(ctx: ControlContext) -> ControlResult:
    b = ResultBuilder(ctx, ControlId.C6, RULE)
    texts = ctx.free_text()
    if not texts and not ctx.model_indicators:
        return b.not_applicable("no case free text and no model-flagged indicators")
    if not b.cite("c6_indicator_threshold"):
        return b.indeterminate(ExceptionCategory.OTHER_CONTROL_RISK)

    found_text = detect_indicators(texts)
    found = found_text | ctx.model_indicators
    threshold = int(ctx.params["c6_indicator_threshold"].value)
    detail = ", ".join(
        f"{code.value} ({'case text' if code in found_text else 'model, verified'})"
        for code in sorted(found)
    )
    if len(found) >= threshold:
        return b.failed(
            RecommendationCode.ESCALATE_CONTROL_REVIEW,
            ExceptionCategory.OTHER_CONTROL_RISK,
            expected=f"fewer than {threshold} fraud indicators",
            observed=f"{len(found)} indicators: {detail}",
            threshold=f"{threshold} or more indicators escalate",
        )
    return b.passed(
        expected=f"fewer than {threshold} fraud indicators",
        observed=f"{len(found)} indicator(s)" + (f": {detail}" if detail else ""),
        threshold=f"{threshold} or more indicators escalate",
    )
