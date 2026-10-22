"""Request-scoped runtime capture control. Native credentials never leave SQLite."""

from services.reference_graph.reconcile import drained_thread
from services.reference_graph.runtime import RuntimeObservations


async def execute(service, entry, action, options):
    runtime = await drained_thread(RuntimeObservations, entry.workspace)
    if action == "runtime_sessions":
        return await drained_thread(runtime.sessions, **options)
    identity = options.get("id")
    if action == "runtime_start" and identity is None:
        identity = (await drained_thread(runtime.begin))["id"]
    async with service._coordinate("runtime:" + entry.workspace.store.scope.key + ":" + identity):
        native = await drained_thread(runtime.native, identity)
        if action == "runtime_start":
            observed = await entry.collect("runtime_start", token=native["token"])
            await entry.check()
            observed.update(events=[], next_offset=native["offset"])
            await drained_thread(runtime.ingest, identity, native["offset"], observed)
        elif action in {"runtime_poll", "runtime_stop"}:
            if action == "runtime_stop":
                await entry.collect("runtime_stop", token=native["token"])
            observed = await entry.collect(
                "runtime_read",
                token=native["token"],
                offset=native["offset"],
                page_size=options.get("limit", 128),
            )
            await entry.check()
            await drained_thread(runtime.ingest, identity, native["offset"], observed)
            return {
                "capture": await drained_thread(runtime.status, identity),
                "has_more": observed.get("has_more", False),
            }
        elif action == "runtime_release":
            await entry.collect("runtime_release", token=native["token"])
            await entry.check()
            return {"released": await drained_thread(runtime.release, identity)}
        elif action in {"runtime_events", "runtime_handles"}:
            policy = await drained_thread(entry.workspace.policy)
            result = await drained_thread(
                getattr(runtime, action.removeprefix("runtime_")),
                identity,
                **{key: value for key, value in options.items() if key != "id"},
            )
            result["_runtime_policy"] = policy
            return result
        return {"capture": await drained_thread(runtime.status, identity)}
