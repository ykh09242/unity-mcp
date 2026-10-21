using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Bounded, editor-session continuation state for prefab preorder and direct child reads.
    /// No Unity objects or loaded prefab contents are retained between requests.
    /// </summary>
    internal static class HierarchyPaging
    {
        internal const int MaxPageSize = 500;
        internal const int MaxDepth = 128;
        internal const int WorkLimit = 4096;
        internal const int OutputLimit = 262144;
        internal const int ComponentLimit = 64;
        internal const int StringLimit = 512;
        private const int StateLimit = 32;
        private const double Lifetime = 300;
        private static readonly Dictionary<string, State> States = new Dictionary<string, State>();
        private static int _hierarchyEpoch;

        static HierarchyPaging()
        {
            EditorApplication.hierarchyChanged += () =>
            {
                unchecked
                {
                    _hierarchyEpoch++;
                }
            };
        }

        internal sealed class Options
        {
            internal int PageSize;
            internal int Depth;
            internal string Cursor;
        }

        internal sealed class State
        {
            internal string Scope;
            internal string Revision;
            internal int PageSize;
            internal int Depth;
            internal int[] NextPath;
            internal int Count;
            internal bool DepthLimited;
            internal string Observed;
            internal double Expires;
        }

        internal sealed class Budget
        {
            internal int Used;
            internal bool Truncated;
            internal readonly CancellationToken Token;

            internal Budget(CancellationToken token)
            {
                Token = token;
            }

            internal void Spend(int count = 1)
            {
                Token.ThrowIfCancellationRequested();
                if (count < 0 || Used > WorkLimit - count)
                    throw new InvalidOperationException("Hierarchy work limit exhausted.");
                Used += count;
            }

            internal string Text(string value)
            {
                if (value == null || value.Length <= StringLimit)
                    return value;
                Truncated = true;
                // Do not split a surrogate pair at the bound.
                int length = char.IsHighSurrogate(value[StringLimit - 1]) ? StringLimit - 1 : StringLimit;
                return value.Substring(0, length);
            }
        }

        internal static Options Parse(JObject parameters, bool children = false)
        {
            string pageName = children ? "children_page_size" : "page_size";
            string cursorName = children ? "children_cursor" : "cursor";
            var pageToken = parameters[pageName] ?? parameters[children ? "childrenPageSize" : "pageSize"];
            int page = ReadInteger(pageToken, 50, 1, MaxPageSize, pageName);
            int depth = children ? 0 : ReadInteger(parameters["max_depth"] ?? parameters["maxDepth"], 64, 0, MaxDepth, "max_depth");
            var cursor = parameters[cursorName];
            string cursorValue = cursor?.Type == JTokenType.String ? cursor.Value<string>() : null;
            // JObject callers can create a String-typed JValue whose value is null.
            // It has the same absent-cursor meaning as a JSON null on the wire.
            if (cursor != null && cursor.Type != JTokenType.Null && (cursor.Type != JTokenType.String || (cursorValue != null && cursorValue.Length != 32)))
                throw new ArgumentException("stale_cursor: expected an opaque editor-session cursor; restart the read.");
            return new Options
            {
                PageSize = page,
                Depth = depth,
                Cursor = cursorValue,
            };
        }

        private static int ReadInteger(JToken token, int fallback, int minimum, int maximum, string name)
        {
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out long value) || value < minimum || value > maximum)
                throw new ArgumentException($"{name} must be an integer between {minimum} and {maximum}.");
            return (int)value;
        }

        internal static State Resume(Options options, string scope, string revision)
        {
            Prune();
            if (options.Cursor == null)
                return new State
                {
                    Scope = scope,
                    Revision = revision,
                    PageSize = options.PageSize,
                    Depth = options.Depth,
                    NextPath = Array.Empty<int>(),
                };
            if (
                !States.TryGetValue(options.Cursor, out State state)
                || state.Scope != scope
                || state.Revision != revision
                || state.PageSize != options.PageSize
                || state.Depth != options.Depth
            )
                throw new ArgumentException("stale_cursor: source, target, options or editor session changed; restart the read.");
            // Copy on use: failed/cancelled requests must not advance a reusable cursor.
            return new State
            {
                Scope = state.Scope,
                Revision = state.Revision,
                PageSize = state.PageSize,
                Depth = state.Depth,
                NextPath = (int[])state.NextPath.Clone(),
                Count = state.Count,
                DepthLimited = state.DepthLimited,
                Observed = state.Observed,
            };
        }

        internal static string Save(State state)
        {
            Prune();
            if (States.Count >= StateLimit)
                States.Remove(States.OrderBy(pair => pair.Value.Expires).First().Key);
            string key = Guid.NewGuid().ToString("N");
            state.Expires = EditorApplication.timeSinceStartup + Lifetime;
            States.Add(key, state);
            return key;
        }

        private static void Prune()
        {
            double now = EditorApplication.timeSinceStartup;
            foreach (string key in States.Where(pair => pair.Value.Expires <= now).Select(pair => pair.Key).ToArray())
                States.Remove(key);
        }

        // m_Component is Unity's native GameObject component-reference array. Read only the bounded
        // prefix through the 2021.3 SerializedProperty API; never serialize component properties.
        internal static List<string> ComponentNames(GameObject go, Budget budget, bool qualified, out int count)
        {
            budget.Spend();
            var names = new List<string>();
            using (var serialized = new SerializedObject(go))
            {
                var components = serialized.FindProperty("m_Component");
                if (components == null || !components.isArray)
                    throw new InvalidOperationException("GameObject component references are unavailable.");
                count = components.arraySize;
                int take = Math.Min(count, ComponentLimit);
                for (int i = 0; i < take; i++)
                {
                    budget.Spend();
                    var reference = components.GetArrayElementAtIndex(i).FindPropertyRelative("component");
                    var component = reference?.objectReferenceValue as Component;
                    if (component != null)
                        names.Add(budget.Text(qualified ? component.GetType().FullName : component.GetType().Name));
                }
                budget.Truncated |= count > take;
            }
            return names;
        }

        internal static string ObjectPath(Transform transform, Budget budget)
        {
            var names = new List<string>();
            while (transform != null && names.Count <= MaxDepth)
            {
                budget.Spend();
                names.Add(budget.Text(transform.name));
                transform = transform.parent;
            }
            budget.Truncated |= transform != null;
            names.Reverse();
            // Bound construction as well as the final string.
            var path = new StringBuilder(StringLimit);
            foreach (string name in names)
            {
                int remaining = StringLimit - path.Length;
                if (remaining <= 1)
                {
                    budget.Truncated = true;
                    break;
                }
                if (path.Length > 0)
                    path.Append('/');
                int take = Math.Min(name.Length, StringLimit - path.Length);
                if (take > 0 && char.IsHighSurrogate(name[take - 1]))
                    take--;
                path.Append(name, 0, take);
                budget.Truncated |= take < name.Length;
                if (path.Length == StringLimit)
                    budget.Truncated = true;
            }
            return budget.Text(path.ToString());
        }

        private static string Anchor(Transform parent, int index, Budget budget)
        {
            if (index < 0 || index >= parent.childCount)
                return "-";
            budget.Spend();
            var child = parent.GetChild(index);
            return child.gameObject.GetInstanceIDCompat() + ":" + budget.Text(child.name);
        }

        private static string SceneObservation(GameObject go, int previous, Budget budget)
        {
            Transform transform = go.transform;
            budget.Spend();
            return string.Join(
                "|",
                go.scene.handle,
                go.GetInstanceIDCompat(),
                transform.parent != null ? transform.parent.gameObject.GetInstanceIDCompat() : 0,
                transform.childCount,
                EditorUtility.GetDirtyCount(go),
                EditorUtility.GetDirtyCount(transform),
                _hierarchyEpoch,
                budget.Text(go.name),
                Anchor(transform, 0, budget),
                Anchor(transform, transform.childCount - 1, budget),
                Anchor(transform, previous, budget)
            );
        }

        internal static JObject Children(GameObject go, Options options, Budget budget)
        {
            string scope = "children:" + go.scene.handle + ":" + go.GetInstanceIDCompat();
            State state = Resume(options, scope, "live_indices");
            if (state.Observed != null && state.Observed != SceneObservation(go, state.Count - 1, budget))
                throw new ArgumentException("stale_cursor: an observed hierarchy change invalidated this live-index read; restart after edits.");
            int total = go.transform.childCount;
            int end = Math.Min(total, state.Count + options.PageSize);
            var ids = new JArray();
            for (int i = state.Count; i < end; i++)
            {
                budget.Spend();
                ids.Add(go.transform.GetChild(i).gameObject.GetInstanceIDCompat());
            }
            state.Count = end;
            state.Observed = SceneObservation(go, end - 1, budget);
            return new JObject
            {
                ["children"] = ids,
                ["childrenTotal"] = total,
                ["childrenPageSize"] = options.PageSize,
                ["childrenNextCursor"] = end < total ? Save(state) : null,
                ["childrenTruncated"] = end < total,
                ["childrenConsistency"] = "live_indices",
                ["childrenRestartAdvice"] =
                    "Restart after any hierarchy edit. Only bounded target, revision and observed-anchor checks are performed; unobserved same-frame mutations may affect later pages.",
            };
        }

        internal static int CompactBytes(JToken value) => Encoding.UTF8.GetByteCount(value.ToString(Formatting.None));
    }
}
