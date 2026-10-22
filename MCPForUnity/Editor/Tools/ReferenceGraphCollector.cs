using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Linq.Expressions;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Read-only, paged native evidence. Persistent user state belongs to the server.</summary>
    internal static class ReferenceGraphCollector
    {
        private static string[] inventory;
        private static string inventoryToken;
        private static string inventoryEpoch;
        private static long inventoryRevision;
        private static double inventoryUsed;
        private static string dependencyGuid;
        private static string dependencyToken;
        private static string[] dependencies;
        private static string dependencyEpoch;
        private static long dependencyRevision;
        private static double dependencyUsed;
        private static UnityEngine.Object addressableSettings;
        private static string addressableEpoch;
        private static long addressableRevision = -1;
        private static double addressableUsed;
        private static double nextSweep;
        private static bool cleanupScheduled;
        private static EventInfo addressableEvent;
        private static Delegate addressableObserver;
        private static int addressableChanged;
        private static string addressableObservationError;
        private static readonly HashSet<string> SerializedExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".asset",
            ".prefab",
            ".unity",
            ".mat",
            ".controller",
            ".overrideController",
            ".playable",
            ".anim",
        };

        internal static bool Admitted(string path)
        {
            if (
                string.IsNullOrEmpty(path)
                || path.Contains('\\')
                || !(path.StartsWith("Assets/", StringComparison.Ordinal) || path.StartsWith("Packages/", StringComparison.Ordinal))
            )
                return false;
            string[] segments = path.Split('/');
            if (segments.Any(part => string.IsNullOrEmpty(part) || part.StartsWith(".", StringComparison.Ordinal) || part == ".."))
                return false;
            return true;
        }

        internal static JObject Handle(JObject request)
        {
            if (!ToolConsentThread.IsMainThread)
                throw new InvalidOperationException("Reference collection requires the Editor thread.");
            ReferenceGraphJournal.Activate();
            FlushPendingChanges();
            Type addressables = Type.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings, Unity.Addressables.Editor");
            if (addressables != null && addressableObserver == null && addressableObservationError == null)
            {
                try
                {
                    // Cover edits made while this domain had no active observer.
                    ObserveAddressables(addressables.Assembly);
                    ReferenceGraphJournal.RecordCatalogChange();
                }
                catch (Exception error)
                {
                    addressableObservationError = error.GetBaseException().GetType().Name;
                }
            }
            ReleaseExpired();
            ScheduleCleanup();
            string action = request.Value<string>("action") ?? "status";
            int count = Integer(request, "page_size", 128, 1, 1000);
            if (action == "status")
                return Status();
            if (action == "changes")
                return ReferenceGraphJournal.Read(request.Value<string>("epoch"), request.Value<long?>("after") ?? 0, count);
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
                throw new InvalidOperationException("The Editor is importing or compiling; retry collection when idle.");
            switch (action)
            {
                case "inventory":
                    return Inventory(request, count);
                case "asset":
                    string path = request.Value<string>("path");
                    if (path != null && !Admitted(path))
                        throw new ArgumentException("Asset resolution requires an admitted project path.");
                    return Describe(path == null ? ValidateGuid(request) : AssetDatabase.AssetPathToGUID(path));
                case "dependencies":
                    return Dependencies(request, count);
                case "source":
                    return Source(request);
                case "compilation":
                    return CompilationBindings(request);
                default:
                    throw new ArgumentException("Unknown reference collection action.");
            }
        }

        private static JObject Status() =>
            new JObject
            {
                ["instance_id"] = ReferenceGraphJournal.InstanceId,
                ["project_id"] = ProjectIdentityUtility.GetProjectHash(),
                ["epoch"] = ReferenceGraphJournal.Epoch,
                ["revision"] = ReferenceGraphJournal.Revision,
                ["compiling"] = EditorApplication.isCompiling,
                ["importing"] = EditorApplication.isUpdating,
                ["compilation_failed"] = EditorUtility.scriptCompilationFailed,
                ["compilation_context"] = ReferenceGraphCompilation.Status(),
                ["unity_version"] = Application.unityVersion,
                ["addressables_event_error"] = addressableObservationError,
                ["scope"] = "Assets and Packages; collection exclusions belong to the requesting user's graph policy",
            };

        private static int Integer(JObject request, string key, int fallback, int min, int max)
        {
            JToken token = request[key];
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int result) || result < min || result > max)
                throw new ArgumentException(key + " is outside the supported page range.");
            return result;
        }

        private static string ValidateGuid(JObject request)
        {
            string guid = request.Value<string>("guid");
            if (guid == null || guid.Length != 32 || guid.Any(c => !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')))
                throw new ArgumentException("A lowercase Unity asset GUID is required.");
            return guid;
        }

        private static JObject Inventory(JObject request, int count)
        {
            int offset = Integer(request, "offset", 0, 0, int.MaxValue);
            string token = request.Value<string>("snapshot");
            if (token == null)
            {
                if (offset != 0)
                    throw new ArgumentException("Inventory continuation requires its snapshot.");
                if (inventory == null || inventoryEpoch != ReferenceGraphJournal.Epoch)
                {
                    inventoryEpoch = ReferenceGraphJournal.Epoch;
                    inventoryRevision = ReferenceGraphJournal.Revision;
                    inventory = AssetDatabase.FindAssets("");
                    Array.Sort(inventory, StringComparer.Ordinal);
                    inventoryToken = Guid.NewGuid().ToString("N");
                }
            }
            else if (inventory == null || token != inventoryToken || inventoryEpoch != ReferenceGraphJournal.Epoch)
                throw new InvalidOperationException("Inventory snapshot expired; restart the census.");
            if (offset > inventory.Length)
                throw new ArgumentException("Inventory offset is past the snapshot.");
            var rows = new JArray();
            int end = offset + Math.Min(count, inventory.Length - offset);
            for (int i = offset; i < end; i++)
            {
                JObject row = Describe(inventory[i]);
                if (row.Value<bool>("present"))
                    rows.Add(row);
            }
            inventoryUsed = EditorApplication.timeSinceStartup;
            ScheduleCleanup();
            return new JObject
            {
                ["snapshot"] = inventoryToken,
                ["epoch"] = inventoryEpoch,
                ["revision"] = inventoryRevision,
                ["assets"] = rows,
                ["next_offset"] = end < inventory.Length ? (JToken)end : JValue.CreateNull(),
            };
        }

        private static JObject Describe(string guid)
        {
            string path = AssetDatabase.GUIDToAssetPath(guid);
            bool present = Admitted(path) && !AssetDatabase.IsValidFolder(path);
            var row = new JObject { ["guid"] = guid, ["present"] = present };
            if (!present)
                return row;
            row["path"] = path;
            row["kind"] = path.EndsWith(".cs", StringComparison.OrdinalIgnoreCase) ? "script" : "asset";
            row["identity_coverage"] = "main_type_only";
            try
            {
                row["main_type"] = AssetDatabase.GetMainAssetTypeAtPath(path)?.FullName;
            }
            catch (Exception error)
            {
                row["identity_error"] = error.GetBaseException().GetType().Name;
            }
            row["source_format"] =
                row.Value<string>("kind") == "script" ? "csharp"
                : SerializedExtensions.Contains(Path.GetExtension(path)) ? "unity_yaml"
                : "none";
            var keys = new JArray();
            int resources = path.LastIndexOf("/Resources/", StringComparison.Ordinal);
            if (resources >= 0)
            {
                string key = path.Substring(resources + "/Resources/".Length);
                string extension = Path.GetExtension(key);
                keys.Add(new JObject { ["scheme"] = "resources", ["value"] = key.Substring(0, key.Length - extension.Length) });
            }
            row["keys"] = keys;
            AddAddressableKeys(row, guid, keys);
            return row;
        }

        private static void AddAddressableKeys(JObject row, string guid, JArray keys)
        {
            Type defaults = Type.GetType("UnityEditor.AddressableAssets.AddressableAssetSettingsDefaultObject, Unity.Addressables.Editor");
            if (defaults == null)
            {
                row["addressables"] = "not_installed";
                return;
            }
            row["addressables_version"] = defaults.Assembly.GetName().Version.ToString();
            try
            {
                object settings = ReadAddressableSettings(defaults);
                row["addressables"] = settings == null ? "not_configured" : "available";
                if (settings == null)
                    return;
                MethodInfo find = settings.GetType().GetMethod("FindAssetEntry", new[] { typeof(string), typeof(bool) });
                if (find == null)
                    throw new MissingMethodException("FindAssetEntry(guid, includeImplicit)");
                object entry = find.Invoke(settings, new object[] { guid, true });
                if (entry == null)
                    return;
                string address = (string)entry.GetType().GetProperty("address").GetValue(entry);
                keys.Add(new JObject { ["scheme"] = "addressables", ["value"] = address });
                keys.Add(new JObject { ["scheme"] = "addressables", ["value"] = guid });
                foreach (object label in (IEnumerable)entry.GetType().GetProperty("labels").GetValue(entry))
                    keys.Add(new JObject { ["scheme"] = "addressables_label", ["value"] = (string)label });
            }
            catch (Exception error)
            {
                row["addressables"] = "unavailable";
                row["addressables_error"] = error.GetBaseException().GetType().Name;
            }
        }

        private static UnityEngine.Object ReadAddressableSettings(Type defaults)
        {
            ObserveAddressables(defaults.Assembly);
            addressableUsed = EditorApplication.timeSinceStartup;
            ScheduleCleanup();
            if (addressableEpoch == ReferenceGraphJournal.Epoch && addressableRevision == ReferenceGraphJournal.Revision)
                return addressableSettings;
            // GetSettings(false) still migrates legacy defaults. Read the stored
            // config object instead, so inspection cannot create or save assets.
            string key = (string)defaults.GetField("kDefaultConfigObjectName").GetValue(null);
            UnityEngine.Object settings = null;
            if (EditorBuildSettings.TryGetConfigObject<UnityEngine.Object>(key, out var config))
            {
                FieldInfo field = defaults.GetField("m_AddressableAssetSettingsGuid", BindingFlags.Instance | BindingFlags.NonPublic);
                if (field == null)
                    throw new MissingFieldException("m_AddressableAssetSettingsGuid");
                string path = AssetDatabase.GUIDToAssetPath((string)field.GetValue(config));
                if (Admitted(path))
                    settings = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
            }
            else
            {
                string legacyKey = (string)defaults.GetField("kDefaultConfigAssetName").GetValue(null);
                EditorBuildSettings.TryGetConfigObject(legacyKey, out settings);
                if (settings != null && !Admitted(AssetDatabase.GetAssetPath(settings)))
                    settings = null;
            }
            addressableSettings = settings;
            addressableEpoch = ReferenceGraphJournal.Epoch;
            addressableRevision = ReferenceGraphJournal.Revision;
            return settings;
        }

        private static void ObserveAddressables(Assembly assembly)
        {
            if (addressableObserver != null)
                return;
            Type settings = assembly.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings", true);
            EventInfo changed = settings.GetEvent("OnModificationGlobal", BindingFlags.Public | BindingFlags.Static);
            if (changed == null)
                throw new MissingMemberException("OnModificationGlobal");
            ParameterExpression[] parameters = changed
                .EventHandlerType.GetMethod("Invoke")
                .GetParameters()
                .Select(parameter => Expression.Parameter(parameter.ParameterType))
                .ToArray();
            MethodInfo mark = typeof(ReferenceGraphCollector).GetMethod(nameof(MarkAddressablesChanged), BindingFlags.Static | BindingFlags.NonPublic);
            Delegate observer = Expression.Lambda(changed.EventHandlerType, Expression.Call(mark), parameters).Compile();
            changed.AddEventHandler(null, observer);
            addressableEvent = changed;
            addressableObserver = observer;
            AssemblyReloadEvents.beforeAssemblyReload += ReleaseAddressablesObserver;
            EditorApplication.quitting += ReleaseAddressablesObserver;
        }

        private static void MarkAddressablesChanged() => Interlocked.Exchange(ref addressableChanged, 1);

        internal static void FlushPendingChanges()
        {
            if (Interlocked.Exchange(ref addressableChanged, 0) != 0)
                ReferenceGraphJournal.RecordCatalogChange();
        }

        private static void ReleaseAddressablesObserver()
        {
            if (addressableObserver != null)
                addressableEvent.RemoveEventHandler(null, addressableObserver);
            addressableObserver = null;
            addressableEvent = null;
            AssemblyReloadEvents.beforeAssemblyReload -= ReleaseAddressablesObserver;
            EditorApplication.quitting -= ReleaseAddressablesObserver;
        }

        private static JObject Dependencies(JObject request, int count)
        {
            string guid = ValidateGuid(request);
            JObject asset = Describe(guid);
            if (!asset.Value<bool>("present"))
                return asset;
            string path = asset.Value<string>("path");
            int offset = Integer(request, "offset", 0, 0, int.MaxValue);
            string token = request.Value<string>("snapshot");
            string currentToken = guid + ":" + ReferenceGraphJournal.Epoch + ":" + ReferenceGraphJournal.Revision;
            if (token != null && token != currentToken)
                throw new InvalidOperationException("Dependency snapshot changed; restart this asset.");
            if (token == null && offset != 0)
                throw new ArgumentException("Dependency continuation requires its snapshot.");
            if (dependencies == null || dependencyGuid != guid || dependencyToken != currentToken)
            {
                dependencies = AssetDatabase
                    .GetDependencies(path, false)
                    .Where(target => target != path && Admitted(target))
                    .Select(AssetDatabase.AssetPathToGUID)
                    .Where(target => !string.IsNullOrEmpty(target))
                    .Distinct(StringComparer.Ordinal)
                    .OrderBy(target => target, StringComparer.Ordinal)
                    .ToArray();
                dependencyGuid = guid;
                dependencyEpoch = ReferenceGraphJournal.Epoch;
                dependencyRevision = ReferenceGraphJournal.Revision;
                dependencyToken = currentToken;
            }
            if (offset > dependencies.Length)
                throw new ArgumentException("Dependency offset is past the snapshot.");
            int end = offset + Math.Min(count, dependencies.Length - offset);
            dependencyUsed = EditorApplication.timeSinceStartup;
            ScheduleCleanup();
            asset["snapshot"] = dependencyToken;
            asset["epoch"] = dependencyEpoch;
            asset["revision"] = dependencyRevision;
            asset["fingerprint"] = AssetDatabase.GetAssetDependencyHash(path).ToString();
            asset["targets"] = new JArray(dependencies.Skip(offset).Take(end - offset));
            asset["next_offset"] = end < dependencies.Length ? (JToken)end : JValue.CreateNull();
            return asset;
        }

        private static JObject Source(JObject request)
        {
            string guid = ValidateGuid(request);
            JObject asset = Describe(guid);
            if (!asset.Value<bool>("present") || asset.Value<string>("source_format") == "none")
                throw new ArgumentException("Source collection requires admitted C# or Unity serialized text.");
            string path = asset.Value<string>("path");
            string resolved = ResolveSourcePath(path);
            JObject context = asset.Value<string>("source_format") == "csharp" ? ReferenceGraphCompilation.ForSource(path, resolved) : null;
            var info = new FileInfo(resolved);
            string stamp = info.Length + ":" + info.LastWriteTimeUtc.Ticks;
            string fingerprint = stamp + (context == null ? "" : ":" + context.Value<string>("revision"));
            string expected = request.Value<string>("fingerprint");
            long offset = request.Value<long?>("offset") ?? 0;
            if (offset < 0 || offset > info.Length || (offset != 0 && expected == null) || (expected != null && expected != fingerprint))
                throw new InvalidOperationException("Source changed or continuation is invalid; restart reading it.");
            byte[] bytes = new byte[(int)Math.Min(65536, info.Length - offset)];
            int length = 0;
            using (var stream = new FileStream(resolved, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                if (asset.Value<string>("source_format") == "unity_yaml")
                {
                    byte[] header = new byte[5];
                    if (stream.Read(header, 0, header.Length) != header.Length || System.Text.Encoding.ASCII.GetString(header) != "%YAML")
                        return new JObject
                        {
                            ["guid"] = guid,
                            ["source_format"] = "binary",
                            ["coverage"] = "serialized_string_references_unavailable",
                        };
                }
                stream.Position = offset;
                while (length < bytes.Length)
                {
                    int read = stream.Read(bytes, length, bytes.Length - length);
                    if (read == 0)
                        throw new IOException("Source changed while reading.");
                    length += read;
                }
            }
            info.Refresh();
            if (stamp != info.Length + ":" + info.LastWriteTimeUtc.Ticks)
                throw new IOException("Source changed while reading.");
            return new JObject
            {
                ["guid"] = guid,
                ["source_format"] = asset["source_format"],
                ["fingerprint"] = fingerprint,
                ["compilation_context"] = offset == 0 ? context : null,
                ["bytes_base64"] = Convert.ToBase64String(bytes),
                ["next_offset"] = offset + length < info.Length ? (JToken)(offset + length) : JValue.CreateNull(),
            };
        }

        private static string ResolveSourcePath(string path)
        {
            if (!Admitted(path))
                throw new ArgumentException("Source path must belong to the admitted project.");
            string resolved;
            if (path.StartsWith("Packages/", StringComparison.Ordinal))
            {
                var package = UnityEditor.PackageManager.PackageInfo.FindForAssetPath(path);
                if (package == null)
                    throw new ArgumentException("Package source cannot be resolved.");
                resolved = Path.Combine(package.resolvedPath, path.Substring(("Packages/" + package.name + "/").Length));
            }
            else
            {
                if (!ProjectIdentityUtility.TryGetConsentProjectRoot(out string root))
                    throw new InvalidOperationException("The current Editor project root is unavailable.");
                resolved = Path.Combine(root, path);
            }
            return GuardedFileSystem.CheckPath(resolved);
        }

        private static JObject CompilationBindings(JObject request)
        {
            if (!(request["paths"] is JArray paths) || paths.Count > 1000 || paths.Any(path => path.Type != JTokenType.String))
                throw new ArgumentException("Compilation metadata requires a page of at most 1000 source paths.");
            JObject status = ReferenceGraphCompilation.Status();
            if (status.Value<string>("state") != "available" || request.Value<string>("revision") != status.Value<string>("revision"))
                throw new InvalidOperationException("Compilation metadata changed; restart the page.");
            var rows = new JArray();
            foreach (string path in paths.Values<string>())
            {
                if (!Admitted(path) || !path.EndsWith(".cs", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("Compilation metadata requires C# source paths.");
                try
                {
                    string signature = ReferenceGraphCompilation.Binding(path, ResolveSourcePath(path));
                    rows.Add(new JObject { ["path"] = path, ["signature"] = signature });
                }
                catch (Exception error)
                {
                    rows.Add(new JObject { ["path"] = path, ["error"] = error.GetType().Name });
                }
            }
            return new JObject { ["revision"] = status["revision"], ["bindings"] = rows };
        }

        private static void ScheduleCleanup()
        {
            if (cleanupScheduled)
                return;
            cleanupScheduled = true;
            nextSweep = EditorApplication.timeSinceStartup + 60;
            EditorApplication.update += ReleaseExpired;
        }

        private static void ReleaseExpired()
        {
            double now = EditorApplication.timeSinceStartup;
            if (now < nextSweep)
                return;
            nextSweep = now + 60;
            ReferenceGraphCompilation.ReleaseExpired(now);
            if (inventory != null && now - inventoryUsed > 600)
                inventory = null;
            if (dependencies != null && now - dependencyUsed > 600)
                dependencies = null;
            if (now - addressableUsed > 600)
            {
                addressableSettings = null;
                addressableRevision = -1;
            }
            if (inventory == null && dependencies == null && addressableSettings == null && !ReferenceGraphCompilation.Retained && cleanupScheduled)
            {
                EditorApplication.update -= ReleaseExpired;
                cleanupScheduled = false;
            }
        }
    }
}
