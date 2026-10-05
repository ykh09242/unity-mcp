using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Targeted scene uGUI edits. Optional UI/TMP packages are resolved at runtime.</summary>
    [McpForUnityTool("manage_ugui", Group = "ui")]
    public static class ManageUGUI
    {
        private const string Ui = "UnityEngine.UI.";
        private const string Tmp = "TMPro.TextMeshProUGUI";
        private static readonly HashSet<string> RectKeys = Keys("anchorMin anchorMax pivot anchoredPosition sizeDelta offsetMin offsetMax localScale localEulerAngles");
        private static readonly HashSet<string> TextKeys = Keys("text fontSize color alignment enableAutoSizing fontSizeMin fontSizeMax raycastTarget");
        private static readonly HashSet<string> CanvasKeys = Keys("renderMode sortingOrder overrideSorting pixelPerfect worldCamera planeDistance scaleFactor referencePixelsPerUnit uiScaleMode referenceResolution screenMatchMode matchWidthOrHeight");
        private static readonly HashSet<string> LinearKeys = Keys("padding spacing childAlignment childControlWidth childControlHeight childForceExpandWidth childForceExpandHeight childScaleWidth childScaleHeight reverseArrangement");
        private static readonly HashSet<string> GridKeys = Keys("padding childAlignment cellSize spacing startCorner startAxis constraint constraintCount");
        private static readonly HashSet<string> ElementKeys = Keys("ignoreLayout minWidth minHeight preferredWidth preferredHeight flexibleWidth flexibleHeight layoutPriority");
        private static readonly HashSet<string> FitterKeys = Keys("horizontalFit verticalFit");

        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters are required.");
            try
            {
                var p = new ToolParams(@params);
                string action = p.Get("action")?.ToLowerInvariant();
                bool includeInactive = ReadBool(p.GetRaw("include_inactive"), false, "include_inactive");
                int maxNodes = ReadInt(p.GetRaw("max_nodes"), 200, 1, 1000, "max_nodes");
                if (action == "ping")
                    return new SuccessResponse("pong", new
                    {
                        tool = "manage_ugui",
                        ugui = TypeOf(Ui + "Image") != null,
                        tmp = TypeOf(Tmp) != null
                    });
                if (action == "create")
                    return Create(p, includeInactive);
                if (!new[]
                {
                    "get_hierarchy",
                    "set_rect",
                    "set_layout",
                    "set_text",
                    "set_canvas",
                    "diagnose"
                }.Contains(action))
                    return new ErrorResponse("Valid actions: ping, get_hierarchy, create, set_rect, set_layout, set_text, set_canvas, diagnose.");
                var go = Resolve(p.GetRaw("target"), includeInactive);
                if (action == "get_hierarchy")
                    return Hierarchy(go, includeInactive, maxNodes);
                if (action == "diagnose")
                {
                    JToken resolutions = p.GetRaw("resolutions");
                    ValidateResolutions(resolutions);
                    return UguiDiagnostics.Diagnose(go, resolutions as JArray, includeInactive, maxNodes);
                }

                var properties = p.GetRaw("properties") as JObject;
                if (properties == null || properties.Count == 0)
                    throw new ArgumentException("properties must be a non-empty object.");
                switch (action)
                {
                    case "set_rect":
                        return SetRect(go, properties);

                    case "set_layout":
                        return SetLayout(go, properties);

                    case "set_text":
                        return SetText(go, properties);

                    case "set_canvas":
                        return SetCanvas(go, properties);

                    default:
                        throw new ArgumentException("Unknown action.");
                }
            }
            catch (Exception e)
            {
                return new ErrorResponse(e is TargetInvocationException && e.InnerException != null ? e.InnerException.Message : e.Message);
            }
        }

        private static object Create(ToolParams p, bool includeInactive)
        {
            string kind = (p.Get("element_type") ?? "").ToLowerInvariant();
            if (!new[]
            {
                "canvas",
                "panel",
                "image",
                "button",
                "text"
            }.Contains(kind))
                throw new ArgumentException("element_type must be canvas, panel, image, button or text.");
            string name = p.Get("name", kind == "canvas" ? "Canvas" : CultureInfo.InvariantCulture.TextInfo.ToTitleCase(kind));
            if (string.IsNullOrWhiteSpace(name) || name.IndexOfAny(new[] { '/', '\\', '\0', '\r', '\n' }) >= 0)
                throw new ArgumentException("name must be a non-empty single hierarchy name.");
            GameObject parent = p.Has("parent") ? Resolve(p.GetRaw("parent"), includeInactive) : null;
            if (kind != "canvas" && (parent == null || !(parent.transform is RectTransform) || parent.GetComponentInParent<Canvas>(true) == null))
                throw new ArgumentException("A non-canvas element requires a RectTransform parent beneath a Canvas.");
            Type image = kind == "panel" || kind == "image" || kind == "button" ? RequireType(Ui + "Image") : null;
            Type button = kind == "button" ? RequireType(Ui + "Button") : null;
            Type text = kind == "text" ? RequireType(Tmp) : null;
            Type scaler = kind == "canvas" ? RequireType(Ui + "CanvasScaler") : null;
            Type raycaster = kind == "canvas" ? RequireType(Ui + "GraphicRaycaster") : null;
            UnityEngine.Object font = text == null ? null : DefaultFont();
            JObject props = p.GetRaw("properties") as JObject;
            if (p.Has("properties") && props == null)
                throw new ArgumentException("properties must be an object.");
            props = props ?? new JObject();
            var allowed = new HashSet<string>(RectKeys);
            if (text != null)
                allowed.UnionWith(TextKeys);
            if (image != null)
                allowed.Add("color");
            CheckKeys(props, allowed);
            JObject rectProps = Select(props, RectKeys);
            ValidateRect(null, rectProps, kind == "panel");
            if (rectProps.Count > 0 && parent != null && Enabled(Find(parent, Ui + "LayoutGroup")))
                throw new ArgumentException("The requested parent has an active LayoutGroup. Create the child with default rect settings, then configure its LayoutElement.");
            var rectValues = Prepare(typeof(RectTransform), rectProps, RectKeys);
            var componentValues = text != null
                ? Prepare(text, Select(props, TextKeys), TextKeys)
                : image != null
                    ? Prepare(image, Select(props, Keys("color")), Keys("color"))
                    : new List<Assignment>();
            if (text != null)
                ValidateTextRange(null, props);
            return Mutate("Create uGUI " + kind, () =>
            {
                var go = new GameObject(name, typeof(RectTransform));
                Undo.RegisterCreatedObjectUndo(go, "Create uGUI " + kind);
                if (parent != null)
                    Undo.SetTransformParent(go.transform, parent.transform, "Parent uGUI element");
                else
                {
                    var stage = PrefabStageUtility.GetCurrentPrefabStage();
                    if (stage != null)
                        Undo.SetTransformParent(go.transform, stage.prefabContentsRoot.transform, "Parent uGUI canvas");
                }

                var rt = (RectTransform)go.transform;
                rt.localScale = Vector3.one;
                rt.localRotation = Quaternion.identity;
                rt.localPosition = Vector3.zero;
                rt.anchorMin = rt.anchorMax = rt.pivot = new Vector2(.5f, .5f);
                rt.sizeDelta = new Vector2(kind == "text" ? 300 : 160, kind == "text" ? 60 : 80);
                if (kind == "panel")
                {
                    rt.anchorMin = Vector2.zero;
                    rt.anchorMax = Vector2.one;
                    rt.offsetMin = rt.offsetMax = Vector2.zero;
                }

                if (kind == "canvas")
                {
                    var canvas = Undo.AddComponent<Canvas>(go);
                    canvas.renderMode = RenderMode.ScreenSpaceOverlay;
                    var cs = Undo.AddComponent(go, scaler);
                    cs.GetType().GetProperty("uiScaleMode").SetValue(
                        cs,
                        Enum.Parse(
                            cs.GetType().GetProperty("uiScaleMode").PropertyType,
                            "ScaleWithScreenSize"));
                    cs.GetType().GetProperty("referenceResolution").SetValue(cs, new Vector2(1920, 1080));
                    cs.GetType().GetProperty("matchWidthOrHeight").SetValue(cs, .5f);
                    Undo.AddComponent(go, raycaster);
                }

                Component visual = null;
                if (image != null)
                {
                    visual = Undo.AddComponent(go, image);
                    image.GetProperty("color").SetValue(visual, kind == "panel" ? new Color(.12f, .12f, .12f, 1) : Color.white);
                    image.GetProperty("raycastTarget").SetValue(visual, button != null);
                }

                if (button != null)
                {
                    var b = Undo.AddComponent(go, button);
                    button.GetProperty("targetGraphic").SetValue(b, visual);
                }

                if (text != null)
                {
                    visual = Undo.AddComponent(go, text);
                    text.GetProperty("font").SetValue(visual, font);
                    text.GetProperty("text").SetValue(visual, name);
                    text.GetProperty("fontSize").SetValue(visual, 24f);
                    text.GetProperty("fontSizeMin").SetValue(visual, 8f);
                    text.GetProperty("fontSizeMax").SetValue(visual, 72f);
                    text.GetProperty("color").SetValue(visual, Color.white);
                    text.GetProperty("raycastTarget").SetValue(visual, false);
                }

                Apply(rt, rectValues);
                if (visual != null)
                    Apply(visual, componentValues);
                Dirty(rt);
                if (visual != null)
                    Dirty(visual);
                return new SuccessResponse("Created uGUI element.", new
                {
                    instance_id = go.GetInstanceIDCompat(),
                    path = PathOf(go),
                    element_type = kind,
                    warnings = kind == "button" ? new[] { "Button requires an EventSystem and input module in the scene to receive input." } : Array.Empty<string>()
                });
            });
        }

        private static object SetRect(GameObject go, JObject props)
        {
            var rt = go.transform as RectTransform;
            if (rt == null)
                throw new ArgumentException("Target has no RectTransform.");
            if (IsDriven(rt))
                throw new ArgumentException("RectTransform is layout-driven. Edit the parent layout or this object's LayoutElement/ContentSizeFitter instead.");
            ValidateRect(rt, props);
            return Edit(rt, Prepare(typeof(RectTransform), props, RectKeys), go, "Set uGUI rect");
        }

        private static object SetText(GameObject go, JObject props)
        {
            var c = Find(go, Tmp) ?? Find(go, Ui + "Text");
            if (c == null)
                throw new ArgumentException("Target has no TMP or legacy Text component. Create text with element_type=text after configuring TMP essentials.");
            ValidateTextRange(c, props);
            return Edit(c, Prepare(c.GetType(), props, TextKeys), go, "Set uGUI text");
        }

        private static object SetCanvas(GameObject go, JObject props)
        {
            var canvas = go.GetComponent<Canvas>();
            if (canvas == null)
                throw new ArgumentException("Target has no Canvas.");
            CheckKeys(props, CanvasKeys);
            var canvasKeys = Keys("renderMode sortingOrder overrideSorting pixelPerfect worldCamera planeDistance scaleFactor referencePixelsPerUnit");
            var scalerKeys = Keys("uiScaleMode referenceResolution screenMatchMode matchWidthOrHeight");
            var scaler = Find(go, Ui + "CanvasScaler");
            var controlledKeys = Keys("scaleFactor referencePixelsPerUnit");
            bool ownsScaling = canvas.isRootCanvas && scaler is Behaviour scalerBehaviour && scalerBehaviour.enabled;
            if (controlledKeys.Any(key => props[key] != null) && !ownsScaling)
                throw new ArgumentException(
                    "scaleFactor and referencePixelsPerUnit require an enabled CanvasScaler on the root Canvas for persistent, undoable edits. Target the root Canvas and enable its CanvasScaler.");
            if (ownsScaling)
            {
                canvasKeys.ExceptWith(controlledKeys);
                scalerKeys.UnionWith(controlledKeys);
            }

            JObject scalerProps = Select(props, scalerKeys);
            if (scalerProps.Count > 0 && scaler == null)
                throw new ArgumentException("Target has no CanvasScaler.");
            var cv = Prepare(typeof(Canvas), Select(props, canvasKeys), canvasKeys);
            var sv = scalerProps.Count == 0 ? new List<Assignment>() : Prepare(scaler.GetType(), scalerProps, scalerKeys);
            return Mutate("Set uGUI canvas", () =>
            {
                Apply(canvas, cv);
                if (scaler != null && sv.Count > 0)
                    Apply(scaler, sv);
                return Changed(go);
            });
        }

        private static object SetLayout(GameObject go, JObject props)
        {
            if (!(go.transform is RectTransform))
                throw new ArgumentException("Target has no RectTransform.");
            string kind = props["type"]?.Type == JTokenType.String ? props["type"].ToString().ToLowerInvariant() : null;
            string typeName;
            HashSet<string> allowed;
            switch (kind)
            {
                case "vertical":
                    typeName = "VerticalLayoutGroup";
                    allowed = LinearKeys;
                    break;

                case "horizontal":
                    typeName = "HorizontalLayoutGroup";
                    allowed = LinearKeys;
                    break;

                case "grid":
                    typeName = "GridLayoutGroup";
                    allowed = GridKeys;
                    break;

                case "layout_element":
                    typeName = "LayoutElement";
                    allowed = ElementKeys;
                    break;

                case "content_size_fitter":
                    typeName = "ContentSizeFitter";
                    allowed = FitterKeys;
                    break;

                default:
                    throw new ArgumentException("properties.type must be vertical, horizontal, grid, layout_element or content_size_fitter.");
            }

            Type type = RequireType(Ui + typeName);
            var c = go.GetComponent(type);
            if (kind == "vertical" || kind == "horizontal" || kind == "grid")
            {
                var existing = Find(go, Ui + "LayoutGroup");
                if (existing != null && !type.IsInstanceOfType(existing))
                    throw new ArgumentException("Target already has a different LayoutGroup; edit it instead.");
            }

            var values = new JObject(props);
            values.Remove("type");
            var assignments = Prepare(type, values, allowed);
            return Mutate("Set uGUI layout", () =>
            {
                var component = c != null ? c : Undo.AddComponent(go, type);
                Apply(component, assignments);
                return Changed(go);
            });
        }

        private static object Edit(Component component, List<Assignment> values, GameObject go, string label)
        {
            return Mutate(label, () =>
            {
                Apply(component, values);
                return Changed(go);
            });
        }

        private static object Changed(GameObject go) => new SuccessResponse("Updated uGUI element.", new
        {
            instance_id = go.GetInstanceIDCompat(),
            path = PathOf(go)
        });

        private static object Mutate(string label, Func<object> change)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new ArgumentException("uGUI editing is supported in Edit Mode only.");
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName(label);
            try
            {
                object result = change();
                Undo.FlushUndoRecordObjects();
                Undo.CollapseUndoOperations(group);
                return result;
            }
            catch
            {
                Undo.FlushUndoRecordObjects();
                Undo.RevertAllDownToGroup(group);
                throw;
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
        }

        private sealed class Assignment
        {
            public PropertyInfo Property;
            public object Value;
        }

        private static List<Assignment> Prepare(Type type, JObject props, HashSet<string> allowed)
        {
            CheckKeys(props, allowed);
            var values = new List<Assignment>();
            foreach (var entry in props.Properties())
            {
                var property = type.GetProperty(entry.Name, BindingFlags.Instance | BindingFlags.Public);
                if (property == null || !property.CanWrite || property.GetIndexParameters().Length != 0)
                    throw new ArgumentException($"Property '{entry.Name}' is unavailable on {type.Name} in this Unity version.");
                object value = ConvertValue(entry.Value, property.PropertyType, entry.Name);
                ValidateDomain(entry.Name, value);
                values.Add(new Assignment { Property = property, Value = value });
            }

            // Offsets depend on anchors and pivot, irrespective of JSON property order.
            return values.OrderBy(v => RectOrder(v.Property.Name)).ToList();
        }

        private static int RectOrder(string key)
        {
            if (key == "anchorMin" || key == "anchorMax")
                return 0;
            if (key == "pivot")
                return 1;
            if (key == "offsetMin" || key == "offsetMax")
                return 3;
            return 2;
        }

        private static void Apply(Component component, List<Assignment> values)
        {
            if (values.Count == 0)
                return;
            Undo.RecordObject(component, "Edit uGUI properties");
            foreach (var value in values)
                value.Property.SetValue(component, value.Value);
            Dirty(component);
        }

        private static void Dirty(Component c)
        {
            PrefabUtility.RecordPrefabInstancePropertyModifications(c);
            EditorUtility.SetDirty(c);
            if (c.gameObject.scene.IsValid())
                EditorSceneManager.MarkSceneDirty(c.gameObject.scene);
        }

        private static object ConvertValue(JToken token, Type type, string key)
        {
            if (type == typeof(float))
                return Number(token, key);
            if (type == typeof(int))
                return ReadInt(token, null, int.MinValue, int.MaxValue, key);
            if (type == typeof(bool))
                return ReadBool(token, null, key);
            if (type == typeof(string))
            {
                if (token.Type != JTokenType.String)
                    throw new ArgumentException(key + " must be a string.");
                return token.ToString();
            }

            if (type == typeof(Vector2))
            {
                float[] v = Vector(token, 2, key);
                return new Vector2(v[0], v[1]);
            }

            if (type == typeof(Vector3))
            {
                float[] v = Vector(token, 3, key);
                return new Vector3(v[0], v[1], v[2]);
            }

            if (type == typeof(Color))
            {
                float[] v = Vector(token, 4, key, true);
                if (v.Any(n => n < 0 || n > 1))
                    throw new ArgumentException("color channels must be 0..1.");
                return new Color(v[0], v[1], v[2], v[3]);
            }

            if (type == typeof(RectOffset))
            {
                if (!(token is JObject o) || o.Count != 4 || new[]
                {
                    "left",
                    "right",
                    "top",
                    "bottom"
                }.Any(k => o[k] == null))
                    throw new ArgumentException("padding must contain left, right, top and bottom integers.");
                return new RectOffset(
                    ReadInt(o["left"], null, 0, 100000, key),
                    ReadInt(o["right"], null, 0, 100000, key),
                    ReadInt(o["top"], null, 0, 100000, key),
                    ReadInt(o["bottom"], null, 0, 100000, key));
            }

            if (type == typeof(Camera))
            {
                if (token.Type == JTokenType.Null)
                    return null;
                var camera = Resolve(token, true).GetComponent<Camera>();
                if (camera == null)
                    throw new ArgumentException("worldCamera target has no Camera.");
                return camera;
            }

            if (type.IsEnum)
            {
                if (token.Type != JTokenType.String
                    || !Enum.GetNames(type).Any(n => string.Equals(n, token.ToString(), StringComparison.OrdinalIgnoreCase))
                    || !Enum.TryParse(type, token.ToString(), true, out object value)
                    || !Enum.IsDefined(type, value))
                    throw new ArgumentException(key + " must be one of: " + string.Join(", ", Enum.GetNames(type)));
                return value;
            }

            throw new ArgumentException("Unsupported property type for " + key);
        }

        private static void ValidateDomain(string key, object value)
        {
            if (value is float f)
            {
                if (new[]
                {
                    "fontSize",
                    "fontSizeMin",
                    "fontSizeMax",
                    "scaleFactor",
                    "referencePixelsPerUnit",
                    "planeDistance"
                }.Contains(key) && f <= 0)
                    throw new ArgumentException(key + " must be positive.");
                if (key == "matchWidthOrHeight" && (f < 0 || f > 1))
                    throw new ArgumentException(key + " must be 0..1.");
                if (ElementKeys.Contains(key) && key != "layoutPriority" && f < -1)
                    throw new ArgumentException(key + " must be at least -1.");
            }

            if (value is int n && (key == "constraintCount" || key == "fontSize") && n < 1)
                throw new ArgumentException(key + " must be positive.");
            if (value is int order && key == "sortingOrder" && (order < short.MinValue || order > short.MaxValue))
                throw new ArgumentException("sortingOrder must be between -32768 and 32767.");
            if (value is Vector2 v && (key == "referenceResolution" || key == "cellSize") && (v.x <= 0 || v.y <= 0))
                throw new ArgumentException(key + " dimensions must be positive.");
        }

        private static void ValidateRect(RectTransform rt, JObject props, bool stretchDefaults = false)
        {
            CheckKeys(props, RectKeys);
            Vector2 min = props["anchorMin"] != null
                ? (Vector2)ConvertValue(props["anchorMin"], typeof(Vector2), "anchorMin")
                : rt != null
                    ? rt.anchorMin
                    : stretchDefaults ? Vector2.zero : new Vector2(.5f, .5f);
            Vector2 max = props["anchorMax"] != null
                ? (Vector2)ConvertValue(props["anchorMax"], typeof(Vector2), "anchorMax")
                : rt != null
                    ? rt.anchorMax
                    : stretchDefaults ? Vector2.one : new Vector2(.5f, .5f);
            if (min.x > max.x || min.y > max.y)
                throw new ArgumentException("anchorMin must not exceed anchorMax.");
            if (props["pivot"] != null)
            {
                var pivot = (Vector2)ConvertValue(props["pivot"], typeof(Vector2), "pivot");
                if (pivot.x < 0 || pivot.x > 1 || pivot.y < 0 || pivot.y > 1)
                    throw new ArgumentException("pivot must be 0..1 on each axis.");
            }

            if ((props["offsetMin"] != null || props["offsetMax"] != null) && (props["sizeDelta"] != null || props["anchoredPosition"] != null))
                throw new ArgumentException("Use offsets or sizeDelta/anchoredPosition in one request, since these properties overlap.");
            // Offset setters derive position and size using float arithmetic. Individually
            // finite endpoints can still overflow those serialized fields.
            Vector2 size = rt != null ? rt.sizeDelta : stretchDefaults ? Vector2.zero : new Vector2(160, 80);
            Vector2 position = rt != null ? rt.anchoredPosition : Vector2.zero;
            Vector2 projectedPivot = props["pivot"] != null
                ? (Vector2)ConvertValue(props["pivot"], typeof(Vector2), "pivot")
                : rt != null ? rt.pivot : new Vector2(.5f, .5f);
            foreach (var entry in props.Properties().Where(p => p.Name == "offsetMin" || p.Name == "offsetMax"))
            {
                Vector2 value = (Vector2)ConvertValue(entry.Value, typeof(Vector2), entry.Name);
                bool isMin = entry.Name == "offsetMin";
                Vector2 offset = value - (isMin
                    ? position - Vector2.Scale(size, projectedPivot)
                    : position + Vector2.Scale(size, Vector2.one - projectedPivot));
                size += isMin ? -offset : offset;
                position += Vector2.Scale(offset, isMin ? Vector2.one - projectedPivot : projectedPivot);
                if (!Finite(size) || !Finite(position))
                    throw new ArgumentException("Offsets would overflow RectTransform position or size. Use smaller finite offsets.");
            }
        }

        private static bool Finite(Vector2 value) => !float.IsNaN(value.x) && !float.IsInfinity(value.x) && !float.IsNaN(value.y) && !float.IsInfinity(value.y);

        private static void ValidateTextRange(Component c, JObject props)
        {
            float? min = props["fontSizeMin"] == null
                ? c == null ? 8f : ReadFloatProperty(c, "fontSizeMin")
                : Number(props["fontSizeMin"], "fontSizeMin");
            float? max = props["fontSizeMax"] == null
                ? c == null ? 72f : ReadFloatProperty(c, "fontSizeMax")
                : Number(props["fontSizeMax"], "fontSizeMax");
            if (min.HasValue && max.HasValue && min > max)
                throw new ArgumentException("fontSizeMin must not exceed fontSizeMax.");
        }

        private static float? ReadFloatProperty(Component c, string key) => c == null
            ? null
            : c.GetType().GetProperty(key)?.GetValue(c) as float?;

        private static bool IsDriven(RectTransform rt)
        {
            Type fitterType = TypeOf(Ui + "ContentSizeFitter");
            var driven = typeof(RectTransform).GetProperty("drivenByObject");
            if (driven?.GetValue(rt) is UnityEngine.Object owner && owner != null)
            {
                if (owner.GetType() != fitterType)
                    return true;
                // An unconstrained built-in fitter can retain ownership without driving any property.
                // Stale flags still require a normal layout pass before edits can preserve Undo.
                var flags = typeof(RectTransform).GetProperty("drivenProperties", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (!(flags?.GetValue(rt) is DrivenTransformProperties properties) || properties != DrivenTransformProperties.None)
                    return true;
            }
            if (fitterType != null)
            {
                foreach (var fitter in rt.GetComponents(fitterType))
                {
                    if (Enabled(fitter)
                        && (Convert.ToInt32(fitter.GetType().GetProperty("horizontalFit").GetValue(fitter)) != 0
                            || Convert.ToInt32(fitter.GetType().GetProperty("verticalFit").GetValue(fitter)) != 0))
                        return true;
                }
            }

            if (rt.parent == null || !rt.gameObject.activeInHierarchy)
                return false;
            Type ignorer = UnityTypeResolver.ResolveAny(Ui + "ILayoutIgnorer");
            if (ignorer != null)
            {
                var components = rt.GetComponents(ignorer);
                // Match LayoutGroup.CalculateLayoutInputHorizontal: disabled ignorers
                // still participate, and any false value includes the child in layout.
                if (components.Length > 0 && components.All(c => (bool)ignorer.GetProperty("ignoreLayout").GetValue(c)))
                    return false;
            }

            return Enabled(Find(rt.parent.gameObject, Ui + "LayoutGroup"));
        }

        private static bool Enabled(Component c) => c != null && (!(c is Behaviour b) || b.isActiveAndEnabled);

        private static UnityEngine.Object DefaultFont()
        {
            Type settings = UnityTypeResolver.ResolveAny("TMPro.TMP_Settings");
            UnityEngine.Object font;
            try
            {
                font = settings?.GetProperty("defaultFontAsset", BindingFlags.Static | BindingFlags.Public)?.GetValue(null) as UnityEngine.Object;
            }
            catch (TargetInvocationException e)when (e.InnerException is NullReferenceException)
            {
                throw new ArgumentException("TMP settings/default font are missing. Configure TMP essentials before creating text. No import dialog was opened.");
            }

            if (font == null)
                throw new ArgumentException("TMP default font is missing. Configure TMP essentials/defaultFontAsset before creating text. No import dialog was opened.");
            return font;
        }

        private static Component Find(GameObject go, string name)
        {
            Type type = TypeOf(name);
            return type == null ? null : go.GetComponent(type);
        }

        private static Type TypeOf(string name) => UnityTypeResolver.ResolveComponent(name);

        private static Type RequireType(string name) => TypeOf(name) ?? throw new ArgumentException(
            "Required optional component is unavailable: " + name + ". Install/enable the corresponding uGUI or TMP package.");

        private static GameObject Resolve(JToken target, bool includeInactive)
        {
            if (target == null || (target.Type != JTokenType.String && target.Type != JTokenType.Integer) || string.IsNullOrWhiteSpace(target.ToString()))
                throw new ArgumentException("target/parent must be a scene GameObject name, hierarchy path or instance ID.");
            string name = target is JValue scalar ? scalar.ToString(CultureInfo.InvariantCulture) : target.ToString();
            if (target.Type == JTokenType.Integer && !int.TryParse(name, NumberStyles.Integer, CultureInfo.InvariantCulture, out _))
                throw new ArgumentException("Numeric target/parent instance ID must fit a signed 32-bit integer.");
            if (int.TryParse(name, NumberStyles.Integer, CultureInfo.InvariantCulture, out int id))
            {
                var go = GameObjectLookup.FindById(id);
                RequireSceneObject(go, includeInactive);
                return go;
            }

            var matches = SceneObjects(includeInactive)
                .Where(go => name.Contains("/") ? PathOf(go) == name.TrimStart('/') : go.name == name)
                .Take(2)
                .ToArray();
            if (matches.Length == 0)
                throw new ArgumentException("Target was not found in loaded scenes/current prefab stage: " + name);
            if (matches.Length > 1)
                throw new ArgumentException("Target is ambiguous. Use a unique full hierarchy path or instance ID: " + name);
            return matches[0];
        }

        private static void RequireSceneObject(GameObject go, bool includeInactive)
        {
            if (go == null || EditorUtility.IsPersistent(go) || !go.scene.IsValid() || !go.scene.isLoaded)
                throw new ArgumentException("Target must be a loaded scene object; prefab/asset edits by asset ID are unsupported.");
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && go.scene != stage.scene)
                throw new ArgumentException("Target is outside the current prefab stage.");
            if (stage == null && EditorSceneManager.IsPreviewScene(go.scene))
                throw new ArgumentException("Preview scene targets are unsupported.");
            if (!includeInactive && !go.activeInHierarchy)
                throw new ArgumentException("Target is inactive; set include_inactive=true.");
        }

        private static IEnumerable<GameObject> SceneObjects(bool includeInactive)
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null)
            {
                foreach (var go in Descendants(stage.prefabContentsRoot, includeInactive))
                    yield return go;
                yield break;
            }

            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.IsValid() || !scene.isLoaded || EditorSceneManager.IsPreviewScene(scene))
                    continue;
                foreach (var root in scene.GetRootGameObjects())
                    foreach (var go in Descendants(root, includeInactive))
                        yield return go;
            }
        }

        private static IEnumerable<GameObject> Descendants(GameObject root, bool includeInactive)
        {
            if (!includeInactive && !root.activeInHierarchy)
                yield break;
            yield return root;
            // Keep one frame per depth; a wide hierarchy must not enqueue all children
            // before a capped reader can stop at max_nodes.
            var pending = new Stack<TraversalFrame>();
            pending.Push(new TraversalFrame { Transform = root.transform });
            while (pending.Count > 0)
            {
                var frame = pending.Peek();
                if (frame.NextChild >= frame.Transform.childCount)
                {
                    pending.Pop();
                    continue;
                }

                var child = frame.Transform.GetChild(frame.NextChild++);
                if (!includeInactive && !child.gameObject.activeInHierarchy)
                    continue;
                yield return child.gameObject;
                pending.Push(new TraversalFrame { Transform = child });
            }
        }

        private sealed class TraversalFrame
        {
            public Transform Transform;
            public int NextChild;
        }

        private static object Hierarchy(GameObject root, bool includeInactive, int maxNodes)
        {
            var nodes = new JArray();
            bool truncated = false;
            foreach (var go in Descendants(root, includeInactive))
            {
                if (nodes.Count == maxNodes)
                {
                    truncated = true;
                    break;
                }

                var rt = go.transform as RectTransform;
                var c = Find(go, Tmp) ?? Find(go, Ui + "Text");
                var node = new JObject
                {
                    ["instance_id"] = go.GetInstanceIDCompat(),
                    ["name"] = go.name,
                    ["path"] = PathOf(go),
                    ["active"] = go.activeInHierarchy,
                    ["parent_id"] = go.transform.parent == null ? 0 : go.transform.parent.gameObject.GetInstanceIDCompat(),
                    ["components"] = new JArray(go.GetComponents<Component>().Where(x => x != null).Select(x => x.GetType().FullName))
                };
                if (rt != null)
                    node["rect"] = new JObject
                    {
                        ["anchorMin"] = Vec(rt.anchorMin),
                        ["anchorMax"] = Vec(rt.anchorMax),
                        ["pivot"] = Vec(rt.pivot),
                        ["anchoredPosition"] = Vec(rt.anchoredPosition),
                        ["sizeDelta"] = Vec(rt.sizeDelta),
                        ["width"] = rt.rect.width,
                        ["height"] = rt.rect.height,
                        ["layout_driven"] = IsDriven(rt)
                    };
                if (c != null)
                    node["text"] = c.GetType().GetProperty("text")?.GetValue(c)?.ToString();
                nodes.Add(node);
            }

            return new SuccessResponse("Read uGUI hierarchy.", new
            {
                nodes,
                count = nodes.Count,
                truncated,
                max_nodes = maxNodes
            });
        }

        private static JArray Vec(Vector2 v) => new JArray(v.x, v.y);

        private static string PathOf(GameObject go)
        {
            var names = new Stack<string>();
            for (Transform t = go.transform; t != null; t = t.parent)
                names.Push(t.name);
            return string.Join("/", names);
        }

        private static void ValidateResolutions(JToken token)
        {
            if (token == null)
                return;
            if (!(token is JArray a) || a.Count < 1 || a.Count > 8)
                throw new ArgumentException("resolutions must contain 1..8 resolution objects.");
            foreach (var item in a)
            {
                if (!(item is JObject o) || o.Count != 2 || o["width"] == null || o["height"] == null)
                    throw new ArgumentException("Each resolution must contain width and height.");
                ReadInt(o["width"], null, 64, 8192, "width");
                ReadInt(o["height"], null, 64, 8192, "height");
            }
        }

        private static HashSet<string> Keys(string keys) => new HashSet<string>(keys.Split(' '), StringComparer.Ordinal);

        private static JObject Select(JObject props, HashSet<string> keys) => new JObject(props.Properties()
            .Where(p => keys.Contains(p.Name))
            .Select(p => new JProperty(p.Name, p.Value.DeepClone())));

        private static void CheckKeys(JObject props, HashSet<string> allowed)
        {
            foreach (var p in props.Properties())
                if (!allowed.Contains(p.Name))
                    throw new ArgumentException("Unsupported property: " + p.Name + ". Supported: " + string.Join(", ", allowed.OrderBy(k => k)));
        }

        private static float Number(JToken token, string key)
        {
            if (token == null || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float))
                throw new ArgumentException(key + " must be a finite number.");
            double n = token.Value<double>();
            if (double.IsNaN(n) || double.IsInfinity(n) || n > float.MaxValue || n < -float.MaxValue)
                throw new ArgumentException(key + " must be a finite float.");
            return (float)n;
        }

        private static float[] Vector(JToken token, int count, string key, bool color = false)
        {
            JToken[] items;
            if (token is JArray a && a.Count == count)
                items = a.ToArray();
            else if (token is JObject o && o.Count == count)
            {
                string[] names = color ? new[]
                {
                    "r",
                    "g",
                    "b",
                    "a"
                }

                : new[]
                {
                    "x",
                    "y",
                    "z"
                };
                items = names.Take(count).Select(n => o[n]).ToArray();
            }
            else
                throw new ArgumentException(key + " must be a " + count + "-number array or vector object.");
            return items.Select(t => Number(t, key)).ToArray();
        }

        private static int ReadInt(JToken token, int? fallback, int min, int max, string key)
        {
            if (token == null && fallback.HasValue)
                return fallback.Value;
            string value = token is JValue scalar ? scalar.ToString(CultureInfo.InvariantCulture) : token?.ToString();
            if (token == null || token.Type != JTokenType.Integer || !int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int n) || n < min || n > max)
                throw new ArgumentException(key + " must be an integer in " + min + ".." + max + ".");
            return n;
        }

        private static bool ReadBool(JToken token, bool? fallback, string key)
        {
            if (token == null && fallback.HasValue)
                return fallback.Value;
            if (token == null || token.Type != JTokenType.Boolean)
                throw new ArgumentException(key + " must be a boolean.");
            return token.Value<bool>();
        }
    }
}
