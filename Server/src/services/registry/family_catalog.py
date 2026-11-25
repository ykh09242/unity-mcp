"""A scoped, deterministic directory for agents choosing operations from their declarations."""

from collections import defaultdict

from services.registry.action_catalog import (
    action_card,
    concrete_resources,
    declared_actions,
    operation_owner,
    resource_observation,
    resource_templates,
    tool_description,
)


def _summary(description):
    return (description or "").strip().partition("\n")[0].partition(". ")[0][:240]


def browse_families(request, entries, resources, *, visible_names, blocked):
    advertised = True if visible_names is not None else None
    directory = {}
    tools = {}
    reads = {}
    if request.route_surfaces != "resources":
        owners = defaultdict(list)
        for entry in sorted(entries, key=lambda item: item["name"]):
            name = entry["name"]
            if visible_names is not None and name not in visible_names:
                continue
            for action in sorted(set(declared_actions(entry))) or [None]:
                if ("tool", name, action or "") not in blocked:
                    owners[operation_owner(name, action) if action else name].append(
                        (entry, action)
                    )
        for owner, bindings in owners.items():
            source = next((e for e, _ in bindings if e["name"] == owner), None)
            entry, selector = bindings[0]
            # A public binding identifies the family even when its native owner is hidden.
            family = owner if source else f"action:{entry['name']}/{selector}"
            tools[family] = bindings
            directory[family] = {
                "family": family,
                "kind": "tool",
                "purpose": _summary(tool_description(source or entry)),
                "tools": list(dict.fromkeys(e["name"] for e, _ in bindings)),
                "count": len(bindings),
            }
    if request.route_surfaces != "tools":
        for item in [*concrete_resources(resources), *resource_templates(resources)]:
            template = "uri_template" in item
            kind = "resource_template" if template else "resource"
            key = item["uri_template"] if template else item["uri"]
            if (kind, key, "") in blocked:
                continue
            directory[key] = {
                "family": key,
                "kind": kind,
                "purpose": _summary(item["description"]),
                "count": 1,
            }
            reads[key] = {
                "kind": kind,
                "uri_template" if template else "uri": key,
                "name": item["name"],
                "purpose": item["description"],
                "intent": "observe",
                "required_parameters": item.get("required_parameters", []),
                **({"optional_parameters": item["optional_parameters"]} if template else {}),
                **(
                    {"observation": item["observation"]}
                    if item.get("observation")
                    else resource_observation(item)
                ),
                "advertised": advertised,
                "readiness": "unobserved",
            }
    response = {
        "scope": "mcp_context" if visible_names is not None else "local_catalog",
        "selection": "agent",
        "matching": "catalog",
        "query": request.query,
        "route_surfaces": request.route_surfaces,
        "readiness": "unobserved",
        "offset": request.offset,
    }
    start, end = request.offset, request.offset + request.limit
    if request.family is None:
        keys = sorted(directory)
        total = len(keys)
        response.update(
            view="families",
            families=[directory[k] for k in keys[start:end]],
            hint="Read the directory through next_offset. Choose relevant families by the outcome requested in the original, then request each family with selection=agent.",
        )
    elif request.family in directory:
        family = request.family
        total = directory[family]["count"]
        if family in tools:
            operations = [
                {
                    "kind": "tool",
                    **action_card(entry, selector),
                    "advertised": advertised,
                    "readiness": "unobserved",
                }
                for entry, selector in tools[family][start:end]
            ]
        else:
            operations = [reads[family]][start:end]
        response.update(
            view="operations",
            family=family,
            operations=operations,
            hint="Read all pages of each chosen family. Compare what each binding returns or changes, its inputs and effects, then select an exact offered binding.",
        )
    else:
        return {
            **response,
            "success": False,
            "error": "Unknown family in this scope. Read the current family directory.",
        }
    return {**response, "total": total, "next_offset": end if end < total else None}
