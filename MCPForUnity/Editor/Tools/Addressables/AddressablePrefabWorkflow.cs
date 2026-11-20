using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Addressables
{
    // A command receipt owns one delivery. This checkpoint owns the dependency between
    // two separately receipted deliveries in this Editor process.
    internal static class AddressablePrefabWorkflow
    {
        private const string Store = "MCPForUnity.AddressablePrefab.v1";
        private static readonly HashSet<string> Running = new HashSet<string>();

        internal static bool IsAction(string action) =>
            action == "preview_prefab" || action == "create_prefab" || action == "register_prefab" || action == "prefab_status" || action == "cancel_prefab";

        private sealed class Plan
        {
            public JObject intent;
            public string phase;
            public string session;
            public long expires;
            public string revision;
            public string sourceGuid;
            public string sourceHash;
            public string parentGuid;
            public string groupPath;
            public string groupFiles;
            public string guid;
            public string hash;
            public bool cancellationRequested;
        }

        private static Dictionary<string, Plan> Read()
        {
            string raw = SessionState.GetString(Store, "{}");
            if (raw.Length > 512 * 1024)
                AddressablesState.Fail("prefab_checkpoint_unavailable", "The session checkpoint exceeds its bound.");
            return JsonConvert.DeserializeObject<Dictionary<string, Plan>>(raw) ?? throw new InvalidDataException();
        }

        private static void Write(string key, Plan plan)
        {
            // Read again because a native import or Addressables callback can be reentrant.
            var plans = Read();
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            foreach (string expired in plans.Where(p => p.Value.expires <= now && !Running.Contains(p.Key)).Select(p => p.Key).ToArray())
                plans.Remove(expired);
            if (!plans.ContainsKey(key) && plans.Count >= 64)
                AddressablesState.Fail("prefab_checkpoint_capacity", "64 unexpired prefab workflows are retained.");
            plans[key] = plan;
            SessionState.SetString(Store, JsonConvert.SerializeObject(plans));
        }

        internal static object Handle(JObject request, string action)
        {
            string key = AddressablesState.Text(request["checkpoint"], "checkpoint", 64);
            if (key.Length != 64 || !key.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))
                AddressablesState.Fail("invalid_prefab_checkpoint", "A server-bound workflow checkpoint is required.");
            var fields =
                action == "preview_prefab"
                    ? new[] { "action", "checkpoint", "source_prefab", "prefab_path", "group_guid", "settings_path" }
                    : new[] { "action", "checkpoint" };
            if (fields.Length != request.Count || fields.Any(name => request.Property(name) == null))
                AddressablesState.Fail("invalid_prefab_request", "The supplied fields do not match this workflow action.");
            var plans = Read();
            plans.TryGetValue(key, out Plan plan);
            if (plan != null && (plan.expires <= DateTimeOffset.UtcNow.ToUnixTimeSeconds() || plan.session != CommandResumeState.SessionId))
                plan = null;
            if (action == "preview_prefab")
            {
                var intent = (JObject)request.DeepClone();
                intent.Remove("action");
                intent.Remove("checkpoint");
                if (plan == null)
                {
                    plan = Preview(intent);
                    Write(key, plan);
                }
                else if (!JToken.DeepEquals(intent, plan.intent))
                    AddressablesState.Fail("prefab_intent_conflict", "This operation ID already belongs to a different plan.");
            }
            if (plan == null)
                return new ErrorResponse(
                    "prefab_checkpoint_unavailable",
                    new { state = "unknown", message = "No current owner/project/process checkpoint. Inspect the original target." }
                );
            if (plan.phase == "creating" || plan.phase == "registering")
            {
                if (action == "cancel_prefab")
                {
                    plan.cancellationRequested = true;
                    Write(key, plan);
                }
                return Report(plan, Running.Contains(key) ? "running" : "unknown", "Native invocation fenced; settlement has not been confirmed.");
            }
            if (plan.phase == "cancelled")
                return Report(plan, "cancelled");
            if (action == "cancel_prefab" && plan.phase != "registered")
            {
                plan.phase = "cancelled";
                Write(key, plan);
                return Report(plan, plan.phase);
            }
            AddressablesState settings;
            string conflict;
            try
            {
                settings = AddressablesState.Capture(plan.intent);
                conflict = Conflict(plan, settings);
            }
            catch (AddressablesException error)
            {
                return Report(plan, "conflict", error.Code + ": " + error.Message);
            }
            catch (IOException)
            {
                return Report(plan, "conflict", "The expected asset or metadata file could not be read.");
            }
            if (conflict != null)
                return Report(plan, "conflict", conflict);
            if (action == "preview_prefab" || action == "prefab_status" || plan.phase == "registered" || (action == "create_prefab" && plan.phase == "created"))
                return Report(plan, plan.phase);
            if (action == "register_prefab" && plan.phase != "created")
                return Report(plan, "blocked", "Registration depends on the confirmed created prefab GUID.");
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                return Report(plan, "blocked", "Asset editing requires stable Edit Mode.");
            bool create = action == "create_prefab";
            Object group = (Object)settings.Groups[(string)plan.intent["group_guid"]];
            MethodInfo registration = Registration(settings, group);
            if (!create && EditorUtility.IsDirty(group))
                return Report(plan, "conflict", "Target group has unsaved edits.");
            plan.phase = create ? "creating" : "registering";
            Write(key, plan);
            Running.Add(key);
            try
            {
                string path = (string)plan.intent["prefab_path"];
                if (create)
                {
                    if (!AssetDatabase.CopyAsset((string)plan.intent["source_prefab"], path))
                        return Report(plan, "unknown", "Prefab copy did not confirm its result.");
                    string guid = AssetDatabase.AssetPathToGUID(path);
                    if (string.IsNullOrEmpty(guid) || guid == plan.sourceGuid || AssetDatabase.LoadAssetAtPath<GameObject>(path) == null)
                        return Report(plan, "unknown", "Unity did not confirm a new prefab GUID.");
                    plan.guid = guid;
                    plan.hash = Hash(path);
                    plan.cancellationRequested = Read()[key].cancellationRequested;
                    plan.phase = plan.cancellationRequested ? "cancelled" : "created";
                }
                else
                {
                    registration.Invoke(settings.Settings, new object[] { plan.guid, group, false, true });
                    if (DiskHash(plan.groupPath) != plan.groupFiles || !OnlyPlannedEntryAdded(plan, settings, AddressablesState.Capture(plan.intent)))
                        return Report(plan, "unknown", "Native registration changed unexpected fields. Inspect the group before saving.");
                    EditorUtility.SetDirty(group);
                    AssetDatabase.SaveAssetIfDirty(group);
                    if (EditorUtility.IsDirty(group) || !MatchesEntry(plan, AddressablesState.Capture(plan.intent)))
                        return Report(plan, "unknown", "The target group did not confirm the saved entry.");
                    plan.cancellationRequested = Read()[key].cancellationRequested;
                    plan.phase = "registered";
                }
                Write(key, plan);
                return Report(plan, plan.phase);
            }
            catch (Exception)
            {
                // Read the last stored fence, never report tentative local fields as settled.
                return Report(Read()[key], "unknown", "Native settlement could not be proven. Inspect the target before another edit.");
            }
            finally
            {
                Running.Remove(key);
            }
        }

        private static string Hash(string path) => AssetDatabase.GetAssetDependencyHash(path) + ":" + DiskHash(path);

        private static string DiskHash(string path)
        {
            using var sha = SHA256.Create();
            using var prefab = File.OpenRead(AssetPathUtility.GetFullAssetPath(path));
            using var meta = File.OpenRead(AssetPathUtility.GetFullAssetPath(path + ".meta"));
            if (prefab.Length + meta.Length > 16 * 1024 * 1024)
                AddressablesState.Fail("prefab_revision_limit", "A guarded asset and its metadata exceed 16 MiB.");
            return Convert.ToBase64String(sha.ComputeHash(prefab)) + ":" + Convert.ToBase64String(sha.ComputeHash(meta));
        }

        private static string PrefabPath(JObject intent, string field)
        {
            string path = AddressablesState.Text(intent[field], field);
            if (
                !path.StartsWith("Assets/", StringComparison.Ordinal)
                || !path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)
                || AssetPathUtility.GetContainedAssetPath(path) != path
            )
                AddressablesState.Fail("invalid_prefab_path", "A canonical, contained Assets prefab path is required.");
            return path;
        }

        private static MethodInfo Registration(AddressablesState state, Object group)
        {
            Type entry = state.Settings.GetType().Assembly.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetEntry");
            return AddressablesCompatibility.RequireMethod(
                state.Settings,
                "register_prefab",
                "CreateOrMoveEntry",
                entry,
                typeof(string),
                group.GetType(),
                typeof(bool),
                typeof(bool)
            );
        }

        private static Plan Preview(JObject intent)
        {
            string path = PrefabPath(intent, "prefab_path");
            string source = PrefabPath(intent, "source_prefab");
            string parent = path.Substring(0, path.LastIndexOf('/'));
            if (!AssetDatabase.IsValidFolder(parent) || AssetPathUtility.IsAssetDestinationOccupied(path))
                AddressablesState.Fail("prefab_destination_conflict", "The destination must be vacant with an existing parent.");
            if (AssetDatabase.LoadAssetAtPath<GameObject>(source) == null)
                AddressablesState.Fail("prefab_source_missing", "An existing source prefab is required.");
            string settingsPath = AddressablesState.Path(intent["settings_path"]);
            if (AssetPathUtility.GetContainedAssetPath(settingsPath) != settingsPath)
                AddressablesState.Fail("invalid_prefab_path", "A canonical settings asset is required.");
            var state = AddressablesState.Capture(intent);
            string groupId = AddressablesState.Text(intent["group_guid"], "group_guid", 32);
            if (!state.Groups.TryGetValue(groupId, out object group) || (bool)AddressablesState.Get(group, "ReadOnly"))
                AddressablesState.Fail("prefab_group_unavailable", "A writable existing group is required.");
            if (state.Document["entries"].Any(row => (string)row["address"] == path))
                AddressablesState.Fail("prefab_address_conflict", "The planned address is already assigned.");
            if (
                path.Split('/')
                    .Any(segment =>
                        segment.Equals("Resources", StringComparison.OrdinalIgnoreCase)
                        || segment.Equals("Editor", StringComparison.OrdinalIgnoreCase)
                        || segment.Equals("StreamingAssets", StringComparison.OrdinalIgnoreCase)
                    )
            )
                AddressablesState.Fail("prefab_address_conflict", "The destination must be a directly addressable runtime asset.");
            Registration(state, (Object)group);
            string groupPath = AssetDatabase.GetAssetPath((Object)group);
            return new Plan
            {
                intent = intent,
                phase = "planned",
                session = CommandResumeState.SessionId,
                expires = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 7200,
                revision = state.Revision,
                sourceGuid = AssetDatabase.AssetPathToGUID(source),
                sourceHash = Hash(source),
                parentGuid = AssetDatabase.AssetPathToGUID(parent),
                groupPath = groupPath,
                groupFiles = DiskHash(groupPath),
            };
        }

        private static bool MatchesEntry(Plan plan, AddressablesState state) =>
            state
                .Document["entries"]
                .Any(row =>
                    (string)row["guid"] == plan.guid
                    && (string)row["group_guid"] == (string)plan.intent["group_guid"]
                    && (string)row["address"] == (string)plan.intent["prefab_path"]
                    && (string)row["path"] == (string)plan.intent["prefab_path"]
                );

        private static string Conflict(Plan plan, AddressablesState state)
        {
            string path = PrefabPath(plan.intent, "prefab_path");
            if (AssetDatabase.AssetPathToGUID(path.Substring(0, path.LastIndexOf('/'))) != plan.parentGuid)
                return "Destination parent GUID changed.";
            if (plan.guid == null)
            {
                string source = PrefabPath(plan.intent, "source_prefab");
                if (AssetPathUtility.IsAssetDestinationOccupied(path))
                    return "Prefab destination is now occupied.";
                if (AssetDatabase.AssetPathToGUID(source) != plan.sourceGuid || Hash(source) != plan.sourceHash)
                    return "Source prefab GUID or dependency hash changed.";
            }
            else if (AssetDatabase.AssetPathToGUID(path) != plan.guid || Hash(path) != plan.hash)
                return "Created prefab GUID or dependency hash changed.";
            if (plan.phase == "registered")
                return MatchesEntry(plan, state) ? null : "Registered entry group, address or asset path changed.";
            if (DiskHash(plan.groupPath) != plan.groupFiles)
                return "Saved Addressables group or metadata changed.";
            return state.Revision != plan.revision ? "Addressables revision changed; expected " + plan.revision + ", observed " + state.Revision + "." : null;
        }

        private static bool OnlyPlannedEntryAdded(Plan plan, AddressablesState before, AddressablesState after)
        {
            if (!MatchesEntry(plan, after))
                return false;
            var added = after.Document["entries"].Single(row => (string)row["guid"] == plan.guid);
            if (added["labels"].HasValues || (bool)added["read_only"])
                return false;
            added.Remove();
            var group = after.Document["groups"].Single(row => (string)row["guid"] == (string)plan.intent["group_guid"]);
            group["entry_guids"].Single(id => (string)id == plan.guid).Remove();
            return JToken.DeepEquals(before.Document, after.Document);
        }

        private static object Report(Plan plan, string state, string detail = null)
        {
            var completed = new List<string>();
            if (plan.guid != null)
                completed.Add("create_prefab");
            if (plan.phase == "registered")
                completed.Add("register_prefab");
            var data = new
            {
                state,
                detail,
                cancellation_requested = plan.cancellationRequested || plan.phase == "cancelled",
                editor_session_id = plan.session,
                prefab_guid = plan.guid,
                prefab_hash = plan.hash,
                settings_revision = plan.revision,
                source_prefab = (string)plan.intent["source_prefab"],
                affected_paths = new[] { (string)plan.intent["prefab_path"], plan.groupPath },
                settings_path = (string)plan.intent["settings_path"],
                group_guid = (string)plan.intent["group_guid"],
                dependency = "register_prefab.entry_guid = create_prefab.prefab_guid",
                completed_steps = completed,
                outstanding_steps = new[] { "create_prefab", "register_prefab" }.Except(completed).ToArray(),
            };
            if (state == "unknown" || state == "conflict" || state == "blocked")
                return new ErrorResponse("prefab_" + state, data);
            return new SuccessResponse("Prefab workflow " + state, data);
        }
    }
}
