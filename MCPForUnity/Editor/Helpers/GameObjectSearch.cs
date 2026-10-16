using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Threading;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Opt-in, bounded scene search. Cursors retain only scalar observations and a traversal spine.
    /// Exact concatenation requires an unchanged source; Editor events are not a mutation lock.
    /// </summary>
    internal static class GameObjectSearch
    {
        internal const int WorkLimit = 4096;
        internal const int RootLimit = 4096;
        internal const int SceneLimit = 32;
        internal const int OutputLimit = 16384;
        private const int CacheLimit = 32;
        private const double Lifetime = 30;
        private static readonly Dictionary<string, State> States = new Dictionary<string, State>();
        private static int _epoch;

        // Native tests observe real operations; these never replace traversal or Unity calls.
        private static Action BeforeRootEnumerationForTests;
        private static Action<int> TraversalStepForTests;

        static GameObjectSearch()
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
                _epoch++;
            }
        }

        private static void ObjectsChanged(ref ObjectChangeEventStream stream) => Changed();

        private sealed class Failure : Exception
        {
            internal readonly string Code;

            internal Failure(string code, string message)
                : base(message)
            {
                Code = code;
            }
        }

        private sealed class Predicate
        {
            internal string Method;
            internal string Term;
            internal Type Component;
            internal int Number;
        }

        private sealed class Query
        {
            internal string Scope;
            internal string Cursor;
            internal string Signature;
            internal int PageSize;
            internal int Depth;
            internal bool Inactive;
            internal readonly List<Predicate> Predicates = new List<Predicate>();
        }

        private struct SceneInfo
        {
            internal int Handle;
            internal int ManagerIndex;
            internal int Roots;
        }

        private struct Observation : IEquatable<Observation>
        {
            internal int Id;
            internal int Dirty;
            internal int Children;
            internal int First;
            internal int Last;
            internal int Next;

            public bool Equals(Observation other) =>
                Id == other.Id && Dirty == other.Dirty && Children == other.Children && First == other.First && Last == other.Last && Next == other.Next;
        }

        private sealed class State
        {
            internal string Signature;
            internal SceneInfo[] Scenes;
            internal int Epoch;
            internal int SceneIndex;
            internal int RootIndex;
            internal ulong? RootIdentity;
            internal int[] Path = Array.Empty<int>();
            internal Observation[] Observations = Array.Empty<Observation>();
            internal long Matched;
            internal bool DepthLimited;
            internal double Expires;

            internal State Copy() =>
                new State
                {
                    Signature = Signature,
                    Scenes = (SceneInfo[])Scenes.Clone(),
                    Epoch = Epoch,
                    SceneIndex = SceneIndex,
                    RootIndex = RootIndex,
                    RootIdentity = RootIdentity,
                    Path = (int[])Path.Clone(),
                    Observations = (Observation[])Observations.Clone(),
                    Matched = Matched,
                    DepthLimited = DepthLimited,
                    Expires = Expires,
                };
        }

        private sealed class Budget
        {
            internal int Used;
            internal readonly CancellationToken Token;

            internal Budget(CancellationToken token)
            {
                Token = token;
            }

            internal void Spend()
            {
                Token.ThrowIfCancellationRequested();
                if (++Used > WorkLimit)
                    throw new InvalidOperationException("Search work reservation was exceeded.");
                TraversalStepForTests?.Invoke(Used);
                Token.ThrowIfCancellationRequested();
            }
        }

        internal static object Handle(JObject parameters, CancellationToken token)
        {
            try
            {
                token.ThrowIfCancellationRequested();
                Query query = Parse(parameters, token);
                SceneInfo[] scenes = SelectScenes(query.Scope, token);
                State state = Resume(query, scenes);
                var budget = new Budget(token);
                var ids = new List<int>(query.PageSize);
                int prepared = 0;
                int enumerations = 0;
                string stop = "complete";
                var spine = new List<Transform>();
                var path = new List<int>(state.Path);

                if (state.SceneIndex < scenes.Length)
                {
                    Scene scene = ResolveScene(query.Scope, scenes[state.SceneIndex]);
                    // Unity obtains all roots in one synchronous native operation. Admit BEFORE that call.
                    int count = scene.rootCount;
                    if (count > RootLimit)
                        throw Limit("Selected scene exceeds the 4096-root admission limit.");
                    var roots = new List<GameObject>(count);
                    token.ThrowIfCancellationRequested();
                    BeforeRootEnumerationForTests?.Invoke();
                    scene.GetRootGameObjects(roots);
                    enumerations++;
                    if (roots.Count != count || roots.Count > RootLimit)
                        throw Stale();
                    var ordered = new List<KeyValuePair<int, GameObject>>(count);
                    foreach (GameObject root in roots)
                    {
                        token.ThrowIfCancellationRequested();
                        if (root == null || EditorUtility.IsPersistent(root))
                            throw Stale();
                        ordered.Add(new KeyValuePair<int, GameObject>(root.GetInstanceIDCompat(), root));
                        prepared++;
                    }
                    ordered.Sort((left, right) => left.Key.CompareTo(right.Key));
                    ulong identity = 14695981039346656037UL;
                    foreach (var root in ordered)
                        unchecked
                        {
                            identity = (identity ^ (uint)root.Key) * 1099511628211UL;
                        }
                    if (state.RootIdentity.HasValue && state.RootIdentity.Value != identity)
                        throw Stale();
                    state.RootIdentity = identity;
                    if (state.RootIndex < ordered.Count)
                    {
                        spine.Add(ordered[state.RootIndex].Value.transform);
                        foreach (int child in path)
                        {
                            budget.Spend();
                            Transform parent = spine[spine.Count - 1];
                            if (child < 0 || child >= parent.childCount)
                                throw Stale();
                            spine.Add(parent.GetChild(child));
                        }
                        if (state.Observations.Length != 0 && !Observe(spine, path, budget).SequenceEqual(state.Observations))
                            throw Stale();
                    }
                    else if (state.Observations.Length != 0 || path.Count != 0)
                        throw Stale();

                    // Reserve predicate evaluation, worst-depth advancement, and the final bounded spine observation.
                    int reserve = 17 * (query.Depth + 1) + 20;
                    while (spine.Count != 0)
                    {
                        if (ids.Count == query.PageSize)
                        {
                            stop = "page_size";
                            break;
                        }
                        if (WorkLimit - budget.Used < reserve)
                        {
                            stop = "work_budget";
                            break;
                        }
                        budget.Spend();
                        GameObject go = spine[spine.Count - 1].gameObject;
                        bool eligible = query.Inactive || go.activeInHierarchy;
                        bool match = eligible;
                        if (match)
                            foreach (Predicate predicate in query.Predicates)
                                if (!Matches(predicate, go, spine, budget))
                                {
                                    match = false;
                                    break;
                                }
                        // IDs need at most 12 compact bytes each. 4096 bytes reserves all bounded metadata.
                        if (match && 4096 + (ids.Count + 1) * 12 > OutputLimit)
                        {
                            stop = "output_budget";
                            break;
                        }
                        if (match)
                        {
                            ids.Add(go.GetInstanceIDCompat());
                            state.Matched++;
                        }
                        Advance(state, spine, path, ordered, query.Depth, eligible, budget);
                    }
                    if (spine.Count != 0)
                    {
                        state.Path = path.ToArray();
                        state.Observations = Observe(spine, path, budget);
                    }
                    else
                    {
                        state.SceneIndex++;
                        state.RootIndex = 0;
                        state.RootIdentity = null;
                        state.Path = Array.Empty<int>();
                        state.Observations = Array.Empty<Observation>();
                        if (state.SceneIndex < scenes.Length)
                            stop = "root_enumeration_budget";
                    }
                }

                token.ThrowIfCancellationRequested();
                if (_epoch != state.Epoch || !SameScenes(scenes, SelectScenes(query.Scope, token)))
                    throw Stale();
                bool complete = state.SceneIndex >= scenes.Length;
                // Build and measure before publishing state, so failures never advance or consume input cursors.
                var data = new JObject
                {
                    ["instanceIDs"] = new JArray(ids),
                    ["pageSize"] = query.PageSize,
                    ["cursor"] = query.Cursor,
                    ["nextCursor"] = complete ? null : new string('0', 32),
                    ["totalCount"] = complete ? new JValue(state.Matched) : JValue.CreateNull(),
                    ["hasMore"] = !complete,
                    ["totalKnown"] = complete,
                    ["scanComplete"] = complete,
                    ["matchedSoFar"] = state.Matched,
                    ["sceneScope"] = query.Scope,
                    ["sceneHandles"] = new JArray(scenes.Select(item => item.Handle)),
                    ["maxDepth"] = query.Depth,
                    ["depthLimited"] = state.DepthLimited,
                    ["consistency"] = "stable_source_required",
                    ["restartAdvice"] = "Restart after scene edits, cursor expiry or Editor reload. Empty continuation pages are valid.",
                    ["traversalWorkUsed"] = budget.Used,
                    ["traversalWorkLimit"] = WorkLimit,
                    ["rootPreparationCount"] = prepared,
                    ["rootPreparationLimit"] = RootLimit,
                    ["rootEnumerationCalls"] = enumerations,
                    ["outputLimitBytes"] = OutputLimit,
                    ["stopReason"] = complete ? "complete" : stop,
                };
                var response = new SuccessResponse("Found GameObjects", data);
                if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(response)) > OutputLimit)
                    throw new InvalidOperationException("Search output reservation was exceeded.");
                if (!complete)
                    data["nextCursor"] = Save(state);
                return response;
            }
            catch (ArgumentException failure)
            {
                return new ErrorResponse("invalid_parameters", new { message = failure.Message });
            }
            catch (Failure failure)
            {
                return new ErrorResponse(failure.Code, new { message = failure.Message, restart = failure.Code == "stale_cursor" });
            }
        }

        private static void Advance(
            State state,
            List<Transform> spine,
            List<int> path,
            List<KeyValuePair<int, GameObject>> roots,
            int depth,
            bool eligible,
            Budget budget
        )
        {
            budget.Spend();
            Transform current = spine[spine.Count - 1];
            int children = current.childCount;
            if (eligible && children > 0)
            {
                if (path.Count < depth)
                {
                    budget.Spend();
                    spine.Add(current.GetChild(0));
                    path.Add(0);
                    return;
                }
                state.DepthLimited = true;
            }
            while (path.Count != 0)
            {
                spine.RemoveAt(spine.Count - 1);
                int next = path[path.Count - 1] + 1;
                path.RemoveAt(path.Count - 1);
                budget.Spend();
                Transform parent = spine[spine.Count - 1];
                if (next < parent.childCount)
                {
                    budget.Spend();
                    spine.Add(parent.GetChild(next));
                    path.Add(next);
                    return;
                }
            }
            spine.Clear();
            state.RootIndex++;
            if (state.RootIndex < roots.Count)
                spine.Add(roots[state.RootIndex].Value.transform);
        }

        private static Observation[] Observe(List<Transform> spine, List<int> path, Budget budget)
        {
            var result = new Observation[spine.Count];
            for (int i = 0; i < spine.Count; i++)
            {
                budget.Spend();
                Transform transform = spine[i];
                if (transform == null)
                    throw Stale();
                budget.Spend();
                int count = transform.childCount;
                budget.Spend();
                int dirty = EditorUtility.GetDirtyCount(transform.gameObject);
                int first = 0,
                    last = 0,
                    next = 0;
                if (count > 0)
                {
                    budget.Spend();
                    first = transform.GetChild(0).gameObject.GetInstanceIDCompat();
                    budget.Spend();
                    last = transform.GetChild(count - 1).gameObject.GetInstanceIDCompat();
                }
                if (i < path.Count && path[i] + 1 < count)
                {
                    budget.Spend();
                    next = transform.GetChild(path[i] + 1).gameObject.GetInstanceIDCompat();
                }
                result[i] = new Observation
                {
                    Id = transform.gameObject.GetInstanceIDCompat(),
                    Dirty = dirty,
                    Children = count,
                    First = first,
                    Last = last,
                    Next = next,
                };
            }
            return result;
        }

        private static bool Matches(Predicate predicate, GameObject go, List<Transform> spine, Budget budget)
        {
            budget.Spend();
            switch (predicate.Method)
            {
                case "by_name":
                    return string.Equals(go.name, predicate.Term, StringComparison.Ordinal);
                case "by_tag":
                    // CompareTag logs for undeclared query strings even when its exception is caught.
                    // Read the assigned tag instead: exact matching stays local to this candidate.
                    return string.Equals(go.tag, predicate.Term, StringComparison.Ordinal);
                case "by_layer":
                    return predicate.Number >= 0 && go.layer == predicate.Number;
                case "by_component":
                    return predicate.Component != null && go.GetComponent(predicate.Component) != null;
                case "by_id":
                    return go.GetInstanceIDCompat() == predicate.Number;
                case "by_path":
                    return MatchesPath(predicate.Term, spine, budget);
                default:
                    return false;
            }
        }

        private static bool MatchesPath(string query, List<Transform> spine, Budget budget)
        {
            // Compare backwards against the virtual joined path. This also preserves slash-in-name semantics
            // without allocating a full path or walking beyond the admitted hierarchy depth.
            bool rooted = query[0] == '/';
            int start = rooted ? 1 : 0;
            int position = query.Length - 1;
            for (int level = spine.Count - 1; level >= 0; level--)
            {
                budget.Spend();
                string name = spine[level].name;
                for (int index = name.Length - 1; index >= 0; index--)
                {
                    if (position < start)
                        return !rooted && name[index] == '/';
                    if (name[index] != query[position--])
                        return false;
                }
                if (level > 0)
                {
                    if (position < start)
                        return !rooted;
                    if (query[position--] != '/')
                        return false;
                }
            }
            return position < start;
        }

        internal static string SceneScope(JObject parameters)
        {
            JToken token = Alias(parameters, "scene_scope", "sceneScope");
            string scope =
                token == null || token.Type == JTokenType.Null ? "legacy"
                : token.Type == JTokenType.String ? token.Value<string>()
                : null;
            if (scope != "legacy" && scope != "active" && scope != "all_loaded")
                throw new ArgumentException("scene_scope must be legacy, active or all_loaded.");
            if (scope == "legacy" && (parameters["conditions"] != null || parameters["max_depth"] != null || parameters["maxDepth"] != null))
                throw new ArgumentException("conditions and max_depth require scene_scope active or all_loaded.");
            return scope;
        }

        private static Query Parse(JObject parameters, CancellationToken token)
        {
            var query = new Query
            {
                Scope = SceneScope(parameters),
                PageSize = Integer(Alias(parameters, "page_size", "pageSize"), 50, 1, 500, "page_size"),
                Depth = Integer(Alias(parameters, "max_depth", "maxDepth"), 64, 0, 128, "max_depth"),
            };
            if (parameters["page_number"] != null || parameters["pageNumber"] != null)
                throw Invalid("Scoped searches require opaque cursors, not page_number.");
            JToken cursor = parameters["cursor"];
            if (cursor != null && cursor.Type != JTokenType.Null)
            {
                if (
                    cursor.Type != JTokenType.String
                    || cursor.Value<string>() == null
                    || cursor.Value<string>().Length != 32
                    || !cursor.Value<string>().All(IsHex)
                )
                    throw Stale();
                query.Cursor = cursor.Value<string>();
            }
            Alias(parameters, "include_inactive", "includeInactive");
            Alias(parameters, "search_inactive", "searchInactive");
            var toolParams = new ToolParams(parameters);
            query.Inactive = toolParams.GetBool("includeInactive", false) || toolParams.GetBool("searchInactive", false);
            JToken term = Alias(parameters, "search_term", "searchTerm");
            if (term == null || term.Type == JTokenType.Null || term.Type == JTokenType.String && string.IsNullOrEmpty(term.Value<string>()))
                term = parameters["target"];
            JToken method = Alias(parameters, "search_method", "searchMethod");
            if (term != null && term.Type != JTokenType.Null && !(term.Type == JTokenType.String && string.IsNullOrEmpty(term.Value<string>())))
                Add(query, method ?? new JValue("by_name"), term);
            JToken conditions = parameters["conditions"];
            if (conditions != null && conditions.Type != JTokenType.Null)
            {
                if (!(conditions is JArray array) || array.Count > 8)
                    throw Invalid("conditions must be an array with at most eight predicates.");
                foreach (JToken entry in array)
                {
                    if (
                        !(entry is JObject item)
                        || item.Count != 2
                        || item.Properties().Any(property => property.Name != "search_method" && property.Name != "search_term")
                    )
                        throw Invalid("Each condition must contain exactly search_method and search_term.");
                    Add(query, item["search_method"], item["search_term"]);
                }
            }
            if (query.Predicates.Count == 0 || query.Predicates.Count > 8)
                throw Invalid("Scoped searches require between one and eight predicates including the top-level search.");
            var types = new Dictionary<string, Type>(StringComparer.Ordinal);
            foreach (Predicate predicate in query.Predicates)
            {
                token.ThrowIfCancellationRequested();
                if (predicate.Method == "by_component")
                {
                    if (!types.TryGetValue(predicate.Term, out Type type))
                    {
                        type = GameObjectLookup.FindComponentType(predicate.Term);
                        types[predicate.Term] = type;
                    }
                    predicate.Component = type;
                }
            }
            query.Signature = new JArray(
                query.Scope,
                query.PageSize,
                query.Depth,
                query.Inactive,
                new JArray(query.Predicates.Select(item => new JArray(item.Method, item.Term)))
            ).ToString(Formatting.None);
            return query;
        }

        private static void Add(Query query, JToken method, JToken term)
        {
            if (
                method?.Type != JTokenType.String
                || term?.Type != JTokenType.String
                || string.IsNullOrEmpty(term.Value<string>())
                || term.Value<string>().Length > 512
            )
                throw Invalid("Every predicate needs a method and a nonempty term of at most 512 UTF-16 units.");
            string name = method.Value<string>();
            if (name != "by_name" && name != "by_tag" && name != "by_layer" && name != "by_component" && name != "by_path" && name != "by_id")
                throw Invalid("Unknown scoped search_method.");
            var predicate = new Predicate
            {
                Method = name,
                Term = term.Value<string>(),
                Number = 0,
            };
            if (name == "by_layer")
            {
                if (!int.TryParse(predicate.Term, NumberStyles.Integer, CultureInfo.InvariantCulture, out predicate.Number))
                    predicate.Number = LayerMask.NameToLayer(predicate.Term);
                if (predicate.Number < 0 || predicate.Number > 31)
                    predicate.Number = -1;
            }
            if (name == "by_id")
                int.TryParse(predicate.Term, NumberStyles.Integer, CultureInfo.InvariantCulture, out predicate.Number);
            query.Predicates.Add(predicate);
        }

        private static SceneInfo[] SelectScenes(string scope, CancellationToken token)
        {
            var result = new List<SceneInfo>();
            if (scope == "active")
            {
                Scene active = SceneManager.GetActiveScene();
                if (!Supported(active))
                    throw new Failure(
                        "invalid_scene_scope",
                        "The reported active scene is invalid, unloaded or an unsupported preview/stage context. Handle: " + active.handle
                    );
                AddScene(active, result, -1);
            }
            else
            {
                int count = SceneManager.sceneCount;
                if (count > SceneLimit)
                    throw Limit("all_loaded supports at most 32 SceneManager entries.");
                for (int i = 0; i < count; i++)
                {
                    token.ThrowIfCancellationRequested();
                    Scene scene = SceneManager.GetSceneAt(i);
                    if (Supported(scene))
                        AddScene(scene, result, i);
                }
            }
            return result.ToArray();
        }

        private static Scene ResolveScene(string scope, SceneInfo selected)
        {
            Scene scene;
            if (scope == "active")
                scene = SceneManager.GetActiveScene();
            else
            {
                int count = SceneManager.sceneCount;
                if (count > SceneLimit)
                    throw Limit("all_loaded supports at most 32 SceneManager entries.");
                if (selected.ManagerIndex < 0 || selected.ManagerIndex >= count)
                    throw Stale();
                scene = SceneManager.GetSceneAt(selected.ManagerIndex);
            }
            if (!Supported(scene) || scene.handle != selected.Handle)
                throw Stale();
            return scene;
        }

        private static bool Supported(Scene scene)
        {
            if (!scene.IsValid() || !scene.isLoaded || EditorSceneManager.IsPreviewScene(scene))
                return false;
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            return stage == null || stage.scene.handle != scene.handle;
        }

        private static void AddScene(Scene scene, List<SceneInfo> result, int managerIndex)
        {
            int roots = scene.rootCount;
            if (roots > RootLimit)
                throw Limit("Selected scene exceeds the 4096-root admission limit. Handle: " + scene.handle);
            result.Add(
                new SceneInfo
                {
                    Handle = scene.handle,
                    ManagerIndex = managerIndex,
                    Roots = roots,
                }
            );
        }

        private static bool SameScenes(SceneInfo[] left, SceneInfo[] right)
        {
            if (left.Length != right.Length)
                return false;
            for (int i = 0; i < left.Length; i++)
                if (left[i].Handle != right[i].Handle || left[i].ManagerIndex != right[i].ManagerIndex || left[i].Roots != right[i].Roots)
                    return false;
            return true;
        }

        private static State Resume(Query query, SceneInfo[] scenes)
        {
            Prune();
            if (query.Cursor == null)
                return new State
                {
                    Signature = query.Signature,
                    Scenes = scenes,
                    Epoch = _epoch,
                };
            if (
                !States.TryGetValue(query.Cursor, out State state)
                || state.Signature != query.Signature
                || state.Epoch != _epoch
                || !SameScenes(state.Scenes, scenes)
            )
                throw Stale();
            return state.Copy();
        }

        private static string Save(State state)
        {
            Prune();
            if (States.Count >= CacheLimit)
                States.Remove(States.OrderBy(item => item.Value.Expires).First().Key);
            string key = Guid.NewGuid().ToString("N");
            state.Expires = EditorApplication.timeSinceStartup + Lifetime;
            States.Add(key, state);
            return key;
        }

        private static void Prune()
        {
            double now = EditorApplication.timeSinceStartup;
            foreach (string key in States.Where(item => item.Value.Expires <= now).Select(item => item.Key).ToArray())
                States.Remove(key);
        }

        private static JToken Alias(JObject parameters, string snake, string camel)
        {
            JToken left = parameters[snake],
                right = parameters[camel];
            if (left != null && right != null && !JToken.DeepEquals(left, right))
                throw new ArgumentException("Conflicting aliases: " + snake + " and " + camel + ".");
            return left ?? right;
        }

        private static int Integer(JToken token, int fallback, int minimum, int maximum, string name)
        {
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out long value) || value < minimum || value > maximum)
                throw Invalid(name + " must be an integer between " + minimum + " and " + maximum + ".");
            return (int)value;
        }

        private static bool IsHex(char value) => value >= '0' && value <= '9' || value >= 'a' && value <= 'f' || value >= 'A' && value <= 'F';

        private static Failure Invalid(string message) => new Failure("invalid_parameters", message);

        private static Failure Stale() => new Failure("stale_cursor", "Source, options or Editor session changed, or cursor expired. Restart the search.");

        private static Failure Limit(string message) => new Failure("source_limit_exceeded", message);
    }
}
