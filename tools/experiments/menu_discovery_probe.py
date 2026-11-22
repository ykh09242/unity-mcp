"""Replay one query on four otherwise identical SDK hosts. No Unity command is sent."""

import argparse
import asyncio
import json

from fastmcp import Client, FastMCP

from services.resources import register_all_resources
from services.tools import register_all_tools
from tools.experiments.blind_tool_actions import MENU_INVENTORIES
from tools.experiments.blind_tool_diagnostics import search_receipt
from tools.experiments.blind_tool_factors import install_factor


async def probe(query, catalog_query, surface):
    args = {"query": query, "intent": "observe", "limit": 3, "route_surfaces": surface}
    if catalog_query:
        args["catalog_query"] = catalog_query
    rows = []
    for wording in ("baseline", "candidate"):
        for ranking in ("baseline", "candidate"):
            server = FastMCP("menu-discovery")
            register_all_tools(server)
            register_all_resources(server)
            if wording == "baseline":
                install_factor(server, "menu-wording-baseline")
            if ranking == "baseline":
                install_factor(server, "menu-ranking-baseline")
            async with Client(server, mode="legacy", cache=False) as client:
                tools = await client.list_tools()
                menu = next(tool for tool in tools if tool.name == "execute_menu_item")
                result = await client.call_tool("search_unity_tools", args)
                ranking_receipt = search_receipt(result.structured_content)
                top = ranking_receipt["top3"]
                rows.append(
                    {
                        "wording": wording,
                        "ranking": ranking,
                        "advertised_menu_description": menu.description,
                        "tool_count": len(tools),
                        "top3": top,
                        "inventory_rank": next(
                            (
                                i
                                for i, group in enumerate(top, 1)
                                if MENU_INVENTORIES.intersection(group)
                            ),
                            None,
                        ),
                    }
                )
    return {"search": args, "arms": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="Verbatim original request in any language")
    parser.add_argument(
        "--catalog-query", help="Optional separate English retrieval phrase supplied by the caller"
    )
    parser.add_argument("--surface", choices=("both", "resources", "tools"), default="both")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(probe(args.query, args.catalog_query, args.surface)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
