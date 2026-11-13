using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Resources.Editor
{
    /// <summary>
    /// Provides detailed information about the current editor selection.
    /// </summary>
    [InitializeOnLoad]
    [McpForUnityResource("get_selection")]
    public static class Selection
    {
        private const int MaximumPageSize = 500;
        private const int MaximumCursors = 64;
        private const int MaximumNameLength = 256;
        private static readonly string Session = Guid.NewGuid().ToString("N");
        private static readonly Dictionary<string, PageCursor> Cursors = new Dictionary<string, PageCursor>(StringComparer.Ordinal);
        private static long Revision;
        private static long AccessOrder;
        private static int[] SelectionIdentity;
        private static int ActiveIdentity;

        // Inert observation seam for native volume tests; no public command changes it.
        private static Action DescriptionObserved = null;

        private sealed class PageCursor
        {
            internal int Offset;
            internal int Size;
            internal string Fields;
            internal long Revision;
            internal double Created;
            internal long Order;
        }

        static Selection()
        {
            UnityEditor.Selection.selectionChanged += InvalidatePages;
            AssemblyReloadEvents.beforeAssemblyReload += InvalidatePages;
            EditorApplication.quitting += InvalidatePages;
        }

        private static void InvalidatePages()
        {
            Revision++;
            Cursors.Clear();
            SelectionIdentity = null;
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                if (@params?["paged"] != null)
                {
                    if (@params["paged"].Type != JTokenType.Boolean)
                        return new ErrorResponse("invalid_selection_page", new { reason = "paged must be a boolean" });
                    if (@params["paged"].Value<bool>())
                        return ReadPage(@params);
                }
                var activeObject = UnityEditor.Selection.activeObject;
                var selectionInfo = new
                {
                    activeObject = activeObject?.name,
                    activeGameObject = UnityEditor.Selection.activeGameObject?.name,
                    activeTransform = UnityEditor.Selection.activeTransform?.name,
                    activeInstanceID = activeObject?.GetInstanceIDCompat() ?? 0,
                    count = UnityEditor.Selection.count,
                    objects = UnityEditor
                        .Selection.objects.Select(obj => new
                        {
                            name = obj?.name,
                            type = obj?.GetType().FullName,
                            instanceID = obj?.GetInstanceIDCompat(),
                        })
                        .ToList(),
                    gameObjects = UnityEditor.Selection.gameObjects.Select(go => new { name = go?.name, instanceID = go?.GetInstanceIDCompat() }).ToList(),
                    assetGUIDs = UnityEditor.Selection.assetGUIDs,
                };

                return new SuccessResponse("Retrieved current selection details.", selectionInfo);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error getting selection: {e.Message}");
            }
        }

        private static object ReadPage(JObject parameters)
        {
            if (!PaginationBounds.TryRead(parameters["page_size"], 50, 1, MaximumPageSize, "page_size", out int size, out string error))
                return new ErrorResponse("invalid_selection_page", new { reason = error });
            var fieldToken = parameters["fields"];
            if (fieldToken != null && fieldToken.Type != JTokenType.String)
                return new ErrorResponse("invalid_selection_fields");
            string fields = fieldToken?.Value<string>() ?? "objects";
            if (fields != "objects" && fields != "gameObjects" && fields != "assetGUIDs" && fields != "all" && fields != "none")
                return new ErrorResponse("invalid_selection_fields");

            var active = UnityEditor.Selection.activeObject;
            int activeId = active != null ? active.GetInstanceIDCompat() : 0;
            // The event can be delayed. Compare exact cheap IDs/order before accepting a
            // continuation, so equal-size changes and active-object changes cannot mix pages.
            // Counts-only reads need neither this array nor a retained identity.
            var objects = fields == "none" ? null : UnityEditor.Selection.objects;
            if (objects != null)
            {
                bool changed = SelectionIdentity != null && (SelectionIdentity.Length != objects.Length || ActiveIdentity != activeId);
                if (SelectionIdentity != null && !changed)
                    for (int i = 0; i < objects.Length && !changed; i++)
                        changed = SelectionIdentity[i] != (objects[i] != null ? objects[i].GetInstanceIDCompat() : 0);
                if (changed)
                    InvalidatePages();
                if (SelectionIdentity == null)
                {
                    SelectionIdentity = new int[objects.Length];
                    for (int i = 0; i < objects.Length; i++)
                        SelectionIdentity[i] = objects[i] != null ? objects[i].GetInstanceIDCompat() : 0;
                    ActiveIdentity = activeId;
                }
            }

            double now = EditorApplication.timeSinceStartup;
            foreach (string key in Cursors.Where(pair => now - pair.Value.Created >= 30).Select(pair => pair.Key).ToArray())
                Cursors.Remove(key);
            int offset = 0;
            var cursorToken = parameters["cursor"];
            if (cursorToken != null)
            {
                if (
                    cursorToken.Type != JTokenType.String
                    || !Cursors.TryGetValue(cursorToken.Value<string>(), out var previous)
                    || previous.Revision != Revision
                    || previous.Size != size
                    || previous.Fields != fields
                )
                    return new ErrorResponse("stale_cursor", new { reason = "Selection changed, cursor expired, or page options differ. Restart the read." });
                offset = previous.Offset;
            }

            // Unity allocates these reference/identifier arrays. Only the selected page is
            // described below; no full-selection name/type projection or retained descriptions.
            long revision = Revision;
            var gameObjects = UnityEditor.Selection.gameObjects;
            var assetGuids = UnityEditor.Selection.assetGUIDs;
            var data = new JObject
            {
                ["activeObject"] = Name(active),
                ["activeGameObject"] = Name(UnityEditor.Selection.activeGameObject),
                ["activeTransform"] = Name(UnityEditor.Selection.activeTransform),
                ["activeInstanceID"] = activeId,
                ["count"] = UnityEditor.Selection.count,
                ["objectCount"] = objects?.Length ?? UnityEditor.Selection.count,
                ["gameObjectCount"] = gameObjects.Length,
                ["assetGUIDCount"] = assetGuids.Length,
                ["selectionRevision"] = Session + ":" + revision,
                ["fields"] = fields,
                ["pageSize"] = size,
                ["offset"] = offset,
                ["maxNameLength"] = MaximumNameLength,
            };
            int total = 0;
            if (fields == "objects" || fields == "all")
            {
                var rows = new JArray();
                for (int i = offset; i < objects.Length && i - offset < size; i++)
                {
                    DescriptionObserved?.Invoke();
                    var obj = objects[i];
                    rows.Add(
                        new JObject
                        {
                            ["name"] = Name(obj),
                            ["type"] = obj != null ? obj.GetType().FullName : null,
                            ["instanceID"] = obj != null ? obj.GetInstanceIDCompat() : (int?)null,
                        }
                    );
                }
                data["objects"] = rows;
                total = objects.Length;
            }
            if (fields == "gameObjects" || fields == "all")
            {
                var rows = new JArray();
                for (int i = offset; i < gameObjects.Length && i - offset < size; i++)
                {
                    DescriptionObserved?.Invoke();
                    var obj = gameObjects[i];
                    rows.Add(new JObject { ["name"] = Name(obj), ["instanceID"] = obj != null ? obj.GetInstanceIDCompat() : (int?)null });
                }
                data["gameObjects"] = rows;
                total = Math.Max(total, gameObjects.Length);
            }
            if (fields == "assetGUIDs" || fields == "all")
            {
                var rows = new JArray();
                for (int i = offset; i < assetGuids.Length && i - offset < size; i++)
                    rows.Add(assetGuids[i]);
                data["assetGUIDs"] = rows;
                total = Math.Max(total, assetGuids.Length);
            }
            if (revision != Revision)
                return new ErrorResponse("stale_cursor", new { reason = "Selection changed during the read. Restart the read." });
            bool more = offset + size < total;
            string next = null;
            if (more)
            {
                if (Cursors.Count >= MaximumCursors)
                    Cursors.Remove(Cursors.OrderBy(pair => pair.Value.Order).First().Key);
                next = Guid.NewGuid().ToString("N");
                Cursors[next] = new PageCursor
                {
                    Offset = offset + size,
                    Size = size,
                    Fields = fields,
                    Revision = revision,
                    Created = now,
                    Order = ++AccessOrder,
                };
            }
            data["hasMore"] = more;
            data["nextCursor"] = next;
            return new SuccessResponse("Retrieved bounded selection details.", data);
        }

        private static string Name(UnityEngine.Object value)
        {
            if (value == null)
                return null;
            string name = value.name;
            int length = Math.Min(name.Length, MaximumNameLength);
            if (length < name.Length && length > 0 && char.IsHighSurrogate(name[length - 1]) && char.IsLowSurrogate(name[length]))
                length--;
            return name.Substring(0, length);
        }
    }
}
