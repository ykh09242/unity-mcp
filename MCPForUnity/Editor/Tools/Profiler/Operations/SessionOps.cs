using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.Profiling;
using UProfiler = UnityEngine.Profiling.Profiler;

namespace MCPForUnity.Editor.Tools.Profiler
{
    internal static class SessionOps
    {
        private static readonly string[] AreaNames = Enum.GetNames(typeof(ProfilerArea));

        internal static object Start(JObject @params)
        {
            var p = new ToolParams(@params);
            if (!ProfilerPathUtility.TryResolve(p, "log_file", false, false, out string logFile, out var pathError))
                return pathError;
            bool? enableCallstacks = ParamCoercion.CoerceBoolNullable(p.GetRaw("enable_callstacks"));

            if (!string.IsNullOrEmpty(logFile))
            {
                string dir = Path.GetDirectoryName(logFile);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    return new ErrorResponse($"Log file directory does not exist: {dir}");
            }

            UProfiler.enabled = true;

            if (!string.IsNullOrEmpty(logFile))
            {
                UProfiler.logFile = logFile;
                UProfiler.enableBinaryLog = true;
            }

            if (enableCallstacks.HasValue)
                UProfiler.enableAllocationCallstacks = enableCallstacks.Value;

            return new SuccessResponse(
                "Profiler started.",
                new
                {
                    enabled = UProfiler.enabled,
                    recording = UProfiler.enableBinaryLog,
                    log_file = UProfiler.enableBinaryLog ? UProfiler.logFile : null,
                    allocation_callstacks = UProfiler.enableAllocationCallstacks,
                }
            );
        }

        internal static object Stop(JObject @params)
        {
            string previousLogFile = UProfiler.enableBinaryLog ? UProfiler.logFile : null;

            UProfiler.enableBinaryLog = false;
            UProfiler.enableAllocationCallstacks = false;
            UProfiler.enabled = false;

            return new SuccessResponse("Profiler stopped.", new { enabled = false, previous_log_file = previousLogFile });
        }

        internal static object Status(JObject @params)
        {
            var areas = new Dictionary<string, bool>();
            foreach (string name in AreaNames)
            {
                if (Enum.TryParse<ProfilerArea>(name, out var area))
                    areas[name] = UProfiler.GetAreaEnabled(area);
            }

            return new SuccessResponse(
                "Profiler status.",
                new
                {
                    enabled = UProfiler.enabled,
                    recording = UProfiler.enableBinaryLog,
                    log_file = UProfiler.enableBinaryLog ? UProfiler.logFile : null,
                    allocation_callstacks = UProfiler.enableAllocationCallstacks,
                    areas,
                }
            );
        }

        internal static object SetAreas(JObject @params)
        {
            var areasToken = @params["areas"] as JObject;
            if (areasToken == null)
                return new ErrorResponse($"'areas' parameter required. Valid areas: {string.Join(", ", AreaNames)}");

            var updated = new Dictionary<string, bool>();
            var pending = new List<KeyValuePair<ProfilerArea, bool>>();
            foreach (var prop in areasToken.Properties())
            {
                if (!Enum.TryParse<ProfilerArea>(prop.Name, true, out var area) || !Enum.IsDefined(typeof(ProfilerArea), area))
                    return new ErrorResponse($"Unknown area '{prop.Name}'. Valid: {string.Join(", ", AreaNames)}");

                if (prop.Value.Type != JTokenType.Boolean)
                    return new ErrorResponse($"Area '{prop.Name}' value must be a boolean (true/false), got: {prop.Value}");
                bool enabled = prop.Value.ReadScalar<bool>();
                pending.Add(new KeyValuePair<ProfilerArea, bool>(area, enabled));
                updated[prop.Name] = enabled;
            }

            // Reject malformed requests before changing any profiler area.
            foreach (var setting in pending)
                UProfiler.SetAreaEnabled(setting.Key, setting.Value);

            return new SuccessResponse($"Updated {updated.Count} profiler area(s).", new { areas = updated });
        }
    }
}
