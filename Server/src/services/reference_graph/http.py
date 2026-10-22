"""Authenticated Editor facade; the server still resolves principal and graph identity."""

from starlette.responses import JSONResponse

from core.config import config
from services.credential_lease import CredentialLeaseError, bind_credential_lease
from services.operation_permissions import OperationPermissionError
from services.reference_graph.store import RevisionConflict
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


class EditorContext:
    def __init__(self, instance, expected):
        self.instance = instance
        self.reference_graph_expected_scope = expected

    async def get_state(self, key):
        return self.instance if key == "unity_instance" else None


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
        }:
            raise ValueError("Unsupported operations action")
        ctx = EditorContext(body["instance"], (body["editor_id"], body["project_id"]))
        with bind_credential_lease(lease):
            if lease is not None:
                await lease.check()
            result = await service.execute(ctx, body["action"], body.get("options", {}))
            if lease is not None:
                await lease.check()
        return JSONResponse(result)
    except (CredentialLeaseError, OperationPermissionError):
        return JSONResponse(
            {"success": False, "error": "authorization_unavailable"}, status_code=403
        )
    except RevisionConflict:
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
