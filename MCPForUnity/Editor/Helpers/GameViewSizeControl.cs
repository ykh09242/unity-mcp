using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    // Game View presets have no public API. Probe required members before mutation,
    // and report unsupported editor versions rather than assuming an internal layout.
    internal static class GameViewSizeControl
    {
        private const BindingFlags Members = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
        private const string RestoreKey = "MCPForUnity.GameViewRestoreV1";
        private const string Prefix = "MCP ";
        private const int MaxDimension = 8192;
        private const long MaxFixedPixels = 16777216;

        private sealed class Selection
        {
            public string group;
            public int index;
            public int width;
            public int height;
            public string size_type;
            public string label;
            public int view_instance_id;
        }

        private sealed class Api
        {
            public EditorWindow window;
            public object group;
            public string groupName;
            public PropertyInfo selectedIndex;
            public PropertyInfo targetRenderSize;
            public Type sizeType;
            public Type kindType;
            public MethodInfo getSize;
            public MethodInfo getCount;
            public MethodInfo addSize;
            public MethodInfo selectSize;

            public Api(int? viewId = null)
            {
                Assembly assembly = typeof(UnityEditor.Editor).Assembly;
                Type viewType = assembly.GetType("UnityEditor.GameView", true);
                Type sizesType = assembly.GetType("UnityEditor.GameViewSizes", true);
                sizeType = assembly.GetType("UnityEditor.GameViewSize", true);
                kindType = assembly.GetType("UnityEditor.GameViewSizeType", true);
                window = UnityEngine
                    .Resources.FindObjectsOfTypeAll(viewType)
                    .OfType<EditorWindow>()
                    .FirstOrDefault(view => !viewId.HasValue || view.GetInstanceIDCompat() == viewId.Value);
                if (window == null)
                    throw new InvalidOperationException("Open a Game View before querying or changing its size.");
                selectedIndex = Require(viewType.GetProperty("selectedSizeIndex", Members), "selectedSizeIndex");
                targetRenderSize = Require(viewType.GetProperty("targetRenderSize", Members), "targetRenderSize");
                selectSize = Require(viewType.GetMethod("SizeSelectionCallback", Members), "SizeSelectionCallback");
                var singleton = typeof(ScriptableSingleton<>).MakeGenericType(sizesType);
                object sizes = Require(singleton.GetProperty("instance", Members), "instance").GetValue(null);
                object groupType = Require(sizesType.GetProperty("currentGroupType", Members), "currentGroupType").GetValue(sizes);
                groupName = groupType.ToString();
                group = Require(sizesType.GetMethod("GetGroup", Members), "GetGroup").Invoke(sizes, new[] { groupType });
                Type groupClass = group.GetType();
                getSize = Require(groupClass.GetMethod("GetGameViewSize", Members), "GetGameViewSize");
                getCount = Require(groupClass.GetMethod("GetTotalCount", Members), "GetTotalCount");
                addSize = Require(groupClass.GetMethod("AddCustomSize", Members), "AddCustomSize");
                if (Count > 1024)
                    throw new InvalidOperationException("Game View preset count exceeds the supported limit of 1024.");
                foreach (string property in new[] { "width", "height", "sizeType", "baseText" })
                    Require(sizeType.GetProperty(property, Members), property);
                Require(sizeType.GetConstructor(new[] { kindType, typeof(int), typeof(int), typeof(string) }), "GameViewSize constructor");
            }

            public Selection Read(int index)
            {
                object size = getSize.Invoke(group, new object[] { index });
                return new Selection
                {
                    group = groupName,
                    index = index,
                    view_instance_id = window.GetInstanceIDCompat(),
                    width = (int)sizeType.GetProperty("width", Members).GetValue(size),
                    height = (int)sizeType.GetProperty("height", Members).GetValue(size),
                    size_type = sizeType.GetProperty("sizeType", Members).GetValue(size).ToString(),
                    label = (string)sizeType.GetProperty("baseText", Members).GetValue(size),
                };
            }

            public int Count => (int)getCount.Invoke(group, null);
            public Selection Current => Read((int)selectedIndex.GetValue(window));
            public object RenderSize
            {
                get
                {
                    var size = (Vector2)targetRenderSize.GetValue(window);
                    return new { width = Mathf.RoundToInt(size.x), height = Mathf.RoundToInt(size.y) };
                }
            }

            public void Select(int index)
            {
                selectSize.Invoke(window, new object[] { index, null });
                window.Repaint();
            }
        }

        private static T Require<T>(T member, string name)
            where T : class => member ?? throw new NotSupportedException($"Unity {Application.unityVersion} does not expose required Game View member {name}.");

        public static object Execute(string action, JObject parameters)
        {
            try
            {
                // Validate untrusted input before reflection touches a window or preset.
                Selection requested = action == "set_game_view_size" ? Parse(parameters) : null;
                var restores =
                    JsonConvert.DeserializeObject<Dictionary<string, Selection>>(SessionState.GetString(RestoreKey, "{}"))
                    ?? new Dictionary<string, Selection>();
                string token = parameters["restore_token"]?.ToString();
                if (action == "restore_game_view_size" && (string.IsNullOrEmpty(token) || !restores.TryGetValue(token, out requested)))
                    return new ErrorResponse("game_view_restore_token_not_found");
                var api = new Api(action == "restore_game_view_size" ? (int?)requested.view_instance_id : null);
                Selection previous = api.Current;
                if (action == "get_game_view_size")
                    return new SuccessResponse(
                        "Game View size.",
                        new
                        {
                            current_size = previous,
                            render_size = api.RenderSize,
                            view_instance_id = api.window.GetInstanceIDCompat(),
                            presets = Enumerable.Range(0, Math.Min(api.Count, 256)).Select(api.Read).ToArray(),
                            presets_truncated = api.Count > 256,
                        }
                    );
                if (action == "restore_game_view_size")
                {
                    if (requested.group != api.groupName)
                        return new ErrorResponse("Game View build-target group changed; switch back before restoring.");
                    int restoreIndex = Find(api, requested, true);
                    if (restoreIndex < 0)
                        return new ErrorResponse("Original Game View preset is no longer available.");
                    api.Select(restoreIndex);
                    restores.Remove(token);
                    SessionState.SetString(RestoreKey, JsonConvert.SerializeObject(restores));
                    return new SuccessResponse(
                        "Game View size restored.",
                        new
                        {
                            current_size = api.Current,
                            render_size = api.RenderSize,
                            previous_size = previous,
                        }
                    );
                }
                string preset = parameters["preset"]?.ToString();
                int index;
                if (!string.IsNullOrEmpty(preset))
                {
                    var matches = Enumerable
                        .Range(0, Math.Min(api.Count, 256))
                        .Where(i => string.Equals(api.Read(i).label, preset, StringComparison.OrdinalIgnoreCase))
                        .ToArray();
                    if (matches.Length != 1)
                        return new ErrorResponse("Preset name must uniquely match a label returned by get_game_view_size.");
                    index = matches[0];
                    Selection selected = api.Read(index);
                    if (selected.size_type == "FixedResolution")
                    {
                        try
                        {
                            ValidateFixedSize(selected.width, selected.height);
                        }
                        catch (ArgumentException)
                        {
                            return new ErrorResponse(
                                "game_view_size_limit_exceeded",
                                new
                                {
                                    width = selected.width,
                                    height = selected.height,
                                    max_dimension = MaxDimension,
                                    max_pixels = MaxFixedPixels,
                                }
                            );
                        }
                    }
                }
                else
                {
                    index = Find(api, requested, false);
                    if (index < 0)
                    {
                        if (Enumerable.Range(0, api.Count).Count(i => (api.Read(i).label ?? "").StartsWith(Prefix, StringComparison.Ordinal)) >= 16)
                            return new ErrorResponse("At most 16 MCP custom presets per group; reuse a preset or remove unused ones in Game View.");
                        object size = Activator.CreateInstance(
                            api.sizeType,
                            Enum.Parse(api.kindType, requested.size_type),
                            requested.width,
                            requested.height,
                            $"{Prefix}{requested.width}x{requested.height} {requested.size_type}"
                        );
                        api.addSize.Invoke(api.group, new[] { size });
                        index = api.Count - 1;
                    }
                }
                // Persist before selecting, so a domain reload cannot lose the restoration route.
                string restoreToken = Guid.NewGuid().ToString("N");
                if (restores.Count >= 16)
                    restores.Remove(restores.Keys.First());
                restores[restoreToken] = previous;
                SessionState.SetString(RestoreKey, JsonConvert.SerializeObject(restores));
                try
                {
                    api.Select(index);
                }
                catch (Exception e)
                {
                    return new ErrorResponse(
                        "game_view_size_change_failed",
                        new
                        {
                            previous_size = previous,
                            restore_token = restoreToken,
                            detail = (e.InnerException ?? e).Message,
                        }
                    );
                }
                return new SuccessResponse(
                    "Game View size selected.",
                    new
                    {
                        current_size = api.Current,
                        render_size = api.RenderSize,
                        previous_size = previous,
                        restore_token = restoreToken,
                        note = "Custom presets remain available for reuse; restoring changes the selected preset. Screenshots do not change this selection.",
                    }
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse(
                    "game_view_size_unavailable",
                    new { unity_version = Application.unityVersion, detail = (e.InnerException ?? e).Message }
                );
            }
        }

        private static Selection Parse(JObject parameters)
        {
            if (!string.IsNullOrEmpty(parameters["preset"]?.ToString()))
            {
                if (parameters["width"] != null || parameters["height"] != null || parameters["aspect_ratio"] != null)
                    throw new ArgumentException("Specify a preset or dimensions/aspect_ratio, not both.");
                return null;
            }
            if (parameters["aspect_ratio"] != null)
            {
                if (parameters["width"] != null || parameters["height"] != null)
                    throw new ArgumentException("Specify width/height or aspect_ratio, not both.");
                string[] parts = parameters["aspect_ratio"].ToString().Split(':');
                if (
                    parts.Length != 2
                    || !int.TryParse(parts[0], out int width)
                    || !int.TryParse(parts[1], out int height)
                    || width < 1
                    || width > MaxDimension
                    || height < 1
                    || height > MaxDimension
                )
                    throw new ArgumentException("aspect_ratio must be W:H with integer components between 1 and 8192.");
                return new Selection
                {
                    width = width,
                    height = height,
                    size_type = "AspectRatio",
                };
            }
            if (
                parameters["width"] == null
                || parameters["height"] == null
                || parameters["width"].Type == JTokenType.Null
                || parameters["height"].Type == JTokenType.Null
            )
                throw new ArgumentException("width and height are required for a fixed resolution.");
            if (
                !PaginationBounds.TryRead(parameters["width"], 0, int.MinValue, int.MaxValue, "width", out int w, out string error)
                || !PaginationBounds.TryRead(parameters["height"], 0, int.MinValue, int.MaxValue, "height", out int h, out error)
            )
                throw new ArgumentException(error);
            ValidateFixedSize(w, h);
            return new Selection
            {
                width = w,
                height = h,
                size_type = "FixedResolution",
            };
        }

        private static void ValidateFixedSize(int width, int height)
        {
            if (width < 1 || width > MaxDimension || height < 1 || height > MaxDimension)
            {
                throw new ArgumentException($"Fixed Game View width and height must be between 1 and {MaxDimension}.");
            }
            if ((long)width * height > MaxFixedPixels)
            {
                throw new ArgumentException("Game View resolution exceeds the 16 megapixel budget.");
            }
        }

        private static int Find(Api api, Selection size, bool label)
        {
            bool Matches(Selection candidate) =>
                candidate.width == size.width
                && candidate.height == size.height
                && candidate.size_type == size.size_type
                && (!label || candidate.label == size.label);
            if (label && size.index >= 0 && size.index < api.Count && Matches(api.Read(size.index)))
                return size.index;
            for (int i = 0; i < api.Count; i++)
            {
                Selection candidate = api.Read(i);
                if (Matches(candidate))
                    return i;
            }
            return -1;
        }
    }
}
