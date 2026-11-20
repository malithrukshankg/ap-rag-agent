"""Citation verification at TB3 (architecture §1, §5.1).

A citation stands only if all hold:
1. its source id is in this run's evidence pack (retrieved chunks or the case's own text);
2. the quote, after undoing prompt markup escaping, is a verbatim substring of the source;
3. the source text still hashes to the sha256 recorded at retrieval;
4. an untrusted source is cited only as risk evidence, never as authority.

A claim with any failing citation, with no citation, or that disputes its control's result
(`agrees_with_control: false`) moves to unknowns. A risk indicator whose citation fails is
dropped. Every rejection is reported so the caller can emit `citation.rejected`.
"""

import hashlib
import html
from collections.abc import Sequence
from typing import Literal

from ap_agent.assessment.redaction import EvidencePack
from ap_agent.assessment.schema import Claim, ModelAssessment
from ap_agent.controls.result import ControlResult
from ap_agent.kernel.enums import CitationUse, IndicatorCode, Trust
from ap_agent.kernel.types import Strict

RejectReason = Literal[
    "not_in_retrieval_set", "quote_not_verbatim", "hash_mismatch", "untrusted_as_authority"
]


class Rejection(Strict):
    chunk_id: str
    reason: RejectReason


class VerifiedAssessment(Strict):
    findings: list[Claim]
    inferences: list[str]
    unknowns: list[str]
    risk_indicators: list[IndicatorCode]
    rejections: list[Rejection]
    claims_dropped: int


def check_citation(
    pack: EvidencePack, chunk_id: str, quote: str, use: CitationUse
) -> RejectReason | None:
    item = pack.item(chunk_id)
    if item is None:
        return "not_in_retrieval_set"
    if html.unescape(quote) not in item.text:
        return "quote_not_verbatim"
    if hashlib.sha256(item.text.encode("utf-8")).hexdigest() != item.expected_sha256:
        return "hash_mismatch"
    if use is CitationUse.AUTHORITY and item.trust is not Trust.TRUSTED:
        return "untrusted_as_authority"
    return None


def verify_assessment(
    assessment: ModelAssessment, pack: EvidencePack, results: Sequence[ControlResult]
) -> VerifiedAssessment:
    controls = {r.control_id for r in results}
    kept: list[Claim] = []
    unknowns = list(assessment.unknowns)
    rejections: list[Rejection] = []
    dropped = 0

    for claim in assessment.findings:
        reasons = [
            (c.chunk_id, check_citation(pack, c.chunk_id, c.quote, c.use)) for c in claim.citations
        ]
        failed = [Rejection(chunk_id=cid, reason=r) for cid, r in reasons if r is not None]
        rejections.extend(failed)
        if failed or not claim.citations:
            dropped += 1
            unknowns.append(f"Unverified claim about {claim.finding_ref.value}: {claim.text}")
        elif not claim.agrees_with_control or claim.finding_ref not in controls:
            dropped += 1
            unknowns.append(
                f"Model disputes the {claim.finding_ref.value} result (not applied): {claim.text}"
            )
        else:
            kept.append(claim)

    indicators: list[IndicatorCode] = []
    for ri in assessment.risk_indicators:
        reason = check_citation(pack, ri.chunk_id, ri.quote, CitationUse.RISK_EVIDENCE)
        if reason is None:
            if ri.indicator not in indicators:
                indicators.append(ri.indicator)
        else:
            rejections.append(Rejection(chunk_id=ri.chunk_id, reason=reason))

    return VerifiedAssessment(
        findings=kept,
        inferences=list(assessment.inferences),
        unknowns=unknowns,
        risk_indicators=indicators,
        rejections=rejections,
        claims_dropped=dropped,
    )
