using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
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
    // Only create_control uses this transaction. Legacy creation keeps its existing behavior.
    internal static class UguiCompoundCreation
    {
        private static Action<string, GameObject> FaultForTests = null;
        private static Func<string, Type> ResolveForTests = null;
        private static Func<Type, string, PropertyInfo> PropertyForTests = null;
        private static Action<Component, string> AfterAssignmentForTests = null;
        private static Action BeforeRollbackForTests = null;
        private static Action BeforeFinalizeForTests = null;
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private const BindingFlags PublicInstance = BindingFlags.Public | BindingFlags.Instance;

        private sealed class ReferenceComparer : IEqualityComparer<GameObject>
        {
            public bool Equals(GameObject a, GameObject b) => ReferenceEquals(a, b);

            public int GetHashCode(GameObject obj) => RuntimeHelpers.GetHashCode(obj);
        }

        private sealed class Prior
        {
            internal GameObject Object;
            internal Transform Parent;
            internal Scene Scene;
            internal int Sibling;
        }

        private sealed class MemberSlot
        {
            internal PropertyInfo Property;
            internal FieldInfo Field;
            internal Type PropertyType => Property != null ? Property.PropertyType : Field.FieldType;
            internal Type DeclaringType => Property != null ? Property.DeclaringType : Field.DeclaringType;

            internal object GetValue(object target) => Property != null ? Property.GetValue(target) : Field.GetValue(target);

            internal void SetValue(object target, object value)
            {
                if (Property != null)
                    Property.SetValue(target, value);
                else
                    Field.SetValue(target, value);
            }
        }

        private sealed class Plan
        {
            internal string Kind,
                Name,
                EventSystem;
            internal GameObject Parent;
            internal GameObject ExistingEventSystem;
            internal Vector2 Size,
                Position;
            internal JObject Values;
            internal UnityEngine.Object Font;
            internal Scene Scene;
            internal bool NewEventSystem;
            internal readonly Dictionary<string, Type> Types = new Dictionary<string, Type>();
            internal readonly Dictionary<string, MemberSlot> Properties = new Dictionary<string, MemberSlot>();
            internal readonly Dictionary<string, MethodInfo> Methods = new Dictionary<string, MethodInfo>();
            internal List<Prior> Before;
        }

        private sealed class Transaction
        {
            internal readonly Plan Plan;
            internal readonly List<GameObject> Authored = new List<GameObject>();
            internal readonly HashSet<GameObject> Generated = new HashSet<GameObject>(new ReferenceComparer());
            internal readonly Dictionary<string, GameObject> Roles = new Dictionary<string, GameObject>();
            internal readonly List<JObject> Errors = new List<JObject>();
            internal readonly HashSet<GameObject> Before;
            internal bool Unknown;

            internal Transaction(Plan plan)
            {
                Plan = plan;
                Before = new HashSet<GameObject>(plan.Before.Select(p => p.Object), new ReferenceComparer());
            }

            internal void Error(string phase, Exception error)
            {
                Unknown |= phase != "build";
                string type = Unwrap(error).GetType().Name;
                Errors.Add(new JObject { ["phase"] = phase, ["type"] = type.Length <= 96 ? type : type.Substring(0, 96) });
            }
        }

        internal static object Run(JObject input)
        {
            Plan plan = Prepare(input); // All intrinsic admission and identity snapshots precede Undo/allocation.
            var tx = new Transaction(plan);
            int group = -1;
            JObject success = null;
            try
            {
                Undo.IncrementCurrentGroup();
                group = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Create compound uGUI");
                success = Build(tx);
                if (plan.Before.Any(prior => prior.Object == null || UnderOwned(tx, prior.Object.transform)))
                    throw new InvalidOperationException("compound_preexisting_ownership_changed");
                if (Utf8.GetByteCount(success.ToString(Formatting.None)) > 8192)
                    throw new InvalidOperationException("compound_readback_limit");
                Undo.FlushUndoRecordObjects();
                Undo.CollapseUndoOperations(group);
            }
            catch (Exception error)
            {
                tx.Error("build", error);
                if (group >= 0)
                {
                    try
                    {
                        Rollback(tx, group);
                    }
                    catch (Exception rollbackError)
                    {
                        tx.Error("rollback_internal", rollbackError);
                    }
                }
            }
            finally
            {
                if (group >= 0)
                {
                    try
                    {
                        BeforeFinalizeForTests?.Invoke();
                        Undo.IncrementCurrentGroup();
                    }
                    catch (Exception error)
                    {
                        tx.Error("finalize", error);
                    }
                }
            }
            if (tx.Errors.Count == 0)
                return success;
            return new JObject
            {
                ["success"] = false,
                ["message"] = "compound_creation_failed",
                ["data"] = new JObject
                {
                    ["allocation_started"] = tx.Authored.Count > 0,
                    ["rollback_status"] = tx.Unknown ? "unknown" : "complete",
                    ["error_count"] = tx.Errors.Count,
                    ["errors_truncated"] = tx.Errors.Count > 8,
                    ["errors"] = new JArray(tx.Errors.Take(8)),
                },
            };
        }

        private static Exception Unwrap(Exception error) =>
            error is TargetInvocationException invocation && invocation.InnerException != null ? invocation.InnerException : error;

        private static void Closed(JObject obj, params string[] keys)
        {
            if (obj == null || obj.Properties().Any(p => !keys.Contains(p.Name)))
                throw new ArgumentException("invalid_compound_keys");
        }

        private static string Text(JToken token, string fallback, int bytes)
        {
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.String)
                throw new ArgumentException("invalid_compound_text");
            string value = token.Value<string>();
            if (Utf8.GetByteCount(value) > bytes)
                throw new ArgumentException("compound_text_limit");
            return value;
        }

        private static bool Bool(JToken token, bool fallback)
        {
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Boolean)
                throw new ArgumentException("invalid_compound_boolean");
            return token.Value<bool>();
        }

        private static float Number(JToken token, float fallback)
        {
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Float && token.Type != JTokenType.Integer)
                throw new ArgumentException("invalid_compound_number");
            double value = token.Value<double>();
            float result = (float)value;
            if (double.IsNaN(value) || double.IsInfinity(value) || float.IsNaN(result) || float.IsInfinity(result))
                throw new ArgumentException("invalid_compound_number");
            return result;
        }

        private static Vector2 Pair(JToken token, Vector2 fallback, bool size)
        {
            if (token == null)
                return fallback;
            if (!(token is JArray values) || values.Count != 2)
                throw new ArgumentException("invalid_compound_pair");
            var result = new Vector2(Number(values[0], 0), Number(values[1], 0));
            if (size && (result.x < 1 || result.y < 1 || result.x > 8192 || result.y > 8192))
                throw new ArgumentException("invalid_compound_size");
            return result;
        }

        private static Type Resolve(string name)
        {
            Type type = ResolveForTests != null ? ResolveForTests(name) : UnityTypeResolver.ResolveAny(name);
            if (type == null)
                throw new NotSupportedException("compound_type_unavailable:" + name);
            return type;
        }

        private static Type Need(Plan p, string name)
        {
            Type type = Resolve(name);
            p.Types.Add(name, type);
            return type;
        }

        private static MemberSlot Member(Type type, string name, Type exact = null, bool write = true)
        {
            PropertyInfo property = PropertyForTests != null ? PropertyForTests(type, name) : type.GetProperty(name, PublicInstance);
            // The installed uGUI Toggle exposes graphic as a public field, unlike Selectable.targetGraphic.
            // Keep this exception closed to that known writable member; injected missing-member checks still refuse.
            if (property == null && PropertyForTests == null && type.FullName == "UnityEngine.UI.Toggle" && name == "graphic")
            {
                FieldInfo field = type.GetField(name, PublicInstance);
                if (field == null || field.IsInitOnly || field.IsLiteral || (exact != null && field.FieldType != exact))
                    throw new NotSupportedException("compound_member_unavailable:" + name);
                return new MemberSlot { Field = field };
            }
            if (
                property == null
                || property.GetIndexParameters().Length != 0
                || property.GetGetMethod() == null
                || (write && property.GetSetMethod() == null)
                || (exact != null && property.PropertyType != exact)
            )
                throw new NotSupportedException("compound_member_unavailable:" + name);
            return new MemberSlot { Property = property };
        }

        private static void NeedMember(Plan p, Type type, string name, Type exact = null) =>
            p.Properties[type.FullName + "." + name] = Member(type, name, exact);

        private static void NeedMethod(Plan p, Type type, string name, params Type[] args)
        {
            MethodInfo method = type.GetMethod(name, PublicInstance, null, args, null);
            if (method == null)
                throw new NotSupportedException("compound_method_unavailable:" + name);
            p.Methods[type.FullName + "." + name] = method;
        }

        private static Plan Prepare(JObject input)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new InvalidOperationException("compound_requires_edit_mode");
            Closed(input, "action", "element_type", "parent", "name", "properties", "include_inactive");
            if (input.Value<string>("action") != "create_control")
                throw new ArgumentException("invalid_compound_action");
            string kind = Text(input["element_type"], null, 32);
            if (!new[] { "scroll_view", "toggle", "slider", "tmp_input" }.Contains(kind))
                throw new ArgumentException("invalid_compound_kind");
            string name = Text(input["name"], null, 256);
            ManageUGUI.ValidateCreationName(name);
            if (string.IsNullOrWhiteSpace(name) || name.Any(c => c < 32))
                throw new ArgumentException("invalid_compound_name");
            JToken selector = input["parent"];
            if (selector == null || !(selector.Type == JTokenType.Integer || selector.Type == JTokenType.String))
                throw new ArgumentException("invalid_compound_parent");
            if (
                selector.Type == JTokenType.Integer
                && (selector.Value<long>() < int.MinValue || selector.Value<long>() > int.MaxValue || selector.Value<long>() == 0)
            )
                throw new ArgumentException("invalid_compound_parent");
            if (selector.Type == JTokenType.String && string.IsNullOrWhiteSpace(Text(selector, null, 1024)))
                throw new ArgumentException("invalid_compound_parent");
            bool inactive = Bool(input["include_inactive"], false);
            GameObject parent = ManageUGUI.Resolve(selector, inactive);
            Scene scene = SceneManager.GetActiveScene();
            if (
                parent == null
                || EditorUtility.IsPersistent(parent)
                || !(parent.transform is RectTransform)
                || parent.GetComponentInParent<Canvas>(true) == null
                || !scene.IsValid()
                || !scene.isLoaded
                || !parent.scene.Equals(scene)
                || (!inactive && !parent.activeInHierarchy)
                || PrefabStageUtility.GetCurrentPrefabStage() != null
                || EditorSceneManager.IsPreviewScene(scene)
            )
                throw new ArgumentException("compound_parent_requires_active_canvas_scene");
            var values =
                input["properties"] as JObject ?? (input["properties"] == null ? new JObject() : throw new ArgumentException("invalid_compound_properties"));
            var common = new[] { "sizeDelta", "anchoredPosition", "eventSystem" };
            string[] specific =
                kind == "scroll_view" ? new[] { "spacing" }
                : kind == "toggle" ? new[] { "label", "isOn" }
                : kind == "slider" ? new[] { "minValue", "maxValue", "value", "wholeNumbers", "direction" }
                : new[] { "text", "placeholder", "characterLimit" };
            Closed(values, common.Concat(specific).ToArray());
            var p = new Plan
            {
                Kind = kind,
                Name = name,
                Parent = parent,
                Scene = scene,
                Values = new JObject(),
                Size = Pair(values["sizeDelta"], kind == "scroll_view" ? new Vector2(200, 200) : new Vector2(160, kind == "tmp_input" ? 36 : 24), true),
                Position = Pair(values["anchoredPosition"], Vector2.zero, false),
                EventSystem = Text(values["eventSystem"], "preserve", 32),
            };
            if (p.EventSystem != "preserve" && p.EventSystem != "create_legacy_if_missing")
                throw new ArgumentException("invalid_compound_event_system");
            if (kind == "scroll_view")
            {
                float spacing = Number(values["spacing"], 0);
                if (spacing < 0 || spacing > 128)
                    throw new ArgumentException("invalid_compound_spacing");
                p.Values["spacing"] = spacing;
            }
            if (kind == "toggle")
            {
                p.Values["label"] = Text(values["label"], "Toggle", 1024);
                p.Values["isOn"] = Bool(values["isOn"], false);
            }
            if (kind == "tmp_input")
            {
                string text = Text(values["text"], "", 1024);
                JToken limit = values["characterLimit"];
                if (limit != null && (limit.Type != JTokenType.Integer || limit.Value<long>() < 0 || limit.Value<long>() > 1024))
                    throw new ArgumentException("invalid_compound_character_limit");
                int characters = limit == null ? 256 : limit.Value<int>();
                if (characters != 0 && text.Length > characters)
                    throw new ArgumentException("compound_character_limit");
                p.Values["text"] = text;
                p.Values["placeholder"] = Text(values["placeholder"], "Enter text...", 1024);
                p.Values["characterLimit"] = characters;
            }
            if (kind == "slider")
            {
                float min = Number(values["minValue"], 0),
                    max = Number(values["maxValue"], 1),
                    value = Number(values["value"], 0);
                bool whole = Bool(values["wholeNumbers"], false);
                float span = max - min;
                if (
                    !(min < max)
                    || float.IsInfinity(span)
                    || value < min
                    || value > max
                    || (whole && (Math.Truncate(min) != min || Math.Truncate(max) != max || Math.Truncate(value) != value))
                )
                    throw new ArgumentException("invalid_compound_range");
                string direction = Text(values["direction"], "LeftToRight", 32);
                if (!new[] { "LeftToRight", "RightToLeft", "BottomToTop", "TopToBottom" }.Contains(direction))
                    throw new ArgumentException("invalid_compound_direction");
                p.Values["minValue"] = min;
                p.Values["maxValue"] = max;
                p.Values["value"] = value;
                p.Values["wholeNumbers"] = whole;
                p.Values["direction"] = direction;
            }
            if (Utf8.GetByteCount(input.ToString(Formatting.None)) > 16384)
                throw new ArgumentException("compound_request_limit");
            Type image = Need(p, "UnityEngine.UI.Image");
            NeedMember(p, image, "color", typeof(Color));
            NeedMember(p, image, "raycastTarget", typeof(bool));
            if (kind == "scroll_view")
            {
                Type scroll = Need(p, "UnityEngine.UI.ScrollRect"),
                    mask = Need(p, "UnityEngine.UI.Mask");
                Type group = Need(p, "UnityEngine.UI.VerticalLayoutGroup"),
                    fitter = Need(p, "UnityEngine.UI.ContentSizeFitter");
                NeedMember(p, scroll, "content", typeof(RectTransform));
                NeedMember(p, scroll, "viewport", typeof(RectTransform));
                NeedMember(p, scroll, "horizontal", typeof(bool));
                NeedMember(p, scroll, "vertical", typeof(bool));
                NeedMember(p, mask, "showMaskGraphic", typeof(bool));
                NeedMember(p, scroll, "movementType");
                RequireEnum(p, scroll, "movementType", "Clamped");
                NeedMember(p, group, "spacing", typeof(float));
                foreach (string member in new[] { "childControlWidth", "childControlHeight", "childForceExpandWidth", "childForceExpandHeight" })
                    NeedMember(p, group, member, typeof(bool));
                foreach (string member in new[] { "horizontalFit", "verticalFit" })
                    NeedMember(p, fitter, member);
                RequireEnum(p, fitter, "horizontalFit", "Unconstrained");
                RequireEnum(p, fitter, "verticalFit", "PreferredSize");
            }
            if (kind == "toggle" || kind == "slider" || kind == "tmp_input")
            {
                string full =
                    kind == "toggle" ? "UnityEngine.UI.Toggle"
                    : kind == "slider" ? "UnityEngine.UI.Slider"
                    : "TMPro.TMP_InputField";
                Type control = Need(p, full),
                    graphic = Resolve("UnityEngine.UI.Graphic");
                NeedMember(p, control, "targetGraphic", graphic);
                if (kind == "toggle")
                {
                    NeedMember(p, control, "graphic", graphic);
                    NeedMember(p, control, "isOn", typeof(bool));
                    NeedMethod(p, control, "SetIsOnWithoutNotify", typeof(bool));
                }
                if (kind == "slider")
                {
                    foreach (string member in new[] { "fillRect", "handleRect" })
                        NeedMember(p, control, member, typeof(RectTransform));
                    foreach (string member in new[] { "minValue", "maxValue", "value" })
                        NeedMember(p, control, member, typeof(float));
                    NeedMember(p, control, "wholeNumbers", typeof(bool));
                    NeedMethod(p, control, "SetValueWithoutNotify", typeof(float));
                    NeedMember(p, control, "direction");
                    RequireEnum(p, control, "direction", p.Values.Value<string>("direction"));
                }
                if (kind == "tmp_input")
                {
                    Type text = Resolve("TMPro.TMP_Text");
                    Need(p, "UnityEngine.UI.RectMask2D");
                    NeedMember(p, control, "textViewport", typeof(RectTransform));
                    NeedMember(p, control, "textComponent", text);
                    NeedMember(p, control, "placeholder", graphic);
                    NeedMember(p, control, "richText", typeof(bool));
                    NeedMember(p, control, "text", typeof(string));
                    NeedMethod(p, control, "SetTextWithoutNotify", typeof(string));
                    NeedMember(p, control, "fontAsset", Resolve("TMPro.TMP_FontAsset"));
                    NeedMember(p, control, "characterLimit", typeof(int));
                    NeedMember(p, control, "lineType");
                    RequireEnum(p, control, "lineType", "SingleLine");
                    NeedMember(p, control, "contentType");
                    RequireEnum(p, control, "contentType", "Standard");
                }
            }
            if (kind == "toggle" || kind == "tmp_input")
            {
                Type text = Need(p, "TMPro.TextMeshProUGUI"),
                    font = Resolve("TMPro.TMP_FontAsset");
                NeedMember(p, text, "font", font);
                NeedMember(p, text, "text", typeof(string));
                NeedMember(p, text, "fontSize", typeof(float));
                NeedMember(p, text, "richText", typeof(bool));
                NeedMember(p, text, "emojiFallbackSupport", typeof(bool));
                NeedMember(p, text, "raycastTarget", typeof(bool));
                if (kind == "tmp_input")
                    NeedMember(p, text, "color", typeof(Color));
                p.Font = ManageUGUI.DefaultFont();
                CheckFontGraph(p.Font, font);
            }
            p.Before = Snapshot();
            if (p.EventSystem == "create_legacy_if_missing")
            {
#if ENABLE_LEGACY_INPUT_MANAGER
                Type es = Need(p, "UnityEngine.EventSystems.EventSystem");
                Need(p, "UnityEngine.EventSystems.StandaloneInputModule");
                var systems = p
                    .Before.Where(prior => prior.Object != null && prior.Object.GetComponent(es) != null)
                    .Select(prior => prior.Object)
                    .Take(2)
                    .ToArray();
                if (systems.Length > 1)
                    throw new ArgumentException("compound_ambiguous_event_system");
                p.NewEventSystem = systems.Length == 0;
                p.ExistingEventSystem = systems.SingleOrDefault();
#else
                throw new NotSupportedException("compound_legacy_input_unavailable");
#endif
            }
            return p;
        }

        private static List<Prior> Snapshot()
        {
            var result = new List<Prior>();
            int loaded = 0;
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                if (!scene.isLoaded)
                    continue;
                if (++loaded > 32)
                    throw new ArgumentException("compound_scene_admission_limit");
                if (scene.rootCount > 16384 - result.Count)
                    throw new ArgumentException("compound_object_admission_limit");
                var pending = new Stack<Transform>(scene.GetRootGameObjects().Select(root => root.transform));
                while (pending.Count > 0)
                {
                    Transform transform = pending.Pop();
                    if (result.Count >= 16384)
                        throw new ArgumentException("compound_object_admission_limit");
                    result.Add(
                        new Prior
                        {
                            Object = transform.gameObject,
                            Parent = transform.parent,
                            Scene = scene,
                            Sibling = transform.GetSiblingIndex(),
                        }
                    );
                    if (transform.childCount > 16384 - result.Count - pending.Count)
                        throw new ArgumentException("compound_object_admission_limit");
                    for (int child = 0; child < transform.childCount; child++)
                        pending.Push(transform.GetChild(child));
                }
            }
            return result;
        }

        private static void CheckFontGraph(UnityEngine.Object root, Type fontType)
        {
            Type settings = Resolve("TMPro.TMP_Settings");
            var fallback = settings.GetProperty("fallbackFontAssets", BindingFlags.Public | BindingFlags.Static);
            if (root == null || !fontType.IsInstanceOfType(root) || fallback == null)
                throw new NotSupportedException("compound_static_font_unavailable");
            var pending = new Queue<UnityEngine.Object>();
            pending.Enqueue(root);
            int edges = 0;
            Action<object> add = value =>
            {
                if (value == null)
                    return;
                if (++edges > 64 || !(value is UnityEngine.Object asset) || !fontType.IsInstanceOfType(asset))
                    throw new NotSupportedException("compound_font_graph_limit");
                pending.Enqueue(asset);
            };
            object globalValue = fallback.GetValue(null);
            var globals = globalValue as IEnumerable;
            if (globalValue != null && (!(globalValue is ICollection collection) || collection.Count > 64 || globals == null))
                throw new NotSupportedException("compound_font_fallback_unavailable");
            if (globals != null)
                foreach (var item in globals)
                    add(item);
            var visited = new List<UnityEngine.Object>();
            while (pending.Count > 0)
            {
                var asset = pending.Dequeue();
                if (visited.Any(item => ReferenceEquals(item, asset)))
                    continue;
                if (visited.Count >= 16)
                    throw new NotSupportedException("compound_font_graph_limit");
                visited.Add(asset);
                var population = Member(fontType, "atlasPopulationMode", null, false);
                if (population.GetValue(asset)?.ToString() != "Static")
                    throw new NotSupportedException("compound_requires_static_fonts");
                foreach (string member in new[] { "fallbackFontAssetTable", "fontWeightTable" })
                {
                    object value = Member(fontType, member, null, false).GetValue(asset);
                    if (value == null)
                        continue;
                    if (!(value is IEnumerable values) || !(value is ICollection slots) || slots.Count > (member == "fontWeightTable" ? 10 : 64))
                        throw new NotSupportedException("compound_font_graph_unavailable");
                    foreach (object item in values)
                    {
                        if (member == "fallbackFontAssetTable")
                            add(item);
                        else if (item != null)
                        {
                            foreach (string field in new[] { "regularTypeface", "italicTypeface" })
                            {
                                FieldInfo slot = item.GetType().GetField(field, PublicInstance);
                                if (slot == null)
                                    throw new NotSupportedException("compound_font_weight_unavailable");
                                add(slot.GetValue(item));
                            }
                        }
                    }
                }
            }
        }

        private static void RequireEnum(Plan plan, Type type, string member, string value)
        {
            Type enumType = plan.Properties[type.FullName + "." + member].PropertyType;
            if (!enumType.IsEnum || !Enum.IsDefined(enumType, value))
                throw new NotSupportedException("compound_enum_unavailable:" + member);
        }

        private static void SetEnum(Transaction tx, Component component, string member, string value) =>
            Set(tx, component, member, Enum.Parse(Slot(tx.Plan, component, member).PropertyType, value));

        private static GameObject Node(Transaction tx, string role, string name, Transform parent)
        {
            if (tx.Authored.Count >= 7)
                throw new InvalidOperationException("compound_authored_object_limit");
            var go = new GameObject(name, typeof(RectTransform));
            tx.Authored.Add(go);
            tx.Roles.Add(role, go);
            go.SetActive(false);
            Undo.RegisterCreatedObjectUndo(go, "Create compound node");
            go.layer = tx.Plan.Parent.layer;
            if (parent != null)
                go.transform.SetParent(parent, false);
            else if (!go.scene.Equals(tx.Plan.Scene))
                SceneManager.MoveGameObjectToScene(go, tx.Plan.Scene);
            var rt = (RectTransform)go.transform;
            rt.anchorMin = rt.anchorMax = rt.pivot = new Vector2(.5f, .5f);
            rt.localScale = Vector3.one;
            rt.localRotation = Quaternion.identity;
            rt.localPosition = Vector3.zero;
            rt.anchoredPosition = Vector2.zero;
            rt.sizeDelta = new Vector2(20, 20);
            return go;
        }

        private static Component Add(Transaction tx, GameObject go, string type) => Undo.AddComponent(go, tx.Plan.Types[type]);

        private static MemberSlot Slot(Plan p, Component component, string name) =>
            p.Properties.First(pair => pair.Key.EndsWith("." + name, StringComparison.Ordinal) && pair.Value.DeclaringType.IsInstanceOfType(component)).Value;

        private static void Set(Transaction tx, Component component, string name, object value)
        {
            MemberSlot slot = Slot(tx.Plan, component, name);
            Undo.RecordObject(component, "Configure compound component");
            slot.SetValue(component, value);
            AfterAssignmentForTests?.Invoke(component, name);
            object actual = slot.GetValue(component);
            if (!Equals(actual, value))
                throw new InvalidOperationException("compound_member_readback_mismatch:" + name);
        }

        private static object Get(Transaction tx, Component component, string name) => Slot(tx.Plan, component, name).GetValue(component);

        private static void Call(Transaction tx, Component component, string method, object value)
        {
            var slot = tx.Plan.Methods.Values.First(m => m.Name == method && m.DeclaringType.IsInstanceOfType(component));
            Undo.RecordObject(component, "Configure compound component");
            slot.Invoke(component, new[] { value });
        }

        private static Component Image(Transaction tx, GameObject go, Color color, bool raycast = true)
        {
            var image = Add(tx, go, "UnityEngine.UI.Image");
            Set(tx, image, "color", color);
            Set(tx, image, "raycastTarget", raycast);
            return image;
        }

        private static void Stretch(GameObject go, Vector2 min, Vector2 max, Vector2? inset = null)
        {
            var rt = (RectTransform)go.transform;
            rt.anchorMin = min;
            rt.anchorMax = max;
            rt.offsetMin = inset ?? Vector2.zero;
            rt.offsetMax = -(inset ?? Vector2.zero);
        }

        private static Component TextNode(Transaction tx, string role, Transform parent, string value)
        {
            var go = Node(
                tx,
                role,
                role == "label" ? "Label"
                    : role == "placeholder" ? "Placeholder"
                    : "Text",
                parent
            );
            Stretch(go, Vector2.zero, Vector2.one, new Vector2(4, 2));
            var text = Add(tx, go, "TMPro.TextMeshProUGUI");
            Set(tx, text, "richText", false);
            Set(tx, text, "emojiFallbackSupport", false);
            Set(tx, text, "font", tx.Plan.Font);
            Set(tx, text, "fontSize", 14f);
            Set(tx, text, "raycastTarget", false);
            Set(tx, text, "text", value);
            return text;
        }

        private static JObject Build(Transaction tx)
        {
            Plan p = tx.Plan;
            var root = Node(tx, "root", p.Name, p.Parent.transform);
            var rt = (RectTransform)root.transform;
            rt.sizeDelta = p.Size;
            rt.anchoredPosition = p.Position;
            FaultForTests?.Invoke("after_root", root);
            var refs = new JObject();
            Component control;
            if (p.Kind == "scroll_view")
            {
                Image(tx, root, new Color(.2f, .2f, .2f, 1));
                control = Add(tx, root, "UnityEngine.UI.ScrollRect");
                var viewport = Node(tx, "viewport", "Viewport", root.transform);
                Stretch(viewport, Vector2.zero, Vector2.one);
                Image(tx, viewport, Color.white);
                var mask = Add(tx, viewport, "UnityEngine.UI.Mask");
                Set(tx, mask, "showMaskGraphic", false);
                var content = Node(tx, "content", "Content", viewport.transform);
                var contentRect = (RectTransform)content.transform;
                contentRect.anchorMin = new Vector2(0, 1);
                contentRect.anchorMax = Vector2.one;
                contentRect.pivot = new Vector2(.5f, 1);
                contentRect.sizeDelta = Vector2.zero;
                contentRect.anchoredPosition = Vector2.zero;
                var group = Add(tx, content, "UnityEngine.UI.VerticalLayoutGroup");
                var fitter = Add(tx, content, "UnityEngine.UI.ContentSizeFitter");
                Set(tx, group, "spacing", p.Values.Value<float>("spacing"));
                Set(tx, group, "childControlWidth", true);
                Set(tx, group, "childControlHeight", false);
                Set(tx, group, "childForceExpandWidth", true);
                Set(tx, group, "childForceExpandHeight", false);
                SetEnum(tx, fitter, "horizontalFit", "Unconstrained");
                SetEnum(tx, fitter, "verticalFit", "PreferredSize");
                FaultForTests?.Invoke("after_children", root);
                Set(tx, control, "viewport", (RectTransform)viewport.transform);
                Set(tx, control, "content", contentRect);
                Set(tx, control, "horizontal", false);
                Set(tx, control, "vertical", true);
                SetEnum(tx, control, "movementType", "Clamped");
                refs["viewport"] = viewport.GetInstanceIDCompat();
                refs["content"] = content.GetInstanceIDCompat();
            }
            else if (p.Kind == "toggle")
            {
                control = Add(tx, root, "UnityEngine.UI.Toggle");
                var bg = Node(tx, "background", "Background", root.transform);
                var bgImage = Image(tx, bg, Color.gray);
                ((RectTransform)bg.transform).anchoredPosition = new Vector2(-p.Size.x * .5f + 12, 0);
                var check = Node(tx, "checkmark", "Checkmark", bg.transform);
                var checkImage = Image(tx, check, Color.white, false);
                Stretch(check, Vector2.zero, Vector2.one, new Vector2(4, 4));
                var label = TextNode(tx, "label", root.transform, p.Values.Value<string>("label"));
                var labelRect = (RectTransform)label.transform;
                labelRect.offsetMin = new Vector2(28, 0);
                labelRect.offsetMax = Vector2.zero;
                FaultForTests?.Invoke("after_children", root);
                Set(tx, control, "targetGraphic", bgImage);
                Set(tx, control, "graphic", checkImage);
                Call(tx, control, "SetIsOnWithoutNotify", p.Values.Value<bool>("isOn"));
                refs["targetGraphic"] = bg.GetInstanceIDCompat();
                refs["graphic"] = check.GetInstanceIDCompat();
            }
            else if (p.Kind == "slider")
            {
                control = Add(tx, root, "UnityEngine.UI.Slider");
                var bg = Node(tx, "background", "Background", root.transform);
                Stretch(bg, new Vector2(0, .25f), new Vector2(1, .75f));
                Image(tx, bg, Color.gray);
                var fillArea = Node(tx, "fill_area", "Fill Area", root.transform);
                Stretch(fillArea, new Vector2(0, .25f), new Vector2(1, .75f), new Vector2(10, 0));
                var fill = Node(tx, "fill", "Fill", fillArea.transform);
                Stretch(fill, Vector2.zero, Vector2.one);
                Image(tx, fill, Color.white, false);
                var handleArea = Node(tx, "handle_area", "Handle Slide Area", root.transform);
                Stretch(handleArea, Vector2.zero, Vector2.one, new Vector2(10, 0));
                var handle = Node(tx, "handle", "Handle", handleArea.transform);
                var graphic = Image(tx, handle, Color.white);
                FaultForTests?.Invoke("after_children", root);
                Set(tx, control, "targetGraphic", graphic);
                Set(tx, control, "fillRect", (RectTransform)fill.transform);
                Set(tx, control, "handleRect", (RectTransform)handle.transform);
                float min = p.Values.Value<float>("minValue"),
                    max = p.Values.Value<float>("maxValue");
                // Set max first if the requested minimum lies above the installed default maximum.
                if (min > (float)Get(tx, control, "maxValue"))
                    Set(tx, control, "maxValue", max);
                Set(tx, control, "minValue", min);
                Set(tx, control, "maxValue", max);
                Set(tx, control, "wholeNumbers", p.Values.Value<bool>("wholeNumbers"));
                Call(tx, control, "SetValueWithoutNotify", p.Values.Value<float>("value"));
                SetEnum(tx, control, "direction", p.Values.Value<string>("direction"));
                refs["fillRect"] = fill.GetInstanceIDCompat();
                refs["handleRect"] = handle.GetInstanceIDCompat();
                refs["targetGraphic"] = handle.GetInstanceIDCompat();
            }
            else
            {
                var image = Image(tx, root, Color.white);
                control = Add(tx, root, "TMPro.TMP_InputField");
                Set(tx, control, "richText", false);
                SetEnum(tx, control, "contentType", "Standard");
                SetEnum(tx, control, "lineType", "SingleLine");
                Set(tx, control, "characterLimit", p.Values.Value<int>("characterLimit"));
                var viewport = Node(tx, "viewport", "Text Area", root.transform);
                Stretch(viewport, Vector2.zero, Vector2.one, new Vector2(5, 3));
                Add(tx, viewport, "UnityEngine.UI.RectMask2D");
                var placeholder = TextNode(tx, "placeholder", viewport.transform, p.Values.Value<string>("placeholder"));
                var text = TextNode(tx, "text", viewport.transform, "");
                Set(tx, text, "color", Color.black);
                Set(tx, placeholder, "color", new Color(0.4f, 0.4f, 0.4f, 1f));
                FaultForTests?.Invoke("after_children", root);
                Set(tx, control, "targetGraphic", image);
                Set(tx, control, "textViewport", (RectTransform)viewport.transform);
                Set(tx, control, "textComponent", text);
                Set(tx, control, "placeholder", placeholder);
                Set(tx, control, "fontAsset", p.Font);
                Call(tx, control, "SetTextWithoutNotify", p.Values.Value<string>("text"));
                refs["textViewport"] = viewport.GetInstanceIDCompat();
                refs["textComponent"] = text.gameObject.GetInstanceIDCompat();
                refs["placeholder"] = placeholder.gameObject.GetInstanceIDCompat();
            }
            FaultForTests?.Invoke("after_reference_wiring", root);
            string esOutcome = p.EventSystem == "preserve" ? "preserved" : "reused_unchanged";
            if (p.NewEventSystem)
            {
                var es = Node(tx, "event_system", "EventSystem", null);
                Add(tx, es, "UnityEngine.EventSystems.EventSystem");
                Add(tx, es, "UnityEngine.EventSystems.StandaloneInputModule");
                esOutcome = "created_legacy";
                FaultForTests?.Invoke("after_new_event_system", es);
            }
            foreach (var go in tx.Authored.AsEnumerable().Reverse())
            {
                Undo.RecordObject(go, "Activate compound node");
                go.SetActive(true);
            }
            // Read actual values after activation; no renderer/layout or readiness calls are made.
            if (
                root.transform.parent != p.Parent.transform
                || rt.sizeDelta != p.Size
                || rt.anchoredPosition != p.Position
                || tx.Authored.Any(go =>
                    go == null
                    || go.layer != p.Parent.layer
                    || (
                        !ReferenceEquals(go, root)
                        && !ReferenceEquals(go, tx.Roles.ContainsKey("event_system") ? tx.Roles["event_system"] : null)
                        && !go.transform.IsChildOf(root.transform)
                    )
                )
            )
                throw new InvalidOperationException("compound_final_graph_mismatch");
            foreach (JProperty reference in refs.Properties().ToArray())
            {
                var actual = Get(tx, control, reference.Name) as Component;
                if (actual == null || actual.gameObject.GetInstanceIDCompat() != reference.Value.Value<int>())
                    throw new InvalidOperationException("compound_final_reference_mismatch");
                refs[reference.Name] = actual.gameObject.GetInstanceIDCompat();
            }
            if (p.Types.TryGetValue("TMPro.TextMeshProUGUI", out Type textType))
                foreach (var role in new[] { "label", "text", "placeholder" }.Where(tx.Roles.ContainsKey))
                {
                    var text = tx.Roles[role].GetComponent(textType);
                    if (
                        !Equals(Get(tx, text, "richText"), false)
                        || !Equals(Get(tx, text, "emojiFallbackSupport"), false)
                        || !ReferenceEquals(Get(tx, text, "font"), p.Font)
                    )
                        throw new InvalidOperationException("compound_final_text_safety_mismatch");
                }
            if (p.Font != null)
            {
                if (
                    p.Kind == "tmp_input"
                    && (
                        !ReferenceEquals(Get(tx, control, "fontAsset"), p.Font)
                        || !Equals(Get(tx, control, "richText"), false)
                        || Get(tx, control, "lineType").ToString() != "SingleLine"
                        || Get(tx, control, "contentType").ToString() != "Standard"
                    )
                )
                    throw new InvalidOperationException("compound_final_input_mismatch");
                refs["fontAsset"] = p.Font.GetInstanceIDCompat();
            }
            var values = new JObject();
            foreach (var item in p.Values.Properties())
            {
                string member = item.Name;
                if (item.Name == "label")
                    values[item.Name] = JToken.FromObject(Get(tx, tx.Roles["label"].GetComponent(p.Types["TMPro.TextMeshProUGUI"]), "text"));
                else if (item.Name == "placeholder")
                    values[item.Name] = JToken.FromObject(Get(tx, tx.Roles["placeholder"].GetComponent(p.Types["TMPro.TextMeshProUGUI"]), "text"));
                else if (item.Name == "spacing")
                    values[item.Name] = JToken.FromObject(Get(tx, tx.Roles["content"].GetComponent(p.Types["UnityEngine.UI.VerticalLayoutGroup"]), "spacing"));
                else if (item.Name == "direction")
                    values[item.Name] = Get(tx, control, member).ToString();
                else
                    values[item.Name] = JToken.FromObject(Get(tx, control, member));
                if (!JToken.DeepEquals(values[item.Name], item.Value))
                    throw new InvalidOperationException("compound_final_readback_mismatch");
            }
            JObject eventState = null;
            GameObject observedSystem =
                p.EventSystem == "preserve" ? null
                : p.NewEventSystem ? tx.Roles["event_system"]
                : p.ExistingEventSystem;
            if (observedSystem != null)
            {
                var system = (Behaviour)observedSystem.GetComponent(p.Types["UnityEngine.EventSystems.EventSystem"]);
                eventState = new JObject
                {
                    ["instanceID"] = observedSystem.GetInstanceIDCompat(),
                    ["active_in_hierarchy"] = observedSystem.activeInHierarchy,
                    ["enabled"] = system.enabled,
                    ["legacy_module_present"] = observedSystem.GetComponent(p.Types["UnityEngine.EventSystems.StandaloneInputModule"]) != null,
                };
            }
            return new JObject
            {
                ["success"] = true,
                ["data"] = new JObject
                {
                    ["instanceID"] = root.GetInstanceIDCompat(),
                    ["name"] = root.name,
                    ["element_type"] = p.Kind,
                    ["roles"] = JObject.FromObject(tx.Roles.ToDictionary(pair => pair.Key, pair => pair.Value.GetInstanceIDCompat())),
                    ["references"] = refs,
                    ["sizeDelta"] = new JArray(rt.sizeDelta.x, rt.sizeDelta.y),
                    ["anchoredPosition"] = new JArray(rt.anchoredPosition.x, rt.anchoredPosition.y),
                    ["values"] = values,
                    ["event_system"] = esOutcome,
                    ["event_system_state"] = eventState,
                    ["authored_objects"] = tx.Authored.Count,
                },
            };
        }

        private static bool UnderOwned(Transaction tx, Transform transform) =>
            tx.Authored.Any(go => go != null && (ReferenceEquals(go.transform, transform) || transform.IsChildOf(go.transform)));

        private static void Protect(Transaction tx)
        {
            foreach (Prior prior in tx.Plan.Before)
            {
                if (prior.Object == null || !UnderOwned(tx, prior.Object.transform))
                    continue;
                tx.Unknown = true;
                tx.Errors.Add(new JObject { ["phase"] = "protect_preexisting", ["type"] = "CallbackReparentedObject" });
                try
                {
                    prior.Object.transform.SetParent(null, true);
                    if (!prior.Object.scene.Equals(prior.Scene))
                        SceneManager.MoveGameObjectToScene(prior.Object, prior.Scene);
                    if (prior.Parent != null && !UnderOwned(tx, prior.Parent))
                        prior.Object.transform.SetParent(prior.Parent, true);
                    prior.Object.transform.SetSiblingIndex(prior.Sibling);
                }
                catch (Exception error)
                {
                    tx.Error("detach_preexisting", error);
                }
            }
        }

        private static void Rollback(Transaction tx, int group)
        {
            // Preserve generated descendants by actual reference even if an Undo callback subsequently reparents them.
            foreach (var root in tx.Authored.Where(go => go != null))
            foreach (var child in root.GetComponentsInChildren<Transform>(true))
                if (!tx.Before.Contains(child.gameObject))
                    tx.Generated.Add(child.gameObject);
            Protect(tx);
            // Never invoke Undo's recursive destruction while any pre-existing reference remains under an owned root.
            bool safe = !tx.Plan.Before.Any(prior => prior.Object != null && UnderOwned(tx, prior.Object.transform));
            if (safe)
            {
                try
                {
                    Undo.FlushUndoRecordObjects();
                }
                catch (Exception error)
                {
                    tx.Error("rollback_flush", error);
                }
                try
                {
                    BeforeRollbackForTests?.Invoke();
                    Undo.RevertAllDownToGroup(group);
                }
                catch (Exception error)
                {
                    tx.Error("rollback", error);
                }
            }
            else
                tx.Error("rollback", new InvalidOperationException("protected_object_still_attached"));
            // A failed Undo may leave descendants, including DontSave TMP internals. Protect again after callbacks.
            Protect(tx);
            foreach (GameObject go in tx.Generated.Concat(tx.Authored).Distinct(new ReferenceComparer()).Reverse())
            {
                if (go == null)
                    continue;
                if (tx.Plan.Before.Any(prior => prior.Object != null && (ReferenceEquals(prior.Object, go) || prior.Object.transform.IsChildOf(go.transform))))
                {
                    tx.Error("cleanup", new InvalidOperationException("protected_descendant"));
                    continue;
                }
                try
                {
                    UnityEngine.Object.DestroyImmediate(go);
                }
                catch (Exception error)
                {
                    tx.Error("cleanup", error);
                }
            }
            if (tx.Generated.Concat(tx.Authored).Any(go => go != null))
                tx.Error("cleanup", new InvalidOperationException("owned_residue"));
            if (tx.Plan.Before.Any(prior => prior.Object == null))
                tx.Error("preexisting_lifetime", new InvalidOperationException("preexisting_object_destroyed_by_callback"));
        }
    }
}
