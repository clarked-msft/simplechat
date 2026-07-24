# functions_rmf_analysis_metrics.py
"""Privacy-preserving telemetry for recurring RMF control selections."""

from __future__ import annotations

import hashlib
import hmac
import logging

from config import RMF_ANALYSIS_METRICS_KEY
from functions_appinsights import log_event


MAX_SELECTION_DURATION_MS = 24 * 60 * 60 * 1000
MAX_CONTROL_IDS = 1000
MAX_CONTROL_ID_LENGTH = 128
logger = logging.getLogger(__name__)


def _digest(label: str, value: str) -> str:
    key = str(RMF_ANALYSIS_METRICS_KEY or "").encode("utf-8")
    if not key:
        raise ValueError("RMF_ANALYSIS_METRICS_KEY is not configured")
    message = f"rmf-analysis-metrics:v1:{label}:{value}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def record_rmf_analysis_selection(
    *,
    workspace_id: str,
    control_ids: list[str],
    selection_duration_ms: int | str | None,
    outcome: str,
) -> None:
    """Record aggregate recurrence signals without exposing the selected controls."""
    if len(control_ids) > MAX_CONTROL_IDS:
        return
    canonical_ids = sorted(
        {
            normalized
            for control_id in control_ids
            if (normalized := str(control_id).strip().upper())
            and len(normalized) <= MAX_CONTROL_ID_LENGTH
        }
    )
    if not workspace_id or not canonical_ids:
        return

    try:
        duration_ms = int(selection_duration_ms)
    except (TypeError, ValueError):
        return
    if duration_ms < 0 or duration_ms > MAX_SELECTION_DURATION_MS:
        return

    try:
        workspace_digest = _digest("workspace", workspace_id)
        scope_digest = _digest("scope", f"{workspace_digest}:{','.join(canonical_ids)}")
        log_event(
            "RMF custom analysis selection",
            extra={
                "event_name": "rmf_custom_analysis_selection",
                "workspace_scope_digest": workspace_digest,
                "selection_scope_digest": scope_digest,
                "selected_control_count": len(canonical_ids),
                "selection_duration_ms": duration_ms,
                "submission_outcome": str(outcome or "unknown"),
            },
        )
    except Exception:
        logger.warning("RMF analysis selection metric could not be recorded", exc_info=True)


def record_rmf_analysis_baseline(*, workspace_id: str, baseline_control_count: int) -> None:
    """Record an authoritative baseline size without exposing the workspace ID."""
    if not workspace_id or baseline_control_count < 0:
        return
    try:
        log_event(
            "RMF analysis baseline size",
            extra={
                "event_name": "rmf_analysis_baseline_size",
                "workspace_scope_digest": _digest("workspace", workspace_id),
                "baseline_control_count": baseline_control_count,
            },
        )
    except Exception:
        logger.warning("RMF analysis baseline metric could not be recorded", exc_info=True)
