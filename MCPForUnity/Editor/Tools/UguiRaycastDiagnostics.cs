using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>One current native query; custom filter execution is an explicit, non-transactional opt-in.</summary>
    internal static class UguiRaycastDiagnostics
    {
        private const int RaycasterLimit = 32;
        private const int HitLimit = 128;
        private const int HandlerAncestorLimit = 64;
        private static readonly HashSet<string> Keys = new HashSet<string>(StringComparer.Ordinal)
        {
            "action",
            "target",
            "screen_position",
            "allow_custom_filters",
            "strict_read",
            "max_nodes",
            "max_depth",
            "max_evaluations",
            "max_characters",
        };
        private static readonly HashSet<string> Graphics = new HashSet<string>(StringComparer.Ordinal)
        {
            "UnityEngine.UI.Image",
            "UnityEngine.UI.RawImage",
            "UnityEngine.UI.Text",
        };
        private static readonly HashSet<string> Filters = new HashSet<string>(StringComparer.Ordinal)
        {
            "UnityEngine.UI.Image",
            "UnityEngine.UI.Mask",
            "UnityEngine.UI.RectMask2D",
            "UnityEngine.CanvasGroup",
        };

        private sealed class Unavailable : Exception
        {
            public readonly string Code;

            public Unavailable(string code) => Code = code;
        }

        private sealed class Query
        {
            public int MaxNodes,
                MaxDepth,
                MaxWork,
                MaxCharacters;
            public int Work,
                Characters,
                Raycasters,
                Graphics,
                Slots;
            public bool Allow,
                StrictRead,
                Attempted,
                Custom,
                CustomKnown;
            public int TargetId;
            public int? EventId;
            public string ModuleType;
            public JObject UnsupportedComponent;
            public bool? ModuleActive;
            public Vector2 Position;
            public readonly JArray Hits = new JArray();
            public int? Total;
            public bool? FirstTargetsHandler;
            public bool Truncated;

            public void Spend(int amount)
            {
                if (amount < 0 || amount > MaxWork - Work)
                    throw new Unavailable("max_evaluations");
                Work += amount;
            }

            public bool TrySpend(int amount)
            {
                if (amount < 0 || amount > MaxWork - Work)
                    return false;
                Work += amount;
                return true;
            }

            public void Reserve(int amount)
            {
                if (amount < 0 || amount > MaxCharacters - Characters)
                    throw new Unavailable("max_characters");
                Characters += amount;
            }

            public JObject Reply(string status, string reason)
            {
                if (status != "observed")
                {
                    Hits.Clear();
                    Total = null;
                    FirstTargetsHandler = null;
                    Truncated = false;
                }
                var data = new JObject
                {
                    ["kind"] = "ugui_raycast_diagnostics",
                    ["schema_version"] = 1,
                    ["framework"] = "ugui",
                    ["mode"] = Application.isPlaying ? "play" : "edit",
                    ["frame"] = Time.frameCount,
                    ["target_id"] = TargetId,
                    ["screen_position"] = new JArray(Position.x, Position.y),
                    ["coordinate_system"] = "current_screen_pixels_bottom_left",
                    ["display"] = 0,
                    ["status"] = status,
                    ["reason"] = reason,
                    ["unsupported_component"] = UnsupportedComponent,
                    ["policy"] = new JObject
                    {
                        ["allow_custom_filters"] = Allow,
                        ["custom_filters_present"] = CustomKnown ? (JToken)new JValue(Custom) : JValue.CreateNull(),
                        ["native_query_attempted"] = Attempted,
                        ["user_code_may_have_run"] = Attempted && Allow && Custom,
                        ["callback_invocations"] = "not_instrumented",
                        ["package_explicit_event_dispatch"] = false,
                        ["package_explicit_source_edits"] = false,
                    },
                    ["input"] = new JObject
                    {
                        ["event_system_id"] = EventId.HasValue ? (JToken)new JValue(EventId.Value) : JValue.CreateNull(),
                        ["current_module_type"] = ModuleType,
                        ["current_module_active"] = ModuleActive.HasValue ? (JToken)new JValue(ModuleActive.Value) : JValue.CreateNull(),
                        ["delivery"] = "not_attempted",
                    },
                    ["hits"] = Hits,
                    ["hits_total"] = Total.HasValue ? (JToken)new JValue(Total.Value) : JValue.CreateNull(),
                    ["hits_truncated"] = Truncated,
                    ["first_hit_targets_handler"] = FirstTargetsHandler.HasValue ? (JToken)new JValue(FirstTargetsHandler.Value) : JValue.CreateNull(),
                    ["budget"] = new JObject
                    {
                        ["max_nodes"] = MaxNodes,
                        ["max_depth"] = MaxDepth,
                        ["max_evaluations"] = MaxWork,
                        ["max_characters"] = MaxCharacters,
                        ["raycasters_inspected"] = Raycasters,
                        ["graphics_inspected"] = Graphics,
                        ["evaluations"] = Work,
                        ["characters_reserved"] = Characters,
                    },
                    ["limitations"] = new JArray(
                        "Current native cached raycast state; no layout, render, input or selection update is requested.",
                        "Hits and module presence do not guarantee visibility, OS input or event receipt.",
                        "Opted-in user filters can mutate state, dispatch events, throw or never return; no rollback, atomic snapshot or hard deadline is promised."
                    ),
                };
                if (StrictRead)
                    data["policy"]["strict_read"] = true;
                return new JObject
                {
                    ["success"] = status != "failed",
                    ["message"] = status == "failed" ? "raycast_query_failed" : "raycast_evidence",
                    ["data"] = data,
                };
            }
        }

        /// <summary>The dispatcher must resolve the current target without editing it; shared integration is separate.</summary>
        internal static JObject Diagnose(GameObject target, JObject request)
        {
            Query query;
            try
            {
                query = Admit(target, request);
            }
            catch (ArgumentException)
            {
                return new JObject
                {
                    ["success"] = false,
                    ["message"] = "invalid_raycast_request",
                    ["data"] = null,
                };
            }
            catch (OverflowException)
            {
                return new JObject
                {
                    ["success"] = false,
                    ["message"] = "invalid_raycast_request",
                    ["data"] = null,
                };
            }
            try
            {
                if (EditorUtility.IsPersistent(target) || !target.scene.IsValid() || EditorSceneManager.IsPreviewScene(target.scene))
                    throw new Unavailable("invalid_scene_target");
                if (
                    Screen.width <= 0
                    || Screen.height <= 0
                    || query.Position.x < 0
                    || query.Position.y < 0
                    || query.Position.x >= Screen.width
                    || query.Position.y >= Screen.height
                )
                    throw new Unavailable("screen_position_unavailable");
                var ui = AppDomain.CurrentDomain.GetAssemblies().SingleOrDefault(a => a.GetName().Name == "UnityEngine.UI");
                if (ui == null)
                    throw new Unavailable("ugui_unavailable");
                if (!OfficialPackage(ui, "com.unity.ugui"))
                    throw new Unavailable("unsupported_read_policy");
                Type eventType = NativeType(ui, "UnityEngine.EventSystems.EventSystem");
                var systems = Field(eventType, null, "m_EventSystems", query) as IList;
                if (systems == null || systems.GetType() != typeof(List<>).MakeGenericType(eventType))
                    throw new Unavailable("unsupported_native_shape");
                if (systems.Count != 1)
                    throw new Unavailable(systems.Count == 0 ? "missing_event_system" : "ambiguous_event_system");
                query.Spend(1);
                var events = systems[0] as Behaviour;
                if (events == null || events.GetType() != eventType || !events.isActiveAndEnabled)
                    throw new Unavailable("unsupported_event_system");
                query.EventId = events.gameObject.GetInstanceIDCompat();
                var module = Field(eventType, events, "m_CurrentInputModule", query, NativeType(ui, "UnityEngine.EventSystems.BaseInputModule")) as Behaviour;
                if (module != null)
                {
                    query.Reserve(256);
                    query.ModuleType = Metadata(module.GetType().FullName);
                    query.ModuleActive = module.isActiveAndEnabled;
                }
                Preflight(ui, query);
                query.CustomKnown = true;
                query.Slots = Math.Min(HitLimit, query.Graphics);
                query.Reserve(query.Slots * 256);
                Type pointerType = NativeType(ui, "UnityEngine.EventSystems.PointerEventData");
                Type resultType = NativeType(ui, "UnityEngine.EventSystems.RaycastResult");
                Type resultList = typeof(List<>).MakeGenericType(resultType);
                var constructor = pointerType.GetConstructor(new[] { eventType });
                var positionProperty = pointerType.GetProperty("position", typeof(Vector2));
                var method = eventType.GetMethod("RaycastAll", new[] { pointerType, resultList });
                if (
                    constructor == null
                    || constructor.GetParameters()[0].ParameterType != eventType
                    || positionProperty?.SetMethod == null
                    || positionProperty.SetMethod.IsStatic
                    || positionProperty.GetIndexParameters().Length != 0
                    || method == null
                    || method.IsStatic
                    || method.ContainsGenericParameters
                    || method.ReturnType != typeof(void)
                    || method.GetParameters()[0].ParameterType != pointerType
                    || method.GetParameters()[1].ParameterType != resultList
                )
                    throw new Unavailable("unsupported_native_shape");
                string objectField = ValidateHitFields(ui, resultType);
                var pointer = constructor.Invoke(new object[] { events });
                positionProperty.SetValue(pointer, query.Position);
                var results = (IList)Activator.CreateInstance(resultList);
                query.Attempted = true;
                try
                {
                    method.Invoke(events, new object[] { pointer, results });
                }
                catch (TargetInvocationException)
                {
                    return query.Reply("failed", "native_query_threw");
                }
                query.Total = results.Count;
                int take = Math.Min(query.Slots, results.Count);
                for (int index = 0; index < take; index++)
                    query.Hits.Add(Hit(ui, resultType, objectField, results[index], index, query));
                query.Truncated = take < results.Count;
                if (results.Count == 0)
                    query.FirstTargetsHandler = false;
                else if (take > 0 && target != null)
                {
                    var first = Field(resultType, results[0], objectField, query) as GameObject;
                    if (
                        TryHandler(ui, target, query, out var targetHandler)
                        && TryHandler(ui, first, query, out var hitHandler)
                        && targetHandler != null
                        && hitHandler != null
                    )
                        query.FirstTargetsHandler = targetHandler == hitHandler;
                }
                return query.Reply("observed", null);
            }
            catch (Unavailable unavailable)
            {
                return query.Reply("unknown", unavailable.Code);
            }
            catch (TargetInvocationException)
            {
                return query.Reply("unknown", "unsupported_native_shape");
            }
            catch (MemberAccessException)
            {
                return query.Reply("unknown", "unsupported_native_shape");
            }
            catch (AmbiguousMatchException)
            {
                return query.Reply("unknown", "unsupported_native_shape");
            }
            catch (InvalidCastException)
            {
                return query.Reply("unknown", "unsupported_native_shape");
            }
            catch (ArgumentException)
            {
                return query.Reply("unknown", "unsupported_native_shape");
            }
            catch (InvalidOperationException)
            {
                return query.Reply(query.Attempted ? "failed" : "unknown", query.Attempted ? "query_inventory_changed" : "unsupported_native_shape");
            }
        }

        private static Query Admit(GameObject target, JObject request)
        {
            if (
                target == null
                || !(target.transform is RectTransform)
                || request == null
                || request.Count > Keys.Count
                || request.Properties().Any(p => !Keys.Contains(p.Name))
            )
                throw new ArgumentException();
            if (request["action"] != null && (request["action"].Type != JTokenType.String || (string)request["action"] != "diagnose_raycast"))
                throw new ArgumentException();
            var point = request["screen_position"] as JArray;
            if (point == null || point.Count != 2 || !Number(point[0], out float x) || !Number(point[1], out float y))
                throw new ArgumentException();
            var allow = request["allow_custom_filters"];
            if (allow != null && allow.Type != JTokenType.Boolean)
                throw new ArgumentException();
            var strict = request["strict_read"];
            if (strict != null && (strict.Type != JTokenType.Boolean || (strict.Value<bool>() && allow?.Value<bool>() == true)))
                throw new ArgumentException();
            var targetToken = request["target"];
            if (targetToken != null && targetToken.Type != JTokenType.Integer && targetToken.Type != JTokenType.String)
                throw new ArgumentException();
            if (targetToken != null && targetToken.Type == JTokenType.Integer && targetToken.Value<long>() != target.GetInstanceIDCompat())
                throw new ArgumentException();
            if (targetToken != null && targetToken.Type == JTokenType.String)
            {
                string selector = (string)targetToken;
                if (selector == null || selector.Length > 4096 || string.IsNullOrWhiteSpace(selector))
                    throw new ArgumentException();
                new UTF8Encoding(false, true).GetByteCount(selector);
            }
            return new Query
            {
                TargetId = target.GetInstanceIDCompat(),
                Position = new Vector2(x, y),
                Allow = allow != null && allow.Value<bool>(),
                StrictRead = strict?.Value<bool>() == true,
                MaxNodes = Integer(request, "max_nodes", 200, 1, 1000),
                MaxDepth = Integer(request, "max_depth", 10, 0, 24),
                MaxWork = Integer(request, "max_evaluations", 20000, 1, 200000),
                MaxCharacters = Integer(request, "max_characters", 65536, 1, 262144),
            };
        }

        private static void Preflight(Assembly ui, Query query)
        {
            Type raycaster = NativeType(ui, "UnityEngine.UI.GraphicRaycaster");
            var manager = NativeType(ui, "UnityEngine.EventSystems.RaycasterManager");
            var rays = Field(manager, null, "s_Raycasters", query) as IList;
            if (rays == null || rays.GetType() != typeof(List<>).MakeGenericType(NativeType(ui, "UnityEngine.EventSystems.BaseRaycaster")))
                throw new Unavailable("unsupported_native_shape");
            if (rays.Count > RaycasterLimit)
                throw new Unavailable("raycaster_limit");
            var registryType = NativeType(ui, "UnityEngine.UI.GraphicRegistry");
            var registry = Field(registryType, null, "s_Instance", query, registryType);
            if (registry == null)
                throw new Unavailable("registry_uninitialized");
            Type graphicType = NativeType(ui, "UnityEngine.UI.Graphic");
            MethodInfo graphicsForCanvas = GraphicsAccessor(registryType, graphicType);
            Type listType = typeof(List<>).MakeGenericType(graphicType);
            Type setType = ui.GetType("UnityEngine.UI.Collections.IndexedSet\u00601", false)?.MakeGenericType(graphicType);
            Type arrayType = graphicType.MakeArrayType();
            var countProperty = typeof(ICollection<>).MakeGenericType(graphicType).GetProperty("Count");
            var itemProperty = typeof(IList<>).MakeGenericType(graphicType).GetProperty("Item");
            if (query.StrictRead)
            {
                // A previous throwing project filter can leave foreign graphics in native sort scratch.
                var scratch = Field(raycaster, null, "s_SortedGraphics", query) as IList;
                if (scratch == null || scratch.GetType() != listType || scratch.Count != 0)
                    throw new Unavailable("unsupported_read_policy");
            }
            Type maskableGraphicType = NativeType(ui, "UnityEngine.UI.MaskableGraphic");
            Type filterType = typeof(CanvasGroup).GetInterfaces().SingleOrDefault(t => t.FullName == "UnityEngine.ICanvasRaycastFilter");
            if (filterType == null)
                throw new Unavailable("unsupported_native_shape");
            for (int index = 0; index < rays.Count; index++)
            {
                query.Spend(1);
                query.Raycasters++;
                var ray = rays[index] as Behaviour;
                if (ray == null || ray.GetType() != raycaster)
                    throw new Unavailable("unsupported_raycaster");
                if (!ray.isActiveAndEnabled)
                    continue;
                query.Spend(4);
                var canvas = ray.GetComponent<Canvas>();
                var camera = Property(raycaster, ray, "eventCamera", query, typeof(Camera)) as Camera;
                if (canvas == null || canvas.targetDisplay != 0 || (camera != null && (camera.targetDisplay != 0 || camera.targetTexture != null)))
                    throw new Unavailable("unsupported_display_route");
                var blocking = NativeType(ui, "UnityEngine.UI.GraphicRaycaster+BlockingObjects");
                if (!blocking.IsEnum || Enum.GetName(blocking, Property(raycaster, ray, "blockingObjects", query, blocking)) != "None")
                    throw new Unavailable("unsupported_blocking_objects");
                // Both official accessors can lazily create the registry. Its existing
                // singleton was checked above. Only the package's own collections may run.
                query.Spend(2);
                var entries = graphicsForCanvas.Invoke(null, new object[] { canvas });
                if (entries == null || (entries.GetType() != listType && entries.GetType() != setType && entries.GetType() != arrayType))
                    throw new Unavailable("unsupported_native_shape");
                int count = (int)countProperty.GetValue(entries);
                if (count < 0)
                    throw new Unavailable("unsupported_native_shape");
                if (count > query.MaxNodes - query.Graphics)
                    throw new Unavailable("max_nodes");
                for (int item = 0; item < count; item++)
                {
                    query.Spend(1);
                    query.Graphics++;
                    var graphic = itemProperty.GetValue(entries, new object[] { item }) as Component;
                    if (graphic == null)
                        throw new Unavailable("unsupported_native_shape");
                    Type actual = graphic.GetType();
                    bool knownGraphic = actual.Assembly == ui && Graphics.Contains(actual.FullName);
                    // TMP's exact built-ins inherit native Graphic/MaskableGraphic.Raycast and
                    // Graphic.raycastTarget. The caret remains a real candidate, not an inert exception.
                    bool tmp =
                        actual.Assembly.GetName().Name == "Unity.TextMeshPro"
                        && new[] { "TMPro.TMP_SelectionCaret", "TMPro.TextMeshProUGUI", "TMPro.TMP_SubMeshUI" }.Contains(actual.FullName);
                    if (tmp)
                    {
                        query.Spend(2);
                        Type raycastOwner = actual.GetMethod("Raycast", new[] { typeof(Vector2), typeof(Camera) })?.DeclaringType;
                        tmp =
                            (raycastOwner == graphicType || raycastOwner == maskableGraphicType)
                            && actual.GetProperty("raycastTarget")?.GetMethod?.DeclaringType == graphicType
                            && (OfficialPackage(actual.Assembly, "com.unity.ugui") || OfficialPackage(actual.Assembly, "com.unity.textmeshpro"));
                    }
                    if (!knownGraphic && !tmp)
                    {
                        query.Reserve(512);
                        query.UnsupportedComponent = DescribeComponent(graphic, query);
                        throw new Unavailable("unsupported_graphic");
                    }
                    // Graphic.canvasRenderer can AddComponent on a missing renderer.
                    // The fixed read accepts only existing native components.
                    if (query.StrictRead)
                    {
                        query.Spend(2);
                        if (!(graphic.transform is RectTransform) || graphic.GetComponent<CanvasRenderer>() == null)
                            throw new Unavailable("unsupported_read_policy");
                    }
                    if (!(bool)Property(graphicType, graphic, "raycastTarget", query, typeof(bool)))
                        continue;
                    int depth = 0;
                    for (Transform current = graphic.transform; current != null; current = current.parent, depth++)
                    {
                        if (depth > query.MaxDepth)
                            throw new Unavailable("max_depth");
                        query.Spend(1);
                        // Native allocation occurs here; the accepted loop is charged before component inspection.
                        Component[] components = current.GetComponents<Component>();
                        query.Spend(components.Length);
                        bool stop = false;
                        foreach (var component in components)
                        {
                            if (component == null)
                                throw new Unavailable("unsupported_native_shape");
                            if (component is Canvas boundary && boundary.overrideSorting)
                                stop = true;
                            if (!filterType.IsAssignableFrom(component.GetType()))
                                continue;
                            bool known =
                                component.GetType() == typeof(CanvasGroup)
                                || (component.GetType().Assembly == ui && Filters.Contains(component.GetType().FullName));
                            if (!known)
                            {
                                query.Custom = query.CustomKnown = true;
                                if (!query.Allow)
                                    throw new Unavailable("custom_filters_not_allowed");
                            }
                            // Image alpha sampling can log an error and dispatch project log handlers.
                            if (
                                query.StrictRead
                                && component.GetType() == NativeType(ui, "UnityEngine.UI.Image")
                                && !((float)Property(component.GetType(), component, "alphaHitTestMinimumThreshold", query, typeof(float)) <= 0f)
                            )
                                throw new Unavailable("unsupported_read_policy");
                        }
                        if (stop)
                            break;
                    }
                }
            }
        }

        private static JObject DescribeComponent(Component component, Query query)
        {
            var names = new List<string>();
            Transform current = component.transform;
            int length = 0;
            bool truncated = false;
            while (current != null && names.Count < 16 && length < 240)
            {
                query.Spend(1);
                string name = Metadata(current.name);
                if (name.Length > 240 - length)
                {
                    name = name.Substring(0, 240 - length);
                    truncated = true;
                }
                names.Add(name);
                length += name.Length + 1;
                current = current.parent;
            }
            names.Reverse();
            return new JObject
            {
                ["instance_id"] = component.gameObject.GetInstanceIDCompat(),
                ["component_type"] = Metadata(component.GetType().FullName),
                ["path"] = string.Join("/", names),
                ["path_truncated"] = truncated || current != null,
            };
        }

        private static string ValidateHitFields(Assembly ui, Type result)
        {
            const BindingFlags fields = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            string objectField = result.GetField("m_GameObject", fields) != null ? "m_GameObject" : "gameObject";
            if (
                !result.IsValueType
                || result.GetField(objectField, fields)?.FieldType != typeof(GameObject)
                || result.GetField("module", fields)?.FieldType != NativeType(ui, "UnityEngine.EventSystems.BaseRaycaster")
                || result.GetField("distance", fields)?.FieldType != typeof(float)
                || result.GetField("index", fields)?.FieldType != typeof(float)
                || result.GetField("screenPosition", fields)?.FieldType != typeof(Vector2)
            )
                throw new Unavailable("unsupported_native_shape");
            foreach (string name in new[] { "sortingLayer", "sortingOrder", "depth", "displayIndex" })
                if (result.GetField(name, fields)?.FieldType != typeof(int))
                    throw new Unavailable("unsupported_native_shape");
            return objectField;
        }

        private static MethodInfo GraphicsAccessor(Type registry, Type graphic)
        {
            var parameters = new[] { typeof(Canvas) };
            const BindingFlags flags = BindingFlags.Public | BindingFlags.Static | BindingFlags.DeclaredOnly;
            var method = registry.GetMethod("GetRaycastableGraphicsForCanvas", flags, null, parameters, null)
                ?? registry.GetMethod("GetGraphicsForCanvas", flags, null, parameters, null);
            if (
                method == null
                || method.IsGenericMethod
                || method.GetParameters()[0].ParameterType != typeof(Canvas)
                || method.ReturnType != typeof(IList<>).MakeGenericType(graphic)
            )
                throw new Unavailable("unsupported_native_shape");
            return method;
        }

        private static JObject Hit(Assembly ui, Type resultType, string objectField, object result, int rank, Query query)
        {
            var go = Field(resultType, result, objectField, query) as GameObject;
            var ray = Field(resultType, result, "module", query) as Component;
            if (go == null || ray == null || ray.GetType() != NativeType(ui, "UnityEngine.UI.GraphicRaycaster"))
                throw new Unavailable("query_inventory_changed");
            string name = Metadata(go.name);
            query.Spend(3);
            var root = Property(ray.GetType(), ray, "rootRaycaster", query, NativeType(ui, "UnityEngine.EventSystems.BaseRaycaster")) as Component;
            var camera = Property(ray.GetType(), ray, "eventCamera", query, typeof(Camera)) as Camera;
            int layer = (int)Field(resultType, result, "sortingLayer", query);
            float distance = (float)Field(resultType, result, "distance", query);
            float index = (float)Field(resultType, result, "index", query);
            var position = (Vector2)Field(resultType, result, "screenPosition", query);
            if (
                !Finite(distance)
                || distance < 0
                || !Finite(index)
                || index < 0
                || !Finite(position.x)
                || !Finite(position.y)
                || (int)Field(resultType, result, "displayIndex", query) != 0
                || (int)Field(resultType, result, "depth", query) < 0
            )
                throw new Unavailable("invalid_native_hit");
            return new JObject
            {
                ["rank"] = rank,
                ["game_object_id"] = go.GetInstanceIDCompat(),
                ["name"] = name,
                ["raycaster_id"] = ray.GetInstanceIDCompat(),
                ["root_raycaster_id"] = root == null ? ray.GetInstanceIDCompat() : root.GetInstanceIDCompat(),
                ["event_camera_id"] = camera == null ? JValue.CreateNull() : (JToken)new JValue(camera.GetInstanceIDCompat()),
                ["sorting_layer_id"] = layer,
                ["sorting_layer_value"] = SortingLayer.GetLayerValueFromID(layer),
                ["sorting_order"] = (int)Field(resultType, result, "sortingOrder", query),
                ["depth"] = (int)Field(resultType, result, "depth", query),
                ["distance"] = distance,
                ["index"] = index,
                ["display_index"] = (int)Field(resultType, result, "displayIndex", query),
                ["screen_position"] = new JArray(position.x, position.y),
            };
        }

        // Matches installed ExecuteEvents.GetEventHandler click-handler eligibility without its unbounded walk.
        // These are metadata reads, not event-interface calls; sorting/filter boundaries do not stop this walk.
        private static bool TryHandler(Assembly ui, GameObject target, Query query, out GameObject handler)
        {
            handler = null;
            Type clickHandler = NativeType(ui, "UnityEngine.EventSystems.IPointerClickHandler");
            if (!query.TrySpend(1))
                return false;
            Transform current = target == null ? null : target.transform;
            for (int depth = 0; current != null; depth++)
            {
                if (depth >= HandlerAncestorLimit || !query.TrySpend(2))
                    return false;
                GameObject go = current.gameObject;
                if (go.activeInHierarchy)
                {
                    if (!query.TrySpend(1))
                        return false;
                    // Native allocation can precede the count; no component is inspected until admitted.
                    Component[] components = go.GetComponents<Component>();
                    if (!query.TrySpend(components.Length))
                        return false;
                    foreach (var component in components)
                    {
                        if (!clickHandler.IsInstanceOfType(component))
                            continue;
                        if (!query.TrySpend(1))
                            return false;
                        if (component is Behaviour behaviour && !behaviour.isActiveAndEnabled)
                            continue;
                        handler = go;
                        return true;
                    }
                }
                if (!query.TrySpend(1))
                    return false;
                current = current.parent;
            }
            return true;
        }

        private static bool OfficialPackage(Assembly assembly, string packageName)
        {
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(assembly);
            return package != null
                && package.name == packageName
                && (
                    package.source == UnityEditor.PackageManager.PackageSource.BuiltIn
                    || (
                        package.source == UnityEditor.PackageManager.PackageSource.Registry
                        && package.registry?.url?.TrimEnd('/') == "https://packages.unity.com"
                    )
                );
        }

        private static Type NativeType(Assembly ui, string name)
        {
            Type type = ui.GetType(name, false);
            if (type == null)
                throw new Unavailable("unsupported_native_shape");
            return type;
        }

        private static object Field(Type type, object source, string name, Query query, Type expected = null)
        {
            query.Spend(1);
            var flags = (source == null ? BindingFlags.Static : BindingFlags.Instance) | BindingFlags.Public | BindingFlags.NonPublic;
            FieldInfo field = type.GetField(name, flags);
            if (field == null || (expected != null && field.FieldType != expected))
                throw new Unavailable("unsupported_native_shape");
            return field.GetValue(source);
        }

        private static object Property(Type type, object source, string name, Query query, Type expected)
        {
            query.Spend(1);
            PropertyInfo property = type.GetProperty(name, BindingFlags.Public | BindingFlags.Instance, null, expected, Type.EmptyTypes, null);
            if (property?.GetMethod == null)
                throw new Unavailable("unsupported_native_shape");
            return property.GetValue(source);
        }

        private static string Metadata(string value)
        {
            if (value == null || value.Length > 256)
                throw new Unavailable("invalid_metadata");
            try
            {
                new UTF8Encoding(false, true).GetByteCount(value);
            }
            catch (EncoderFallbackException)
            {
                throw new Unavailable("invalid_metadata");
            }
            return value;
        }

        private static int Integer(JObject request, string name, int fallback, int low, int high)
        {
            JToken token = request[name];
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Integer)
                throw new ArgumentException();
            long value = token.Value<long>();
            if (value < low || value > high)
                throw new ArgumentException();
            return (int)value;
        }

        private static bool Number(JToken token, out float value)
        {
            value = 0;
            if (token == null || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float))
                return false;
            value = token.Value<float>();
            return Finite(value);
        }

        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
    }
}
