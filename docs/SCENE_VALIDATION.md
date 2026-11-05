# Bounded scene validation

`scene_validate(action="validate")` and legacy `manage_scene(action="validate")`
observe the loaded active scene without opening or saving scenes. Both accept
`max_objects` (1–10000, default 1000), `max_milliseconds` (1–1000, default 25),
`continuation`, and the existing `auto_repair`. Native callers may use
`maxObjects`, `maxMilliseconds`, and `autoRepair`.

A successful response preserves `sceneName`, `totalIssues`, `missingScripts`,
`brokenPrefabs`, `repaired`, `issues`, `truncated`, and `note`. Always inspect
`scanComplete`: an incomplete response reports cumulative observed counts rather
than whole-scene totals, and never describes an unobserved scene as clean.
`objectsScannedThisCall` counts only objects inspected by that request;
`objectsScanned` is cumulative. `stopReason` distinguishes completion, object,
cooperative time, traversal-work, and depth limits. `truncated` continues to mean
that the issue details were capped; it is independent of scan completeness.

For read-only continuation, send the returned non-null `continuation` with the
same budgets against the same active scene. A continuation can be replayed until
expiry or invalidation. Cancellation and failed requests do not advance the
input continuation. Tokens expire after five minutes; at most 16 are retained,
so eviction can expire a token earlier. Editor reload, observed scene/hierarchy
changes, a changed active scene, or changed budgets require a fresh scan.
Malformed tokens produce `invalid_scene_validation`; unavailable or stale tokens
produce `stale_scene_validation` with restart guidance.

This is a live observation, not a frozen snapshot. Editor object/hierarchy events,
root identities, and the bounded traversal spine detect drift. Same-frame edits
outside observed anchors may affect later slices; restart after editing. A tiny
time budget may return a slice with no new objects while advancing traversal;
repeated unchanged requests still make traversal progress.

Traversal does not materialize descendant arrays. It holds at most 4096 traversal
frames, 200 issue details, and bounded strings (512 characters per name/path).
Paths explicitly report `pathTruncated` when their bounded ancestor/name evidence
does not fit. Root enumeration is admitted only for at most 10000 roots and is
one synchronous Unity call. There are at most 40000 traversal steps per slice.
These are cooperative bounds: an individual native inspection, root enumeration,
or setup/spine check cannot be interrupted halfway through. A depth-limit stop is
explicitly incomplete and has no continuation; reduce hierarchy depth to finish.

`auto_repair=true` first builds a complete repair plan within one bounded scan,
then removes missing scripts in a non-undoable commit. Broken prefab references remain
manual fixes. Combining repair with continuation is rejected. If a scan stops
early, `validation_repair_requires_complete` reports zero repairs and applies no
changes. Increase the budgets and restart without continuation; scenes exceeding
the maximum single-scan bounds require manual repair. Cancellation before apply
does not mutate scene objects. Cancellation and the cooperative timer are checked
again immediately before commit. Once native removal begins, the tool finishes
the entire admitted plan even if cancellation arrives during commit. This avoids
leaving half a plan solely because a request deadline or disconnect occurred.
Individual native calls and the bounded commit cannot be interrupted midway.

Unity's Undo snapshots do not restore the missing-script components destroyed by
the native removal API; `repairUndoable=false` and `repairCommitPolicy="complete_plan"`
make this explicit in successful responses. A transport cancellation during commit
can prevent delivery of that response even though the handler completes its plan;
inspect the scene with read-only validation before retrying an uncertain request.
If an unexpected native failure interrupts commit, `scene_validation_repair_failed`
reports the observed `repaired`, `remainingMissingScripts` (null when unavailable),
and `repairState` (`unchanged`, `partial`, `complete`, or `unknown`). It never claims
rollback. Scene objects are modified in memory; the tool does not save scenes or
modify prefab source files.

Example request sequence:

```python
page = scene_validate(action="validate", max_objects=500, max_milliseconds=25)
while not page["data"]["scanComplete"]:
    token = page["data"]["continuation"]
    if token is None:
        break  # Inspect stopReason; a hard admission/depth limit needs intervention.
    page = scene_validate(
        action="validate", max_objects=500, max_milliseconds=25, continuation=token
    )
```
