#!/usr/bin/env python3
"""
Functional tests for the dedicated RMF investigative chat workspace.
Version: 0.250.070
Implemented in: 0.250.070

These tests validate the workspace-scoped proxy contract, request validation,
navigation, and the key safe/read-only UI states.
"""

import ast
import importlib.util
from pathlib import Path
import sys
import types

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path):
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def identity_decorator(*args, **kwargs):
    def decorator(function):
        return function

    return decorator


def load_functions_rmf(monkeypatch):
    stubs = {
        "config": {
            "cosmos_groups_container": object(),
            "RMF_API_BASE_URL": "https://rmf.example",
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
    spec = importlib.util.spec_from_file_location("functions_rmf_chat_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_route_backend_rmf(monkeypatch):
    flask = pytest.importorskip("flask")
    role_checks = []

    authentication = types.ModuleType("functions_authentication")
    authentication.get_current_user_id = lambda: "user-1"
    authentication.get_current_user_info = lambda: {"email": "user@example.com"}
    authentication.login_required = identity_decorator()
    authentication.user_required = identity_decorator()

    group = types.ModuleType("functions_group")
    group.require_active_group = lambda user_id: "workspace-1"

    def assert_group_role(user_id, group_id, allowed_roles):
        role_checks.append(tuple(allowed_roles))
        return "User"

    group.assert_group_role = assert_group_role
    group.find_group_by_id = lambda group_id: {
        "id": group_id,
        "name": "Test workspace",
        "rmf": {"enabled": True},
    }
    group.get_user_role_in_group = lambda group_doc, user_id: "User"

    rmf = types.ModuleType("functions_rmf")

    class RMFServiceError(RuntimeError):
        def __init__(self, message, status_code=502):
            super().__init__(message)
            self.status_code = status_code

    rmf.RMFServiceError = RMFServiceError
    rmf.RMF_EVIDENCE_MANAGER_ROLES = ("Owner", "Admin", "DocumentManager")
    rmf.RMF_EXPORT_MEMBER_ROLES = ("Owner", "Admin", "DocumentManager", "User")
    rmf.RMF_MANAGER_ROLES = ("Owner", "Admin")
    rmf.get_group_rmf_state = lambda group_doc: {"enabled": True}

    route_path = REPO_ROOT / "application" / "single_app" / "route_backend_rmf.py"
    route_tree = ast.parse(route_path.read_text(encoding="utf-8"))
    for node in route_tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "functions_rmf":
            for imported in node.names:
                if not hasattr(rmf, imported.name):
                    setattr(rmf, imported.name, lambda *args, **kwargs: {})

    metrics = types.ModuleType("functions_rmf_analysis_metrics")
    metrics.record_rmf_analysis_baseline = lambda **kwargs: None
    metrics.record_rmf_analysis_selection = lambda **kwargs: None

    settings = types.ModuleType("functions_settings")
    settings.enabled_required = lambda name: identity_decorator()

    swagger = types.ModuleType("swagger_wrapper")
    swagger.get_auth_security = lambda: []
    swagger.swagger_route = lambda **kwargs: identity_decorator()

    monkeypatch.setitem(sys.modules, "functions_authentication", authentication)
    monkeypatch.setitem(sys.modules, "functions_group", group)
    monkeypatch.setitem(sys.modules, "functions_rmf", rmf)
    monkeypatch.setitem(sys.modules, "functions_rmf_analysis_metrics", metrics)
    monkeypatch.setitem(sys.modules, "functions_settings", settings)
    monkeypatch.setitem(sys.modules, "swagger_wrapper", swagger)

    spec = importlib.util.spec_from_file_location(
        "route_backend_rmf_chat_under_test",
        route_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = flask.Flask(__name__)
    blueprint = flask.Blueprint("rmf_chat_test", __name__)
    module.register_route_backend_rmf(blueprint)
    app.register_blueprint(blueprint)
    return app, module, RMFServiceError, role_checks


def test_rmf_chat_client_uses_workspace_scoped_service_contract(monkeypatch):
    rmf = load_functions_rmf(monkeypatch)
    requests = []

    def fake_request(method, path, group_id, user_id, role, **kwargs):
        requests.append((method, path, group_id, user_id, role, kwargs))
        return {"id": "chat-1"}

    monkeypatch.setattr(rmf, "_rmf_request", fake_request)
    context = ("workspace-1", "user-1", "User")
    message = {"expected_revision": 3, "question": "What evidence supports AC-2?"}
    archive = {"expected_revision": 4}

    rmf.get_rmf_chat_capabilities(*context)
    rmf.get_rmf_chat_sessions(*context)
    rmf.create_rmf_chat_session(*context, {"title": "Access control"})
    rmf.get_rmf_chat_session(*context, "chat:1")
    rmf.send_rmf_chat_message(*context, "chat:1", message)
    rmf.archive_rmf_chat_session(*context, "chat:1", archive)
    rmf.get_rmf_chat_source(*context, "chat:1", "source:2")

    assert [(method, path) for method, path, *_ in requests] == [
        ("GET", "/api/v1/workspaces/current/chat/capabilities"),
        ("GET", "/api/v1/workspaces/current/chat/sessions"),
        ("POST", "/api/v1/workspaces/current/chat/sessions"),
        ("GET", "/api/v1/workspaces/current/chat/sessions/chat%3A1"),
        ("POST", "/api/v1/workspaces/current/chat/sessions/chat%3A1/messages"),
        ("POST", "/api/v1/workspaces/current/chat/sessions/chat%3A1/archive"),
        (
            "GET",
            "/api/v1/workspaces/current/chat/sessions/chat%3A1/sources/source%3A2",
        ),
    ]
    assert requests[2][-1]["payload"] == {"title": "Access control"}
    assert requests[4][-1]["payload"] == message
    assert requests[5][-1]["payload"] == archive


def test_rmf_chat_proxy_validates_requests_and_preserves_stale_conflict(monkeypatch):
    app, route, service_error, role_checks = load_route_backend_rmf(monkeypatch)
    calls = []
    route.get_rmf_chat_capabilities = lambda *args: {
        "enabled": True,
        "read_only": True,
        "supports_live": False,
        "source_counts": {"evidence": 3},
        "starter_prompts": ["Where are the current gaps?"],
    }
    route.create_rmf_chat_session = lambda *args: {
        "id": "chat-1",
        "status": "active",
        "revision": 1,
        "messages": [],
    }

    def send_message(*args):
        calls.append(args)
        raise service_error("Assessment changed. Start a new chat.", 409)

    route.send_rmf_chat_message = send_message
    client = app.test_client()

    capabilities = client.get("/api/rmf/workspace/chat/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.get_json()["read_only"] is True

    created = client.post(
        "/api/rmf/workspace/chat/sessions",
        json={"title": "  Access review  "},
    )
    assert created.status_code == 201

    invalid = client.post(
        "/api/rmf/workspace/chat/sessions/chat-1/messages",
        json={"expected_revision": True, "question": "Question"},
    )
    assert invalid.status_code == 400

    stale = client.post(
        "/api/rmf/workspace/chat/sessions/chat-1/messages",
        json={"expected_revision": 1, "question": "  What changed?  "},
    )
    assert stale.status_code == 409
    assert stale.get_json()["error"] == "Assessment changed. Start a new chat."
    assert calls[0][-1] == {
        "expected_revision": 1,
        "question": "What changed?",
    }
    assert role_checks
    assert set(role_checks[0]) == {"Owner", "Admin", "DocumentManager", "User"}


def test_rmf_chat_route_navigation_and_key_ui_states_are_wired():
    config = read_text("application/single_app/config.py")
    frontend = read_text("application/single_app/route_frontend_rmf.py")
    backend = read_text("application/single_app/route_backend_rmf.py")
    template = read_text("application/single_app/templates/rmf_chat.html")
    script = read_text("application/single_app/static/js/rmf/rmf-chat.js")
    stylesheet = read_text("application/single_app/static/css/rmf-chat.css")
    navigation = "\n".join(
        read_text(path)
        for path in (
            "application/single_app/templates/_top_nav.html",
            "application/single_app/templates/_sidebar_nav.html",
            "application/single_app/templates/_sidebar_short_nav.html",
            "application/single_app/templates/rmf_workspace.html",
        )
    )

    assert 'VERSION = "0.250.070"' in config
    assert '@bp.route("/rmf/chat", methods=["GET"])' in frontend
    assert "frontend_rmf.rmf_chat" in navigation
    for endpoint in (
        '"/api/rmf/workspace/chat/capabilities"',
        '"/api/rmf/workspace/chat/sessions"',
        '"/api/rmf/workspace/chat/sessions/<conversation_id>"',
        '"/api/rmf/workspace/chat/sessions/<conversation_id>/messages"',
        '"/api/rmf/workspace/chat/sessions/<conversation_id>/archive"',
        '"/api/rmf/workspace/chat/sessions/<conversation_id>/sources/<source_id>"',
    ):
        assert endpoint in backend

    for state_marker in (
        "data-rmf-active-conversations",
        "data-rmf-archived-conversations",
        "data-rmf-stale-banner",
        "data-rmf-chat-retrieval",
        "data-rmf-starter-prompts",
        "data-rmf-source-drawer",
        "data-rmf-composer-status",
    ):
        assert state_marker in template

    assert "window.DOMPurify.sanitize" in script
    assert "window.marked.parse" in script
    assert "decorateInlineCitations" in script
    assert "No supporting evidence was returned" in script
    assert "error.status === 409" in script
    assert "conversation.is_stale" in script
    assert "expected_revision: conversation.revision" in script
    assert "requestId !== state.conversationRequestId" in script
    assert "requestId !== state.sourceRequestId" in script
    assert "state.loadingConversation" in script
    assert "Grounded retrieval completes before the cited answer appears" in template
    assert "chat cannot change RMF workflows or call live cloud tools" in template
    assert "/api/rmf/workspace/controls" not in script
    assert "/api/rmf/workspace/analysis" not in script
    assert "@media (max-width: 991.98px)" in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
