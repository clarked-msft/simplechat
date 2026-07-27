# route_backend_rmf.py

import json
import re
from urllib.parse import unquote

from flask import Response, jsonify, request, stream_with_context

from functions_authentication import (
    get_current_user_id,
    get_current_user_info,
    login_required,
    user_required,
)
from functions_group import (
    assert_group_role,
    find_group_by_id,
    get_user_role_in_group,
    require_active_group,
)
from functions_rmf import (
    RMF_EVIDENCE_MANAGER_ROLES,
    RMF_EXPORT_MEMBER_ROLES,
    RMF_MANAGER_ROLES,
    RMFServiceError,
    archive_rmf_chat_session,
    cancel_rmf_analysis_job,
    cancel_rmf_attestation_session,
    create_rmf_chat_session,
    create_rmf_export,
    create_rmf_export_template,
    create_rmf_attestation_session,
    download_rmf_export,
    advance_rmf_attestation_session,
    answer_rmf_attestation_session,
    get_rmf_analysis,
    get_rmf_analysis_capabilities,
    get_rmf_analysis_job,
    get_rmf_attestation_capabilities,
    get_rmf_attestation_session,
    get_rmf_attestation_sessions,
    get_rmf_chat_capabilities,
    get_rmf_chat_session,
    get_rmf_chat_sessions,
    get_rmf_chat_source,
    get_rmf_control,
    get_rmf_controls,
    get_rmf_evidence,
    get_rmf_evidence_capabilities,
    get_rmf_evidence_job,
    get_rmf_export,
    get_rmf_export_readiness,
    get_rmf_export_template,
    get_rmf_export_templates,
    get_rmf_exports,
    get_group_rmf_state,
    get_rmf_overview,
    get_rmf_service_state,
    import_rmf_evidence,
    initialize_rmf_service,
    inspect_rmf_export_template,
    mutate_rmf_export_template,
    send_rmf_chat_message,
    set_rmf_control_applicability,
    start_rmf_analysis,
    update_group_rmf_setup_status,
    update_group_rmf_state,
    validate_rmf_xlsx_package,
    withdraw_rmf_evidence,
)
from functions_rmf_analysis_metrics import (
    record_rmf_analysis_baseline,
    record_rmf_analysis_selection,
)
from functions_settings import enabled_required
from swagger_wrapper import get_auth_security, swagger_route


def register_route_backend_rmf(bp):
    export_id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    export_header_pattern = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")
    export_canonical_fields = {
        "status",
        "implementation_statement",
        "responsible_role",
        "inheritance",
        "citations",
        "rationale",
        "unmet_requirements",
        "confidence",
        "provenance",
    }
    max_export_template_bytes = 20 * 1024 * 1024
    chat_id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    def _valid_export_id(value):
        return bool(export_id_pattern.fullmatch(str(value or "")))

    def _valid_chat_id(value):
        return bool(chat_id_pattern.fullmatch(str(value or "")))

    def _read_export_template_upload():
        if set(request.files) != {"file"}:
            return None, None, (jsonify({"error": "Only one template file is allowed"}), 400)
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return None, None, (jsonify({"error": "An XLSX template file is required"}), 400)
        filename = str(upload.filename).replace("\\", "/").split("/")[-1]
        if (
            not filename.lower().endswith(".xlsx")
            or len(filename) > 200
            or not export_header_pattern.fullmatch(filename)
        ):
            return None, None, (jsonify({"error": "A valid .xlsx filename is required"}), 400)
        if upload.mimetype not in {
            "application/octet-stream",
            "application/zip",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            return None, None, (jsonify({"error": "The template content type is invalid"}), 400)
        if request.content_length and request.content_length > max_export_template_bytes + 65536:
            return None, None, (jsonify({"error": "The template exceeds the 20 MB limit"}), 413)
        content = upload.stream.read(max_export_template_bytes + 1)
        if len(content) > max_export_template_bytes:
            return None, None, (jsonify({"error": "The template exceeds the 20 MB limit"}), 413)
        if not content.startswith(b"PK"):
            return None, None, (jsonify({"error": "The uploaded file is not a valid XLSX package"}), 400)
        try:
            validate_rmf_xlsx_package(content)
        except ValueError as exc:
            return None, None, (jsonify({"error": str(exc)}), 400)
        return filename, content, None

    def _validate_export_metadata(raw_metadata):
        try:
            metadata = json.loads(raw_metadata or "{}")
        except (TypeError, ValueError):
            return None, "metadata must be valid JSON"
        if not isinstance(metadata, dict):
            return None, "metadata must be an object"
        family_name = str(metadata.get("family_name") or "").strip()
        profile = metadata.get("profile")
        inspected_sha256 = str(metadata.get("inspected_sha256") or "").strip().lower()
        if not isinstance(profile, dict):
            return None, "profile must be an object"
        sheet_name = str(profile.get("sheet") or "").strip()
        control_id_header = str(profile.get("control_id_header") or "").strip()
        header_row = profile.get("header_row")
        field_mapping = profile.get("column_map")
        if set(metadata) != {"family_name", "profile", "inspected_sha256"}:
            return None, "metadata contains unsupported fields"
        if set(profile) != {"control_id_header", "column_map", "sheet", "header_row"}:
            return None, "profile contains unsupported fields"
        if not 1 <= len(family_name) <= 120:
            return None, "family_name must be between 1 and 120 characters"
        if not re.fullmatch(r"[0-9a-f]{64}", inspected_sha256):
            return None, "inspected_sha256 must be a SHA-256 digest"
        if not 1 <= len(sheet_name) <= 31 or any(char in sheet_name for char in "[]:*?/\\"):
            return None, "sheet_name is invalid"
        if (
            not export_header_pattern.fullmatch(control_id_header)
            or not isinstance(header_row, int)
            or isinstance(header_row, bool)
            or not 1 <= header_row <= 1000
        ):
            return None, "A valid control ID header and header row are required"
        if not isinstance(field_mapping, dict) or not field_mapping:
            return None, "column_map must contain at least one canonical field"
        normalized_mapping = {}
        for canonical, header in field_mapping.items():
            header = str(header or "").strip()
            if canonical not in export_canonical_fields:
                return None, f"Unsupported canonical field: {canonical}"
            if not export_header_pattern.fullmatch(header):
                return None, f"Invalid workbook header for {canonical}"
            normalized_mapping[canonical] = header
        return {
            "family_name": family_name,
            "profile": {
                "control_id_header": control_id_header,
                "column_map": normalized_mapping,
                "sheet": sheet_name,
                "header_row": header_row,
            },
            "inspected_sha256": inspected_sha256,
        }, None

    def _safe_download_filename(content_disposition, export_id, artifact):
        extension = "xlsx" if artifact == "workbook" else "json"
        fallback = f"rmf-export-{export_id}.{extension}"
        match = re.search(
            r"filename\*?=(?:UTF-8''|[\"']?)([^\"';\r\n]+)",
            str(content_disposition or ""),
            flags=re.IGNORECASE,
        )
        candidate = unquote(match.group(1)).strip() if match else fallback
        candidate = candidate.replace("\\", "/").split("/")[-1]
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,199}", candidate)
            or not candidate.lower().endswith(f".{extension}")
        ):
            return fallback
        return candidate

    def _sanitize_export_response(value):
        blocked_keys = {
            "container",
            "credential",
            "credentials",
            "download_url",
            "manifest_url",
            "path",
            "sas",
            "sas_token",
            "storage_url",
            "token",
            "uri",
            "url",
        }
        if isinstance(value, list):
            return [_sanitize_export_response(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {
            key: _sanitize_export_response(item)
            for key, item in value.items()
            if str(key).lower() not in blocked_keys
            and "blob" not in str(key).lower()
            and not str(key).lower().endswith(("_sas", "_token", "_url", "_uri"))
        }

    def _rmf_group_context(user_id, allowed_roles):
        try:
            group_id = require_active_group(user_id)
            role = assert_group_role(user_id, group_id, allowed_roles=allowed_roles)
        except ValueError:
            return None, None, None, (jsonify({"error": "No active group selected"}), 400)
        except LookupError:
            return None, None, None, (jsonify({"error": "Active group not found"}), 404)
        except PermissionError:
            return None, None, None, (jsonify({"error": "Access denied"}), 403)

        group_doc = find_group_by_id(group_id)
        if not get_group_rmf_state(group_doc)["enabled"]:
            return None, None, None, (
                jsonify({"error": "RMF is not enabled for this workspace"}),
                409,
            )
        return group_id, group_doc, role, None

    def _chat_group_context(user_id):
        return _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )

    def _chat_error_response(exc):
        return jsonify({"error": str(exc)}), exc.status_code

    @bp.route("/api/rmf/workspace", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace():
        user_id = get_current_user_id()
        try:
            group_id = require_active_group(user_id)
        except ValueError:
            return jsonify({"error": "No active group selected"}), 400
        except LookupError:
            return jsonify({"error": "Active group not found"}), 404
        except PermissionError:
            return jsonify({"error": "You are not a member of the active group"}), 403

        group_doc = find_group_by_id(group_id)
        role = get_user_role_in_group(group_doc, user_id)
        rmf_state = get_group_rmf_state(group_doc)
        service = None
        service_error = None
        if rmf_state["enabled"]:
            try:
                service = get_rmf_service_state(group_id, user_id, role)
            except RMFServiceError as exc:
                service_error = str(exc)
        return jsonify({
            "groupId": group_id,
            "groupName": group_doc.get("name", ""),
            "role": role,
            "rmf": rmf_state,
            "service": service,
            "serviceError": service_error,
        })

    @bp.route("/api/rmf/workspace/overview", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_overview():
        user_id = get_current_user_id()
        try:
            group_id = require_active_group(user_id)
        except ValueError:
            return jsonify({"error": "No active group selected"}), 400
        except LookupError:
            return jsonify({"error": "Active group not found"}), 404
        except PermissionError:
            return jsonify({"error": "You are not a member of the active group"}), 403

        group_doc = find_group_by_id(group_id)
        if not get_group_rmf_state(group_doc)["enabled"]:
            return jsonify({"error": "RMF is not enabled for this workspace"}), 409
        role = get_user_role_in_group(group_doc, user_id)
        try:
            overview = get_rmf_overview(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(overview), 200

    @bp.route("/api/rmf/workspace/evidence/capabilities", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_evidence_capabilities():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        try:
            capabilities = get_rmf_evidence_capabilities(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(capabilities), 200

    @bp.route("/api/rmf/workspace/evidence", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_evidence():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        try:
            evidence = get_rmf_evidence(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(evidence), 200

    @bp.route("/api/rmf/workspace/evidence", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def add_rmf_workspace_evidence():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=RMF_EVIDENCE_MANAGER_ROLES,
        )
        if error_response:
            return error_response

        payload = request.get_json(silent=True) or {}
        document_id = str(payload.get("document_id") or "").strip()
        classification = str(payload.get("classification") or "").strip()
        if not document_id or not classification:
            return jsonify({"error": "document_id and classification are required"}), 400

        user_info = get_current_user_info() or {}
        uploaded_by = (
            str(user_info.get("email") or user_info.get("displayName") or "").strip()
            or user_id
        )
        try:
            result = import_rmf_evidence(
                group_id,
                user_id,
                role,
                document_id,
                classification,
                uploaded_by,
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(result), 202

    @bp.route("/api/rmf/workspace/evidence/jobs/<job_id>", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_evidence_job(job_id):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        try:
            job = get_rmf_evidence_job(group_id, user_id, role, job_id)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(job), 200

    @bp.route("/api/rmf/workspace/analysis/capabilities", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_analysis_capabilities():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        try:
            capabilities = get_rmf_analysis_capabilities(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        baseline_controls = capabilities.get("baseline_controls", [])
        if isinstance(baseline_controls, list):
            record_rmf_analysis_baseline(
                workspace_id=group_id,
                baseline_control_count=len(baseline_controls),
            )
        return jsonify(capabilities), 200

    @bp.route("/api/rmf/workspace/analysis", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_analysis():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        try:
            analysis = get_rmf_analysis(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(analysis), 200

    @bp.route("/api/rmf/workspace/analysis", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def start_rmf_workspace_analysis():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=RMF_MANAGER_ROLES,
        )
        if error_response:
            return error_response

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        scope = payload.get("scope")
        control_ids = payload.get("control_ids", [])
        fresh = payload.get("fresh", False)
        deep = payload.get("deep", False)
        selection_metrics = payload.get("selection_metrics", {})
        if scope not in {"baseline", "selected"}:
            return jsonify({"error": "scope must be baseline or selected"}), 400
        if (
            not isinstance(control_ids, list)
            or any(
                not isinstance(control_id, str) or not control_id.strip()
                for control_id in control_ids
            )
        ):
            return jsonify({"error": "control_ids must be an array of non-empty strings"}), 400
        if not isinstance(fresh, bool) or not isinstance(deep, bool):
            return jsonify({"error": "fresh and deep must be booleans"}), 400
        if not isinstance(selection_metrics, dict):
            return jsonify({"error": "selection_metrics must be an object"}), 400
        if scope == "selected" and not control_ids:
            return jsonify({"error": "Select at least one control"}), 400
        if scope == "baseline" and control_ids:
            return jsonify({"error": "control_ids must be empty for baseline scope"}), 400

        request_payload = {
            "scope": scope,
            "control_ids": [control_id.strip() for control_id in control_ids],
            "fresh": fresh,
            "deep": deep,
        }
        def _record_selection_outcome(outcome):
            if scope != "selected":
                return
            record_rmf_analysis_selection(
                workspace_id=group_id,
                control_ids=request_payload["control_ids"],
                selection_duration_ms=selection_metrics.get("selection_duration_ms"),
                outcome=outcome,
            )

        try:
            job = start_rmf_analysis(group_id, user_id, role, request_payload)
        except RMFServiceError as exc:
            _record_selection_outcome(f"rmf_error_{exc.status_code}")
            return jsonify({"error": str(exc)}), exc.status_code
        _record_selection_outcome("submitted")
        return jsonify(job), 202

    @bp.route("/api/rmf/workspace/analysis/jobs/<job_id>", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_analysis_job(job_id):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        try:
            job = get_rmf_analysis_job(group_id, user_id, role, job_id)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(job), 200

    @bp.route("/api/rmf/workspace/analysis/jobs/<job_id>/cancel", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def cancel_rmf_workspace_analysis_job(job_id):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=RMF_MANAGER_ROLES,
        )
        if error_response:
            return error_response
        try:
            job = cancel_rmf_analysis_job(group_id, user_id, role, job_id)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(job), 200

    @bp.route("/api/rmf/workspace/chat/capabilities", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_chat_capabilities():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _chat_group_context(user_id)
        if error_response:
            return error_response
        try:
            capabilities = get_rmf_chat_capabilities(group_id, user_id, role)
        except RMFServiceError as exc:
            return _chat_error_response(exc)
        return jsonify(capabilities), 200

    @bp.route("/api/rmf/workspace/chat/sessions", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_chat_sessions():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _chat_group_context(user_id)
        if error_response:
            return error_response
        try:
            sessions = get_rmf_chat_sessions(group_id, user_id, role)
        except RMFServiceError as exc:
            return _chat_error_response(exc)
        return jsonify(sessions), 200

    @bp.route("/api/rmf/workspace/chat/sessions", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def create_rmf_workspace_chat_session():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _chat_group_context(user_id)
        if error_response:
            return error_response
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict) or set(payload) - {"title"}:
            return jsonify({"error": "Only an optional title is supported"}), 400
        title = payload.get("title")
        if title is not None and (
            not isinstance(title, str) or len(title.strip()) > 200
        ):
            return jsonify({"error": "title must be a string of at most 200 characters"}), 400
        request_payload = {}
        if isinstance(title, str) and title.strip():
            request_payload["title"] = title.strip()
        try:
            conversation = create_rmf_chat_session(
                group_id,
                user_id,
                role,
                request_payload,
            )
        except RMFServiceError as exc:
            return _chat_error_response(exc)
        return jsonify(conversation), 201

    @bp.route(
        "/api/rmf/workspace/chat/sessions/<conversation_id>",
        methods=["GET"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_chat_session(conversation_id):
        if not _valid_chat_id(conversation_id):
            return jsonify({"error": "A valid conversation ID is required"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _chat_group_context(user_id)
        if error_response:
            return error_response
        try:
            conversation = get_rmf_chat_session(
                group_id,
                user_id,
                role,
                conversation_id,
            )
        except RMFServiceError as exc:
            return _chat_error_response(exc)
        return jsonify(conversation), 200

    @bp.route(
        "/api/rmf/workspace/chat/sessions/<conversation_id>/messages",
        methods=["POST"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def send_rmf_workspace_chat_message(conversation_id):
        if not _valid_chat_id(conversation_id):
            return jsonify({"error": "A valid conversation ID is required"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _chat_group_context(user_id)
        if error_response:
            return error_response
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {
            "expected_revision",
            "question",
        }:
            return jsonify({
                "error": "expected_revision and question are required"
            }), 400
        expected_revision = payload.get("expected_revision")
        question = payload.get("question")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            return jsonify({"error": "expected_revision must be a non-negative integer"}), 400
        if not isinstance(question, str) or not question.strip():
            return jsonify({"error": "question must be a non-empty string"}), 400
        if len(question.strip()) > 10000:
            return jsonify({"error": "question must not exceed 10000 characters"}), 400
        try:
            conversation = send_rmf_chat_message(
                group_id,
                user_id,
                role,
                conversation_id,
                {
                    "expected_revision": expected_revision,
                    "question": question.strip(),
                },
            )
        except RMFServiceError as exc:
            return _chat_error_response(exc)
        return jsonify(conversation), 200

    @bp.route(
        "/api/rmf/workspace/chat/sessions/<conversation_id>/archive",
        methods=["POST"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def archive_rmf_workspace_chat_session(conversation_id):
        if not _valid_chat_id(conversation_id):
            return jsonify({"error": "A valid conversation ID is required"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _chat_group_context(user_id)
        if error_response:
            return error_response
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"expected_revision"}:
            return jsonify({"error": "expected_revision is required"}), 400
        expected_revision = payload.get("expected_revision")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            return jsonify({"error": "expected_revision must be a non-negative integer"}), 400
        try:
            conversation = archive_rmf_chat_session(
                group_id,
                user_id,
                role,
                conversation_id,
                {"expected_revision": expected_revision},
            )
        except RMFServiceError as exc:
            return _chat_error_response(exc)
        return jsonify(conversation), 200

    @bp.route(
        "/api/rmf/workspace/chat/sessions/<conversation_id>/sources/<source_id>",
        methods=["GET"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_chat_source(conversation_id, source_id):
        if not _valid_chat_id(conversation_id) or not _valid_chat_id(source_id):
            return jsonify({"error": "Valid conversation and source IDs are required"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _chat_group_context(user_id)
        if error_response:
            return error_response
        try:
            source = get_rmf_chat_source(
                group_id,
                user_id,
                role,
                conversation_id,
                source_id,
            )
        except RMFServiceError as exc:
            return _chat_error_response(exc)
        return jsonify(source), 200

    @bp.route("/api/rmf/workspace/controls", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_controls():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response

        query = request.args.get("query", "").strip()
        family = request.args.get("family", "").strip()
        state = request.args.get("state", "").strip()
        implementation_status = request.args.get("status", "").strip()
        has_gaps = request.args.get("has_gaps", "").strip().lower()
        if len(query) > 100 or len(family) > 100:
            return jsonify({"error": "Control filters are too long"}), 400
        if state and state not in {"current", "stale", "not-analyzed"}:
            return jsonify({"error": "Invalid control analysis state"}), 400
        if implementation_status and implementation_status not in {
            "implemented",
            "partially-implemented",
            "planned",
            "inherited",
            "not-applicable",
        }:
            return jsonify({"error": "Invalid control implementation status"}), 400
        if has_gaps and has_gaps not in {"true", "false"}:
            return jsonify({"error": "has_gaps must be true or false"}), 400
        try:
            page = int(request.args.get("page", "1"))
            page_size = int(request.args.get("page_size", "25"))
        except ValueError:
            return jsonify({"error": "page and page_size must be integers"}), 400
        if page < 1 or page_size < 1 or page_size > 100:
            return jsonify({"error": "Invalid control page bounds"}), 400

        params = {"page": page, "page_size": page_size}
        for key, value in (
            ("query", query),
            ("family", family),
            ("state", state),
            ("status", implementation_status),
            ("has_gaps", has_gaps),
        ):
            if value:
                params[key] = value
        try:
            controls = get_rmf_controls(group_id, user_id, role, params=params)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(controls), 200

    @bp.route("/api/rmf/workspace/controls/<control_id>", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_control(control_id):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        if not control_id.strip() or len(control_id) > 100:
            return jsonify({"error": "A valid control ID is required"}), 400
        try:
            control = get_rmf_control(group_id, user_id, role, control_id)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(control), 200

    @bp.route(
        "/api/rmf/workspace/controls/<control_id>/applicability",
        methods=["POST"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def set_rmf_workspace_control_applicability(control_id):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=RMF_MANAGER_ROLES,
        )
        if error_response:
            return error_response
        if not control_id.strip() or len(control_id) > 100:
            return jsonify({"error": "A valid control ID is required"}), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        action = payload.get("action")
        rationale = payload.get("rationale", "")
        candidate_fingerprint = payload.get("candidate_fingerprint")
        if action not in {"approve", "reject", "revoke"}:
            return jsonify({"error": "Invalid applicability action"}), 400
        if not isinstance(rationale, str) or len(rationale) > 2000:
            return jsonify({"error": "Applicability rationale is invalid"}), 400
        if action in {"approve", "reject"} and not rationale.strip():
            return jsonify({"error": "A rationale is required"}), 400
        if candidate_fingerprint is not None and not isinstance(
            candidate_fingerprint, str
        ):
            return jsonify({"error": "Candidate fingerprint is invalid"}), 400
        request_payload = {
            "action": action,
            "rationale": rationale.strip(),
        }
        if candidate_fingerprint:
            request_payload["candidate_fingerprint"] = candidate_fingerprint
        try:
            control = set_rmf_control_applicability(
                group_id,
                user_id,
                role,
                control_id,
                request_payload,
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(control), 200

    @bp.route("/api/rmf/workspace/attestations/capabilities", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_attestation_capabilities():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        try:
            result = get_rmf_attestation_capabilities(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(result), 200

    @bp.route("/api/rmf/workspace/attestations/sessions", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_attestation_sessions():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        try:
            result = get_rmf_attestation_sessions(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(result), 200

    @bp.route(
        "/api/rmf/workspace/attestations/sessions/<session_id>",
        methods=["GET"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_attestation_session(session_id):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        if not session_id.strip() or len(session_id) > 100:
            return jsonify({"error": "A valid attestation session ID is required"}), 400
        try:
            result = get_rmf_attestation_session(
                group_id, user_id, role, session_id
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(result), 200

    @bp.route("/api/rmf/workspace/attestations/sessions", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def create_rmf_workspace_attestation_session():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=RMF_MANAGER_ROLES,
        )
        if error_response:
            return error_response
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        scope = payload.get("scope")
        control_ids = payload.get("control_ids", [])
        all_controls = payload.get("all_controls", False)
        if scope not in {"baseline", "selected"}:
            return jsonify({"error": "scope must be baseline or selected"}), 400
        if (
            not isinstance(control_ids, list)
            or len(control_ids) > 500
            or any(
                not isinstance(control_id, str)
                or not control_id.strip()
                or len(control_id) > 100
                for control_id in control_ids
            )
        ):
            return jsonify({"error": "control_ids must be valid control IDs"}), 400
        if not isinstance(all_controls, bool):
            return jsonify({"error": "all_controls must be a boolean"}), 400
        if scope == "selected" and not control_ids:
            return jsonify({"error": "Select at least one control"}), 400
        if scope == "baseline" and control_ids:
            return jsonify({"error": "control_ids must be empty for baseline scope"}), 400
        request_payload = {
            "scope": scope,
            "control_ids": [control_id.strip().upper() for control_id in control_ids],
            "all_controls": all_controls,
        }
        try:
            result = create_rmf_attestation_session(
                group_id, user_id, role, request_payload
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(result), 201

    @bp.route(
        "/api/rmf/workspace/attestations/sessions/<session_id>/next",
        methods=["POST"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def advance_rmf_workspace_attestation_session(session_id):
        return _mutate_rmf_attestation_session(
            session_id,
            advance_rmf_attestation_session,
            require_answer=False,
        )

    @bp.route(
        "/api/rmf/workspace/attestations/sessions/<session_id>/answers",
        methods=["POST"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def answer_rmf_workspace_attestation_session(session_id):
        return _mutate_rmf_attestation_session(
            session_id,
            answer_rmf_attestation_session,
            require_answer=True,
        )

    def _mutate_rmf_attestation_session(session_id, operation, *, require_answer):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=("Owner", "Admin", "DocumentManager", "User"),
        )
        if error_response:
            return error_response
        if not session_id.strip() or len(session_id) > 100:
            return jsonify({"error": "A valid attestation session ID is required"}), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        revision = payload.get("revision")
        if not isinstance(revision, int) or revision < 0:
            return jsonify({"error": "revision must be a non-negative integer"}), 400
        request_payload = {"expected_revision": revision}
        if require_answer:
            turn_id = payload.get("turn_id")
            answer = payload.get("answer")
            if not isinstance(turn_id, str) or not turn_id.strip() or len(turn_id) > 100:
                return jsonify({"error": "turn_id is required"}), 400
            if (
                not isinstance(answer, str)
                or not answer.strip()
                or len(answer) > 10000
            ):
                return jsonify({"error": "answer must be between 1 and 10000 characters"}), 400
            request_payload.update({
                "turn_id": turn_id.strip(),
                "answer": answer,
            })
        try:
            result = operation(
                group_id,
                user_id,
                role,
                session_id,
                request_payload,
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(result), 200

    @bp.route(
        "/api/rmf/workspace/attestations/sessions/<session_id>/cancel",
        methods=["POST"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def cancel_rmf_workspace_attestation_session(session_id):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=RMF_MANAGER_ROLES,
        )
        if error_response:
            return error_response
        payload = request.get_json(silent=True)
        revision = payload.get("revision") if isinstance(payload, dict) else None
        if not isinstance(revision, int) or revision < 0:
            return jsonify({"error": "revision must be a non-negative integer"}), 400
        try:
            result = cancel_rmf_attestation_session(
                group_id,
                user_id,
                role,
                session_id,
                {"expected_revision": revision},
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(result), 200

    @bp.route("/api/rmf/workspace/export-templates", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_export_templates():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_EXPORT_MEMBER_ROLES
        )
        if error_response:
            return error_response
        try:
            result = get_rmf_export_templates(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 200

    @bp.route("/api/rmf/workspace/export-templates/inspect", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def inspect_rmf_workspace_export_template():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_MANAGER_ROLES
        )
        if error_response:
            return error_response
        if request.form:
            return jsonify({"error": "Inspection accepts only the template file"}), 400
        filename, content, upload_error = _read_export_template_upload()
        if upload_error:
            return upload_error
        try:
            result = inspect_rmf_export_template(
                group_id, user_id, role, filename, content
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 200

    @bp.route("/api/rmf/workspace/export-templates", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def create_rmf_workspace_export_template():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_MANAGER_ROLES
        )
        if error_response:
            return error_response
        if set(request.form) != {"metadata"}:
            return jsonify({"error": "Only file and metadata fields are accepted"}), 400
        filename, content, upload_error = _read_export_template_upload()
        if upload_error:
            return upload_error
        metadata, metadata_error = _validate_export_metadata(request.form.get("metadata"))
        if metadata_error:
            return jsonify({"error": metadata_error}), 400
        try:
            result = create_rmf_export_template(
                group_id, user_id, role, filename, content, metadata
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 201

    @bp.route("/api/rmf/workspace/export-templates/<template_id>", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_export_template(template_id):
        if not _valid_export_id(template_id):
            return jsonify({"error": "A valid template ID is required"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_EXPORT_MEMBER_ROLES
        )
        if error_response:
            return error_response
        try:
            result = get_rmf_export_template(group_id, user_id, role, template_id)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 200

    @bp.route(
        "/api/rmf/workspace/export-templates/<template_id>/<action>",
        methods=["POST"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def mutate_rmf_workspace_export_template(template_id, action):
        if not _valid_export_id(template_id) or action not in {"activate", "retire"}:
            return jsonify({"error": "Invalid template action"}), 400
        payload = request.get_json(silent=True)
        revision = payload.get("revision") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"revision"}
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
        ):
            return jsonify({"error": "A positive revision is required"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_MANAGER_ROLES
        )
        if error_response:
            return error_response
        try:
            template = get_rmf_export_template(
                group_id, user_id, role, template_id
            )
            if not isinstance(template, dict):
                raise RMFServiceError("The RMF service returned an invalid response.")
            current_status = str(template.get("status") or "").lower()
            required_status = "inactive" if action == "activate" else "active"
            if current_status != required_status:
                return jsonify({
                    "error": (
                        f"Only {required_status} template versions may be "
                        f"{action}d."
                    )
                }), 409
            result = mutate_rmf_export_template(
                group_id, user_id, role, template_id, action, revision
            )
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 200

    @bp.route("/api/rmf/workspace/exports/readiness", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_export_readiness():
        if set(request.args) != {"template_id"}:
            return jsonify({"error": "Unsupported readiness selector"}), 400
        template_id = str(request.args.get("template_id") or "").strip()
        if not _valid_export_id(template_id):
            return jsonify({"error": "A valid template ID is required"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_EXPORT_MEMBER_ROLES
        )
        if error_response:
            return error_response
        params = {"template_id": template_id}
        try:
            result = get_rmf_export_readiness(group_id, user_id, role, params)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 200

    @bp.route("/api/rmf/workspace/exports", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_exports():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_EXPORT_MEMBER_ROLES
        )
        if error_response:
            return error_response
        try:
            result = get_rmf_exports(group_id, user_id, role)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 200

    @bp.route("/api/rmf/workspace/exports", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def create_rmf_workspace_export():
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_EXPORT_MEMBER_ROLES
        )
        if error_response:
            return error_response
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        template_id = str(payload.get("template_id") or "").strip()
        if set(payload) - {"template_id", "idempotency_key", "download_name"}:
            return jsonify({"error": "Unsupported export fields"}), 400
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        download_name = str(payload.get("download_name") or "").strip()
        if not _valid_export_id(template_id) or not _valid_export_id(idempotency_key):
            return jsonify({
                "error": "A valid template and idempotency key are required"
            }), 400
        if download_name and (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,199}", download_name)
            or not download_name.lower().endswith(".xlsx")
        ):
            return jsonify({"error": "download_name must be a safe .xlsx filename"}), 400
        normalized = {"template_id": template_id}
        if download_name:
            normalized["download_name"] = download_name
        try:
            result = create_rmf_export(
                group_id, user_id, role, normalized, idempotency_key
            )
        except RMFServiceError as exc:
            if exc.status_code == 409:
                return jsonify({
                    "error": (
                        "This export request conflicts with an earlier request. "
                        "Refresh export history before retrying."
                    )
                }), 409
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 201

    @bp.route("/api/rmf/workspace/exports/<export_id>", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def get_rmf_workspace_export(export_id):
        if not _valid_export_id(export_id):
            return jsonify({"error": "A valid export ID is required"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_EXPORT_MEMBER_ROLES
        )
        if error_response:
            return error_response
        try:
            result = get_rmf_export(group_id, user_id, role, export_id)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(_sanitize_export_response(result)), 200

    @bp.route(
        "/api/rmf/workspace/exports/<export_id>/<artifact>",
        methods=["GET"],
    )
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def download_rmf_workspace_export(export_id, artifact):
        if not _valid_export_id(export_id) or artifact not in {"workbook", "manifest"}:
            return jsonify({"error": "Invalid export download"}), 400
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id, allowed_roles=RMF_EXPORT_MEMBER_ROLES
        )
        if error_response:
            return error_response
        try:
            export_record = get_rmf_export(group_id, user_id, role, export_id)
            if not isinstance(export_record, dict) or str(
                export_record.get("status") or ""
            ).lower() not in {
                "complete",
                "completed",
                "succeeded",
            }:
                return jsonify({
                    "error": "Export artifacts are available only after successful completion."
                }), 409
            upstream = download_rmf_export(group_id, user_id, role, export_id, artifact)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        filename = _safe_download_filename(
            upstream.headers.get("Content-Disposition"), export_id, artifact
        )
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if artifact == "workbook"
            else "application/json"
        )

        def stream():
            try:
                yield from upstream.iter_content(chunk_size=64 * 1024)
            finally:
                upstream.close()

        response = Response(stream_with_context(stream()), content_type=content_type)
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @bp.route("/api/rmf/workspace/evidence/<import_id>/withdraw", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def withdraw_rmf_workspace_evidence(import_id):
        user_id = get_current_user_id()
        group_id, _, role, error_response = _rmf_group_context(
            user_id,
            allowed_roles=RMF_EVIDENCE_MANAGER_ROLES,
        )
        if error_response:
            return error_response
        try:
            result = withdraw_rmf_evidence(group_id, user_id, role, import_id)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        return jsonify(result), 200

    @bp.route("/api/rmf/workspace", methods=["PATCH"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def update_rmf_workspace():
        user_id = get_current_user_id()
        try:
            group_id = require_active_group(user_id)
            assert_group_role(user_id, group_id, allowed_roles=RMF_MANAGER_ROLES)
        except ValueError:
            return jsonify({"error": "No active group selected"}), 400
        except LookupError:
            return jsonify({"error": "Active group not found"}), 404
        except PermissionError:
            return jsonify({"error": "Only group owners and admins can configure RMF"}), 403

        payload = request.get_json(silent=True) or {}
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return jsonify({"error": "enabled must be a boolean"}), 400

        user_info = get_current_user_info() or {}
        state = update_group_rmf_state(
            group_id,
            enabled,
            user_id,
            user_info.get("email", ""),
        )
        return jsonify({"groupId": group_id, "rmf": state}), 200

    @bp.route("/api/rmf/workspace/initialize", methods=["POST"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def initialize_rmf_workspace():
        user_id = get_current_user_id()
        try:
            group_id = require_active_group(user_id)
            assert_group_role(user_id, group_id, allowed_roles=RMF_MANAGER_ROLES)
        except ValueError:
            return jsonify({"error": "No active group selected"}), 400
        except LookupError:
            return jsonify({"error": "Active group not found"}), 404
        except PermissionError:
            return jsonify({"error": "Only group owners and admins can initialize RMF"}), 403

        payload = request.get_json(silent=True) or {}
        system_id = str(payload.get("system_id") or "").strip()
        system_name = str(payload.get("system_name") or "").strip()
        if not system_id or not system_name:
            return jsonify({"error": "system_id and system_name are required"}), 400

        group_doc = find_group_by_id(group_id)
        if not get_group_rmf_state(group_doc)["enabled"]:
            return jsonify({"error": "RMF is not enabled for this workspace"}), 409
        role = get_user_role_in_group(group_doc, user_id)
        try:
            service = initialize_rmf_service(group_id, user_id, role, payload)
        except RMFServiceError as exc:
            return jsonify({"error": str(exc)}), exc.status_code

        rmf_state = update_group_rmf_setup_status(group_id, service["setup_status"])
        return jsonify({"groupId": group_id, "rmf": rmf_state, "service": service}), 201
    download_rmf_export,
    get_rmf_export,
    get_rmf_export_readiness,
    get_rmf_export_template,
    get_rmf_export_templates,
    get_rmf_exports,
    inspect_rmf_export_template,
    mutate_rmf_export_template,
