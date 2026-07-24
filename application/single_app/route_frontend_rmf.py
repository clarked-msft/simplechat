# route_frontend_rmf.py

from flask import render_template

from functions_authentication import get_current_user_id, login_required, user_required
from functions_group import (
    find_group_by_id,
    get_user_role_in_group,
    require_active_group,
)
from functions_rmf import (
    RMF_EVIDENCE_MANAGER_ROLES,
    RMF_MANAGER_ROLES,
    get_group_rmf_state,
)
from functions_settings import enabled_required, get_settings, sanitize_settings_for_user
from swagger_wrapper import get_auth_security, swagger_route


def register_route_frontend_rmf(bp):
    @bp.route("/rmf", methods=["GET"])
    @swagger_route(security=get_auth_security())
    @login_required
    @user_required
    @enabled_required("enable_group_workspaces")
    @enabled_required("enable_rmf")
    def rmf_workspace():
        """Render the RMF shell for the user's active group workspace."""
        user_id = get_current_user_id()
        try:
            group_id = require_active_group(user_id)
        except ValueError:
            return "No active group selected", 400
        except LookupError:
            return "Active group not found", 404
        except PermissionError:
            return "You are not a member of the active group", 403

        group_doc = find_group_by_id(group_id)
        role = get_user_role_in_group(group_doc, user_id)
        settings = sanitize_settings_for_user(get_settings())
        return render_template(
            "rmf_workspace.html",
            settings=settings,
            group=group_doc,
            group_role=role,
            rmf_state=get_group_rmf_state(group_doc),
            can_manage_rmf=role in RMF_MANAGER_ROLES,
            can_manage_rmf_analysis=role in RMF_MANAGER_ROLES,
            can_manage_rmf_evidence=role in RMF_EVIDENCE_MANAGER_ROLES,
        )
