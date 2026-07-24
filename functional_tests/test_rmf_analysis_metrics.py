# test_rmf_analysis_metrics.py
"""
Functional tests for privacy-preserving RMF analysis selection metrics.
Version: 0.250.069
Implemented in: 0.250.070
"""

import importlib.util
from pathlib import Path
import sys
import types


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_metrics(monkeypatch):
    events = []
    config = types.ModuleType("config")
    config.RMF_ANALYSIS_METRICS_KEY = "test-secret"
    appinsights = types.ModuleType("functions_appinsights")
    appinsights.log_event = lambda message, extra=None: events.append((message, extra))
    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setitem(sys.modules, "functions_appinsights", appinsights)

    module_path = (
        REPO_ROOT
        / "application"
        / "single_app"
        / "functions_rmf_analysis_metrics.py"
    )
    spec = importlib.util.spec_from_file_location(
        "rmf_analysis_metrics_under_test",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, events


def test_selection_metric_is_canonical_and_excludes_sensitive_values(monkeypatch):
    metrics, events = load_metrics(monkeypatch)

    metrics.record_rmf_analysis_selection(
        workspace_id="workspace-sensitive",
        control_ids=[" ac-2 ", "AC-1", "ac-1"],
        selection_duration_ms=1250,
        outcome="submitted",
    )
    metrics.record_rmf_analysis_selection(
        workspace_id="workspace-sensitive",
        control_ids=["AC-1", "AC-2"],
        selection_duration_ms=2200,
        outcome="submitted",
    )

    assert len(events) == 2
    first = events[0][1]
    second = events[1][1]
    assert first["selection_scope_digest"] == second["selection_scope_digest"]
    assert first["workspace_scope_digest"] == second["workspace_scope_digest"]
    assert first["selected_control_count"] == 2
    assert first["selection_duration_ms"] == 1250
    assert first["submission_outcome"] == "submitted"
    serialized = repr(events)
    assert "workspace-sensitive" not in serialized
    assert "AC-1" not in serialized
    assert "AC-2" not in serialized


def test_invalid_or_empty_selection_metric_is_not_emitted(monkeypatch):
    metrics, events = load_metrics(monkeypatch)

    metrics.record_rmf_analysis_selection(
        workspace_id="workspace-1",
        control_ids=[],
        selection_duration_ms=10,
        outcome="submitted",
    )
    metrics.record_rmf_analysis_selection(
        workspace_id="workspace-1",
        control_ids=["AC-1"],
        selection_duration_ms=metrics.MAX_SELECTION_DURATION_MS + 1,
        outcome="submitted",
    )

    assert events == []


def test_metrics_failure_never_escapes_to_analysis_route(monkeypatch):
    metrics, events = load_metrics(monkeypatch)
    monkeypatch.setattr(
        metrics,
        "log_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )

    metrics.record_rmf_analysis_selection(
        workspace_id="workspace-1",
        control_ids=["AC-1"],
        selection_duration_ms=10,
        outcome="submitted",
    )

    assert events == []


def test_missing_metrics_key_drops_event_without_predictable_digest(monkeypatch):
    metrics, events = load_metrics(monkeypatch)
    monkeypatch.setattr(metrics, "RMF_ANALYSIS_METRICS_KEY", "")

    metrics.record_rmf_analysis_selection(
        workspace_id="workspace-1",
        control_ids=["AC-1"],
        selection_duration_ms=10,
        outcome="submitted",
    )

    assert events == []


def test_baseline_metric_uses_workspace_digest(monkeypatch):
    metrics, events = load_metrics(monkeypatch)

    metrics.record_rmf_analysis_baseline(
        workspace_id="workspace-sensitive",
        baseline_control_count=42,
    )

    assert len(events) == 1
    event = events[0][1]
    assert event["event_name"] == "rmf_analysis_baseline_size"
    assert event["baseline_control_count"] == 42
    assert "workspace-sensitive" not in repr(event)
