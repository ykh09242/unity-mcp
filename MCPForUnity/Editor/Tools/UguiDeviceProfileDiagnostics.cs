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
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Owned built-in Overlay preview or current camera geometry; never invokes source layout providers.</summary>
    internal static class UguiDeviceProfileDiagnostics
    {
        private static readonly HashSet<string> PreviewNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "UnityEngine.UI.HorizontalLayoutGroup",
            "UnityEngine.UI.VerticalLayoutGroup",
            "UnityEngine.UI.GridLayoutGroup",
            "UnityEngine.UI.LayoutElement",
            "UnityEngine.UI.ContentSizeFitter",
            "UnityEngine.UI.AspectRatioFitter",
            "UnityEngine.UI.Image",
            "UnityEngine.UI.RawImage",
        };
        private static readonly string[] Orientations = { "portrait", "portrait_upside_down", "landscape_left", "landscape_right" };

        private sealed class Profile
        {
            internal string Source,
                Orientation;
            internal int Width,
                Height;
            internal float Dpi;
            internal Rect Safe;
            internal bool Available = true;
        }

        private sealed class Options
        {
            internal JToken Target,
                Camera;
            internal bool Inactive;
            internal int Nodes,
                Depth,
                Evaluations,
                Characters;
            internal float? Minimum;
            internal readonly List<Profile> Profiles = new List<Profile>();
        }

        private sealed class Budget
        {
            internal readonly Options Options;
            internal int Spent,
                Characters,
                SelectorVisited;
            internal readonly JObject Counts = new JObject
            {
                ["snapshot_and_resolver"] = 0,
                ["ancestors"] = 0,
                ["transforms"] = 0,
                ["components"] = 0,
                ["profile_setups"] = 0,
                ["assessment_pairs"] = 0,
                ["projection"] = 0,
                ["output"] = 0,
            };
            internal JObject UnsupportedComponent;
            internal readonly HashSet<string> Reasons = new HashSet<string>(StringComparer.Ordinal);

            internal Budget(Options options) => Options = options;

            internal bool Charge(string kind, int count = 1)
            {
                if (count > Options.Evaluations - Spent)
                {
                    Reasons.Add("max_evaluations");
                    return false;
                }
                Spent += count;
                Counts[kind] = Counts[kind].Value<int>() + count;
                return true;
            }
        }

        private sealed class Node
        {
            internal RectTransform Source,
                Preview;
            internal int Depth;
            internal bool Candidate,
                ConsumesChildTextMetrics;
        }

        private sealed class SelectorFrame
        {
            internal Transform Transform;
            internal string ParentPath,
                Path;
            internal bool Entered;
            internal int NextChild;
        }

        internal static object Diagnose(JObject raw)
        {
            Options options;
            try
            {
                options = Parse(raw);
            }
            catch
            {
                return new ErrorResponse("invalid_device_profile_request");
            }
            var budget = new Budget(options);
            var reports = new JArray();
            JObject projection = null;
            if (!budget.Charge("snapshot_and_resolver", 2))
                return Finish(options, budget, reports, projection);
            foreach (var profile in options.Profiles.Where(p => p.Source == "observed"))
                Observe(profile);
            GameObject target;
            try
            {
                if (!TryResolve(options.Target, options.Inactive, budget, out target))
                    return Finish(options, budget, reports, projection);
            }
            catch
            {
                return new ErrorResponse("invalid_device_profile_target");
            }
            if (!(target.transform is RectTransform))
                return new ErrorResponse("invalid_device_profile_target");
            Canvas canvas = null;
            for (Transform ancestor = target.transform; ancestor != null; ancestor = ancestor.parent)
            {
                if (!budget.Charge("ancestors") || budget.Counts["ancestors"].Value<int>() > 128)
                {
                    budget.Reasons.Add("ancestor_limit");
                    return Finish(options, budget, reports, projection);
                }
                var candidate = ancestor.GetComponent<Canvas>();
                if (canvas == null && candidate != null)
                    canvas = candidate;
            }
            if (canvas == null)
                return new ErrorResponse("device_profile_canvas_unavailable");
            canvas = canvas.rootCanvas;
            try
            {
                if (canvas.renderMode == RenderMode.ScreenSpaceOverlay)
                {
                    if (options.Camera != null && options.Camera.Type != JTokenType.Null)
                        return new ErrorResponse("invalid_device_profile_request");
                    Overlay(target, canvas, options, budget, reports);
                }
                else
                    projection = CameraProfiles(target, canvas, options, budget, reports);
                return Finish(options, budget, reports, projection);
            }
            catch
            {
                return new ErrorResponse("device_profiles_unavailable");
            }
        }

        private static Options Parse(JObject raw)
        {
            Keys(raw, "action target camera include_inactive max_nodes max_depth max_evaluations max_characters device_profiles minimum_touch_target_mm");
            var options = new Options
            {
                Target = raw["target"],
                Camera = raw["camera"],
                Nodes = Integer(raw["max_nodes"], 200, 1, 1000),
                Depth = Integer(raw["max_depth"], 10, 0, 24),
                Evaluations = Integer(raw["max_evaluations"], 20000, 1, 200000),
                Characters = Integer(raw["max_characters"], 65536, 1, 262144),
            };
            Reference(options.Target, false);
            Reference(options.Camera, true);
            if (raw["include_inactive"] != null && raw["include_inactive"].Type != JTokenType.Boolean)
                throw new ArgumentException();
            options.Inactive = raw["include_inactive"]?.Value<bool>() ?? false;
            if (raw["minimum_touch_target_mm"] != null && raw["minimum_touch_target_mm"].Type != JTokenType.Null)
                options.Minimum = Positive(raw["minimum_touch_target_mm"]);
            var profiles = raw["device_profiles"] as JArray;
            if (profiles == null || profiles.Count < 1 || profiles.Count > 8)
                throw new ArgumentException();
            foreach (var token in profiles)
            {
                var obj = token as JObject;
                if (obj == null || obj["source"]?.Type != JTokenType.String)
                    throw new ArgumentException();
                var p = new Profile { Source = obj["source"].Value<string>() };
                if (p.Source == "observed")
                    Keys(obj, "source");
                else if (p.Source == "virtual")
                {
                    Keys(obj, "source width height dpi safe_area orientation");
                    p.Width = Integer(obj["width"], -1, 64, 8192);
                    p.Height = Integer(obj["height"], -1, 64, 8192);
                    p.Dpi = Positive(obj["dpi"]);
                    p.Orientation = obj["orientation"]?.Type == JTokenType.String ? obj["orientation"].Value<string>() : null;
                    if (!Orientations.Contains(p.Orientation))
                        throw new ArgumentException();
                    var safe = obj["safe_area"] as JArray;
                    if (safe == null || safe.Count != 4)
                        throw new ArgumentException();
                    p.Safe = new Rect(Number(safe[0]), Number(safe[1]), Positive(safe[2]), Positive(safe[3]));
                    if (!SafeValid(p.Safe, p.Width, p.Height) || !PositiveFinite(25.4f / p.Dpi))
                        throw new ArgumentException();
                }
                else
                    throw new ArgumentException();
                options.Profiles.Add(p);
            }
            return options;
        }

        private static bool SelectorLimit(Budget b)
        {
            b.Reasons.Add("selector_limit");
            return false;
        }

        private static bool TryResolve(JToken token, bool inactive, Budget b, out GameObject result)
        {
            result = null;
            if (!b.Charge("snapshot_and_resolver", 2))
                return false;
            string selector = ((JValue)token).ToString(CultureInfo.InvariantCulture);
            if (int.TryParse(selector, NumberStyles.Integer, CultureInfo.InvariantCulture, out int id))
            {
                // FindById is a direct compatible native instance-ID lookup, not a scene enumeration.
                var direct = GameObjectLookup.FindById(id);
                if (!b.Charge("snapshot_and_resolver", 8))
                    return false;
                RequireSceneObject(direct, inactive);
                result = direct;
                return true;
            }
            bool pathSelector = selector.Contains("/");
            selector = selector.TrimStart('/');
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            int scenes = stage == null ? SceneManager.sceneCount : 1;
            GameObject found = null;
            for (int sceneIndex = 0; sceneIndex < scenes; sceneIndex++)
            {
                if (!b.Charge("snapshot_and_resolver", 4))
                    return false;
                GameObject[] roots;
                if (stage != null)
                    roots = new[] { stage.prefabContentsRoot };
                else
                {
                    var scene = SceneManager.GetSceneAt(sceneIndex);
                    if (!scene.IsValid() || !scene.isLoaded || EditorSceneManager.IsPreviewScene(scene))
                        continue;
                    // Bound and precharge the complete native roots snapshot before managed enumeration.
                    int count = scene.rootCount;
                    if (count > 1000 - b.SelectorVisited)
                        return SelectorLimit(b);
                    if (!b.Charge("snapshot_and_resolver", count + 1))
                        return false;
                    roots = scene.GetRootGameObjects();
                }
                foreach (var root in roots)
                {
                    if (!b.Charge("snapshot_and_resolver"))
                        return false;
                    var stack = new Stack<SelectorFrame>();
                    stack.Push(new SelectorFrame { Transform = root.transform, ParentPath = "" });
                    while (stack.Count > 0)
                    {
                        // Each resume, child fetch, visit, path extension and comparison shares this request's budget.
                        if (!b.Charge("snapshot_and_resolver"))
                            return false;
                        if (stack.Count > 128)
                            return SelectorLimit(b);
                        var frame = stack.Peek();
                        if (!frame.Entered)
                        {
                            if (b.SelectorVisited == 1000)
                                return SelectorLimit(b);
                            if (!b.Charge("snapshot_and_resolver", 2))
                                return false;
                            b.SelectorVisited++;
                            var go = frame.Transform.gameObject;
                            if (!inactive && !go.activeInHierarchy)
                            {
                                stack.Pop();
                                continue;
                            }
                            string name = go.name;
                            if (name.Length > 4096)
                                return SelectorLimit(b);
                            if (pathSelector)
                            {
                                if (!b.Charge("snapshot_and_resolver"))
                                    return false;
                                if ((frame.ParentPath.Length == 0 ? name.Length : frame.ParentPath.Length + name.Length + 1) > 4096)
                                    return SelectorLimit(b);
                                frame.Path = frame.ParentPath.Length == 0 ? name : frame.ParentPath + "/" + name;
                            }
                            frame.Entered = true;
                            if (!b.Charge("snapshot_and_resolver"))
                                return false;
                            if (string.Equals(pathSelector ? frame.Path : name, selector, StringComparison.Ordinal))
                            {
                                if (!b.Charge("snapshot_and_resolver"))
                                    return false;
                                if (found != null)
                                    throw new ArgumentException();
                                found = go;
                            }
                        }
                        if (!b.Charge("snapshot_and_resolver", 2))
                            return false;
                        if (frame.NextChild >= frame.Transform.childCount)
                        {
                            stack.Pop();
                            continue;
                        }
                        var child = frame.Transform.GetChild(frame.NextChild++);
                        stack.Push(new SelectorFrame { Transform = child, ParentPath = frame.Path });
                    }
                }
            }
            // A match is returned only after all eligible scene objects were checked for a second match.
            if (!b.Charge("snapshot_and_resolver", 8))
                return false;
            RequireSceneObject(found, inactive);
            result = found;
            return true;
        }

        private static void RequireSceneObject(GameObject go, bool inactive)
        {
            if (go == null || EditorUtility.IsPersistent(go) || !go.scene.IsValid() || !go.scene.isLoaded)
                throw new ArgumentException();
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null ? go.scene != stage.scene : EditorSceneManager.IsPreviewScene(go.scene))
                throw new ArgumentException();
            if (!inactive && !go.activeInHierarchy)
                throw new ArgumentException();
        }

        private static void Observe(Profile p)
        {
            p.Width = Screen.width;
            p.Height = Screen.height;
            p.Dpi = Screen.dpi;
            p.Safe = Screen.safeArea;
            NormalizeObservation(p, Screen.orientation);
        }

        private static void NormalizeObservation(Profile p, ScreenOrientation orientation)
        {
            switch (orientation)
            {
                case ScreenOrientation.Portrait:
                    p.Orientation = "portrait";
                    break;
                case ScreenOrientation.PortraitUpsideDown:
                    p.Orientation = "portrait_upside_down";
                    break;
                case ScreenOrientation.LandscapeLeft:
                    p.Orientation = "landscape_left";
                    break;
                case ScreenOrientation.LandscapeRight:
                    p.Orientation = "landscape_right";
                    break;
                default:
                    p.Orientation = null;
                    break;
            }
            p.Available =
                p.Width >= 64 && p.Width <= 8192 && p.Height >= 64 && p.Height <= 8192 && SafeValid(p.Safe, p.Width, p.Height) && p.Orientation != null;
            if (!PositiveFinite(p.Dpi) || !PositiveFinite(25.4f / p.Dpi))
                p.Dpi = 0;
        }

        private static Type Builtin(string name)
        {
            // Resolve the installed optional package, then require the engine-owned uGUI assembly.
            return AppDomain
                .CurrentDomain.GetAssemblies()
                .Where(a => a.GetName().Name == "UnityEngine.UI")
                .Select(a => a.GetType(name, false))
                .FirstOrDefault(t => t != null);
        }

        private static Type NativeTmp(string name = "TMPro.TextMeshProUGUI") =>
            AppDomain
                .CurrentDomain.GetAssemblies()
                .Where(a => a.GetName().Name == "Unity.TextMeshPro")
                .Select(a => a.GetType(name, false))
                .FirstOrDefault(t => t != null);

        private static bool ReadNativeTmp(Component component, Type nativeType, string member)
        {
            if (component.GetType() != nativeType)
                throw new ArgumentException();
            var property = component.GetType().GetProperty(member, BindingFlags.Public | BindingFlags.Instance);
            string assembly = property?.DeclaringType?.Assembly.GetName().Name;
            if (assembly != "Unity.TextMeshPro" && assembly != "UnityEngine.UI")
                throw new ArgumentException();
            return (bool)property.GetValue(component);
        }

        private static void RecordUnsupported(Component component, RectTransform source, Budget b)
        {
            if (b.UnsupportedComponent != null)
                return;
            string name = component.GetType().FullName;
            string path = source.name;
            Transform ancestor = source.parent;
            int depth = 0;
            while (ancestor != null && depth++ < 16 && path.Length < 240 && b.Charge("ancestors"))
            {
                path = ancestor.name + "/" + path;
                ancestor = ancestor.parent;
            }
            var detail = new JObject
            {
                ["instance_id"] = source.gameObject.GetInstanceIDCompat(),
                ["component_type"] = name.Length > 256 ? name.Substring(0, 256) : name,
                ["path"] = path.Length > 256 ? path.Substring(path.Length - 256) : path,
                ["path_truncated"] = ancestor != null || path.Length > 256,
            };
            int characters = detail.ToString(Formatting.None).Length;
            if (characters <= b.Options.Characters - b.Characters && b.Charge("output"))
            {
                b.Characters += characters;
                b.UnsupportedComponent = detail;
            }
            else
                b.Reasons.Add("max_characters");
        }

        private static T Read<T>(Component component, string member, T fallback)
        {
            if (component == null)
                return fallback;
            var type = component.GetType();
            if (type.Assembly.GetName().Name != "UnityEngine.UI" || type.Namespace != "UnityEngine.UI")
                throw new ArgumentException();
            var property = type.GetProperty(member, BindingFlags.Public | BindingFlags.Instance);
            if (property == null || property.DeclaringType.Assembly != type.Assembly)
                throw new ArgumentException();
            return (T)property.GetValue(component);
        }

        private static int EnumRead(Component component, string member) => Convert.ToInt32(Read<object>(component, member, null), CultureInfo.InvariantCulture);

        private static bool Scale(Canvas canvas, Component scaler, Profile p, out float scale, out float ppu, out bool fallback)
        {
            scale = canvas.scaleFactor;
            ppu = canvas.referencePixelsPerUnit;
            fallback = false;
            if (scaler != null && ((Behaviour)scaler).isActiveAndEnabled)
            {
                ppu = Read(scaler, "referencePixelsPerUnit", 100f);
                int mode = EnumRead(scaler, "uiScaleMode");
                if (mode == 0)
                    scale = Read(scaler, "scaleFactor", 1f);
                else if (mode == 1)
                {
                    Vector2 reference = Read(scaler, "referenceResolution", Vector2.zero);
                    if (!PositiveFinite(reference.x) || !PositiveFinite(reference.y))
                        return false;
                    float width = p.Width / reference.x,
                        height = p.Height / reference.y;
                    int match = EnumRead(scaler, "screenMatchMode");
                    if (match == 0)
                    {
                        float factor = Read(scaler, "matchWidthOrHeight", 0f);
                        if (!Finite(factor) || factor < 0 || factor > 1)
                            return false;
                        scale = Mathf.Pow(2, Mathf.Lerp(Mathf.Log(width, 2), Mathf.Log(height, 2), factor));
                    }
                    else if (match == 1)
                        scale = Mathf.Min(width, height);
                    else if (match == 2)
                        scale = Mathf.Max(width, height);
                    else
                        return false;
                }
                else if (mode == 2)
                {
                    float dpi = p.Dpi;
                    if (!PositiveFinite(dpi))
                    {
                        dpi = Read(scaler, "fallbackScreenDPI", 0f);
                        fallback = true;
                    }
                    float unit;
                    switch (EnumRead(scaler, "physicalUnit"))
                    {
                        case 0:
                            unit = 2.54f;
                            break;
                        case 1:
                            unit = 25.4f;
                            break;
                        case 2:
                            unit = 1f;
                            break;
                        case 3:
                            unit = 72f;
                            break;
                        case 4:
                            unit = 6f;
                            break;
                        default:
                            return false;
                    }
                    float spriteDpi = Read(scaler, "defaultSpriteDPI", 0f);
                    if (!PositiveFinite(dpi) || !PositiveFinite(spriteDpi))
                        return false;
                    scale = dpi / unit;
                    ppu = ppu * unit / spriteDpi;
                }
                else
                    return false;
            }
            return PositiveFinite(scale) && PositiveFinite(ppu) && PositiveFinite(p.Width / scale) && PositiveFinite(p.Height / scale);
        }

        private static void Overlay(GameObject target, Canvas canvas, Options options, Budget b, JArray reports)
        {
            Scene scene = default(Scene);
            try
            {
                scene = EditorSceneManager.NewPreviewScene();
                var root = new GameObject("__McpDeviceProfiles", typeof(RectTransform));
                root.hideFlags = HideFlags.HideAndDontSave;
                root.SetActive(false);
                SceneManager.MoveGameObjectToScene(root, scene);
                var nodes = new List<Node>();
                var stack = new Stack<(Transform source, Node parent, int depth)>();
                stack.Push((canvas.transform, null, 0));
                var scalerType = Builtin("UnityEngine.UI.CanvasScaler");
                var tmpType = NativeTmp();
                var tmpInputType = NativeTmp("TMPro.TMP_InputField");
                Component scaler = null;
                bool unsupported = false,
                    targetFound = false;
                while (stack.Count > 0)
                {
                    if (nodes.Count == options.Nodes || !b.Charge("transforms"))
                    {
                        b.Reasons.Add("max_nodes");
                        break;
                    }
                    var item = stack.Pop();
                    if (!(item.source is RectTransform source))
                    {
                        unsupported = true;
                        continue;
                    }
                    var clone = item.parent == null ? root : new GameObject("node", typeof(RectTransform));
                    clone.hideFlags = HideFlags.HideAndDontSave;
                    clone.SetActive(false);
                    var rect = (RectTransform)clone.transform;
                    if (item.parent != null)
                    {
                        rect.SetParent(item.parent.Preview, false);
                        rect.anchorMin = source.anchorMin;
                        rect.anchorMax = source.anchorMax;
                        rect.pivot = source.pivot;
                        rect.sizeDelta = source.sizeDelta;
                        rect.anchoredPosition3D = source.anchoredPosition3D;
                        rect.localRotation = source.localRotation;
                        rect.localScale = source.localScale;
                    }
                    var node = new Node
                    {
                        Source = source,
                        Preview = rect,
                        Depth = item.depth,
                    };
                    nodes.Add(node);
                    targetFound |= source == target.transform;
                    var components = source.GetComponents<Component>();
                    if (!b.Charge("components", components.Length))
                    {
                        unsupported = true;
                        break;
                    }
                    Component fixedText = null,
                        fixedInput = null;
                    bool fitsText = false;
                    foreach (var component in components)
                    {
                        if (component == null)
                        {
                            unsupported = true;
                            continue;
                        }
                        Type type = component.GetType();
                        string name = type.FullName;
                        bool builtin = type == Builtin(name);
                        if (type == tmpType)
                        {
                            // No TMP clone, text parsing, preferred-size getter, font lookup or callback.
                            fixedText = component;
                            continue;
                        }
                        if (type == tmpInputType)
                        {
                            // Input preferred metrics delegate to TMP text; never clone or query them.
                            fixedInput = component;
                            continue;
                        }
                        if (builtin && PreviewNames.Contains(name))
                        {
                            // Screen-space root AspectRatioFitter is inert. Do not enable it on our WorldSpace root.
                            if (item.parent == null && name == "UnityEngine.UI.AspectRatioFitter")
                                continue;
                            var copy = clone.AddComponent(type);
                            EditorUtility.CopySerialized(component, copy);
                            if (((Behaviour)component).isActiveAndEnabled)
                            {
                                if (name == "UnityEngine.UI.HorizontalLayoutGroup" || name == "UnityEngine.UI.VerticalLayoutGroup")
                                    node.ConsumesChildTextMetrics = Read(component, "childControlWidth", true) || Read(component, "childControlHeight", true);
                                if (name == "UnityEngine.UI.ContentSizeFitter")
                                    fitsText = EnumRead(component, "horizontalFit") != 0 || EnumRead(component, "verticalFit") != 0;
                            }
                            if (
                                (name == "UnityEngine.UI.Image" || name == "UnityEngine.UI.RawImage")
                                && ((Behaviour)component).isActiveAndEnabled
                                && Read(component, "raycastTarget", false)
                            )
                                node.Candidate = true;
                        }
                        else if (component is Canvas sourceCanvas)
                        {
                            var copy = clone.AddComponent<Canvas>();
                            copy.renderMode = RenderMode.WorldSpace;
                            copy.enabled = sourceCanvas.enabled;
                            copy.scaleFactor = sourceCanvas.scaleFactor;
                            copy.referencePixelsPerUnit = sourceCanvas.referencePixelsPerUnit;
                            if (item.parent != null)
                                unsupported = true;
                        }
                        else if (builtin && type == scalerType && item.parent == null)
                            scaler = component;
                        else if (component is MonoBehaviour)
                        {
                            // Do not read custom ILayoutIgnorer, Selectable, Graphic or hidden enum getters.
                            // Known non-layout built-ins do not supply geometry to this preview.
                            if (
                                !builtin
                                || (
                                    !new[]
                                    {
                                        "UnityEngine.UI.GraphicRaycaster",
                                        "UnityEngine.UI.Button",
                                        "UnityEngine.UI.Toggle",
                                        "UnityEngine.UI.Slider",
                                        "UnityEngine.UI.Scrollbar",
                                        "UnityEngine.UI.Mask",
                                        "UnityEngine.UI.RectMask2D",
                                    }.Contains(name)
                                )
                            )
                            {
                                unsupported = true;
                                RecordUnsupported(component, source, b);
                            }
                        }
                    }
                    if (fixedInput != null && ((Behaviour)fixedInput).isActiveAndEnabled && (fitsText || item.parent?.ConsumesChildTextMetrics == true))
                    {
                        unsupported = true;
                        RecordUnsupported(fixedInput, source, b);
                    }
                    if (fixedText != null)
                    {
                        bool active = ((Behaviour)fixedText).isActiveAndEnabled;
                        // TMP contributes preferred metrics only when a fitter or a direct parent
                        // linear group consumes them. Grid cells and fixed rectangles do not.
                        if (active && (fitsText || item.parent?.ConsumesChildTextMetrics == true || ReadNativeTmp(fixedText, tmpType, "autoSizeTextContainer")))
                        {
                            unsupported = true;
                            RecordUnsupported(fixedText, source, b);
                        }
                        else if (active && ReadNativeTmp(fixedText, tmpType, "raycastTarget"))
                            node.Candidate = true;
                    }
                    if (item.parent != null)
                        clone.SetActive(source.gameObject.activeSelf);
                    if (source.childCount > 0 && item.depth == options.Depth)
                    {
                        b.Reasons.Add("max_depth");
                        continue;
                    }
                    int count = source.childCount;
                    if (!b.Charge("transforms", count))
                        break;
                    for (int i = count - 1; i >= 0; i--)
                    {
                        var child = source.GetChild(i);
                        if (options.Inactive || child.gameObject.activeInHierarchy)
                            stack.Push((child, node, item.depth + 1));
                    }
                }
                if (!targetFound)
                {
                    b.Reasons.Add("target_outside_preview");
                    return;
                }
                var rootRect = (RectTransform)root.transform;
                rootRect.pivot = new Vector2(0.5f, 0.5f);
                rootRect.localPosition = Vector3.zero;
                rootRect.localRotation = Quaternion.identity;
                rootRect.localScale = Vector3.one;
                var previewCanvas = root.GetComponent<Canvas>();
                var rebuilder = Builtin("UnityEngine.UI.LayoutRebuilder")?.GetMethod("ForceRebuildLayoutImmediate", BindingFlags.Public | BindingFlags.Static);
                if (previewCanvas == null || rebuilder == null)
                    throw new ArgumentException();
                foreach (var p in options.Profiles)
                {
                    if (!b.Charge("profile_setups"))
                        break;
                    var report = Receipt(p, "owned_overlay_preview");
                    reports.Add(report);
                    if (!p.Available || (p.Source == "observed" && canvas.targetDisplay != 0))
                    {
                        Unavailable(report, "observed_profile_unavailable");
                        continue;
                    }
                    if (!Scale(canvas, scaler, p, out float scale, out float ppu, out bool fallback))
                    {
                        Unavailable(report, "invalid_scaler_math");
                        continue;
                    }
                    if (!b.Charge("assessment_pairs", nodes.Count * 2))
                    {
                        Unavailable(report, "budget_incomplete");
                        break;
                    }
                    previewCanvas.scaleFactor = scale;
                    previewCanvas.referencePixelsPerUnit = ppu;
                    rootRect.sizeDelta = new Vector2(p.Width / scale, p.Height / scale);
                    root.SetActive(canvas.gameObject.activeInHierarchy);
                    // Execute only copied exact built-ins, including Image's sprite preferred-size provider.
                    rebuilder.Invoke(null, new object[] { rootRect });
                    report["scale_factor"] = scale;
                    report["reference_pixels_per_unit"] = ppu;
                    report["layout_dpi_source"] =
                        fallback ? "canvas_scaler_fallback"
                        : p.Dpi > 0
                            ? p.Source == "virtual" ? "request"
                                : "screen"
                        : "not_used";
                    if (unsupported || b.Reasons.Count != 0)
                    {
                        report["status"] = "partial";
                        ((JArray)report["reasons"]).Add(unsupported ? "unsupported_layout_provider" : "incomplete_preview");
                    }
                    foreach (var node in nodes)
                    {
                        if (node.Source != target.transform && !node.Source.IsChildOf(target.transform))
                            continue;
                        Rect bounds = Bounds(node.Preview, rootRect, scale, p.Width, p.Height);
                        AddAssessment(
                            report,
                            node.Source.gameObject.GetInstanceIDCompat(),
                            bounds,
                            node.Candidate,
                            unsupported || b.Reasons.Count != 0,
                            p,
                            options,
                            b
                        );
                    }
                    root.SetActive(false);
                }
            }
            finally
            {
                if (scene.IsValid())
                    EditorSceneManager.ClosePreviewScene(scene);
            }
        }

        private static JObject CameraProfiles(GameObject target, Canvas canvas, Options o, Budget b, JArray reports)
        {
            if (!b.Charge("snapshot_and_resolver") || o.Evaluations - b.Spent < 1)
                return null;
            JToken cameraReference = o.Camera;
            if (cameraReference != null && cameraReference.Type != JTokenType.Null)
            {
                try
                {
                    if (!TryResolve(cameraReference, true, b, out GameObject cameraObject))
                        return null;
                    cameraReference = new JValue(cameraObject.GetInstanceIDCompat());
                }
                catch
                {
                    foreach (var p in o.Profiles)
                    {
                        if (!b.Charge("profile_setups"))
                            break;
                        var failed = Receipt(p, "current_camera_geometry");
                        Unavailable(failed, "camera_geometry_unavailable");
                        reports.Add(failed);
                    }
                    return null;
                }
            }
            var request = new JObject
            {
                ["action"] = "diagnose_projection",
                ["target"] = target.GetInstanceIDCompat(),
                ["camera"] = cameraReference,
                ["include_inactive"] = o.Inactive,
                ["max_nodes"] = o.Nodes,
                ["max_depth"] = o.Depth,
                ["max_evaluations"] = o.Evaluations - b.Spent,
                ["max_characters"] = o.Characters,
            };
            var response = JObject.FromObject(UguiProjectionDiagnostics.Diagnose(request));
            var projection = response["data"] as JObject;
            if (projection == null)
            {
                foreach (var p in o.Profiles)
                {
                    if (!b.Charge("profile_setups"))
                        break;
                    var failed = Receipt(p, "current_camera_geometry");
                    Unavailable(failed, "camera_geometry_unavailable");
                    reports.Add(failed);
                }
                return null;
            }
            b.Charge("projection", projection["traversal"]["evaluations"].Value<int>());
            int projectionCharacters = projection.ToString(Formatting.None).Length;
            if (projectionCharacters > o.Characters - b.Characters)
            {
                b.Reasons.Add("max_characters");
                return null;
            }
            if (!b.Charge("output"))
                return null;
            b.Characters += projectionCharacters;
            if (projection["status"].Value<string>() != "complete")
                b.Reasons.Add("projection_incomplete");
            var output = projection["context"]?["output"];
            var nodes = projection["nodes"] as JArray;
            foreach (var p in o.Profiles)
            {
                if (!b.Charge("profile_setups"))
                    break;
                var report = Receipt(p, "current_camera_geometry");
                reports.Add(report);
                bool primary =
                    output?["kind"]?.Value<string>() == "display"
                    && output["target_display"]?.Value<int>() == 0
                    && output["dimension_source"]?.Value<string>() == "current_screen";
                if (!p.Available || (p.Source == "observed" && !primary))
                {
                    Unavailable(report, "observed_profile_unavailable");
                    continue;
                }
                if (output?["pixel_status"]?.Value<string>() != "available")
                {
                    Unavailable(report, "pixels_unavailable");
                    continue;
                }
                if (p.Width != output["width"].Value<int>() || p.Height != output["height"].Value<int>())
                {
                    Unavailable(report, "camera_resolution_resimulation_unsupported");
                    continue;
                }
                report["scale_factor"] = PositiveFinite(canvas.scaleFactor) ? (JToken)new JValue(canvas.scaleFactor) : JValue.CreateNull();
                report["reference_pixels_per_unit"] = PositiveFinite(canvas.referencePixelsPerUnit)
                    ? (JToken)new JValue(canvas.referencePixelsPerUnit)
                    : JValue.CreateNull();
                if (projection["status"].Value<string>() != "complete")
                {
                    report["status"] = "partial";
                    ((JArray)report["reasons"]).Add("projection_incomplete");
                }
                foreach (JObject node in nodes)
                {
                    // These corners already contain CanvasScaler effects. Never multiply by scale again.
                    var bounds = node["clipped_target_pixel_bounds"] as JArray;
                    if (bounds == null || node["pixel_status"].Value<string>() != "available")
                    {
                        report["status"] = "partial";
                        continue;
                    }
                    var rect = new Rect(bounds[0].Value<float>(), bounds[1].Value<float>(), bounds[2].Value<float>(), bounds[3].Value<float>());
                    int id = node["instance_id"].Value<int>();
                    bool candidate = false;
                    if (!b.Charge("snapshot_and_resolver"))
                    {
                        report["status"] = "partial";
                        break;
                    }
                    var source = UnityObjectIdCompat.InstanceIDToObjectCompat(id) as GameObject;
                    if (source != null)
                    {
                        foreach (string name in new[] { "UnityEngine.UI.Image", "UnityEngine.UI.RawImage", "UnityEngine.UI.Text" })
                        {
                            if (!b.Charge("components"))
                                break;
                            Type type = Builtin(name);
                            Component graphic = type == null ? null : source.GetComponent(type);
                            if (
                                graphic != null
                                && graphic.GetType() == type
                                && ((Behaviour)graphic).isActiveAndEnabled
                                && Read(graphic, "raycastTarget", false)
                            )
                                candidate = true;
                        }
                    }
                    AddAssessment(report, id, rect, candidate, false, p, o, b);
                }
            }
            return projection;
        }

        private static JObject Receipt(Profile p, string route) =>
            new JObject
            {
                ["source"] = p.Source,
                ["width"] = p.Width,
                ["height"] = p.Height,
                ["dpi"] = p.Dpi > 0 ? (JToken)new JValue(p.Dpi) : JValue.CreateNull(),
                ["dpi_source"] =
                    p.Dpi > 0
                        ? p.Source == "virtual"
                            ? "request"
                            : "screen"
                        : "unavailable",
                ["safe_area"] = p.Available ? (JToken)RectJson(p.Safe) : JValue.CreateNull(),
                ["orientation"] = p.Orientation,
                ["route"] = route,
                ["status"] = "complete",
                ["reasons"] = new JArray(),
                ["scale_factor"] = null,
                ["reference_pixels_per_unit"] = null,
                ["layout_dpi_source"] = "not_used",
                ["nodes"] = new JArray(),
            };

        private static void Unavailable(JObject report, string reason)
        {
            report["status"] = "unavailable";
            ((JArray)report["reasons"]).Add(reason);
        }

        private static void AddAssessment(JObject report, int id, Rect r, bool candidate, bool unknown, Profile p, Options o, Budget b)
        {
            if (!b.Charge("assessment_pairs"))
            {
                report["status"] = "partial";
                return;
            }
            if (!RectValid(r))
            {
                report["status"] = "partial";
                ((JArray)report["reasons"]).Add(
                    Finite(r.x) && Finite(r.y) && Finite(r.width) && Finite(r.height) && Finite(r.xMax) && Finite(r.yMax)
                        ? "degenerate_geometry"
                        : "nonfinite_geometry"
                );
                return;
            }
            double widthMm = (double)r.width / p.Dpi * 25.4,
                heightMm = (double)r.height / p.Dpi * 25.4;
            bool measured = p.Dpi > 0 && PositiveFinite((float)widthMm) && PositiveFinite((float)heightMm);
            double intersectionWidth = Math.Max(0, Math.Min(r.xMax, p.Safe.xMax) - Math.Max(r.xMin, p.Safe.xMin));
            double intersectionHeight = Math.Max(0, Math.Min(r.yMax, p.Safe.yMax) - Math.Max(r.yMin, p.Safe.yMin));
            double area = (double)r.width * r.height;
            double outside = Math.Max(0, area - intersectionWidth * intersectionHeight);
            if (!Finite(outside))
            {
                report["status"] = "partial";
                return;
            }
            var entry = new JObject
            {
                ["instance_id"] = id,
                ["pixel_bounds"] = RectJson(r),
                ["raycast_rectangle_candidate"] = candidate,
                ["width_mm"] = measured && !unknown ? (JToken)new JValue(widthMm) : JValue.CreateNull(),
                ["height_mm"] = measured && !unknown ? (JToken)new JValue(heightMm) : JValue.CreateNull(),
                ["unsafe_region_area_px2"] = unknown ? (JToken)JValue.CreateNull() : new JValue(outside),
                ["safe_area_relation"] =
                    unknown ? "unknown"
                    : outside > 0 ? "possible_unsafe_region"
                    : "inside_geometric_safe_area",
                ["touch_target"] =
                    !candidate || o.Minimum == null || !measured || unknown ? "unknown"
                    : widthMm < o.Minimum.Value || heightMm < o.Minimum.Value ? "below_caller_threshold"
                    : "meets_caller_threshold",
            };
            int size = entry.ToString(Formatting.None).Length;
            if (size > o.Characters - b.Characters || !b.Charge("output"))
            {
                b.Reasons.Add("max_characters");
                report["status"] = "partial";
                return;
            }
            b.Characters += size;
            ((JArray)report["nodes"]).Add(entry);
        }

        private static Rect Bounds(RectTransform rect, RectTransform root, float scale, int width, int height)
        {
            var corners = new Vector3[4];
            rect.GetWorldCorners(corners);
            float xmin = float.PositiveInfinity,
                ymin = float.PositiveInfinity,
                xmax = float.NegativeInfinity,
                ymax = float.NegativeInfinity;
            foreach (var corner in corners)
            {
                var point = root.InverseTransformPoint(corner);
                float x = point.x * scale + width * 0.5f,
                    y = point.y * scale + height * 0.5f;
                if (!Finite(x) || !Finite(y))
                    return new Rect(float.NaN, 0, 0, 0);
                xmin = Mathf.Min(xmin, x);
                xmax = Mathf.Max(xmax, x);
                ymin = Mathf.Min(ymin, y);
                ymax = Mathf.Max(ymax, y);
            }
            return new Rect(xmin, ymin, xmax - xmin, ymax - ymin);
        }

        private static object Finish(Options o, Budget b, JArray reports, JObject projection)
        {
            if (reports.Count != o.Profiles.Count)
                b.Reasons.Add("profiles_incomplete");
            var data = new JObject
            {
                ["kind"] = "ugui_device_profile_diagnostics",
                ["schema_version"] = 1,
                ["frame"] = Time.frameCount,
                ["status"] = b.Reasons.Count == 0 && reports.All(p => p["status"].Value<string>() == "complete") ? "complete" : "partial",
                ["minimum_touch_target_mm"] = o.Minimum == null ? (JToken)JValue.CreateNull() : new JValue(o.Minimum.Value),
                ["policy"] = new JObject
                {
                    ["pixel_origin"] = "bottom_left",
                    ["orientation"] = "supplied_frame_no_rotation",
                    ["layout"] = "owned_builtin_preview_or_current_camera",
                    ["custom_callbacks"] = "not_invoked",
                    ["measurement"] = "dpi_geometry_estimate",
                    ["interaction"] = "not_observed",
                    ["rendering"] = "not_observed",
                },
                ["profiles"] = reports,
                ["projection"] = projection,
                ["unsupported_component"] = b.UnsupportedComponent,
                ["budget"] = new JObject
                {
                    ["max_nodes"] = o.Nodes,
                    ["max_depth"] = o.Depth,
                    ["max_evaluations"] = o.Evaluations,
                    ["max_characters"] = o.Characters,
                    ["evaluations"] = b.Spent,
                    ["characters"] = b.Characters,
                    ["evaluation_counts"] = b.Counts,
                    ["reasons"] = new JArray(b.Reasons.OrderBy(v => v, StringComparer.Ordinal)),
                },
                ["limitations"] = new JArray
                {
                    "Axis-aligned rectangles may overestimate rotated shapes; sprite alpha, masks and notch occlusion are not evaluated.",
                    "DPI-derived sizes are estimates, not hardware measurements or event delivery guarantees; thresholds are caller supplied.",
                    "Observed Screen metadata applies only to the primary current-screen route. Fallback DPI explains layout only.",
                    "Unknown/custom layout providers are omitted without invoking their getters; affected geometry and findings are unknown.",
                    "Managed work accounting is not a hard deadline on Unity native getters, scene lookup or built-in preview layout internals.",
                },
            };
            if (Encoding.UTF8.GetByteCount(data.ToString(Formatting.None)) > 2 * 1024 * 1024)
                return new ErrorResponse("device_profiles_artifact_limit");
            return new SuccessResponse("Device profile geometry assessed.", data);
        }

        private static void Keys(JObject obj, string names)
        {
            var allowed = new HashSet<string>(names.Split(' '), StringComparer.Ordinal);
            if (obj == null || obj.Properties().Any(p => !allowed.Contains(p.Name)))
                throw new ArgumentException();
        }

        private static int Integer(JToken token, int fallback, int min, int max)
        {
            int value = fallback;
            if (
                token != null
                && token.Type != JTokenType.Null
                && (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            )
                throw new ArgumentException();
            if (value < min || value > max)
                throw new ArgumentException();
            return value;
        }

        private static void Reference(JToken token, bool optional)
        {
            if (optional && (token == null || token.Type == JTokenType.Null))
                return;
            if (token?.Type == JTokenType.Integer)
            {
                Integer(token, 0, int.MinValue, int.MaxValue);
                return;
            }
            if (token?.Type != JTokenType.String)
                throw new ArgumentException();
            string value = token.Value<string>();
            if (string.IsNullOrWhiteSpace(value) || value.Length > 4096)
                throw new ArgumentException();
            new UTF8Encoding(false, true).GetByteCount(value);
        }

        private static float Number(JToken token)
        {
            if (token == null || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float))
                throw new ArgumentException();
            float result = (float)token.Value<double>();
            if (!Finite(result))
                throw new ArgumentException();
            return result;
        }

        private static float Positive(JToken token)
        {
            float result = Number(token);
            if (!PositiveFinite(result))
                throw new ArgumentException();
            return result;
        }

        private static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);

        private static bool PositiveFinite(float value) => Finite(value) && value > 0;

        private static bool RectValid(Rect r) =>
            Finite(r.x) && Finite(r.y) && PositiveFinite(r.width) && PositiveFinite(r.height) && Finite(r.xMax) && Finite(r.yMax);

        private static bool SafeValid(Rect r, int width, int height) => RectValid(r) && r.x >= 0 && r.y >= 0 && r.xMax <= width && r.yMax <= height;

        private static JArray RectJson(Rect r) => new JArray(r.x, r.y, r.width, r.height);
    }
}
