"""
Functional wiring test for the optional RMF workspace shell.
Version: 0.250.076
Implemented in: 0.250.061 through 0.250.076
"""

import importlib.util
import hashlib
import io
import json
from pathlib import Path
import sys
import types
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path):
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def load_functions_rmf(monkeypatch):
    stubs = {
        "config": {
            "cosmos_groups_container": object(),
            "RMF_API_BASE_URL": "https://rmf.example",
            "RMF_API_CHAT_TIMEOUT_SECONDS": 180,
            "RMF_API_KEY_SECRET_NAME": "rmf-key",
            "RMF_API_TIMEOUT_SECONDS": 15,
            "RMF_API_UPLOAD_TIMEOUT_SECONDS": 300,
        },
        "functions_activity_logging": {"log_general_admin_action": lambda **kwargs: None},
        "functions_appinsights": {"log_event": lambda *args, **kwargs: None},
        "functions_chat_bootstrap_cache": {
            "bump_chat_bootstrap_global_cache_version": lambda **kwargs: None
        },
        "functions_documents": {
            "ensure_document_revision_blob": lambda *args, **kwargs: ("documents", "doc"),
            "get_document_record": lambda *args, **kwargs: None,
            "get_document_versions": lambda *args, **kwargs: [],
        },
        "functions_group": {"find_group_by_id": lambda group_id: None},
        "functions_settings": {"get_settings": lambda: {}},
        "functions_simplechat_operations": {
            "download_blob_to_file": lambda container, path, destination: None
        },
    }
    for name, members in stubs.items():
        module = types.ModuleType(name)
        for member_name, value in members.items():
            setattr(module, member_name, value)
        monkeypatch.setitem(sys.modules, name, module)

    module_path = REPO_ROOT / "application" / "single_app" / "functions_rmf.py"
    spec = importlib.util.spec_from_file_location("functions_rmf_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rmf_feature_is_disabled_by_default_and_versioned():
    config = read_text("application/single_app/config.py")
    settings = read_text("application/single_app/functions_settings.py")

    assert 'VERSION = "0.250.076"' in config
    assert "RMF_ANALYSIS_METRICS_KEY = os.getenv('RMF_ANALYSIS_METRICS_KEY', '')" in config
    assert "'enable_rmf': ENABLE_RMF_DEFAULT" in settings
    assert "ENABLE_RMF_DEFAULT = os.getenv('ENABLE_RMF', 'false')" in config


def test_rmf_routes_are_authenticated_and_feature_gated():
    frontend = read_text("application/single_app/route_frontend_rmf.py")
    backend = read_text("application/single_app/route_backend_rmf.py")
    app = read_text("application/single_app/app.py")

    for content in (frontend, backend):
        assert "@swagger_route(security=get_auth_security())" in content
        assert "@login_required" in content
        assert "@user_required" in content
        assert '@enabled_required("enable_group_workspaces")' in content
        assert '@enabled_required("enable_rmf")' in content

    assert "register_route_frontend_rmf" in app
    assert "register_route_backend_rmf" in app
    assert "RMF_MANAGER_ROLES" in backend
    assert "assert_group_role" in backend
    assert 'isinstance(enabled, bool)' in backend
    assert '"/api/rmf/workspace/initialize"' in backend
    assert '"/api/rmf/workspace/overview"' in backend
    assert '"/api/rmf/workspace/evidence/capabilities"' in backend
    assert '"/api/rmf/workspace/evidence"' in backend
    assert '"/api/rmf/workspace/evidence/jobs/<job_id>"' in backend
    assert '"/api/rmf/workspace/evidence/<import_id>/withdraw"' in backend
    assert '"/api/rmf/workspace/analysis/capabilities"' in backend
    assert '"/api/rmf/workspace/analysis"' in backend
    assert '"/api/rmf/workspace/analysis/jobs/<job_id>"' in backend
    assert '"/api/rmf/workspace/analysis/jobs/<job_id>/cancel"' in backend
    assert '"/api/rmf/workspace/controls"' in backend
    assert '"/api/rmf/workspace/controls/<control_id>"' in backend
    assert "initialize_rmf_service" in backend
    assert "get_rmf_overview" in backend
    assert 'get_group_rmf_state(group_doc)["enabled"]' in backend


def test_rmf_workspace_state_and_navigation_are_wired():
    group_functions = read_text("application/single_app/functions_group.py")
    rmf_functions = read_text("application/single_app/functions_rmf.py")
    rmf_template = read_text("application/single_app/templates/rmf_workspace.html")
    admin_template = read_text("application/single_app/templates/admin_settings.html")
    admin_route = read_text("application/single_app/route_frontend_admin_settings.py")
    top_nav = read_text("application/single_app/templates/_top_nav.html")
    sidebar = read_text("application/single_app/templates/_sidebar_nav.html")

    assert '"rmf": {' in group_functions
    assert '"enabled": False' in group_functions
    assert "def get_group_rmf_state" in rmf_functions
    assert "def update_group_rmf_state" in rmf_functions
    assert "def get_rmf_service_state" in rmf_functions
    assert "def get_rmf_overview" in rmf_functions
    assert "X-RMF-Service-Key" in rmf_functions
    assert "cosmos_groups_container.patch_item(" in rmf_functions
    assert 'id="enable_rmf"' in admin_template
    assert "'enable_rmf': form_data.get('enable_rmf') == 'on'" in admin_route
    assert "app_settings.enable_rmf" in top_nav
    assert "app_settings.enable_rmf" in sidebar
    assert "data-rmf-workspace" in rmf_template
    assert "data-rmf-initialize-form" in rmf_template
    assert "data-rmf-overview-content" in rmf_template
    assert "data-rmf-readiness-title" in rmf_template
    assert "data-rmf-coverage-bar" in rmf_template
    assert "data-rmf-component-types" in rmf_template
    assert "data-rmf-evidence-rows" in rmf_template
    assert "can_manage_rmf_analysis=role in RMF_MANAGER_ROLES" in read_text(
        "application/single_app/route_frontend_rmf.py"
    )
    assert "data-rmf-analysis-history" in rmf_template
    assert ".textContent" in rmf_template
    for label in ("Overview", "Evidence", "Analysis", "Controls", "Attestations", "Export"):
        assert label in rmf_template


def test_rmf_evidence_import_uses_exact_authorized_revision_and_server_side_multipart():
    rmf_functions = read_text("application/single_app/functions_rmf.py")
    document_functions = read_text("application/single_app/functions_documents.py")
    backend = read_text("application/single_app/route_backend_rmf.py")

    assert "RMF_EVIDENCE_MANAGER_ROLES" in rmf_functions
    assert "get_document_record(" in rmf_functions
    assert "get_document_versions(" in rmf_functions
    assert 'current_version.get("id") != document_id' in rmf_functions
    assert "ensure_document_revision_blob(" in rmf_functions
    assert "download_blob_to_file(blob_container, blob_path, temp_path)" in rmf_functions
    assert "digest.update(chunk)" in rmf_functions
    assert '"source_document_id": document_id' in rmf_functions
    assert '"revision_family_id": (' in rmf_functions
    assert '"extract_topology": True' in rmf_functions
    assert "MultipartEncoder(" in rmf_functions
    assert '"manifest": json.dumps(manifest)' in rmf_functions
    assert "timeout=RMF_API_UPLOAD_TIMEOUT_SECONDS" in rmf_functions
    assert "allow_redirects=False" in rmf_functions
    assert "retry_auth=False" in rmf_functions
    assert 'classification == "azure-resource-json"' in rmf_functions
    assert '"X-RMF-Service-Key"' in rmf_functions
    assert "RMF_EVIDENCE_MANAGER_ROLES" in backend
    assert 'payload.get("document_id")' in backend
    assert 'payload.get("classification")' in backend
    assert "blob_path" not in backend
    assert "blob_container" not in backend
    assert "_validate_blob_document_id(" in document_functions
    assert "MatchConditions.IfNotModified" in document_functions
    assert 'owning_group_id = document_item.get("group_id") or group_id' in document_functions


def test_rmf_evidence_ui_uses_capabilities_safe_dom_rendering_and_polling():
    rmf_template = read_text("application/single_app/templates/rmf_workspace.html")

    assert 'fetch("/api/rmf/workspace/evidence/capabilities")' in rmf_template
    assert 'fetch("/api/rmf/workspace/evidence")' in rmf_template
    assert "capabilities?.supported_suffixes" in rmf_template
    assert "async function loadAllGroupDocuments()" in rmf_template
    assert "/api/group_documents?page=${page}&page_size=${pageSize}" in rmf_template
    assert "revision_family_id" in rmf_template
    assert "source_document_id" in rmf_template
    assert 'document.createElement("tr")' in rmf_template
    assert ".replaceChildren()" in rmf_template
    assert "innerHTML" not in rmf_template
    assert "Observed Evidence" in rmf_template
    assert "Organization Policy" in rmf_template
    assert "Azure Resource JSON" in rmf_template
    for state in (
        "not imported",
        "queued",
        "processing",
        "current",
        "stale",
        "failed",
        "withdrawing",
        "superseded",
        "withdrawn",
    ):
        assert state in rmf_template
    assert "/api/rmf/workspace/evidence/jobs/" in rmf_template
    assert "async function pollRmfEvidenceJob(jobId, retryAttempt = 0)" in rmf_template
    assert "() => pollRmfEvidenceJob(jobId)" in rmf_template
    assert "retryAttempt < 5" in rmf_template
    assert "result.job?.id" in rmf_template
    assert "return Array.isArray(payload) ? payload : [];" in rmf_template


def test_rmf_analysis_proxy_wrappers_use_approved_service_contract(monkeypatch):
    rmf = load_functions_rmf(monkeypatch)
    requests = []

    def fake_request(method, path, group_id, user_id, role, **kwargs):
        requests.append((method, path, group_id, user_id, role, kwargs))
        return {"id": "job-1"}

    monkeypatch.setattr(rmf, "_rmf_request", fake_request)
    context = ("group-1", "user-1", "Admin")
    payload = {
        "scope": "selected",
        "control_ids": ["AC-1"],
        "fresh": True,
        "deep": False,
    }

    rmf.get_rmf_analysis_capabilities(*context)
    rmf.get_rmf_analysis(*context)
    rmf.start_rmf_analysis(*context, payload)
    rmf.get_rmf_analysis_job(*context, "job-1")
    rmf.cancel_rmf_analysis_job(*context, "job-1")
    rmf.get_rmf_controls(*context, params={"page": 2})
    rmf.get_rmf_control(*context, "AC-1")
    rmf.set_rmf_control_applicability(
        *context,
        "AC-1",
        {"action": "approve", "rationale": "No mobile devices."},
    )

    assert [(method, path) for method, path, *_ in requests] == [
        ("GET", "/api/v1/workspaces/current/analysis/capabilities"),
        ("GET", "/api/v1/workspaces/current/analysis"),
        ("POST", "/api/v1/workspaces/current/analysis"),
        ("GET", "/api/v1/workspaces/current/analysis/jobs/job-1"),
        ("POST", "/api/v1/workspaces/current/analysis/jobs/job-1/cancel"),
        ("GET", "/api/v1/workspaces/current/controls"),
        ("GET", "/api/v1/workspaces/current/controls/AC-1"),
        ("POST", "/api/v1/workspaces/current/controls/AC-1/applicability"),
    ]
    assert requests[2][-1]["payload"] == payload
    assert requests[5][-1]["params"] == {"page": 2}


def test_rmf_analysis_routes_enforce_roles_and_validate_request_shape():
    backend = read_text("application/single_app/route_backend_rmf.py")
    template = read_text("application/single_app/templates/rmf_workspace.html")

    assert backend.count(
        'allowed_roles=("Owner", "Admin", "DocumentManager", "User")'
    ) >= 5
    assert backend.count("allowed_roles=RMF_MANAGER_ROLES") >= 4
    assert 'scope not in {"baseline", "selected"}' in backend
    assert 'payload.get("control_ids", [])' in backend
    assert 'payload.get("fresh", False)' in backend
    assert 'payload.get("deep", False)' in backend
    assert 'payload.get("selection_metrics", {})' in backend
    assert "not isinstance(control_ids, list)" in backend
    assert "not isinstance(fresh, bool) or not isinstance(deep, bool)" in backend
    assert 'scope == "selected" and not control_ids' in backend
    assert 'scope == "baseline" and control_ids' in backend
    assert "record_rmf_analysis_selection(" in backend
    assert "return jsonify(job), 202" in backend
    assert "rmfAnalysisSelectionStartedAt" in template
    assert "const selectionDurationMs = rmfAnalysisSelectionDurationMs ?? Math.max(" in template
    assert "selection_duration_ms: selectionDurationMs" in template
    assert "record_rmf_analysis_baseline(" in backend
    assert "rmfAnalysisSelectionDurationMs" in template


def test_rmf_analysis_ui_is_safe_role_aware_and_polls_with_bounded_retries():
    rmf_template = read_text("application/single_app/templates/rmf_workspace.html")

    assert "can_manage_rmf_analysis | tojson" in rmf_template
    assert "capabilities.baseline_controls" in rmf_template
    assert "control.control_id" in rmf_template
    assert "control.title" in rmf_template
    assert "control.family" in rmf_template
    assert 'value="baseline" checked' in rmf_template
    assert "data-rmf-analysis-deep" in rmf_template
    assert "data-rmf-analysis-fresh" in rmf_template
    assert "can take significantly longer" in rmf_template
    assert "increase model usage and cost" in rmf_template
    assert "rmf-analysis-control-list" in rmf_template
    assert "max-height: min(18rem, 45vh)" in rmf_template
    assert "contain: paint" in rmf_template
    assert '<div class="card shadow-sm border-0 h-100">' not in rmf_template[
        rmf_template.index('id="rmf-analysis"') : rmf_template.index(
            'data-rmf-analysis-status'
        )
    ]
    assert "View only." in rmf_template
    assert 'fetch("/api/rmf/workspace/analysis/capabilities")' in rmf_template
    assert 'fetch("/api/rmf/workspace/analysis")' in rmf_template
    assert "/api/rmf/workspace/analysis/jobs/" in rmf_template
    assert "async function pollRmfAnalysisJob(jobId, retryAttempt = 0)" in rmf_template
    assert "retryAttempt < 5" in rmf_template
    assert "await loadRmfOverview()" in rmf_template
    assert "document.createElement" in rmf_template
    assert ".replaceChildren()" in rmf_template
    assert "innerHTML" not in rmf_template
    for status in ("succeeded", "partial", "failed", "stale", "cancelled"):
        assert status in rmf_template


def test_rmf_controls_ui_and_proxy_are_safe_and_audited():
    rmf_template = read_text("application/single_app/templates/rmf_workspace.html")
    backend = read_text("application/single_app/route_backend_rmf.py")
    rmf_functions = read_text("application/single_app/functions_rmf.py")

    assert "def get_rmf_controls" in rmf_functions
    assert "def get_rmf_control" in rmf_functions
    assert "def set_rmf_control_applicability" in rmf_functions
    assert "params=params" in rmf_functions
    assert "quote(control_id, safe='')" in rmf_functions
    assert 'state not in {"current", "stale", "not-analyzed"}' in backend
    assert "page_size > 100" in backend
    assert 'fetch(`/api/rmf/workspace/controls?${params.toString()}`)' in rmf_template
    assert "/api/rmf/workspace/controls/${encodeURIComponent(controlId)}" in rmf_template
    assert "data-rmf-control-citations" in rmf_template
    assert "data-rmf-control-history" in rmf_template
    assert "prepareRmfAnalysisControl" in rmf_template
    assert "data-rmf-applicability-panel" in rmf_template
    assert "submitRmfApplicability" in rmf_template
    assert "candidate_fingerprint" in rmf_template
    assert "Applicability: ${" in rmf_template
    assert 'action not in {"approve", "reject", "revoke"}' in backend
    assert "allowed_roles=RMF_MANAGER_ROLES" in backend
    assert "requestId !== rmfControlsRequestId" in rmf_template
    assert "AI-drafted content must be reviewed" in rmf_template
    assert "document.createElement" in rmf_template
    assert ".replaceChildren()" in rmf_template
    assert "innerHTML" not in rmf_template


def test_rmf_attestation_ui_and_proxy_are_collaborative_and_audited(monkeypatch):
    rmf_template = read_text("application/single_app/templates/rmf_workspace.html")
    backend = read_text("application/single_app/route_backend_rmf.py")
    rmf = load_functions_rmf(monkeypatch)
    requests = []

    def fake_request(method, path, group_id, user_id, role, **kwargs):
        requests.append((method, path, kwargs))
        return {"id": "session-1", "revision": 1}

    monkeypatch.setattr(rmf, "_rmf_request", fake_request)
    context = ("group-1", "user-1", "User")
    rmf.get_rmf_attestation_capabilities(*context)
    rmf.get_rmf_attestation_sessions(*context)
    rmf.get_rmf_attestation_session(*context, "session-1")
    rmf.create_rmf_attestation_session(
        *context, {"scope": "selected", "control_ids": ["AC-2"], "all_controls": False}
    )
    rmf.advance_rmf_attestation_session(
        *context, "session-1", {"expected_revision": 1}
    )
    rmf.answer_rmf_attestation_session(
        *context,
        "session-1",
        {
            "expected_revision": 2,
            "turn_id": "turn-1",
            "answer": "The IAM team reviews quarterly.",
        },
    )
    rmf.cancel_rmf_attestation_session(
        *context, "session-1", {"expected_revision": 3}
    )

    assert [(method, path) for method, path, _ in requests] == [
        ("GET", "/api/v1/workspaces/current/attestations/capabilities"),
        ("GET", "/api/v1/workspaces/current/attestations/sessions"),
        ("GET", "/api/v1/workspaces/current/attestations/sessions/session-1"),
        ("POST", "/api/v1/workspaces/current/attestations/sessions"),
        ("POST", "/api/v1/workspaces/current/attestations/sessions/session-1/next"),
        ("POST", "/api/v1/workspaces/current/attestations/sessions/session-1/answers"),
        ("POST", "/api/v1/workspaces/current/attestations/sessions/session-1/cancel"),
    ]
    assert "data-rmf-attestation-answer" in rmf_template
    assert "data-rmf-attestation-transcript" in rmf_template
    assert "Self-reported, unverified" in rmf_template
    assert "revision: rmfAttestationCurrent.revision" in rmf_template
    assert "turn_id: current.id" in rmf_template
    assert "response.status === 409" in rmf_template
    assert "prepareRmfAnalysisFromAttestation" in rmf_template
    assert "document.createElement" in rmf_template
    assert ".replaceChildren()" in rmf_template
    assert "innerHTML" not in rmf_template
    assert "get_rmf_attestation_capabilities" in backend
    assert "create_rmf_attestation_session" in backend
    assert "answer_rmf_attestation_session" in backend
    assert "allowed_roles=RMF_MANAGER_ROLES" in backend
    assert 'allowed_roles=("Owner", "Admin", "DocumentManager", "User")' in backend
    assert "answer must be between 1 and 10000 characters" in backend
    assert '"expected_revision": revision' in backend
    assert '"answer": answer' in backend
    assert "!answer.trim()" in rmf_template
    assert "turn.answered_by || turn.actor" in rmf_template
    assert '"Retry assessment"' in rmf_template
    assert ".filter((entry) => Array.isArray(entry.turns) && entry.turns.length)" in rmf_template
    assert '"applicability review required"' in rmf_template
    assert "entry.applicability ||" in rmf_template
    assert "applicability_outcome" not in rmf_template
    assert "entry.applicability_candidate_created" in rmf_template
    assert "entry.applicability_rationale" in rmf_template
    assert "rmfAttestationApplicabilityPending" in rmf_template
    assert "rmfAttestationBlocksAnalysis" in rmf_template
    assert '["candidate", "approved"].includes(entry.applicability_state)' in rmf_template
    assert '"likely not applicable — review required"' in rmf_template
    assert "openRmfAttestationApplicability" in rmf_template
    assert "rmfAttestationEligibleAnalysisControls" in rmf_template
    assert "!rmfAttestationEligibleAnalysisControls(session).length" in rmf_template


def test_rmf_export_workspace_contract_is_safe_role_aware_and_bounded(monkeypatch):
    rmf_template = read_text("application/single_app/templates/rmf_workspace.html")
    backend = read_text("application/single_app/route_backend_rmf.py")
    rmf_functions = read_text("application/single_app/functions_rmf.py")
    rmf = load_functions_rmf(monkeypatch)
    requests = []

    def fake_request(method, path, group_id, user_id, role, **kwargs):
        requests.append((method, path, kwargs))
        return {"id": "export-1"}

    monkeypatch.setattr(rmf, "_rmf_request", fake_request)
    context = ("group-1", "user-1", "Admin")
    rmf.get_rmf_export_templates(*context)
    rmf.get_rmf_export_template(*context, "template-1")
    rmf.get_rmf_export_readiness(*context, {"template_id": "version-2"})
    rmf.get_rmf_exports(*context)
    rmf.create_rmf_export(
        *context,
        {"template_id": "version-2"},
        "request-1",
    )
    rmf.get_rmf_export(*context, "export-1")
    rmf.mutate_rmf_export_template(*context, "version-2", "activate", 7)

    assert [(method, path) for method, path, _ in requests] == [
        ("GET", "/api/v1/workspaces/current/export-templates"),
        ("GET", "/api/v1/workspaces/current/export-templates/template-1"),
        ("GET", "/api/v1/workspaces/current/exports/readiness"),
        ("GET", "/api/v1/workspaces/current/exports"),
        ("POST", "/api/v1/workspaces/current/exports"),
        ("GET", "/api/v1/workspaces/current/exports/export-1"),
        ("POST", "/api/v1/workspaces/current/export-templates/version-2/activate"),
    ]
    assert requests[2][-1]["params"] == {"template_id": "version-2"}
    assert requests[4][-1]["payload"] == {"template_id": "version-2"}
    assert requests[4][-1]["extra_headers"] == {"Idempotency-Key": "request-1"}
    assert requests[6][-1]["payload"] == {"revision": 7}
    assert "RMF_EXPORT_MEMBER_ROLES" in backend
    assert "allowed_roles=RMF_MANAGER_ROLES" in backend
    assert "allowed_roles=RMF_EXPORT_MEMBER_ROLES" in backend
    assert "max_export_template_bytes = 20 * 1024 * 1024" in backend
    assert 'set(request.files) != {"file"}' in backend
    assert 'set(request.form) != {"metadata"}' in backend
    assert "content.startswith(b\"PK\")" in backend
    assert '"idempotency_key"' in backend
    assert '"Idempotency-Key": idempotency_key' in rmf_functions
    assert "not isinstance(payload, dict)" in backend
    assert '"family_name": family_name' in backend
    assert '"column_map": normalized_mapping' in backend
    assert '"inspected_sha256": inspected_sha256' in backend
    assert 'set(payload) != {"revision"}' in backend
    assert "or revision < 1" in backend
    assert '"inactive" if action == "activate" else "active"' in backend
    assert "if current_status != required_status" in backend
    assert "get_rmf_export_template(" in backend[
        backend.index("def mutate_rmf_workspace_export_template") :
        backend.index('@bp.route("/api/rmf/workspace/exports/readiness"')
    ]
    assert "mutateRmfExportTemplate(versionId, \"retire\", version.revision)" in rmf_template
    assert 'return jsonify(_sanitize_export_response(result)), 201' in backend
    assert '"requested_by"' not in backend[
        backend.index("def create_rmf_workspace_export()") :
        backend.index('@bp.route("/api/rmf/workspace/exports/<export_id>"')
    ]
    assert "stream_with_context" in backend
    assert "def _sanitize_export_response" in backend
    assert 'endswith(("_sas", "_token", "_url", "_uri"))' in backend
    assert '"X-Content-Type-Options"] = "nosniff"' in backend
    assert '"Cache-Control"] = "private, no-store"' in backend
    assert "Export artifacts are available only after successful completion." in backend
    assert "allow_redirects=False" in rmf_functions
    assert "X-RMF-Service-Key" in rmf_functions
    assert "data-rmf-export-template-form" in rmf_template
    assert "data-rmf-export-readiness-counts" in rmf_template
    assert "data-rmf-export-history" in rmf_template
    assert 'option.value = JSON.stringify({template_id: versionId})' in rmf_template
    assert "version.family_id" in rmf_template
    assert "version.family_name" in rmf_template
    assert "version.profile" in rmf_template
    assert "profile.column_map" in rmf_template
    assert 'version.status === "inactive"' in rmf_template
    assert 'mutateRmfExportTemplate(versionId, "activate", version.revision)' in rmf_template
    assert 'version.status === "active"' in rmf_template
    assert 'mutateRmfExportTemplate(versionId, "retire", version.revision)' in rmf_template
    assert "inspected_sha256: rmfExportInspection?.sha256" in rmf_template
    assert "rows[String(headerRow)]" in rmf_template
    assert "header?.value" in rmf_template
    assert '["total_rows", "Total rows"]' in rmf_template
    assert '["writable", "Writable"]' in rmf_template
    assert "job.evidence_fingerprint" in rmf_template
    assert "job.manifest_sha256" in rmf_template
    assert "canManageRmfExport" in rmf_template
    assert "rmfExportPollAttempts > 60" in rmf_template
    assert "crypto.randomUUID()" in rmf_template
    assert "rmfExportIdempotencyKey = null" in rmf_template
    assert "Restore and activate" not in rmf_template
    assert "rmfExportInspectionRequestId += 1" in rmf_template
    assert "invalidateRmfExportInspection()" in rmf_template
    assert "requestId !== rmfExportReadinessRequestId" in rmf_template
    assert "requestId !== rmfExportInspectionRequestId" in rmf_template
    assert "const requestId = ++rmfExportLoadRequestId" in rmf_template
    assert rmf_template.count("requestId !== rmfExportLoadRequestId") >= 2
    assert "if (rmfExportActiveId || rmfExportGenerationPending)" in rmf_template
    assert "rmfExportGenerationPending = true" in rmf_template
    assert "rmfExportGenerationPending = false" in rmf_template
    assert "rmfExportActiveId = String(exportId)" in rmf_template
    assert "const activeJob = rmfExportJobs.find" in rmf_template
    generate_block = rmf_template[
        rmf_template.index("async function generateRmfExport()") :
        rmf_template.index("async function loadRmfExports()")
    ]
    assert generate_block.index("await loadRmfExports()") < generate_block.index(
        "rmfExportIdempotencyKey = null"
    )
    assert "if (rmfExportIsSuccessful(payload))" in generate_block
    poll_block = rmf_template[
        rmf_template.index("async function pollRmfExport") :
        rmf_template.index("async function generateRmfExport")
    ]
    assert "if (rmfExportIsSuccessful(payload))" in poll_block
    history_block = rmf_template[
        rmf_template.index("function renderRmfExportHistory()") :
        rmf_template.index("async function pollRmfExport")
    ]
    assert '["succeeded", "completed", "complete"].includes' in history_block
    assert '"partial"].includes' not in history_block
    assert "retryAttempt < 5" in rmf_template
    assert "FormData()" in rmf_template
    assert "document.createElement" in rmf_template
    assert ".replaceChildren()" in rmf_template
    assert "innerHTML" not in rmf_template


def test_rmf_export_template_multipart_is_rebuilt_server_side(monkeypatch):
    rmf = load_functions_rmf(monkeypatch)
    captured = {}

    def fake_request(method, path, group_id, user_id, role, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = kwargs["data"].to_string()
        captured["content_type"] = kwargs["extra_headers"]["Content-Type"]
        return {"id": "version-1"}

    monkeypatch.setattr(rmf, "_rmf_request", fake_request)
    result = rmf.create_rmf_export_template(
        "group-1",
        "user-1",
        "Admin",
        "controls.xlsx",
        b"PK workbook",
        {
            "family_name": "Customer SSP",
            "profile": {
                "sheet": "Controls",
                "header_row": 2,
                "control_id_header": "Control ID",
                "column_map": {"implementation_statement": "Implementation"},
            },
            "inspected_sha256": "a" * 64,
        },
    )

    assert result["id"] == "version-1"
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/workspaces/current/export-templates"
    assert b"controls.xlsx" in captured["body"]
    assert b"PK workbook" in captured["body"]
    assert b"inspected_sha256" in captured["body"]
    assert b"Customer SSP" in captured["body"]
    assert captured["content_type"].startswith("multipart/form-data; boundary=")


def make_xlsx(entries=None, cell_reference="A1", row_number=1):
    workbook_entries = {
        "[Content_Types].xml": b"<Types/>",
        "xl/workbook.xml": b"<workbook/>",
        "xl/worksheets/sheet1.xml": (
            f'<worksheet><sheetData><row r="{row_number}"><c r="{cell_reference}"/>'
            "</row></sheetData></worksheet>"
        ).encode(),
    }
    workbook_entries.update(entries or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in workbook_entries.items():
            package.writestr(name, content)
    return stream.getvalue()


def test_rmf_xlsx_preflight_rejects_macros_external_links_and_bounds(monkeypatch):
    rmf = load_functions_rmf(monkeypatch)

    rmf.validate_rmf_xlsx_package(make_xlsx())

    with pytest.raises(ValueError, match="Macros, external links"):
        rmf.validate_rmf_xlsx_package(
            make_xlsx({"xl/vbaProject.bin": b"macro"})
        )
    with pytest.raises(ValueError, match="External workbook relationships"):
        rmf.validate_rmf_xlsx_package(
            make_xlsx({
                "xl/_rels/workbook.xml.rels": (
                    b'<Relationships><Relationship TargetMode = "external"/>'
                    b"</Relationships>"
                )
            })
        )
    rmf_functions = read_text("application/single_app/functions_rmf.py")
    assert "ElementTree.fromstring(package.read(entry))" in rmf_functions
    assert 'str(value).lower() == "external"' in rmf_functions
    with pytest.raises(ValueError, match="column limit"):
        rmf.validate_rmf_xlsx_package(make_xlsx(cell_reference="XFE1"))
    with pytest.raises(ValueError, match="row limit"):
        rmf.validate_rmf_xlsx_package(make_xlsx(row_number=1_048_577))
    with pytest.raises(ValueError, match="worksheet count"):
        rmf.validate_rmf_xlsx_package(
            make_xlsx({
                f"xl/worksheets/sheet{index}.xml": b"<worksheet/>"
                for index in range(2, 102)
            })
        )
    with pytest.raises(ValueError, match="entry count"):
        rmf.validate_rmf_xlsx_package(
            make_xlsx({f"custom/item-{index}.xml": b"x" for index in range(2000)})
        )
    with pytest.raises(ValueError, match="compression ratio"):
        rmf.validate_rmf_xlsx_package(
            make_xlsx({"custom/high-ratio.bin": b"0" * (2 * 1024 * 1024)})
        )


def test_rmf_export_release_documents_paired_backend_invariants():
    release = read_text(
        "docs/explanation/features/v0.250.069/RMF_REUSABLE_EXPORT_WORKSPACE.md"
    )

    assert "safe text semantics" in release
    assert "`=`, `+`, `-`, `@`, tab" in release
    assert "A durable upload becomes\n  `inactive`" in release
    assert "atomically retiring the prior active version" in release
    assert "Retired versions can never reactivate" in release
    assert "Activate only for inactive versions" in release
    assert "Ordinary members receive active" in release
    assert "SimpleChat exposes no restore" in release
    assert "mismatched reuse returns 409" in release
    assert "Failed or partial artifacts are never downloadable" in release
    assert "template, workbook, and manifest SHA-256 digests" in release


def test_rmf_evidence_import_builds_manifest_and_multipart_from_exact_revision(monkeypatch):
    rmf = load_functions_rmf(monkeypatch)
    document = {
        "id": "revision-2",
        "revision_family_id": "family-1",
        "version": 2,
        "file_name": "network.json",
    }
    monkeypatch.setattr(rmf, "get_document_record", lambda **kwargs: document)
    monkeypatch.setattr(
        rmf,
        "get_document_versions",
        lambda **kwargs: [
            {
                "id": "revision-2",
                "revision_family_id": "family-1",
                "is_current_version": True,
            }
        ],
    )
    monkeypatch.setattr(
        rmf,
        "ensure_document_revision_blob",
        lambda *args, **kwargs: ("group-documents", "group/family/revision-2/network.json"),
    )

    def fake_download(container, path, destination):
        Path(destination).write_bytes(b'{"id":"resource"}')

    monkeypatch.setattr(rmf, "download_blob_to_file", fake_download)
    captured = {}

    def fake_request(method, path, group_id, user_id, role, **kwargs):
        multipart_body = kwargs["data"].to_string()
        captured.update(
            method=method,
            path=path,
            group_id=group_id,
            user_id=user_id,
            role=role,
            multipart_body=multipart_body,
            **kwargs,
        )
        return {
            "evidence_import": {"id": "import-1"},
            "job": {"id": "job-1", "status": "queued"},
        }

    monkeypatch.setattr(rmf, "_rmf_request", fake_request)
    result = rmf.import_rmf_evidence(
        "group-1",
        "user-1",
        "DocumentManager",
        "revision-2",
        "azure-resource-json",
        "owner@example.com",
    )

    assert result == {
        "evidence_import": {"id": "import-1"},
        "job": {"id": "job-1", "status": "queued"},
    }
    assert b"network.json" in captured["multipart_body"]
    assert b'{"id":"resource"}' in captured["multipart_body"]
    manifest = {
        "source_type": "evidence",
        "extract_topology": True,
        "source_document_id": "revision-2",
        "revision_family_id": "family-1",
        "version": 2,
        "original_name": "network.json",
        "content_type": "application/json",
        "sha256": hashlib.sha256(b'{"id":"resource"}').hexdigest(),
        "uploaded_by": "owner@example.com",
    }
    assert json.dumps(manifest).encode() in captured["multipart_body"]
    assert manifest["source_document_id"] == "revision-2"
    assert manifest["revision_family_id"] == "family-1"
    assert manifest["version"] == 2
    assert manifest["source_type"] == "evidence"
    assert manifest["extract_topology"] is True
    assert manifest["uploaded_by"] == "owner@example.com"
    assert len(manifest["sha256"]) == 64
    assert set(manifest) == {
        "source_type",
        "extract_topology",
        "source_document_id",
        "revision_family_id",
        "version",
        "original_name",
        "content_type",
        "sha256",
        "uploaded_by",
    }


def test_rmf_evidence_import_rejects_noncurrent_revision_before_blob_access(monkeypatch):
    rmf = load_functions_rmf(monkeypatch)
    monkeypatch.setattr(rmf, "get_document_record", lambda **kwargs: {"id": "revision-1"})
    monkeypatch.setattr(
        rmf,
        "get_document_versions",
        lambda **kwargs: [{"id": "revision-2", "is_current_version": True}],
    )
    blob_accessed = False

    def fail_blob_access(*args, **kwargs):
        nonlocal blob_accessed
        blob_accessed = True

    monkeypatch.setattr(rmf, "ensure_document_revision_blob", fail_blob_access)
    with pytest.raises(rmf.RMFServiceError) as exc_info:
        rmf.import_rmf_evidence(
            "group-1",
            "user-1",
            "Admin",
            "revision-1",
            "observed-evidence",
            "user-1",
        )

    assert exc_info.value.status_code == 409
    assert blob_accessed is False


def test_rmf_evidence_import_rechecks_current_revision_after_archival(monkeypatch):
    rmf = load_functions_rmf(monkeypatch)
    document = {
        "id": "revision-1",
        "revision_family_id": "family-1",
        "version": 1,
        "file_name": "evidence.txt",
    }
    monkeypatch.setattr(rmf, "get_document_record", lambda **kwargs: document)
    version_calls = 0

    def versions(**kwargs):
        nonlocal version_calls
        version_calls += 1
        current_id = "revision-1" if version_calls == 1 else "revision-2"
        return [{"id": current_id, "is_current_version": True}]

    monkeypatch.setattr(rmf, "get_document_versions", versions)
    monkeypatch.setattr(
        rmf,
        "ensure_document_revision_blob",
        lambda *args, **kwargs: ("group-documents", "archive/revision-1/evidence.txt"),
    )
    downloaded = False

    def download(*args, **kwargs):
        nonlocal downloaded
        downloaded = True

    monkeypatch.setattr(rmf, "download_blob_to_file", download)

    with pytest.raises(rmf.RMFServiceError) as exc_info:
        rmf.import_rmf_evidence(
            "group-1",
            "user-1",
            "Admin",
            "revision-1",
            "observed-evidence",
            "user-1",
        )

    assert exc_info.value.status_code == 409
    assert downloaded is False


def test_rmf_request_reloads_rotated_service_key_once(monkeypatch):
    rmf = load_functions_rmf(monkeypatch)
    keys = iter(["stale-key", "fresh-key"])
    monkeypatch.setattr(rmf, "_get_rmf_service_key", lambda: next(keys))
    requests_seen = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.ok = status_code < 400
            self.content = json.dumps(payload).encode()
            self._payload = payload

        def json(self):
            return self._payload

    def request(method, url, **kwargs):
        requests_seen.append(kwargs["headers"]["X-RMF-Service-Key"])
        if len(requests_seen) == 1:
            return Response(401, {"detail": "invalid key"})
        return Response(200, {"status": "ok"})

    monkeypatch.setattr(rmf.requests, "request", request)

    assert rmf._rmf_request("GET", "/health", "group-1", "user-1", "Admin") == {
        "status": "ok"
    }
    assert requests_seen == ["stale-key", "fresh-key"]
