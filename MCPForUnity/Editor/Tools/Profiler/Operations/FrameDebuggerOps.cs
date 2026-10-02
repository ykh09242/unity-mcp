using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.Profiler
{
    internal static class FrameDebuggerOps
    {
        private static readonly Type UtilType;
        private static readonly PropertyInfo EventCountProp;
        private static readonly MethodInfo EnableMethod;
        private static readonly MethodInfo GetFrameEventsMethod;
        private static readonly MethodInfo GetEventDataMethod;
        private static readonly MethodInfo GetEventInfoNameMethod;
        private static readonly Type EventDataType;
        private static readonly bool Available;

        static FrameDebuggerOps()
        {
            try
            {
                // Unity 6+: moved to FrameDebuggerInternal sub-namespace
                UtilType = Type.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerUtility, UnityEditor");
                // Unity 2021–2022: original location
                UtilType ??= Type.GetType("UnityEditorInternal.FrameDebuggerUtility, UnityEditor");

                if (UtilType == null) return;

                EventCountProp = UtilType.GetProperty("count", BindingFlags.Public | BindingFlags.Static)
                              ?? UtilType.GetProperty("eventsCount", BindingFlags.Public | BindingFlags.Static);

                EnableMethod = UtilType.GetMethod("SetEnabled", BindingFlags.Public | BindingFlags.Static,
                                   null, new[] { typeof(bool), typeof(int) }, null)
                            ?? UtilType.GetMethod("SetEnabled", BindingFlags.Public | BindingFlags.Static);

                GetFrameEventsMethod = UtilType.GetMethod("GetFrameEvents", BindingFlags.Public | BindingFlags.Static);
                GetEventInfoNameMethod = UtilType.GetMethod("GetFrameEventInfoName", BindingFlags.Public | BindingFlags.Static);

                // Unity 6: GetFrameEventData(int, FrameDebuggerEventData) — 2 params, returns bool
                // Older: GetFrameEventData(int) — 1 param, returns event data object
                EventDataType = Type.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerEventData, UnityEditor")
                             ?? Type.GetType("UnityEditorInternal.FrameDebuggerEventData, UnityEditor");

                if (EventDataType != null)
                {
                    GetEventDataMethod = UtilType.GetMethod("GetFrameEventData", BindingFlags.Public | BindingFlags.Static,
                                             null, new[] { typeof(int), EventDataType }, null);
                }
                GetEventDataMethod ??= UtilType.GetMethod("GetFrameEventData", BindingFlags.Public | BindingFlags.Static);

                Available = EventCountProp != null && EnableMethod != null;
            }
            catch
            {
                Available = false;
            }
        }

        internal static object Enable(JObject @params)
        {
            if (!Available)
                return new ErrorResponse("FrameDebuggerUtility not found via reflection.");

            // Frame Debugger requires game to be paused before enabling to capture events.
            if (EditorApplication.isPlaying && !EditorApplication.isPaused)
            {
                return new ErrorResponse(
                    "Game must be paused before enabling Frame Debugger. "
                    + "Call manage_editor action=pause first, then retry frame_debugger_enable.");
            }

            // Open the Frame Debugger window only after the request passes preflight.
            EditorApplication.ExecuteMenuItem("Window/Analysis/Frame Debugger");

            try
            {
                InvokeSetEnabled(true);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Failed to enable Frame Debugger: {ex.Message}");
            }

            int eventCount = GetEventCount();
            return new SuccessResponse("Frame Debugger enabled.", new
            {
                enabled = true,
                event_count = eventCount,
            });
        }

        internal static object Disable(JObject @params)
        {
            if (!Available)
                return new ErrorResponse("FrameDebuggerUtility not found via reflection.");

            try
            {
                InvokeSetEnabled(false);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Failed to disable Frame Debugger: {ex.Message}");
            }

            return new SuccessResponse("Frame Debugger disabled.", new { enabled = false });
        }

        internal static object GetEvents(JObject @params)
        {
            if (!Available)
                return new ErrorResponse("FrameDebuggerUtility not found via reflection.");

            var p = new ToolParams(@params);
            int pageSize = Math.Max(1, Math.Min(p.GetInt("page_size") ?? 50, 500));
            int cursor = Math.Max(0, p.GetInt("cursor") ?? 0);

            int totalEvents = GetEventCount();
            if (totalEvents == 0)
            {
                return new SuccessResponse("Frame Debugger has no events. Is it enabled?", new
                {
                    events = new List<object>(),
                    total_events = 0,
                });
            }

            // Try GetFrameEvents() for the event descriptor array (has type/name info)
            Array frameEvents = null;
            if (GetFrameEventsMethod != null && cursor < totalEvents)
            {
                try
                {
                    frameEvents = GetFrameEventsMethod.Invoke(null, null) as Array;
                }
                catch { /* fall through */ }
            }

            var events = new List<object>();
            int end = (int)Math.Min((long)cursor + pageSize, totalEvents);

            for (int i = cursor; i < end; i++)
            {
                var entry = new Dictionary<string, object> { ["index"] = i };

                // Get event name
                if (GetEventInfoNameMethod != null)
                {
                    try { entry["name"] = (string)GetEventInfoNameMethod.Invoke(null, new object[] { i }); }
                    catch { /* skip */ }
                }

                // Get fields from FrameDebuggerEvent descriptor
                if (frameEvents != null && i < frameEvents.Length)
                {
                    var desc = frameEvents.GetValue(i);
                    var descType = desc.GetType();
                    TryAddField(descType, desc, "type", entry, "event_type", "m_Type");
                    TryAddField(descType, desc, "gameObjectInstanceID", entry);
                }

                // Get detailed event data
                if (GetEventDataMethod != null)
                {
                    try
                    {
                        var paramInfos = GetEventDataMethod.GetParameters();
                        object eventData;

                        if (paramInfos.Length == 2 && EventDataType != null)
                        {
                            // Unity 6: bool GetFrameEventData(int, FrameDebuggerEventData)
                            eventData = Activator.CreateInstance(EventDataType);
                            var args = new object[] { i, eventData };
                            var ok = GetEventDataMethod.Invoke(null, args);
                            eventData = (ok is true) ? args[1] : null;
                        }
                        else
                        {
                            // Older: FrameDebuggerEventData GetFrameEventData(int)
                            eventData = GetEventDataMethod.Invoke(null, new object[] { i });
                        }

                        if (eventData != null)
                        {
                            var edType = eventData.GetType();
                            TryAddField(edType, eventData, "shaderName", entry, null, "m_OriginalShaderName");
                            TryAddField(edType, eventData, "passName", entry, null, "m_PassName");
                            TryAddField(edType, eventData, "rtName", entry, null, "m_RenderTargetName");
                            TryAddField(edType, eventData, "rtWidth", entry, null, "m_RenderTargetWidth");
                            TryAddField(edType, eventData, "rtHeight", entry, null, "m_RenderTargetHeight");
                            TryAddField(edType, eventData, "vertexCount", entry, null, "m_VertexCount");
                            TryAddField(edType, eventData, "indexCount", entry, null, "m_IndexCount");
                            TryAddField(edType, eventData, "instanceCount", entry, null, "m_InstanceCount");
                            TryAddField(edType, eventData, "meshName", entry);
                            if (!entry.ContainsKey("meshName") &&
                                ReadFieldOrProperty(edType, eventData, "mesh", "m_Mesh") is UnityEngine.Mesh mesh && mesh != null)
                                entry["meshName"] = mesh.name;
                        }
                    }
                    catch { /* skip event data for this index */ }
                }

                events.Add(entry);
            }

            var result = new Dictionary<string, object>
            {
                ["events"] = events,
                ["total_events"] = totalEvents,
                ["page_size"] = pageSize,
                ["cursor"] = cursor,
            };
            if (end < totalEvents)
                result["next_cursor"] = end;

            return new SuccessResponse($"Frame Debugger events {cursor}-{end - 1} of {totalEvents}.", result);
        }

        private static void InvokeSetEnabled(bool value)
        {
            int paramCount = EnableMethod.GetParameters().Length;
            if (paramCount == 2)
                EnableMethod.Invoke(null, new object[] { value, 0 });
            else if (paramCount == 1)
                EnableMethod.Invoke(null, new object[] { value });
            else
                throw new InvalidOperationException($"SetEnabled has unexpected {paramCount} parameters.");
        }

        private static int GetEventCount()
        {
            try { return (int)EventCountProp.GetValue(null); }
            catch { return 0; }
        }

        private static void TryAddField(Type type, object obj, string fieldName, Dictionary<string, object> dict,
            string outputKey = null, string alias = null)
        {
            object val = ReadFieldOrProperty(type, obj, fieldName, alias);
            if (val != null)
                dict[outputKey ?? fieldName] = val.GetType().IsEnum ? val.ToString() : val;
        }

        private static object ReadFieldOrProperty(Type type, object obj, string fieldName, string alias = null)
        {
            for (int i = 0; i < (alias == null ? 1 : 2); i++)
            {
                try
                {
                    string name = i == 0 ? fieldName : alias;
                    var field = type.GetField(name, BindingFlags.Public | BindingFlags.Instance)
                             ?? type.GetField(name, BindingFlags.NonPublic | BindingFlags.Instance);
                    var prop = type.GetProperty(name, BindingFlags.Public | BindingFlags.Instance)
                            ?? type.GetProperty(name, BindingFlags.NonPublic | BindingFlags.Instance);
                    object val = field != null ? field.GetValue(obj)
                               : prop != null ? prop.GetValue(obj)
                               : null;
                    if (val != null)
                        return val;
                }
                catch { /* skip unavailable fields */ }
            }
            return null;
        }

    }
}
