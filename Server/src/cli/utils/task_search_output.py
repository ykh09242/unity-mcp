"""Bounded human-readable discovery with support and next-inspection context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cli.utils.followup import followup_command

if TYPE_CHECKING:
    from cli.utils.config import CLIConfig
    from services.registry.task_search import SearchResponse, SearchRoute


def _summary(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def _route_target(route: SearchRoute) -> str:
    return (
        route.get("uri")
        or route.get("uri_template")
        or ".".join(value for value in (route.get("tool"), route.get("operation")) if value)
    )


def format_task_search(result: SearchResponse, config: CLIConfig) -> str:
    """Show useful suggestions without implying runtime availability or permission."""
    entries = result["results"]
    groups = result["route_groups"]
    lines = [f"Route groups: {len(groups)} shown of {result['total_route_groups']} matches"]
    if groups and result["recommended_action"] == "browse_catalog":
        lines.append("Weak lexical evidence. Browse by purpose before choosing an action.")
    for group in groups:
        route = group["representative"]
        lines.append(f"  {route['kind']} {_route_target(route)} — {_summary(route['purpose'])}")
        if route.get("evidence_limit"):
            lines.append("    " + _summary(route["evidence_limit"]))
        for alternative in group["alternatives"]:
            lines.append(f"    Alternative {alternative['kind']} {_route_target(alternative)}")
    if not groups:
        lines.append("No matching tools or resources. Browse the catalog to select by purpose.")
    for group in result["navigation"]["groups"]:
        lines.append(f"  {group['group']}: {group['purpose']}")
    if result["navigation"]["resources"]["total"]:
        lines.append("  resources: " + result["navigation"]["resources"]["purpose"])
    if result["navigation"]["resource_templates"]["total"]:
        lines.append(
            "  resource_templates: " + result["navigation"]["resource_templates"]["purpose"]
        )
        lines.append(
            "  " + followup_command(config, ["tool", "catalog", "--group", "resource_templates"])
        )
    lines.append("  " + followup_command(config, ["tool", "catalog"]))
    for entry in entries:
        lines.append(f"\n{entry['name']} [{entry['maturity']}] — {_summary(entry['title'])}")
        if entry["description"]:
            lines.append("  " + _summary(entry["description"]))
        actions = entry["actions"]
        if actions:
            labels = [
                _summary(action, 128)
                + (
                    " [experimental]"
                    if entry["action_maturity"].get(action) == "experimental"
                    else ""
                )
                for action in actions[:8]
            ]
            lines.append("  Actions: " + ", ".join(labels))
            if len(actions) > 8:
                lines.append(
                    f"  {len(actions) - 8} more actions; use --format json for all details."
                )
        requirements = entry["requirements"]
        for requirement in requirements[:3]:
            lines.append("  Requires: " + _summary(requirement))
        if len(requirements) > 3:
            lines.append("  More requirements omitted; use --format json for all details.")
        skills = entry["official_skills"]
        for skill in skills[:2]:
            lines.append(f"  Skill: {_summary(skill['name'], 128)} — {_summary(skill['url'], 512)}")
        if len(skills) > 2:
            lines.append("  More skill links omitted; use --format json for all details.")
        lines.append("  Readiness: unobserved; inspect the selected Editor before execution.")
        lines.append("  Inspect: " + followup_command(config, ["tool", "describe", entry["name"]]))
    if result["route_groups_truncated"] or result["truncated"]:
        lines.append("\nMore matches exist; narrow the query or increase --limit (maximum 20).")
    lines.append("\n" + result["note"])
    return "\n".join(lines)
