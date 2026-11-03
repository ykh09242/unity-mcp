"""Authenticated Editor facade; the server still resolves principal and graph identity."""

from contextlib import asynccontextmanager
import time

from starlette.responses import JSONResponse

from core.config import config
from models.response_limits import MAX_RESPONSE_BYTES, response_size
from services.credential_lease import CredentialLeaseError, bind_credential_lease
from services.operation_permissions import OperationPermissionError, authorize_operation
from services.operation_policy import resolve_operation_policy
from services.reference_graph.store import RevisionConflict, guard_graph_edit
from services.reference_graph.response_budget import GraphResponseTooLarge
from services.registry.operation_invocation import (
    admission_for,
    operation_payload_charge,
    operation_session,
)
from services.registry.operation_scheduler import OperationScopeChanged
from services.registry.operation_target import capture_target
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


class EditorContext:
    def __init__(self, instance, expected):
        self.instance = instance
        self.reference_graph_expected_scope = expected

    async def get_state(self, key):
        return self.instance if key == "unity_instance" else None


@asynccontextmanager
async def _authorized_operation(instance, lease, action, options):
    if lease is None:
        yield
        return
    name = "manage_reference_graph"
    params = {"action": action, "options": options}
    await lease.check()
    authorize_operation(lease.permissions, name, params)
    expires_at = time.monotonic() + PluginHub._command_timeouts(name, params)[1]
    target = await capture_target(instance, user_id=lease.user_id)
    if target is None or target.authorize_public is None:
        raise RuntimeError("Selected Editor is unavailable")
    check_now = await target.authorize_public(name, params)
    check_now()
    retained = operation_payload_charge(params)
    if retained is None:
        raise ValueError("Graph request exceeds the operation payload limit")
    mode = resolve_operation_policy(name, params, trusted_builtin=True).mode
    # Nested status reads must run on the same socket generation as the public grant.
    async with operation_session(
        admission_for(target, mode, retained_bytes=retained, expires_at=expires_at)
    ):
        await target.authorize_public(name, params)
        try:
            with guard_graph_edit(check_now):
                yield
            await target.authorize_public(name, params)
            check_now()
        except RuntimeError:
            # Native dispatch can report a changed target as a transport refusal.
            # Keep the facade's scope/authorization error when its own fence was lost.
            check_now()
            raise


async def handle(request, service):
    lease = getattr(request.state, AUTHENTICATED_LEASE_STATE, None)
    if (config.http_remote_hosted and lease is None) or (
        not config.http_remote_hosted
        and not getattr(request.state, AUTHENTICATED_LOCAL_STATE, False)
    ):
        return JSONResponse({"success": False, "error": "authentication_required"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {
            "action",
            "options",
            "instance",
            "editor_id",
            "project_id",
        }:
            raise ValueError("Unsupported operations request fields")
        if any(
            not isinstance(body.get(key), str) or not body[key]
            for key in ("instance", "editor_id", "project_id")
        ):
            raise ValueError("Current Editor instance and project identity are required")
        if body.get("action") not in {
            "overview",
            "status",
            "retry",
            "export_config",
            "import_config",
            "cleanup",
            "watch",
            "query",
            "explain",
            "impact",
            "path",
            "release_query",
        }:
            raise ValueError("Unsupported operations action")
        ctx = EditorContext(body["instance"], (body["editor_id"], body["project_id"]))
        action, options = body["action"], body.get("options", {})
        with bind_credential_lease(lease):
            async with _authorized_operation(ctx.instance, lease, action, options):
                result = await service.execute(ctx, action, options)
        if response_size(result, max_bytes=MAX_RESPONSE_BYTES) is None:
            raise GraphResponseTooLarge()
        response = JSONResponse(result)
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise GraphResponseTooLarge()
        return response
    except (CredentialLeaseError, OperationPermissionError):
        return JSONResponse(
            {"success": False, "error": "authorization_unavailable"}, status_code=403
        )
    except GraphResponseTooLarge:
        return JSONResponse(
            {"success": False, "error": "reference_graph_response_too_large"}, status_code=413
        )
    except (RevisionConflict, OperationScopeChanged):
        return JSONResponse(
            {
                "success": False,
                "error": "reference_graph_changed",
                "message": "Scope or configuration changed; refresh before retrying.",
            },
            status_code=409,
        )
    except PermissionError:
        return JSONResponse(
            {"success": False, "error": "source_access_unavailable"}, status_code=403
        )
    except (ValueError, TypeError, KeyError):
        return JSONResponse(
            {
                "success": False,
                "error": "invalid_operations_request",
                "message": "Check action fields, destination version and explicit source reauthorization.",
            },
            status_code=400,
        )
    except (OSError, RuntimeError):
        return JSONResponse(
            {
                "success": False,
                "error": "reference_graph_unavailable",
                "message": "Check the Editor connection and server diagnostics, then refresh.",
            },
            status_code=503,
        )
