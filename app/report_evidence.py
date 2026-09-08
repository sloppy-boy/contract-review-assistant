"""Backward-compatible evidence presentation without fabricating missing sources."""
from copy import deepcopy

DECISIONS = {"pending", "accepted", "accepted_with_changes", "rejected", "escalated"}


def normalize_report(report: dict, *, dispositions: dict | None = None) -> dict:
    result = deepcopy(report)
    clauses = result.setdefault("clauses", [])
    by_clause = {}
    for index, clause in enumerate(clauses, 1):
        clause.setdefault("paragraphId", f"P{index:04d}")
        clause.setdefault("location", "")
        by_clause.setdefault(clause.get("clauseId"), clause)
    for risk in result.setdefault("risks", []):
        clause = by_clause.get(risk.get("clauseId"), {})
        risk["paragraphId"] = risk.get("paragraphId") or clause.get("paragraphId", "")
        risk["location"] = risk.get("location") or clause.get("location", "")
        risk.setdefault("legalBasis", {"tier": "none", "articleId": "", "version": "", "quote": ""})
        risk.setdefault("source", "rule" if str(risk.get("worker", "")).startswith("rule") else "model")
        risk.setdefault("sourceDetails", {})
        if not risk.get("sources"):
            risk["sources"] = [{"source": risk["source"], **risk["sourceDetails"]}]
        confidence = risk.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            confidence = None
        risk["confidence"] = confidence
        basis = risk.get("legalBasis") or {}
        risk["legalBasis"] = {"tier": "none", "articleId": "", "version": "", "quote": "", **basis}
        risk["requiresHumanReview"] = bool(risk.get("requiresHumanReview") or risk.get("severity") == "high"
            or basis.get("tier", "none") != "direct" or not risk.get("evidence") or confidence is None
            or risk.get("status") == "disputed")
        risk.setdefault("escalationRecommendation", risk.get("escalationPolicy", ""))
        if dispositions is not None:
            decision = dispositions.get(risk.get("id"))
            risk.pop("reviewDecision", None)
            risk["humanStatus"] = decision["decision"] if decision else "pending"
            risk["reviewStatus"] = ("pending_review" if decision["decision"] == "pending" else decision["decision"]) if decision else ("pending_review" if risk["requiresHumanReview"] else "not_required")
            if decision:
                risk["reviewDecision"] = deepcopy(decision)
        else:
            decision = risk.get("reviewDecision") or {}
            risk["humanStatus"] = decision.get("decision", "pending")
            risk.setdefault("reviewStatus", "pending_review" if risk["requiresHumanReview"] else "not_required")
    return result
