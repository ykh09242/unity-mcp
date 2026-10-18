using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Bounded observation of existing TMP caches. Never reads text or asks TMP to calculate anything.</summary>
    internal static class UguiTmpLayoutDiagnostics
    {
        internal static Func<Type, UnityEngine.Object[]> InventoryForTests = null;
        internal static Action<Component, string> ObservationForTests = null;
        private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
        private static readonly string[] MetricNames =
        {
            "native_overflow",
            "first_overflow_index",
            "native_truncated",
            "character_count",
            "line_count",
            "base_font_size",
            "autosize",
            "min_point_size",
            "max_point_size",
            "fallback_characters",
            "font_count",
            "ink_bounds",
            "text_rect",
            "margins",
        };

        private sealed class Options
        {
            internal JToken Target;
            internal bool Inactive;
            internal int Nodes = 200,
                Depth = 10,
                Work = 20000,
                Characters = 65536;

            internal static Options Parse(JObject raw)
            {
                var allowed = new HashSet<string>(
                    new[]
                    {
                        "action",
                        "target",
                        "include_inactive",
                        "includeInactive",
                        "max_nodes",
                        "maxNodes",
                        "max_depth",
                        "maxDepth",
                        "max_evaluations",
                        "maxEvaluations",
                        "max_characters",
                        "maxCharacters",
                    },
                    StringComparer.Ordinal
                );
                if (raw == null || raw.Properties().Any(p => !allowed.Contains(p.Name)))
                    throw new ArgumentException();
                var o = new Options { Target = raw["target"] };
                if (
                    o.Target == null
                    || !(
                        o.Target.Type == JTokenType.Integer && int.TryParse(o.Target.ToString(), out _)
                        || o.Target.Type == JTokenType.String && Valid(o.Target.Value<string>(), 4096) && !string.IsNullOrWhiteSpace(o.Target.Value<string>())
                    )
                )
                    throw new ArgumentException();
                o.Nodes = Number(One(raw, "max_nodes", "maxNodes"), 200, 1, 1000);
                o.Depth = Number(One(raw, "max_depth", "maxDepth"), 10, 0, 24);
                o.Work = Number(One(raw, "max_evaluations", "maxEvaluations"), 20000, 1, 200000);
                o.Characters = Number(One(raw, "max_characters", "maxCharacters"), 65536, 1, 262144);
                var inactive = One(raw, "include_inactive", "includeInactive");
                if (inactive != null && inactive.Type != JTokenType.Boolean)
                    throw new ArgumentException();
                o.Inactive = inactive?.Value<bool>() ?? false;
                return o;
            }

            private static JToken One(JObject raw, string snake, string camel)
            {
                if (raw.Property(snake) != null && raw.Property(camel) != null)
                    throw new ArgumentException();
                return raw[snake] ?? raw[camel];
            }

            private static int Number(JToken token, int fallback, int low, int high)
            {
                if (token == null)
                    return fallback;
                if (
                    token.Type != JTokenType.Integer
                    || !int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int value)
                    || value < low
                    || value > high
                )
                    throw new ArgumentException();
                return value;
            }
        }

        private sealed class Budget
        {
            internal Options Options;
            internal int Visited,
                Work,
                Characters,
                CachedCharacters,
                CachedLines;
            internal readonly JArray Reasons = new JArray();

            internal bool Charge(int amount)
            {
                if (amount < 0 || amount > Options.Work - Work)
                {
                    Stop("max_evaluations");
                    return false;
                }
                Work += amount;
                return true;
            }

            internal void Stop(string reason)
            {
                if (!Reasons.Any(x => x.Value<string>() == reason))
                    Reasons.Add(reason);
            }

            internal string Copy(string value, int cap)
            {
                value = value ?? "";
                int count = Math.Min(value.Length, Math.Min(cap, Options.Characters - Characters));
                if (count > 0 && char.IsHighSurrogate(value[count - 1]))
                    count--;
                string result = value.Substring(0, count);
                if (!Valid(result, cap))
                {
                    result = "";
                    Stop("invalid_metadata");
                }
                Characters += result.Length;
                if (value.Length > cap)
                    Stop("per_string_limit");
                if (count < Math.Min(value.Length, cap))
                    Stop("max_characters");
                return result;
            }
        }

        private sealed class Privacy
        {
            internal readonly HashSet<int> Owners = new HashSet<int>();
            internal readonly HashSet<int> Renderers = new HashSet<int>();

            internal static Privacy Capture(Type tmpAssemblyType)
            {
                var p = new Privacy();
                int total = 0;
                foreach (
                    var type in new[]
                    {
                        EngineType("UnityEngine.UI", "UnityEngine.UI.InputField"),
                        tmpAssemblyType.Assembly.GetType("TMPro.TMP_InputField", false),
                    }
                )
                {
                    if (type == null)
                        throw new InvalidOperationException();
                    var display = type.GetField("m_TextComponent", Fields);
                    var placeholder = type.GetField("m_Placeholder", Fields);
                    if (
                        display == null
                        || placeholder == null
                        || !typeof(Component).IsAssignableFrom(display.FieldType)
                        || !typeof(Component).IsAssignableFrom(placeholder.FieldType)
                    )
                        throw new InvalidOperationException();
                    var inventory = InventoryForTests != null ? InventoryForTests(type) : UnityEngine.Resources.FindObjectsOfTypeAll(type);
                    if (inventory == null || inventory.Length > 4096 - total)
                        throw new InvalidOperationException();
                    total += inventory.Length;
                    foreach (var value in inventory)
                    {
                        var component = value as Component;
                        if (component == null || !type.IsInstanceOfType(component))
                            throw new InvalidOperationException();
                        p.Owners.Add(component.transform.GetInstanceIDCompat());
                        foreach (var field in new[] { display, placeholder })
                        {
                            var renderer = field.GetValue(component) as Component;
                            if (renderer != null)
                                p.Renderers.Add(renderer.GetInstanceIDCompat());
                        }
                        if (p.Renderers.Count > 8192)
                            throw new InvalidOperationException();
                    }
                }
                return p;
            }

            internal bool Redact(Component text)
            {
                if (Renderers.Contains(text.GetInstanceIDCompat()))
                    return true;
                Transform current = text.transform;
                for (int i = 0; i < 128 && current != null; i++, current = current.parent)
                    if (Owners.Contains(current.GetInstanceIDCompat()))
                        return true;
                if (current != null)
                    throw new InvalidOperationException();
                return false;
            }
        }

        private sealed class Adapter
        {
            internal Type Text,
                Base,
                Info,
                Character,
                Line;
            internal readonly Dictionary<string, FieldInfo> TextFields = new Dictionary<string, FieldInfo>();
            internal readonly Dictionary<string, FieldInfo> InfoFields = new Dictionary<string, FieldInfo>();
            internal readonly Dictionary<string, FieldInfo> CharacterFields = new Dictionary<string, FieldInfo>();
            internal FieldInfo LineHeight;

            internal static Adapter Create()
            {
                Type text = EngineType("Unity.TextMeshPro", "TMPro.TextMeshProUGUI");
                if (text == null)
                    return null;
                var a = new Adapter
                {
                    Text = text,
                    Base = text.Assembly.GetType("TMPro.TMP_Text"),
                    Info = text.Assembly.GetType("TMPro.TMP_TextInfo"),
                    Character = text.Assembly.GetType("TMPro.TMP_CharacterInfo"),
                    Line = text.Assembly.GetType("TMPro.TMP_LineInfo"),
                };
                if (a.Base == null || text.BaseType != a.Base || a.Info == null || a.Character == null || a.Line == null)
                    throw new InvalidOperationException();
                foreach (
                    var pair in new[]
                    {
                        new { Name = "m_textInfo", Type = a.Info },
                        new { Name = "m_havePropertiesChanged", Type = typeof(bool) },
                        new { Name = "m_isLayoutDirty", Type = typeof(bool) },
                        new { Name = "m_IsAutoSizePointSizeSet", Type = typeof(bool) },
                        new { Name = "m_firstOverflowCharacterIndex", Type = typeof(int) },
                        new { Name = "m_isTextTruncated", Type = typeof(bool) },
                        new { Name = "m_fontSize", Type = typeof(float) },
                        new { Name = "m_enableAutoSizing", Type = typeof(bool) },
                        new { Name = "m_margin", Type = typeof(Vector4) },
                        new { Name = "m_fontAsset", Type = text.Assembly.GetType("TMPro.TMP_FontAsset") },
                        new { Name = "m_overflowMode", Type = text.Assembly.GetType("TMPro.TextOverflowModes") },
                        new { Name = "m_linkedTextComponent", Type = a.Base },
                    }
                )
                    a.TextFields.Add(pair.Name, Exact(a.Base, pair.Name, pair.Type));
                foreach (string name in new[] { "characterCount", "lineCount" })
                    a.InfoFields.Add(name, Exact(a.Info, name, typeof(int)));
                a.InfoFields.Add("characterInfo", Exact(a.Info, "characterInfo", a.Character.MakeArrayType()));
                a.InfoFields.Add("lineInfo", Exact(a.Info, "lineInfo", a.Line.MakeArrayType()));
                foreach (
                    var pair in new[]
                    {
                        new { Name = "isVisible", Type = typeof(bool) },
                        new { Name = "pointSize", Type = typeof(float) },
                        new { Name = "fontAsset", Type = text.Assembly.GetType("TMPro.TMP_FontAsset") },
                        new { Name = "bottomLeft", Type = typeof(Vector3) },
                        new { Name = "topLeft", Type = typeof(Vector3) },
                        new { Name = "topRight", Type = typeof(Vector3) },
                        new { Name = "bottomRight", Type = typeof(Vector3) },
                    }
                )
                    a.CharacterFields.Add(pair.Name, Exact(a.Character, pair.Name, pair.Type));
                a.LineHeight = Exact(a.Line, "lineHeight", typeof(float));
                return a;
            }

            private static FieldInfo Exact(Type declaring, string name, Type expected)
            {
                var f = declaring.GetField(name, Fields);
                if (f == null || f.IsStatic || f.DeclaringType != declaring || f.FieldType != expected)
                    throw new InvalidOperationException();
                return f;
            }

            private object Read(Component component, string name)
            {
                ObservationForTests?.Invoke(component, name);
                return TextFields[name].GetValue(component);
            }

            internal void Observe(Component text, JObject node, Budget b)
            {
                if (text.GetType() != Text)
                {
                    Unknown(node, "unsupported_type");
                    return;
                }
                if (!b.Charge(14))
                {
                    Unknown(node, "max_evaluations");
                    return;
                }
                if ((bool)Read(text, "m_havePropertiesChanged") || (bool)Read(text, "m_isLayoutDirty"))
                {
                    Unknown(node, "dirty_layout");
                    return;
                }
                int overflowMode = Convert.ToInt32(Read(text, "m_overflowMode"), CultureInfo.InvariantCulture);
                if (overflowMode < 0 || overflowMode > 6 || overflowMode == 5 || overflowMode == 6 || Read(text, "m_linkedTextComponent") != null)
                {
                    Unknown(node, "unsupported_overflow_mode");
                    return;
                }
                var info = Read(text, "m_textInfo");
                if (info == null || !(bool)Read(text, "m_IsAutoSizePointSizeSet"))
                {
                    Unknown(node, "uninitialized_layout");
                    return;
                }
                int count = (int)InfoFields["characterCount"].GetValue(info),
                    lines = (int)InfoFields["lineCount"].GetValue(info);
                var characters = InfoFields["characterInfo"].GetValue(info) as Array;
                var lineData = InfoFields["lineInfo"].GetValue(info) as Array;
                if (count < 0 || lines < 0 || characters == null || lineData == null || count > characters.Length || lines > lineData.Length)
                {
                    Unknown(node, "inconsistent_cache");
                    return;
                }
                if (count > 4096 || count > 16384 - b.CachedCharacters)
                {
                    Unknown(node, "cache_character_limit");
                    return;
                }
                if (lines > 512 || lines > 2048 - b.CachedLines)
                {
                    Unknown(node, "cache_line_limit");
                    return;
                }
                if (!b.Charge(count * 14 + lines * 2 + 8))
                {
                    Unknown(node, "max_evaluations");
                    return;
                }
                int overflow = (int)Read(text, "m_firstOverflowCharacterIndex");
                float baseSize = (float)Read(text, "m_fontSize");
                bool autosize = (bool)Read(text, "m_enableAutoSizing"),
                    truncated = (bool)Read(text, "m_isTextTruncated");
                Vector4 margins = (Vector4)Read(text, "m_margin");
                var primary = Read(text, "m_fontAsset");
                Rect rect = ((RectTransform)text.transform).rect;
                if (
                    !Finite(baseSize)
                    || baseSize <= 0
                    || overflow < -1
                    || !Finite(rect.x)
                    || !Finite(rect.y)
                    || !Finite(rect.width)
                    || !Finite(rect.height)
                    || rect.width < 0
                    || rect.height < 0
                    || !Finite(margins.x)
                    || !Finite(margins.y)
                    || !Finite(margins.z)
                    || !Finite(margins.w)
                )
                {
                    Unknown(node, "inconsistent_cache");
                    return;
                }
                float minPoint = float.PositiveInfinity,
                    maxPoint = float.NegativeInfinity,
                    left = float.PositiveInfinity,
                    bottom = float.PositiveInfinity,
                    right = float.NegativeInfinity,
                    top = float.NegativeInfinity;
                int fallback = 0;
                var fonts = new HashSet<int>();
                int primaryId = primary is UnityEngine.Object primaryObject ? primaryObject.GetInstanceIDCompat() : 0;
                for (int i = 0; i < count; i++)
                {
                    object c = characters.GetValue(i);
                    b.CachedCharacters++;
                    float point = (float)CharacterFields["pointSize"].GetValue(c);
                    if (!Finite(point) || point < 0)
                    {
                        Unknown(node, "inconsistent_cache");
                        return;
                    }
                    minPoint = Mathf.Min(minPoint, point);
                    maxPoint = Mathf.Max(maxPoint, point);
                    object font = CharacterFields["fontAsset"].GetValue(c);
                    if (font != null)
                    {
                        if (font.GetType() != TextFields["m_fontAsset"].FieldType || !(font is UnityEngine.Object fontObject))
                        {
                            Unknown(node, "unsupported_type");
                            return;
                        }
                        int fontId = fontObject.GetInstanceIDCompat();
                        fonts.Add(fontId);
                        if (fontId != primaryId)
                            fallback++;
                    }
                    if (fonts.Count > 8)
                    {
                        Unknown(node, "cache_font_limit");
                        return;
                    }
                    if (!(bool)CharacterFields["isVisible"].GetValue(c))
                        continue;
                    foreach (string corner in new[] { "bottomLeft", "topLeft", "topRight", "bottomRight" })
                    {
                        var pointValue = (Vector3)CharacterFields[corner].GetValue(c);
                        if (!Finite(pointValue.x) || !Finite(pointValue.y) || !Finite(pointValue.z))
                        {
                            Unknown(node, "unsupported_geometry");
                            return;
                        }
                        left = Mathf.Min(left, pointValue.x);
                        bottom = Mathf.Min(bottom, pointValue.y);
                        right = Mathf.Max(right, pointValue.x);
                        top = Mathf.Max(top, pointValue.y);
                    }
                }
                for (int i = 0; i < lines; i++)
                {
                    b.CachedLines++;
                    if (!Finite((float)LineHeight.GetValue(lineData.GetValue(i))))
                    {
                        Unknown(node, "inconsistent_cache");
                        return;
                    }
                }
                if (!float.IsPositiveInfinity(left) && (!Finite(right - left) || !Finite(top - bottom)))
                {
                    Unknown(node, "unsupported_geometry");
                    return;
                }
                node["status"] = "observed";
                node["reason"] = null;
                node["native_overflow"] = overflow != -1;
                node["first_overflow_index"] = overflow;
                node["native_truncated"] = truncated;
                node["character_count"] = count;
                node["line_count"] = lines;
                node["base_font_size"] = baseSize;
                node["autosize"] = autosize;
                node["min_point_size"] = count == 0 ? JValue.CreateNull() : new JValue(minPoint);
                node["max_point_size"] = count == 0 ? JValue.CreateNull() : new JValue(maxPoint);
                node["fallback_characters"] = fallback;
                node["font_count"] = fonts.Count;
                node["ink_bounds"] = float.IsPositiveInfinity(left) ? (JToken)JValue.CreateNull() : new JArray(left, bottom, right - left, top - bottom);
                node["text_rect"] = new JArray(rect.x, rect.y, rect.width, rect.height);
                node["margins"] = new JArray(margins.x, margins.y, margins.z, margins.w);
            }
        }

        private sealed class Branch
        {
            internal Transform Transform;
            internal int Depth,
                Next;
            internal string Path;
            internal bool Entered;
        }

        internal static object Diagnose(JObject raw)
        {
            Options options;
            try
            {
                options = Options.Parse(raw);
            }
            catch
            {
                return Error("invalid_text_request");
            }
            GameObject target;
            try
            {
                target = ManageUGUI.Resolve(options.Target, options.Inactive);
            }
            catch
            {
                return Error("invalid_text_target");
            }
            if (target == null || !(target.transform is RectTransform))
                return Error("invalid_text_target");
            Adapter adapter;
            try
            {
                adapter = Adapter.Create();
            }
            catch
            {
                return Error("unsupported_tmp_version");
            }
            if (adapter == null)
                return Error("tmp_unavailable");
            Privacy privacy;
            try
            {
                privacy = Privacy.Capture(adapter.Text);
            }
            catch
            {
                return Error("privacy_unavailable");
            }
            // IsRebuilding* methods initialize the singleton and register a Canvas callback when absent.
            // Read only the exact existing registry fields instead; never instantiate it.
            try
            {
                var registry = EngineType("UnityEngine.UI", "UnityEngine.UI.CanvasUpdateRegistry");
                var slot = registry?.GetField("s_Instance", BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.DeclaredOnly);
                if (slot == null || slot.FieldType != registry)
                    return Error("unsupported_tmp_version");
                var current = slot.GetValue(null);
                if (current != null)
                {
                    if (current.GetType() != registry)
                        return Error("unsupported_tmp_version");
                    foreach (string name in new[] { "m_PerformingLayoutUpdate", "m_PerformingGraphicUpdate" })
                    {
                        var field = registry.GetField(name, Fields);
                        if (field == null || field.FieldType != typeof(bool))
                            return Error("unsupported_tmp_version");
                        if ((bool)field.GetValue(current))
                            return Error("layout_in_progress");
                    }
                }
            }
            catch
            {
                return Error("layout_in_progress");
            }
            var budget = new Budget { Options = options };
            var nodes = new JArray();
            var stack = new Stack<Branch>();
            stack.Push(
                new Branch
                {
                    Transform = target.transform,
                    Path = "",
                    Depth = 0,
                }
            );
            while (stack.Count > 0)
            {
                var f = stack.Peek();
                if (!f.Entered)
                {
                    if (budget.Visited >= options.Nodes)
                    {
                        budget.Stop("max_nodes");
                        break;
                    }
                    if (!budget.Charge(2))
                        break;
                    budget.Visited++;
                    f.Entered = true;
                    if (!options.Inactive && !f.Transform.gameObject.activeInHierarchy)
                    {
                        stack.Pop();
                        continue;
                    }
                    var text = f.Transform.gameObject.GetComponent(adapter.Text);
                    if (text != null)
                    {
                        // Positional paths are atomic identity evidence, never clipped into duplicates.
                        if (f.Path.Length > options.Characters - budget.Characters)
                        {
                            budget.Stop("max_characters");
                            break;
                        }
                        var node = new JObject
                        {
                            ["instance_id"] = f.Transform.gameObject.GetInstanceIDCompat(),
                            ["diagnostic_path"] = budget.Copy(f.Path, 1024),
                            ["name"] = budget.Copy(f.Transform.name, 256),
                            ["status"] = "unknown",
                            ["reason"] = "uninitialized_layout",
                        };
                        foreach (string name in MetricNames)
                            node[name] = null;
                        bool redact;
                        try
                        {
                            redact = privacy.Redact(text);
                        }
                        catch
                        {
                            return Error("privacy_unavailable");
                        }
                        if (redact)
                        {
                            node["status"] = "redacted";
                            node["reason"] = "input_owned";
                        }
                        else
                        {
                            try
                            {
                                adapter.Observe(text, node, budget);
                            }
                            catch
                            {
                                Unknown(node, "inconsistent_cache");
                            }
                        }
                        nodes.Add(node);
                    }
                }
                if (!budget.Charge(1))
                    break;
                int children = f.Transform.childCount;
                if (f.Depth == options.Depth)
                {
                    if (children > 0)
                        budget.Stop("max_depth");
                    stack.Pop();
                    continue;
                }
                if (f.Next >= children)
                {
                    stack.Pop();
                    continue;
                }
                if (!budget.Charge(1))
                    break;
                int index = f.Next++;
                stack.Push(
                    new Branch
                    {
                        Transform = f.Transform.GetChild(index),
                        Depth = f.Depth + 1,
                        Path = f.Path + "/" + index.ToString(CultureInfo.InvariantCulture),
                    }
                );
            }
            var data = new JObject
            {
                ["kind"] = "ugui_tmp_layout_diagnostics",
                ["schema_version"] = 1,
                ["framework"] = "ugui_tmp",
                ["mode"] = Application.isPlaying ? "play" : "edit",
                ["frame"] = Time.frameCount,
                ["target_id"] = target.GetInstanceIDCompat(),
                ["status"] = budget.Reasons.Count > 0 || nodes.Any(n => n.Value<string>("status") == "unknown") ? "partial" : "complete",
                ["evidence"] = new JObject
                {
                    ["observation"] = "existing_generated_layout",
                    ["freshness"] = "cached_not_marked_dirty",
                    ["rasterization"] = "not_observed",
                    ["profile"] = null,
                },
                ["nodes"] = nodes,
                ["traversal"] = new JObject
                {
                    ["max_nodes"] = options.Nodes,
                    ["max_depth"] = options.Depth,
                    ["max_evaluations"] = options.Work,
                    ["max_characters"] = options.Characters,
                    ["nodes_visited"] = budget.Visited,
                    ["nodes_emitted"] = nodes.Count,
                    ["evaluations"] = budget.Work,
                    ["characters"] = budget.Characters,
                    ["cached_characters_scanned"] = budget.CachedCharacters,
                    ["cached_lines_scanned"] = budget.CachedLines,
                    ["truncated"] = budget.Reasons.Count > 0,
                    ["reasons"] = budget.Reasons,
                },
                ["limitations"] = new JArray(
                    "Existing generated cache only; flags are not a persistent freshness or pixel-visibility guarantee.",
                    "No text, glyph values, links or font resource names are collected; recognized editable-control caches are redacted.",
                    "Two fixed native input inventories allocate before managed caps; lookup/native allocation time is not bounded by the observation budget.",
                    "No rebuild, hypothetical resolution, preferred-size calculation, source font lookup or atlas population is performed."
                ),
            };
            if (Encoding.UTF8.GetByteCount(data.ToString(Formatting.None)) > 2 * 1024 * 1024)
                return Error("text_response_too_large");
            return new SuccessResponse("Observed existing TMP layout", data);
        }

        private static void Unknown(JObject node, string reason)
        {
            node["status"] = "unknown";
            node["reason"] = reason;
            foreach (string name in MetricNames)
                node[name] = null;
        }

        private static object Error(string code) => new ErrorResponse(code, new { message = code });

        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        private static Type EngineType(string assembly, string name)
        {
            var matches = AppDomain.CurrentDomain.GetAssemblies().Where(a => a.GetName().Name == assembly).ToArray();
            return matches.Length == 1 ? matches[0].GetType(name, false) : null;
        }

        private static bool Valid(string value, int cap)
        {
            if (value == null || value.Length > cap)
                return false;
            for (int i = 0; i < value.Length; i++)
                if (char.IsHighSurrogate(value[i]))
                {
                    if (++i == value.Length || !char.IsLowSurrogate(value[i]))
                        return false;
                }
                else if (char.IsLowSurrogate(value[i]))
                    return false;
            return true;
        }
    }
}
