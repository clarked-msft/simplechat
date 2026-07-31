# functions_rmf.py

import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree

import requests
from requests_toolbelt import MultipartEncoder

from config import cosmos_groups_container
from config import (
    RMF_API_BASE_URL,
    RMF_API_CHAT_TIMEOUT_SECONDS,
    RMF_API_KEY_SECRET_NAME,
    RMF_API_TIMEOUT_SECONDS,
    RMF_API_UPLOAD_TIMEOUT_SECONDS,
)
from functions_activity_logging import log_general_admin_action
from functions_appinsights import log_event
from functions_chat_bootstrap_cache import bump_chat_bootstrap_global_cache_version
from functions_documents import (
    ensure_document_revision_blob,
    get_document_record,
    get_document_versions,
)
from functions_group import find_group_by_id
from functions_settings import get_settings
from functions_simplechat_operations import download_blob_to_file


RMF_MANAGER_ROLES = ("Owner", "Admin")
RMF_EVIDENCE_MANAGER_ROLES = ("Owner", "Admin", "DocumentManager")
RMF_EXPORT_MEMBER_ROLES = ("Owner", "Admin", "DocumentManager", "User")
RMF_ROLE_MAP = {
    "Owner": "manager",
    "Admin": "manager",
    "DocumentManager": "evidence-manager",
    "User": "viewer",
}
_RMF_SERVICE_KEY_TTL_SECONDS = 300
_rmf_service_key_cache = {"value": None, "expires_at": 0.0}
_rmf_service_key_lock = threading.Lock()
_XLSX_MAX_ENTRIES = 2000
_XLSX_MAX_COMPRESSED_BYTES = 20 * 1024 * 1024
_XLSX_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_XLSX_MAX_ENTRY_BYTES = 50 * 1024 * 1024
_XLSX_MAX_SHEETS = 100
_XLSX_MAX_ROWS = 1_048_576
_XLSX_MAX_COLUMNS = 16_384


class RMFServiceError(RuntimeError):
    """A safe-to-display failure returned by the RMF service."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def _xlsx_column_number(cell_reference):
    match = re.match(r"^([A-Za-z]+)", str(cell_reference or ""))
    if not match:
        return 0
    number = 0
    for character in match.group(1).upper():
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def validate_rmf_xlsx_package(content):
    """Preflight an XLSX ZIP without interpreting workbook values."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            entries = package.infolist()
            if not entries or len(entries) > _XLSX_MAX_ENTRIES:
                raise ValueError("The workbook ZIP entry count is invalid.")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise ValueError("The workbook contains duplicate ZIP entries.")
            lowered_names = {name.lower() for name in names}
            required = {"[content_types].xml", "xl/workbook.xml"}
            if not required.issubset(lowered_names):
                raise ValueError("The file is not a complete XLSX workbook.")
            if any(
                name.startswith("/")
                or "\\" in name
                or ".." in name.split("/")
                or name.lower().endswith("vbaproject.bin")
                or name.lower().startswith("xl/externallinks/")
                for name in names
            ):
                raise ValueError("Macros, external links, and unsafe ZIP paths are not allowed.")

            total_uncompressed = 0
            total_compressed = 0
            worksheet_entries = []
            for entry in entries:
                if entry.flag_bits & 0x1:
                    raise ValueError("Encrypted workbook entries are not allowed.")
                if entry.file_size > _XLSX_MAX_ENTRY_BYTES:
                    raise ValueError("A workbook ZIP entry is too large.")
                total_compressed += entry.compress_size
                total_uncompressed += entry.file_size
                if total_compressed > _XLSX_MAX_COMPRESSED_BYTES:
                    raise ValueError("The workbook ZIP data exceeds the compressed size limit.")
                if total_uncompressed > _XLSX_MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("The workbook expands beyond the allowed size.")
                if (
                    entry.compress_size
                    and entry.file_size > 1024 * 1024
                    and entry.file_size / entry.compress_size > 100
                ):
                    raise ValueError("The workbook ZIP compression ratio is unsafe.")
                lowered = entry.filename.lower()
                if lowered.startswith("xl/worksheets/") and lowered.endswith(".xml"):
                    worksheet_entries.append(entry)
                if lowered.endswith(".rels"):
                    relationships = ElementTree.fromstring(package.read(entry))
                    if any(
                        str(attribute).rsplit("}", 1)[-1].lower() == "targetmode"
                        and str(value).lower() == "external"
                        for relationship in relationships.iter()
                        for attribute, value in relationship.attrib.items()
                    ):
                        raise ValueError("External workbook relationships are not allowed.")

            if not worksheet_entries or len(worksheet_entries) > _XLSX_MAX_SHEETS:
                raise ValueError("The workbook worksheet count is invalid.")
            for worksheet in worksheet_entries:
                row_count = 0
                for _, element in ElementTree.iterparse(package.open(worksheet), events=("end",)):
                    tag = element.tag.rsplit("}", 1)[-1]
                    if tag == "row":
                        row_count += 1
                        row_number = int(element.attrib.get("r") or row_count)
                        if row_number > _XLSX_MAX_ROWS:
                            raise ValueError("A worksheet exceeds the row limit.")
                        element.clear()
                    elif tag == "c":
                        if _xlsx_column_number(element.attrib.get("r")) > _XLSX_MAX_COLUMNS:
                            raise ValueError("A worksheet exceeds the column limit.")
                        element.clear()
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, OSError) as exc:
        raise ValueError("The uploaded file is not a valid XLSX package.") from exc


def get_group_rmf_state(group_doc):
    """Return a normalized, public RMF state for a group document."""
    state = group_doc.get("rmf") if isinstance(group_doc, dict) else None
    state = state if isinstance(state, dict) else {}
    return {
        "enabled": bool(state.get("enabled", False)),
        "setupStatus": str(state.get("setupStatus") or "not-configured"),
    }


def _get_rmf_service_key():
    if not RMF_API_KEY_SECRET_NAME:
        raise RMFServiceError("The RMF service credential is not configured.", 503)
    now = time.monotonic()
    with _rmf_service_key_lock:
        if (
            _rmf_service_key_cache["value"]
            and _rmf_service_key_cache["expires_at"] > now
        ):
            return _rmf_service_key_cache["value"]
    from functions_keyvault import retrieve_secret_direct

    try:
        value = retrieve_secret_direct(RMF_API_KEY_SECRET_NAME, settings=get_settings())
        with _rmf_service_key_lock:
            _rmf_service_key_cache["value"] = value
            _rmf_service_key_cache["expires_at"] = now + _RMF_SERVICE_KEY_TTL_SECONDS
        return value
    except Exception as exc:
        log_event(
            "Unable to resolve the RMF service credential.",
            extra={"error": str(exc)},
            level=logging.ERROR,
        )
        raise RMFServiceError("The RMF service credential is unavailable.", 503) from exc


def _invalidate_rmf_service_key():
    with _rmf_service_key_lock:
        _rmf_service_key_cache["value"] = None
        _rmf_service_key_cache["expires_at"] = 0.0


def _rmf_headers(group_id, user_id, group_role):
    rmf_role = RMF_ROLE_MAP.get(group_role)
    if not rmf_role:
        raise RMFServiceError("Your workspace role cannot access RMF.", 403)
    return {
        "X-RMF-Service-Key": _get_rmf_service_key(),
        "X-RMF-Workspace-ID": group_id,
        "X-RMF-User-ID": user_id,
        "X-RMF-Role": rmf_role,
    }


def _rmf_request(
    method,
    path,
    group_id,
    user_id,
    group_role,
    payload=None,
    files=None,
    data=None,
    timeout=None,
    extra_headers=None,
    params=None,
    retry_auth=True,
    timeout_error_message=None,
):
    if not RMF_API_BASE_URL:
        raise RMFServiceError("The RMF service endpoint is not configured.", 503)
    effective_timeout = (
        timeout if timeout is not None else RMF_API_TIMEOUT_SECONDS
    )
    try:
        headers = _rmf_headers(group_id, user_id, group_role)
        headers.update(extra_headers or {})
        response = requests.request(
            method,
            f"{RMF_API_BASE_URL}{path}",
            headers=headers,
            json=payload,
            files=files,
            data=data,
            params=params,
            timeout=effective_timeout,
            allow_redirects=False,
        )
        if response.status_code in {401, 403}:
            _invalidate_rmf_service_key()
            if retry_auth and not isinstance(data, MultipartEncoder):
                return _rmf_request(
                    method,
                    path,
                    group_id,
                    user_id,
                    group_role,
                    payload=payload,
                    files=files,
                    data=data,
                    timeout=timeout,
                    extra_headers=extra_headers,
                    params=params,
                    retry_auth=False,
                    timeout_error_message=timeout_error_message,
                )
    except requests.Timeout as exc:
        log_event(
            "RMF service request exceeded its proxy timeout.",
            extra={"path": path, "timeout_seconds": effective_timeout},
            level=logging.WARNING,
        )
        raise RMFServiceError(
            timeout_error_message
            or "The RMF service request exceeded its proxy timeout.",
            504,
        ) from exc
    except requests.RequestException as exc:
        log_event(
            "RMF service request failed.",
            extra={"path": path, "error": str(exc)},
            level=logging.ERROR,
        )
        raise RMFServiceError("The RMF service is unavailable.") from exc

    if response.ok:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise RMFServiceError("The RMF service returned an invalid response.") from exc

    message = "The RMF service rejected the request."
    try:
        body = response.json()
        message = body.get("detail") or body.get("error") or message
    except ValueError:
        pass
    log_event(
        "RMF service returned an error.",
        extra={"path": path, "status_code": response.status_code},
        level=logging.WARNING,
    )
    raise RMFServiceError(message, response.status_code)


def get_rmf_service_state(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_overview(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/overview",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_evidence_capabilities(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/evidence/capabilities",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_evidence(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/evidence",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_evidence_job(group_id, user_id, group_role, job_id):
    return _rmf_request(
        "GET",
        f"/api/v1/workspaces/current/evidence/jobs/{job_id}",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_analysis_capabilities(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/analysis/capabilities",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_analysis(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/analysis",
        group_id,
        user_id,
        group_role,
    )


def start_rmf_analysis(group_id, user_id, group_role, payload):
    return _rmf_request(
        "POST",
        "/api/v1/workspaces/current/analysis",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def get_rmf_analysis_job(group_id, user_id, group_role, job_id):
    return _rmf_request(
        "GET",
        f"/api/v1/workspaces/current/analysis/jobs/{job_id}",
        group_id,
        user_id,
        group_role,
    )


def cancel_rmf_analysis_job(group_id, user_id, group_role, job_id):
    return _rmf_request(
        "POST",
        f"/api/v1/workspaces/current/analysis/jobs/{job_id}/cancel",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_chat_capabilities(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/chat/capabilities",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_chat_sessions(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/chat/sessions",
        group_id,
        user_id,
        group_role,
    )


def create_rmf_chat_session(group_id, user_id, group_role, payload):
    return _rmf_request(
        "POST",
        "/api/v1/workspaces/current/chat/sessions",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def get_rmf_chat_session(group_id, user_id, group_role, conversation_id):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/chat/sessions/"
        f"{quote(conversation_id, safe='')}",
        group_id,
        user_id,
        group_role,
    )


def send_rmf_chat_message(
    group_id,
    user_id,
    group_role,
    conversation_id,
    payload,
):
    return _rmf_request(
        "POST",
        "/api/v1/workspaces/current/chat/sessions/"
        f"{quote(conversation_id, safe='')}/messages",
        group_id,
        user_id,
        group_role,
        payload=payload,
        timeout=RMF_API_CHAT_TIMEOUT_SECONDS,
        timeout_error_message=(
            "RMF chat generation exceeded the proxy timeout. "
            "The question may be retried with the same idempotency key."
        ),
    )


def archive_rmf_chat_session(
    group_id,
    user_id,
    group_role,
    conversation_id,
    payload,
):
    return _rmf_request(
        "POST",
        "/api/v1/workspaces/current/chat/sessions/"
        f"{quote(conversation_id, safe='')}/archive",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def get_rmf_chat_source(
    group_id,
    user_id,
    group_role,
    conversation_id,
    source_id,
):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/chat/sessions/"
        f"{quote(conversation_id, safe='')}/sources/{quote(source_id, safe='')}",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_controls(group_id, user_id, group_role, params=None):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/controls",
        group_id,
        user_id,
        group_role,
        params=params,
    )


def get_rmf_control(group_id, user_id, group_role, control_id):
    return _rmf_request(
        "GET",
        f"/api/v1/workspaces/current/controls/{quote(control_id, safe='')}",
        group_id,
        user_id,
        group_role,
    )


def set_rmf_control_applicability(
    group_id,
    user_id,
    group_role,
    control_id,
    payload,
):
    return _rmf_request(
        "POST",
        f"/api/v1/workspaces/current/controls/{quote(control_id, safe='')}/applicability",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def get_rmf_attestation_capabilities(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/attestations/capabilities",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_attestation_sessions(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/attestations/sessions",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_attestation_session(group_id, user_id, group_role, session_id):
    return _rmf_request(
        "GET",
        f"/api/v1/workspaces/current/attestations/sessions/{quote(session_id, safe='')}",
        group_id,
        user_id,
        group_role,
    )


def create_rmf_attestation_session(group_id, user_id, group_role, payload):
    return _rmf_request(
        "POST",
        "/api/v1/workspaces/current/attestations/sessions",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def advance_rmf_attestation_session(
    group_id,
    user_id,
    group_role,
    session_id,
    payload,
):
    return _rmf_request(
        "POST",
        f"/api/v1/workspaces/current/attestations/sessions/"
        f"{quote(session_id, safe='')}/next",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def answer_rmf_attestation_session(
    group_id,
    user_id,
    group_role,
    session_id,
    payload,
):
    return _rmf_request(
        "POST",
        f"/api/v1/workspaces/current/attestations/sessions/"
        f"{quote(session_id, safe='')}/answers",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def cancel_rmf_attestation_session(
    group_id,
    user_id,
    group_role,
    session_id,
    payload,
):
    return _rmf_request(
        "POST",
        f"/api/v1/workspaces/current/attestations/sessions/"
        f"{quote(session_id, safe='')}/cancel",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def get_rmf_export_templates(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/export-templates",
        group_id,
        user_id,
        group_role,
    )


def get_rmf_export_template(group_id, user_id, group_role, template_id):
    return _rmf_request(
        "GET",
        f"/api/v1/workspaces/current/export-templates/{quote(template_id, safe='')}",
        group_id,
        user_id,
        group_role,
    )


def _send_rmf_template_multipart(
    group_id,
    user_id,
    group_role,
    path,
    filename,
    content,
    metadata=None,
):
    fields = {"file": (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    if metadata is not None:
        fields["metadata"] = json.dumps(metadata)
    for attempt in range(2):
        encoder = MultipartEncoder(fields=fields)
        try:
            return _rmf_request(
                "POST",
                path,
                group_id,
                user_id,
                group_role,
                data=encoder,
                timeout=RMF_API_UPLOAD_TIMEOUT_SECONDS,
                extra_headers={"Content-Type": encoder.content_type},
            )
        except RMFServiceError as exc:
            if attempt or exc.status_code not in {401, 403}:
                raise


def inspect_rmf_export_template(group_id, user_id, group_role, filename, content):
    return _send_rmf_template_multipart(
        group_id,
        user_id,
        group_role,
        "/api/v1/workspaces/current/export-templates/inspect",
        filename,
        content,
    )


def create_rmf_export_template(
    group_id,
    user_id,
    group_role,
    filename,
    content,
    metadata,
):
    return _send_rmf_template_multipart(
        group_id,
        user_id,
        group_role,
        "/api/v1/workspaces/current/export-templates",
        filename,
        content,
        metadata,
    )


def mutate_rmf_export_template(
    group_id,
    user_id,
    group_role,
    template_id,
    action,
    revision,
):
    return _rmf_request(
        "POST",
        f"/api/v1/workspaces/current/export-templates/"
        f"{quote(template_id, safe='')}/{action}",
        group_id,
        user_id,
        group_role,
        payload={"revision": revision},
    )


def get_rmf_export_readiness(group_id, user_id, group_role, params):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/exports/readiness",
        group_id,
        user_id,
        group_role,
        params=params,
    )


def get_rmf_exports(group_id, user_id, group_role):
    return _rmf_request(
        "GET",
        "/api/v1/workspaces/current/exports",
        group_id,
        user_id,
        group_role,
    )


def create_rmf_export(group_id, user_id, group_role, payload, idempotency_key):
    return _rmf_request(
        "POST",
        "/api/v1/workspaces/current/exports",
        group_id,
        user_id,
        group_role,
        payload=payload,
        extra_headers={"Idempotency-Key": idempotency_key},
    )


def get_rmf_export(group_id, user_id, group_role, export_id):
    return _rmf_request(
        "GET",
        f"/api/v1/workspaces/current/exports/{quote(export_id, safe='')}",
        group_id,
        user_id,
        group_role,
    )


def download_rmf_export(group_id, user_id, group_role, export_id, artifact):
    if artifact not in {"workbook", "manifest"}:
        raise RMFServiceError("Invalid export artifact.", 400)
    path = (
        f"/api/v1/workspaces/current/exports/{quote(export_id, safe='')}/"
        f"{artifact}"
    )
    if not RMF_API_BASE_URL:
        raise RMFServiceError("The RMF service endpoint is not configured.", 503)
    for attempt in range(2):
        try:
            response = requests.get(
                f"{RMF_API_BASE_URL}{path}",
                headers=_rmf_headers(group_id, user_id, group_role),
                timeout=RMF_API_UPLOAD_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise RMFServiceError("The RMF service is unavailable.") from exc
        if response.status_code in {401, 403} and attempt == 0:
            response.close()
            _invalidate_rmf_service_key()
            continue
        if response.ok:
            return response
        message = "The RMF service rejected the download."
        try:
            body = response.json()
            message = body.get("detail") or body.get("error") or message
        except ValueError:
            pass
        status_code = response.status_code
        response.close()
        raise RMFServiceError(message, status_code)


def withdraw_rmf_evidence(group_id, user_id, group_role, import_id):
    return _rmf_request(
        "POST",
        f"/api/v1/workspaces/current/evidence/{import_id}/withdraw",
        group_id,
        user_id,
        group_role,
    )


def import_rmf_evidence(group_id, user_id, group_role, document_id, classification, uploaded_by):
    classification_manifest = {
        "observed-evidence": {"source_type": "evidence"},
        "organization-policy": {"source_type": "policy"},
        "azure-resource-json": {"source_type": "evidence", "extract_topology": True},
    }
    classification_fields = classification_manifest.get(classification)
    if not classification_fields:
        raise RMFServiceError("A valid RMF evidence classification is required.", 400)

    document = get_document_record(
        user_id=user_id,
        document_id=document_id,
        group_id=group_id,
    )
    if not document:
        raise RMFServiceError("Document not found or access denied.", 404)

    versions = get_document_versions(
        user_id=user_id,
        document_id=document_id,
        group_id=group_id,
    )
    current_version = next(
        (version for version in versions if version.get("is_current_version")),
        None,
    )
    if not current_version:
        raise RMFServiceError("The current document revision could not be verified.", 409)
    if current_version.get("id") != document_id:
        raise RMFServiceError(
            "This document revision is no longer current. Refresh and resync the latest revision.",
            409,
        )

    original_name = str(
        document.get("file_name") or document.get("title") or document_id
    ).replace("\\", "/").split("/")[-1] or document_id
    if classification == "azure-resource-json" and not original_name.lower().endswith(".json"):
        raise RMFServiceError("Azure Resource JSON requires a .json document.", 400)

    blob_container, blob_path = ensure_document_revision_blob(
        document,
        user_id=user_id,
        group_id=group_id,
    )
    if not blob_container or not blob_path:
        raise RMFServiceError("The document source file is unavailable.", 404)
    latest_versions = get_document_versions(
        user_id=user_id,
        document_id=document_id,
        group_id=group_id,
    )
    latest_current = next(
        (version for version in latest_versions if version.get("is_current_version")),
        None,
    )
    if not latest_current or latest_current.get("id") != document_id:
        raise RMFServiceError(
            "This document revision changed while it was being prepared. Refresh and try again.",
            409,
        )
    content_type = (
        str(document.get("content_type") or document.get("mime_type") or "").strip()
        or mimetypes.guess_type(original_name)[0]
        or "application/octet-stream"
    )
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            temp_path = temporary.name
        download_blob_to_file(blob_container, blob_path, temp_path)
        digest = hashlib.sha256()
        with open(temp_path, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        manifest = {
            **classification_fields,
            "source_document_id": document_id,
            "revision_family_id": (
                document.get("revision_family_id")
                or current_version.get("revision_family_id")
                or document_id
            ),
            "version": document.get("version") or current_version.get("version"),
            "original_name": original_name,
            "content_type": content_type,
            "sha256": digest.hexdigest(),
            "uploaded_by": uploaded_by,
        }
        for attempt in range(2):
            with open(temp_path, "rb") as stream:
                encoder = MultipartEncoder(
                    fields={
                        "file": (original_name, stream, content_type),
                        "manifest": json.dumps(manifest),
                    }
                )
                try:
                    return _rmf_request(
                        "POST",
                        "/api/v1/workspaces/current/evidence",
                        group_id,
                        user_id,
                        group_role,
                        data=encoder,
                        timeout=RMF_API_UPLOAD_TIMEOUT_SECONDS,
                        extra_headers={"Content-Type": encoder.content_type},
                    )
                except RMFServiceError as exc:
                    if attempt or exc.status_code not in {401, 403}:
                        raise
    except FileNotFoundError as exc:
        raise RMFServiceError("The document source file was not found.", 404) from exc
    except RMFServiceError:
        raise
    except Exception as exc:
        log_event(
            "Unable to stream a document for RMF import.",
            extra={"document_id": document_id, "group_id": group_id, "error": str(exc)},
            level=logging.ERROR,
        )
        raise RMFServiceError("The document source file is unavailable.") from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def initialize_rmf_service(group_id, user_id, group_role, payload):
    return _rmf_request(
        "POST",
        "/api/v1/workspaces/current/initialize",
        group_id,
        user_id,
        group_role,
        payload=payload,
    )


def start_rmf_collect(group_id, user_id, group_role, subscription_id, resource_group, agentic):
    return _rmf_request(
        "POST",
        "/api/v1/workspaces/current/collect",
        group_id,
        user_id,
        group_role,
        payload={
            "subscription_id": subscription_id,
            "resource_group": resource_group or None,
            "agentic": bool(agentic),
        },
    )


def get_rmf_collect_job(group_id, user_id, group_role, job_id):
    return _rmf_request(
        "GET",
        f"/api/v1/workspaces/current/collect/jobs/{job_id}",
        group_id,
        user_id,
        group_role,
    )


def update_group_rmf_setup_status(group_id, setup_status):
    group_doc = find_group_by_id(group_id)
    if not group_doc:
        raise LookupError("Group not found")
    existing = group_doc.get("rmf")
    state = dict(existing) if isinstance(existing, dict) else {}
    state["enabled"] = bool(state.get("enabled", False))
    state["setupStatus"] = setup_status
    modified_date = datetime.utcnow().isoformat()
    cosmos_groups_container.patch_item(
        item=group_id,
        partition_key=group_id,
        patch_operations=[
            {"op": "set", "path": "/rmf/setupStatus", "value": setup_status},
            {"op": "set", "path": "/modifiedDate", "value": modified_date},
        ],
    )
    bump_chat_bootstrap_global_cache_version(reason="group_rmf_setup_status_updated")
    return get_group_rmf_state(find_group_by_id(group_id) or {"rmf": state})


def update_group_rmf_state(group_id, enabled, changed_by, changed_by_email):
    """Persist the per-workspace RMF gate without configuring the RMF service."""
    group_doc = find_group_by_id(group_id)
    if not group_doc:
        raise LookupError("Group not found")

    current = get_group_rmf_state(group_doc)
    group_doc["rmf"] = {
        **current,
        "enabled": bool(enabled),
        "setupStatus": "service-pending" if enabled else "not-configured",
        "modifiedBy": changed_by,
        "modifiedAt": datetime.utcnow().isoformat(),
    }
    modified_date = datetime.utcnow().isoformat()
    cosmos_groups_container.patch_item(
        item=group_id,
        partition_key=group_id,
        patch_operations=[
            {"op": "set", "path": "/rmf", "value": group_doc["rmf"]},
            {"op": "set", "path": "/modifiedDate", "value": modified_date},
        ],
    )
    bump_chat_bootstrap_global_cache_version(reason="group_rmf_state_updated")
    log_general_admin_action(
        admin_user_id=changed_by,
        admin_email=changed_by_email,
        action="group_rmf_state_updated",
        description="Updated the RMF enablement state for a group workspace.",
        additional_context={
            "group_id": group_id,
            "enabled": bool(enabled),
        },
    )
    log_event(
        f"RMF workspace state updated for group {group_id}",
        extra={"group_id": group_id, "enabled": bool(enabled)},
        level=logging.INFO,
    )
    return get_group_rmf_state(group_doc)
