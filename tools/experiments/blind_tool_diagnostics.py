"""Project search ranks and attribute discovery failures to observed stages."""

import re


def _identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[\w.-]{1,128}", value, re.ASCII))


def _catalog_uri(uri):
    graph = "mcpforunity://shader/graph/{encoded_path}"
    return graph if uri == graph + "{?page_size,page_number}" else uri


def _route(row):
    if not isinstance(row, dict):
        return None
    if row.get("kind") == "tool":
        name, operation = row.get("tool") or row.get("name"), row.get("operation")
        if _identifier(name) and (operation is None or _identifier(operation)):
            return f"{name}.{operation}" if operation else name
    uri = _catalog_uri(row.get("uri") or row.get("uri_template"))
    if isinstance(uri, str) and re.fullmatch(r"mcpforunity://[a-z0-9_/{\}-]{1,150}", uri):
        return uri
    return None


def search_receipt(data):
    """Store at most three ranks and documented route identifiers, never the user's query."""
    routes = data.get("routes")
    if not isinstance(routes, list):
        return {}
    groups = data.get("route_groups")
    if isinstance(groups, list):
        ranked = [
            [
                item
                for row in [group.get("representative"), *group.get("alternatives", [])]
                if (item := _route(row))
            ]
            for group in groups[:3]
            if isinstance(group, dict)
        ]
    else:
        ranked = [[name] if (name := _route(row)) else [] for row in routes[:3]]
    count = data.get("total_route_groups")
    return {
        "matching": "observation" if data.get("matching") == "observation" else "lexical",
        **(
            {"observation_source": data["observation_source"]}
            if data.get("observation_source") in {"editor_live", "asset_file", "imported_asset"}
            else {}
        ),
        **(
            {"required_values": [v for v in data["required_values"][:32] if _identifier(v)]}
            if isinstance(data.get("required_values"), list)
            else {}
        ),
        "grouped": isinstance(groups, list),
        "top3": ranked,
        "distinct_groups": count if type(count) is int and count >= 0 else None,
    }


def advertised(surface, route):
    """Return unknown when inventory, action declarations or resource templates are missing."""
    if not surface or surface.get("status") != "observed":
        return None
    unity = surface.get("unity", {})
    if route.startswith("mcpforunity://"):
        if not all(isinstance(unity.get(field), list) for field in ("resources", "templates")):
            return None
        return route in {_catalog_uri(uri) for uri in [*unity["resources"], *unity["templates"]]}
    if not isinstance(unity.get("tools"), list):
        return None
    name, separator, operation = route.partition(".")
    if name not in unity.get("tools", []):
        return False
    if not separator:
        return True
    choices = unity.get("actions", {}).get(name)
    return operation in choices if isinstance(choices, list) else None


def stage_diagnosis(routes, surface, business, searches, *, role_confirmed, grade):
    roster = [advertised(surface, route) for route in routes]
    available = (
        True
        if any(value is True for value in roster)
        else (False if roster and all(value is False for value in roster) else None)
    )
    relevant = [call for call in business if call.route in routes]
    first = next((call for call in searches if call.route == "manage_tools.search"), None)
    result = {
        "advertised": available,
        "first_search": first.evidence if first and first.evidence else None,
    }
    if result["first_search"] is not None:
        result["first_search_top3_suitable"] = any(
            set(rank) & routes for rank in first.evidence.get("top3", [])
        )
    else:
        result["first_search_top3_suitable"] = None
    if not role_confirmed:
        stage = "actor_unknown"
    elif any(call.status == "approval_blocked" for call in relevant) and grade != "complete":
        stage = "permission_blocked"
    elif grade != "complete" and any(call.evidence.get("status") == "failed" for call in relevant):
        stage = "native_failed"
    elif any(
        call.evidence.get("status") in {"unavailable", "unknown", "failed"} for call in relevant
    ):
        stage = "native_unavailable" if grade != "complete" else "observed"
    elif grade == "complete":
        stage = "observed"
    elif relevant:
        stage = "unknown"
    elif available is True and business:
        stage = "advertised_different_path"
    elif available is False:
        stage = "unadvertised"
    else:
        stage = "unknown"
    return {**result, "stage": stage}


def actor_searches(host, wire, actor, role_confirmed):
    if not role_confirmed or not actor:
        return []
    visible = (
        [call for call in host.calls.values() if call.route == "manage_tools.search"]
        if host
        else []
    )
    bound = [call for call in wire if call.route == "manage_tools.search" and call.actor == actor]
    if visible:
        return visible
    return bound if len({call.timeline for call in bound}) <= 1 else []
