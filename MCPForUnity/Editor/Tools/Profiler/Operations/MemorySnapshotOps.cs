using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Profiler
{
    internal static class MemorySnapshotOps
    {
        private static readonly Type MemoryProfilerType =
            Type.GetType("Unity.Profiling.Memory.MemoryProfiler, UnityEngine.CoreModule")
            ?? Type.GetType("UnityEngine.Profiling.Memory.Experimental.MemoryProfiler, UnityEngine.CoreModule")
            ?? Type.GetType("Unity.MemoryProfiler.MemoryProfiler, Unity.MemoryProfiler.Editor");

        private static bool HasPackage => MemoryProfilerType != null;

        internal static async Task<object> TakeSnapshotAsync(JObject @params)
        {
            var p = new ToolParams(@params);
            if (!ProfilerPathUtility.TryResolve(p, "snapshot_path", false, false, out string snapshotPath, out var pathError))
                return pathError;
            bool defaultPath = snapshotPath == null;
            if (defaultPath)
            {
                string dir = Path.Combine(Application.temporaryCachePath, "MemoryCaptures");
                try
                {
                    snapshotPath = ProfilerPathUtility.Resolve(Path.Combine(dir, $"snapshot_{DateTime.Now:yyyyMMdd_HHmmss}.snap"));
                }
                catch (Exception ex)
                {
                    return new ErrorResponse($"Invalid 'snapshot_path': {ex.Message}");
                }
            }
            else if (!Directory.Exists(Path.GetDirectoryName(snapshotPath)))
                return new ErrorResponse("Invalid 'snapshot_path': parent directory does not exist.");

            if (!HasPackage)
                return PackageMissingError();

            var tcs = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            OutputFolderScope folders = null;

            try
            {
                var debugScreenCaptureType =
                    Type.GetType("Unity.Profiling.DebugScreenCapture, UnityEngine.CoreModule")
                    ?? Type.GetType("UnityEngine.Profiling.Experimental.DebugScreenCapture, UnityEngine.CoreModule")
                    ?? Type.GetType("Unity.Profiling.Memory.Experimental.DebugScreenCapture, Unity.MemoryProfiler.Editor");
                var captureFlagsType =
                    Type.GetType("Unity.Profiling.Memory.CaptureFlags, UnityEngine.CoreModule")
                    ?? Type.GetType("UnityEngine.Profiling.Memory.Experimental.CaptureFlags, UnityEngine.CoreModule");

                System.Reflection.MethodInfo takeMethod = null;

                if (debugScreenCaptureType != null && captureFlagsType != null)
                {
                    var screenshotCallbackType = typeof(Action<,,>).MakeGenericType(typeof(string), typeof(bool), debugScreenCaptureType);
                    takeMethod = MemoryProfilerType.GetMethod(
                        "TakeSnapshot",
                        new[] { typeof(string), typeof(Action<string, bool>), screenshotCallbackType, captureFlagsType }
                    );
                }

                if (takeMethod == null && captureFlagsType != null)
                {
                    takeMethod = MemoryProfilerType.GetMethod("TakeSnapshot", new[] { typeof(string), typeof(Action<string, bool>), captureFlagsType });
                }

                if (takeMethod == null && debugScreenCaptureType != null)
                {
                    var screenshotCallbackType = typeof(Action<,,>).MakeGenericType(typeof(string), typeof(bool), debugScreenCaptureType);
                    takeMethod = MemoryProfilerType.GetMethod(
                        "TakeSnapshot",
                        new[] { typeof(string), typeof(Action<string, bool>), screenshotCallbackType, typeof(uint) }
                    );
                }

                if (takeMethod == null)
                {
                    takeMethod = MemoryProfilerType.GetMethod("TakeSnapshot", new[] { typeof(string), typeof(Action<string, bool>) });
                }

                if (takeMethod == null)
                    return new ErrorResponse("Could not find TakeSnapshot method on MemoryProfiler. API may have changed.");

                var takeMethodParams = takeMethod.GetParameters();
                int paramCount = takeMethodParams.Length;
                if (
                    !(paramCount == 4 && (takeMethodParams[3].ParameterType == captureFlagsType || takeMethodParams[3].ParameterType == typeof(uint)))
                    && !(paramCount == 3 && takeMethodParams[2].ParameterType == captureFlagsType)
                    && paramCount != 2
                )
                    return new ErrorResponse($"TakeSnapshot has unexpected {paramCount} parameters. API may have changed.");

                if (defaultPath)
                {
                    folders = new OutputFolderScope(Application.temporaryCachePath);
                    folders.EnsureParentDirectory(snapshotPath);
                }
                snapshotPath = ProfilerPathUtility.Resolve(snapshotPath);
                Action<string, bool> callback = (path, result) =>
                {
                    if (result)
                        folders?.Complete();
                    folders?.Dispose();
                    CompleteSnapshot(tcs, path, result);
                };

                if (paramCount == 4 && takeMethodParams[3].ParameterType == captureFlagsType)
                    takeMethod.Invoke(null, new object[] { snapshotPath, callback, null, GetCaptureFlagsDefault(takeMethodParams[3]) });
                else if (paramCount == 3 && takeMethodParams[2].ParameterType == captureFlagsType)
                    takeMethod.Invoke(null, new object[] { snapshotPath, callback, GetCaptureFlagsDefault(takeMethodParams[2]) });
                else if (paramCount == 4 && takeMethodParams[3].ParameterType == typeof(uint))
                    takeMethod.Invoke(null, new object[] { snapshotPath, callback, null, GetCaptureFlagsDefault(takeMethodParams[3]) });
                else
                    takeMethod.Invoke(null, new object[] { snapshotPath, callback });
            }
            catch (Exception ex)
            {
                folders?.Dispose();
                return new ErrorResponse($"Failed to take snapshot: {ex.Message}");
            }

            var timeout = Task.Delay(TimeSpan.FromSeconds(30));
            var completed = await Task.WhenAny(tcs.Task, timeout);
            if (completed == timeout)
                // A native capture may still be writing. Only its eventual callback owns folder cleanup.
                return new ErrorResponse("Snapshot timed out after 30 seconds.");

            return await tcs.Task;
        }

        private static object GetCaptureFlagsDefault(System.Reflection.ParameterInfo parameter)
        {
            if (parameter.HasDefaultValue)
                return parameter.DefaultValue;

            // Preserve the existing fallback for signatures without an optional default.
            return parameter.ParameterType == typeof(uint) ? (object)0u : Enum.ToObject(parameter.ParameterType, 0);
        }

        private static void CompleteSnapshot(TaskCompletionSource<object> completion, string path, bool result)
        {
            try
            {
                if (result)
                {
                    var fi = new FileInfo(path);
                    long size = fi.Exists ? fi.Length : 0;
                    completion.TrySetResult(
                        new SuccessResponse(
                            "Memory snapshot captured.",
                            new
                            {
                                path,
                                size_bytes = size,
                                size_mb = Math.Round(size / (1024.0 * 1024.0), 2),
                            }
                        )
                    );
                }
                else
                {
                    completion.TrySetResult(new ErrorResponse($"Snapshot capture failed for path: {path}"));
                }
            }
            catch (Exception ex)
            {
                completion.TrySetResult(new ErrorResponse($"Failed to read snapshot metadata: {ex.Message}"));
            }
        }

        internal static object ListSnapshots(JObject @params)
        {
            var p = new ToolParams(@params);
            if (!ProfilerPathUtility.TryResolve(p, "search_path", false, true, out string searchPath, out var pathError))
                return pathError;

            var dirs = new List<string>();
            if (!string.IsNullOrEmpty(searchPath))
            {
                dirs.Add(searchPath);
            }
            else
            {
                dirs.Add(Path.Combine(Application.temporaryCachePath, "MemoryCaptures"));
                dirs.Add(Path.Combine(Path.GetDirectoryName(Application.dataPath), "MemoryCaptures"));
            }

            var snapshots = new List<object>();
            try
            {
                for (int i = 0; i < dirs.Count; i++)
                {
                    string dir = dirs[i] = ProfilerPathUtility.Resolve(dirs[i]);
                    if (!Directory.Exists(dir))
                        continue;
                    foreach (string file in Directory.EnumerateFiles(dir, "*.snap"))
                    {
                        var fi = new FileInfo(ProfilerPathUtility.Resolve(file));
                        snapshots.Add(
                            new
                            {
                                path = fi.FullName,
                                size_bytes = fi.Length,
                                size_mb = Math.Round(fi.Length / (1024.0 * 1024.0), 2),
                                created = fi.CreationTimeUtc.ToString("o"),
                            }
                        );
                    }
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Invalid 'search_path': {ex.Message}");
            }

            return new SuccessResponse($"Found {snapshots.Count} snapshot(s).", new { snapshots, searched_dirs = dirs });
        }

        internal static object CompareSnapshots(JObject @params)
        {
            var p = new ToolParams(@params);
            if (!ProfilerPathUtility.TryResolve(p, "snapshot_a", true, false, out string pathA, out var pathAError))
                return pathAError;
            if (!ProfilerPathUtility.TryResolve(p, "snapshot_b", true, false, out string pathB, out var pathBError))
                return pathBError;

            if (!File.Exists(pathA))
                return new ErrorResponse($"Snapshot file not found: {pathA}");
            if (!File.Exists(pathB))
                return new ErrorResponse($"Snapshot file not found: {pathB}");

            var fiA = new FileInfo(pathA);
            var fiB = new FileInfo(pathB);

            return new SuccessResponse(
                "Snapshot comparison (file-level metadata).",
                new
                {
                    snapshot_a = new
                    {
                        path = fiA.FullName,
                        size_bytes = fiA.Length,
                        size_mb = Math.Round(fiA.Length / (1024.0 * 1024.0), 2),
                        created = fiA.CreationTimeUtc.ToString("o"),
                    },
                    snapshot_b = new
                    {
                        path = fiB.FullName,
                        size_bytes = fiB.Length,
                        size_mb = Math.Round(fiB.Length / (1024.0 * 1024.0), 2),
                        created = fiB.CreationTimeUtc.ToString("o"),
                    },
                    delta = new
                    {
                        size_delta_bytes = fiB.Length - fiA.Length,
                        size_delta_mb = Math.Round((fiB.Length - fiA.Length) / (1024.0 * 1024.0), 2),
                        time_delta_seconds = (fiB.CreationTimeUtc - fiA.CreationTimeUtc).TotalSeconds,
                    },
                    note = "For detailed object-level comparison, open both snapshots in the Memory Profiler window.",
                }
            );
        }

        private static ErrorResponse PackageMissingError()
        {
            return new ErrorResponse(
                "Package com.unity.memoryprofiler is required. "
                    + "Install via Package Manager or: manage_packages action=add_package package_id=com.unity.memoryprofiler"
            );
        }
    }

    internal static class ProfilerPathUtility
    {
        internal static bool TryResolve(ToolParams parameters, string key, bool required, bool directory, out string path, out ErrorResponse error)
        {
            path = null;
            error = null;
            var token = parameters.GetRaw(key);
            if (token == null || token.Type == JTokenType.Null || (token.Type == JTokenType.String && token.Value<string>() == ""))
            {
                if (!required)
                    return true;
                error = new ErrorResponse($"'{key}' parameter is required.");
                return false;
            }
            try
            {
                if (token.Type != JTokenType.String)
                    throw new ArgumentException("Path must be a string.");
                path = Resolve(token.Value<string>());
                if (directory && File.Exists(path))
                    throw new ArgumentException("Path must name a directory.");
                if (!directory && (Directory.Exists(path) || string.IsNullOrEmpty(Path.GetFileName(path))))
                    throw new ArgumentException("Path must name a file.");
                return true;
            }
            catch (Exception ex)
            {
                error = new ErrorResponse($"Invalid '{key}': {ex.Message}");
                return false;
            }
        }

        internal static string Resolve(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
                throw new ArgumentException("Path must not be blank.");
            foreach (char character in path)
                if (char.IsControl(character) || "*?\"<>|".IndexOf(character) >= 0)
                    throw new ArgumentException("Path contains an invalid character.");
            string normalized = path.Replace('\\', Path.DirectorySeparatorChar).Replace('/', Path.DirectorySeparatorChar);
            foreach (string part in normalized.Split(Path.DirectorySeparatorChar))
            {
                if (part == "..")
                    throw new InvalidOperationException("Parent traversal is not permitted.");
                if (Path.DirectorySeparatorChar != '\\' || part.Length == 0 || part == ".")
                    continue;
                if (part.EndsWith(".", StringComparison.Ordinal) || part.EndsWith(" ", StringComparison.Ordinal))
                    throw new ArgumentException("Windows path components must not end in a dot or space.");
                string name = part.Split('.')[0].ToUpperInvariant();
                if (
                    name == "CON"
                    || name == "PRN"
                    || name == "AUX"
                    || name == "NUL"
                    || (
                        name.Length == 4
                        && (name.StartsWith("COM", StringComparison.Ordinal) || name.StartsWith("LPT", StringComparison.Ordinal))
                        && name[3] >= '1'
                        && name[3] <= '9'
                    )
                )
                    throw new ArgumentException("Windows device names are not permitted.");
            }
            int colon = normalized.IndexOf(':');
            if (
                colon >= 0
                && !(
                    Path.DirectorySeparatorChar == '\\'
                    && colon == 1
                    && char.IsLetter(normalized[0])
                    && normalized.Length > 2
                    && normalized[2] == '\\'
                    && normalized.LastIndexOf(':') == colon
                )
            )
                throw new ArgumentException("Drive-relative paths and alternate data streams are not permitted.");

            string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string fullPath = Path.GetFullPath(Path.Combine(projectRoot, normalized));
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            string root =
                fullPath.Equals(projectRoot, comparison) || fullPath.StartsWith(projectRoot + Path.DirectorySeparatorChar, comparison)
                    ? projectRoot
                    : Path.GetFullPath(Application.temporaryCachePath);
            return SafePathUtility.ResolveWithinRoot(root, fullPath);
        }
    }
}
