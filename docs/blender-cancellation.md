# Blender Bridge cancellation

Blender Bridge forwards the Unity dispatcher cancellation token to its socket work and observes it before admitting later local stages. This applies to export/import, screenshots and comparisons, Python execution, and nested graphics commands. Existing menu and panel calls without a token retain their ordinary behavior.

If cancellation is observed while an export reply is pending, the local socket task closes its connection and settles with cancellation. The handler does not then stage the export under Assets, import it, place an instance, or start the next batch command. A cancellation racing a successful reply is checked again before local work. An explicit unsupported `export_scene` response can still use the compatibility Python exporter once; cancellation or a lost reply does not trigger that fallback.

Socket closure is not a remote cancellation acknowledgement. Blender may continue executing Python or exporting a file after the caller has canceled. Its remote outcome is unknown, and a remote export can recreate its unique temporary path after local cleanup. Inspect the remote and local state before retrying a mutation.

Cancellation is cooperative at stage boundaries. A synchronous Unity import or individual mutation that was already admitted can finish. Its published assets are retained; cancellation observed after import prevents subsequent placement, animation, prefab, and Bloom work. Cancellation does not imply that no project or scene changes occurred. Operation-owned temporary/staging cleanup is best effort and never removes unrelated output assets as rollback.

The transport tracks actual handler completion separately from its caller-visible response. A canceled response alone does not establish that cleanup or an admitted synchronous operation has finished.

Cancellation requested before `check_updates` or `sync_addon` starts prevents admission. An already-started Git process or synchronous addon copy is not forcibly interrupted by this change. The existing socket response limits, timeout policy, explicit consent, and Assets path restrictions remain in effect.
