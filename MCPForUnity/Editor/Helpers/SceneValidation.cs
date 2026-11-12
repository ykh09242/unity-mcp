using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Cooperatively bounded scene observation with an explicit non-undoable repair commit.</summary>
    internal static class SceneValidation
    {
        private const int RootLimit = 10000;
        private const int DepthLimit = 4096;
        private const int IssueLimit = 200;
        private const int StateLimit = 16;
        private const double Lifetime = 300;
        private static readonly Dictionary<string, State> States = new Dictionary<string, State>();
        private static int epoch;
        private static Action<int> AfterObjectForTests = null;
        private static Action<int> AfterRepairForTests = null;
        private static Action BeforeRepairCommitForTests = null;
        private static Func<double> Milliseconds = () => Stopwatch.GetTimestamp() * 1000.0 / Stopwatch.Frequency;

        static SceneValidation()
        {
            EditorApplication.hierarchyChanged += Changed;
            ObjectChangeEvents.changesPublished += ObjectsChanged;
            EditorApplication.playModeStateChanged += _ => Changed();
            AssemblyReloadEvents.beforeAssemblyReload += () => States.Clear();
        }

        private static void Changed()
        {
            unchecked
            {
                epoch++;
            }
        }

        private static void ObjectsChanged(ref ObjectChangeEventStream stream) => Changed();

        private struct Frame
        {
            internal int Id;
            internal int Children;
            internal int Dirty;
            internal int Next;
            internal bool Visited;
        }

        private sealed class State
        {
            internal Scene Scene;
            internal int Epoch;
            internal int MaxObjects;
            internal int MaxMilliseconds;
            internal ulong Roots;
            internal int RootIndex;
            internal int Scanned;
            internal int Missing;
            internal int Broken;
            internal double Expires;
            internal List<Frame> Spine = new List<Frame>();
            internal JArray Issues = new JArray();

            internal State Copy()
            {
                var copy = (State)MemberwiseClone();
                copy.Spine = new List<Frame>(Spine);
                copy.Issues = (JArray)Issues.DeepClone();
                return copy;
            }
        }

        private struct RepairTarget
        {
            internal int Id;
            internal int Missing;
        }

        private sealed class RepairPlan
        {
            internal Scene Scene;
            internal double Deadline;
            internal readonly List<RepairTarget> Targets = new List<RepairTarget>();
        }

        private sealed class RepairOutcome
        {
            internal int Repaired;
            internal int? Remaining;
            internal string Error;
            internal bool TimeLimited;
        }

        internal static object Handle(JObject parameters, CancellationToken cancellation)
        {
            try
            {
                return Observe(parameters, cancellation);
            }
            catch (ArgumentException error)
            {
                return new ErrorResponse("invalid_scene_validation", new { reason = error.Message });
            }
        }

        internal static JValue SceneHandle(Scene scene)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(scene.handle.GetRawData());
#else
            return new JValue((int)scene.handle);
#endif
        }

        internal static Scene ResolveScene(JObject parameters)
        {
            if (parameters?["scene_handle"] == null)
                return SceneManager.GetActiveScene();
            JToken requested = parameters["scene_handle"];
            if (requested.Type != JTokenType.Integer || SceneManager.sceneCount > 256)
                throw new ArgumentException("scene_handle requires an exact loaded scene identity; at most256 scenes are supported.");
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene candidate = SceneManager.GetSceneAt(index);
                if (candidate.IsValid() && candidate.isLoaded && SceneHandle(candidate).ToString() == requested.ToString())
                    return candidate;
            }
            throw new ArgumentException("scene_handle does not identify a loaded scene.");
        }

        private static int Integer(JToken token, int fallback, int maximum, string name)
        {
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out long value) || value < 1 || value > maximum)
                throw new ArgumentException($"{name} must be an integer between 1 and {maximum}.");
            return (int)value;
        }

        private static object Observe(JObject parameters, CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            double start = Milliseconds();
            int maxObjects = Integer(parameters["maxObjects"] ?? parameters["max_objects"], 1000, 10000, "max_objects");
            int maxMilliseconds = Integer(parameters["maxMilliseconds"] ?? parameters["max_milliseconds"], 25, 1000, "max_milliseconds");
            JToken cursorToken = parameters["continuation"];
            string cursor = cursorToken?.Type == JTokenType.String ? cursorToken.Value<string>() : null;
            if (
                cursorToken != null
                && cursorToken.Type != JTokenType.Null
                && (cursorToken.Type != JTokenType.String || (cursor != null && (cursor.Length != 32 || !Guid.TryParseExact(cursor, "N", out _))))
            )
                throw new ArgumentException("continuation must be an opaque returned token; restart validation.");
            bool repair = ParamCoercion.CoerceBoolNullable(parameters["autoRepair"] ?? parameters["auto_repair"]) == true;
            if (repair && cursor != null)
                throw new ArgumentException("auto_repair requires a new complete scan; omit continuation.");
            Scene scene = ResolveScene(parameters);
            if (!scene.IsValid() || !scene.isLoaded)
                throw new ArgumentException("A loaded active scene is required.");
            if (scene.rootCount > RootLimit)
                return new ErrorResponse(
                    "scene_validation_limit",
                    new { reason = "The active scene exceeds the 10000-root admission limit; no scan or repair started." }
                );

            // Unity root enumeration is one synchronous call. Admit its allocation before entering it.
            var roots = new List<GameObject>(scene.rootCount + 1);
            scene.GetRootGameObjects(roots);
            ulong identity = RootIdentity(roots, cancellation);
            Prune();
            State state;
            if (cursor == null)
                state = new State
                {
                    Scene = scene,
                    Epoch = epoch,
                    Roots = identity,
                    MaxObjects = maxObjects,
                    MaxMilliseconds = maxMilliseconds,
                };
            else
            {
                if (
                    !States.TryGetValue(cursor, out State saved)
                    || saved.Scene != scene
                    || saved.Epoch != epoch
                    || saved.Roots != identity
                    || saved.MaxObjects != maxObjects
                    || saved.MaxMilliseconds != maxMilliseconds
                )
                    return Stale();
                state = saved.Copy();
                if (!CheckSpine(state, scene, cancellation))
                    return Stale();
            }

            var plan = new RepairPlan { Scene = scene, Deadline = start + maxMilliseconds };
            int scanned = 0;
            int work = 0;
            string stop = "complete";
            while (state.Spine.Count > 0 || state.RootIndex < roots.Count)
            {
                cancellation.ThrowIfCancellationRequested();
                // Guarantee one traversal operation even when setup exhausted a tiny time budget.
                if (work > 0 && Milliseconds() - start >= maxMilliseconds)
                {
                    stop = "time_budget";
                    break;
                }
                if (++work > 40000)
                {
                    stop = "work_budget";
                    break;
                }
                if (state.Spine.Count == 0)
                {
                    state.Spine.Add(FrameFor(roots[state.RootIndex++]));
                    continue;
                }
                int top = state.Spine.Count - 1;
                Frame frame = state.Spine[top];
                var go = UnityObjectIdCompat.InstanceIDToObjectCompat(frame.Id) as GameObject;
                if (go == null || go.scene != scene || go.transform.childCount != frame.Children || EditorUtility.GetDirtyCount(go) != frame.Dirty)
                    return Stale();
                if (!frame.Visited)
                {
                    if (scanned >= maxObjects)
                    {
                        stop = "object_budget";
                        break;
                    }
                    int missing = GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(go);
                    state.Missing += missing;
                    if (missing > 0)
                    {
                        AddIssue(state, go, "missing_script", missing);
                        if (repair)
                            plan.Targets.Add(new RepairTarget { Id = frame.Id, Missing = missing });
                    }
                    if (PrefabUtility.GetPrefabInstanceStatus(go) == PrefabInstanceStatus.MissingAsset)
                    {
                        state.Broken++;
                        AddIssue(state, go, "broken_prefab", 0);
                    }
                    state.Scanned++;
                    scanned++;
                    frame.Visited = true;
                    state.Spine[top] = frame;
                    AfterObjectForTests?.Invoke(frame.Id);
                    continue;
                }
                if (frame.Next < frame.Children)
                {
                    if (state.Spine.Count == DepthLimit)
                    {
                        stop = "depth_limit";
                        break;
                    }
                    Transform child = go.transform.GetChild(frame.Next++);
                    state.Spine[top] = frame;
                    state.Spine.Add(FrameFor(child.gameObject));
                }
                else
                    state.Spine.RemoveAt(top);
            }
            cancellation.ThrowIfCancellationRequested();
            if (
                epoch != state.Epoch
                || ResolveScene(parameters) != scene
                || !scene.IsValid()
                || !scene.isLoaded
                || roots.Count != scene.rootCount
                || !CheckSpine(state, scene, cancellation)
            )
                return Stale();
            bool complete = state.Spine.Count == 0 && state.RootIndex == roots.Count;
            if (repair && !complete)
                return new ErrorResponse(
                    "validation_repair_requires_complete",
                    new
                    {
                        repaired = 0,
                        scanComplete = false,
                        stopReason = stop,
                        reason = "No repair applied. Increase max_objects (up to 10000) and max_milliseconds (up to 1000), then restart without continuation. Larger scenes require manual repair.",
                    }
                );
            RepairOutcome outcome = repair ? Repair(plan, cancellation) : new RepairOutcome();
            if (outcome.TimeLimited)
                return new ErrorResponse(
                    "validation_repair_requires_complete",
                    new
                    {
                        repaired = 0,
                        scanComplete = true,
                        stopReason = "time_budget",
                        reason = "No repair applied. The precommit time budget expired; increase max_milliseconds and restart without continuation.",
                    }
                );
            if (outcome.Error != null)
                return new ErrorResponse(
                    "scene_validation_repair_failed",
                    new
                    {
                        repaired = outcome.Repaired,
                        remainingMissingScripts = outcome.Remaining,
                        scanComplete = true,
                        repairState = outcome.Remaining.HasValue
                            ? (
                                outcome.Remaining == 0 ? "complete"
                                : outcome.Repaired == 0 ? "unchanged"
                                : "partial"
                            )
                            : "unknown",
                        undoable = false,
                        repairCommitPolicy = "complete_plan",
                        reason = outcome.Error,
                        recovery = "Native removal failed; no rollback was attempted. Inspect the current scene and run validation again before further repair.",
                    }
                );
            int repaired = outcome.Repaired;
            string next = null;
            if (!complete && stop != "depth_limit")
            {
                if (States.Count >= StateLimit)
                    States.Remove(States.OrderBy(item => item.Value.Expires).First().Key);
                next = Guid.NewGuid().ToString("N");
                state.Expires = EditorApplication.timeSinceStartup + Lifetime;
                States.Add(next, state);
            }
            int total = state.Missing + state.Broken;
            string message = complete
                ? (total == 0 ? $"Scene '{scene.name}' is clean — no issues found." : $"Scene '{scene.name}' has {total} issue(s).")
                : $"Scene '{scene.name}' validation is incomplete; {total} issue(s) observed so far.";
            if (repaired > 0)
                message += $" Auto-repaired {repaired} missing script(s). This repair is not undoable.";
            return new SuccessResponse(
                message,
                new
                {
                    sceneName = scene.name,
                    scene_handle = SceneHandle(scene),
                    scenePath = scene.path,
                    totalIssues = total,
                    missingScripts = state.Missing,
                    brokenPrefabs = state.Broken,
                    repaired,
                    repairUndoable = false,
                    repairCommitPolicy = repair ? "complete_plan" : null,
                    issues = state.Issues,
                    truncated = total > state.Issues.Count,
                    note = state.Broken > 0 ? "Broken prefab references are not auto-repaired (too risky). Fix manually." : null,
                    scanComplete = complete,
                    continuation = next,
                    stopReason = complete ? "complete" : stop,
                    objectsScanned = state.Scanned,
                    objectsScannedThisCall = scanned,
                    consistency = "live_observation",
                    restartAdvice = "Restart after edits, expiry, eviction or Editor reload. Same-frame edits outside observed anchors may affect later slices; this is not a snapshot. Depth-limit stops cannot resume.",
                }
            );
        }

        private static Frame FrameFor(GameObject go) =>
            new Frame
            {
                Id = go.GetInstanceIDCompat(),
                Children = go.transform.childCount,
                Dirty = EditorUtility.GetDirtyCount(go),
            };

        private static bool CheckSpine(State state, Scene scene, CancellationToken cancellation)
        {
            Transform parent = null;
            foreach (Frame frame in state.Spine)
            {
                cancellation.ThrowIfCancellationRequested();
                var go = UnityObjectIdCompat.InstanceIDToObjectCompat(frame.Id) as GameObject;
                if (
                    go == null
                    || go.scene != scene
                    || go.transform.parent != parent
                    || go.transform.childCount != frame.Children
                    || EditorUtility.GetDirtyCount(go) != frame.Dirty
                )
                    return false;
                parent = go.transform;
            }
            return true;
        }

        private static ulong RootIdentity(List<GameObject> roots, CancellationToken cancellation)
        {
            ulong identity = 14695981039346656037UL;
            foreach (GameObject root in roots)
            {
                cancellation.ThrowIfCancellationRequested();
                unchecked
                {
                    identity = (identity ^ (uint)root.GetInstanceIDCompat()) * 1099511628211UL;
                }
            }
            return identity;
        }

        private static void AddIssue(State state, GameObject go, string type, int count)
        {
            if (state.Issues.Count >= IssueLimit)
                return;
            var names = new List<string>();
            Transform current = go.transform;
            int characters = 0;
            while (current != null && names.Count < 128 && characters < 512)
            {
                string name = Text(current.name);
                names.Add(name);
                characters += name.Length + 1;
                current = current.parent;
            }
            names.Reverse();
            var issue = new JObject
            {
                ["type"] = type,
                ["gameObject"] = Text(go.name),
                ["path"] = Text(string.Join("/", names)),
                ["pathTruncated"] = current != null || characters > 512,
            };
            if (type == "missing_script")
                issue["count"] = count;
            else
                issue["status"] = PrefabInstanceStatus.MissingAsset.ToString();
            state.Issues.Add(issue);
        }

        private static string Text(string value)
        {
            if (value.Length <= 512)
                return value;
            return value.Substring(0, char.IsHighSurrogate(value[511]) ? 511 : 512);
        }

        private static RepairOutcome Repair(RepairPlan plan, CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            var outcome = new RepairOutcome();
            if (plan.Targets.Count == 0)
                return outcome;
            // Resolve the entire admitted plan before committing. Cancellation is still safe here.
            foreach (RepairTarget target in plan.Targets)
            {
                cancellation.ThrowIfCancellationRequested();
                if (Milliseconds() >= plan.Deadline)
                {
                    outcome.TimeLimited = true;
                    return outcome;
                }
                var go = UnityObjectIdCompat.InstanceIDToObjectCompat(target.Id) as GameObject;
                if (go == null || go.scene != plan.Scene || GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(go) != target.Missing)
                {
                    outcome.Error = "Scene changed before repair; no native removal started.";
                    return outcome;
                }
            }
            BeforeRepairCommitForTests?.Invoke();
            cancellation.ThrowIfCancellationRequested();
            if (Milliseconds() >= plan.Deadline)
            {
                outcome.TimeLimited = true;
                return outcome;
            }
            // Missing-script destruction is not restored by Unity's Undo snapshots. Once removal
            // starts, finish this bounded plan even if cancellation arrives; never pretend rollback.
            try
            {
                foreach (RepairTarget target in plan.Targets)
                {
                    var go = UnityObjectIdCompat.InstanceIDToObjectCompat(target.Id) as GameObject;
                    if (go == null || go.scene != plan.Scene)
                        throw new InvalidOperationException("Scene changed during repair commit.");
                    outcome.Repaired += GameObjectUtility.RemoveMonoBehavioursWithMissingScript(go);
                    AfterRepairForTests?.Invoke(target.Id);
                }
            }
            catch (Exception error)
            {
                outcome.Error = Text(error.Message);
                int remaining = 0;
                bool known = true;
                foreach (RepairTarget target in plan.Targets)
                {
                    try
                    {
                        var go = UnityObjectIdCompat.InstanceIDToObjectCompat(target.Id) as GameObject;
                        if (go == null || go.scene != plan.Scene)
                        {
                            known = false;
                            continue;
                        }
                        remaining += GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(go);
                    }
                    catch (Exception)
                    {
                        known = false;
                    }
                }
                outcome.Remaining = known ? remaining : (int?)null;
                if (known)
                    outcome.Repaired = Math.Max(0, plan.Targets.Sum(target => target.Missing) - remaining);
            }
            finally
            {
                if ((outcome.Repaired > 0 || outcome.Error != null) && plan.Scene.IsValid() && plan.Scene.isLoaded)
                    EditorSceneManager.MarkSceneDirty(plan.Scene);
            }
            return outcome;
        }

        private static ErrorResponse Stale() =>
            new ErrorResponse(
                "stale_scene_validation",
                new { reason = "Scene, options or observed revision changed, or continuation expired/was evicted. Restart without continuation." }
            );

        private static void Prune()
        {
            double now = EditorApplication.timeSinceStartup;
            foreach (string key in States.Where(item => item.Value.Expires <= now).Select(item => item.Key).ToArray())
                States.Remove(key);
        }
    }
}
