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
    internal static partial class AddressablePrefabWorkflow
    {
        private const string Store = "MCPForUnity.AddressablePrefab.v1";
        private static readonly HashSet<string> Running = new HashSet<string>();

        internal static bool IsAction(string action) => DeclaredActionContracts.IsPrefabAction(action);

        // Only the disposable process-death fixture supplies an implementation.
        static partial void ObserveDurableWrite(string boundary);

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
            public string settingsGuid;
            public string settingsFiles;
            public string stableRevision;
            public string approvedObservation;
            public string approvedSource;
            public string successor;
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

        private static void Write(string key, Plan plan, string previous = null, string expectedSignature = null)
        {
            // Read again because a native import or Addressables callback can be reentrant.
            var plans = Read();
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            foreach (string expired in plans.Where(p => p.Value.expires <= now && !Running.Contains(p.Key)).Select(p => p.Key).ToArray())
                plans.Remove(expired);
            if (!plans.ContainsKey(key) && plans.Count >= 64)
                AddressablesState.Fail("prefab_checkpoint_capacity", "64 unexpired prefab workflows are retained.");
            plans[key] = plan;
            PersistEvidence(key, plan, previous, expectedSignature);
            ObserveDurableWrite(plan.phase);
            SessionState.SetString(Store, JsonConvert.SerializeObject(plans));
        }

        internal static object Handle(JObject request, string action)
        {
            string key = HexField(request, DeclaredActionContracts.PrefabCheckpoint);
            if (!DeclaredActionContracts.MatchesPrefabFields(request, action))
                AddressablesState.Fail("invalid_prefab_request", "The supplied fields do not match this workflow action.");
            var plans = Read();
            plans.TryGetValue(key, out Plan plan);
            if (plan != null && (plan.expires <= DateTimeOffset.UtcNow.ToUnixTimeSeconds() || plan.session != CommandResumeState.SessionId))
                plan = null;
            if (action == "reconcile_prefab" || action == "replan_prefab")
                return Recover(request, key, action, plan);
            if (action == "preview_prefab")
            {
                var intent = (JObject)request.DeepClone();
                intent.Remove("action");
                intent.Remove(DeclaredActionContracts.PrefabCheckpoint);
                if (plan == null)
                {
                    if (ReadEvidence(key) != null)
                        return Unavailable();
                    plan = Preview(intent);
                    Write(key, plan);
                }
                else if (!JToken.DeepEquals(intent, plan.intent))
                    AddressablesState.Fail("prefab_intent_conflict", "This operation ID already belongs to a different plan.");
            }
            if (plan == null)
                return Unavailable();
            if (plan.phase == "creating" || plan.phase == "registering")
            {
                if (action == "cancel_prefab")
                {
                    plan.cancellationRequested = true;
                    Write(key, plan);
                }
                return Report(key, action, plan, Running.Contains(key) ? "running" : "unknown", "Native invocation fenced; settlement has not been confirmed.");
            }
            if (plan.phase == "cancelled")
                return Report(key, action, plan, "cancelled");
            if (action == "cancel_prefab" && plan.phase != "registered")
            {
                plan.phase = "cancelled";
                Write(key, plan);
                return Report(key, action, plan, plan.phase);
            }
            AddressablesState settings;
            JObject conflict;
            try
            {
                settings = AddressablesState.Capture(plan.intent);
                conflict = Conflict(plan, settings);
            }
            catch (AddressablesException error)
            {
                return Report(key, action, plan, "conflict", error.Code + ": " + error.Message);
            }
            catch (IOException)
            {
                return Report(key, action, plan, "conflict", "The expected asset or metadata file could not be read.");
            }
            if (conflict != null)
                return Report(key, action, plan, "conflict", (string)conflict["detail"], conflict);
            if (action == "preview_prefab" || action == "prefab_status" || plan.phase == "registered" || (action == "create_prefab" && plan.phase == "created"))
                return Report(key, action, plan, plan.phase);
            if (action == "register_prefab" && plan.phase != "created")
                return Report(key, action, plan, "blocked", "Registration depends on the confirmed created prefab GUID.");
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                return Report(key, action, plan, "blocked", "Asset editing requires stable Edit Mode.");
            bool create = action == "create_prefab";
            Object group = (Object)settings.Groups[(string)plan.intent["group_guid"]];
            MethodInfo registration = create ? null : Registration(settings, group);
            if (!create && EditorUtility.IsDirty(group))
                return Report(
                    key,
                    action,
                    plan,
                    "conflict",
                    "Target group has unsaved edits.",
                    Difference("group_dirty", false, true, "Target group has unsaved edits.")
                );
            plan.phase = create ? "creating" : "registering";
            Write(key, plan);
            Running.Add(key);
            try
            {
                string path = (string)plan.intent["prefab_path"];
                if (create)
                {
                    if (!AssetDatabase.CopyAsset((string)plan.intent["source_prefab"], path))
                        return Report(key, action, plan, "unknown", "Prefab copy did not confirm its result.");
                    ObserveDurableWrite("prefab_saved");
                    string guid = AssetDatabase.AssetPathToGUID(path);
                    if (string.IsNullOrEmpty(guid) || guid == plan.sourceGuid || AssetDatabase.LoadAssetAtPath<GameObject>(path) == null)
                        return Report(key, action, plan, "unknown", "Unity did not confirm a new prefab GUID.");
                    plan.guid = guid;
                    plan.hash = Hash(path);
                    plan.cancellationRequested = Read()[key].cancellationRequested;
                    plan.phase = plan.cancellationRequested ? "cancelled" : "created";
                }
                else
                {
                    registration.Invoke(settings.Settings, new object[] { plan.guid, group, false, true });
                    var applied = AddressablesState.Capture(plan.intent);
                    string appliedRevision = applied.Revision;
                    if (
                        DiskHash(plan.groupPath) != plan.groupFiles
                        || (plan.settingsFiles != null && DiskHash((string)plan.intent["settings_path"]) != plan.settingsFiles)
                        || !OnlyPlannedEntryAdded(plan, settings, applied)
                    )
                        return Report(key, action, plan, "unknown", "Native registration changed unexpected fields. Inspect the group before saving.");
                    EditorUtility.SetDirty(group);
                    AssetDatabase.SaveAssetIfDirty(group);
                    ObserveDurableWrite("registration_saved");
                    var savedState = AddressablesState.Capture(plan.intent);
                    if (EditorUtility.IsDirty(group) || savedState.Revision != appliedRevision)
                        return Report(key, action, plan, "unknown", "The target group did not confirm the saved entry.");
                    plan.stableRevision = StableRevision(savedState);
                    plan.groupFiles = DiskHash(plan.groupPath);
                    plan.cancellationRequested = Read()[key].cancellationRequested;
                    plan.phase = "registered";
                }
                Write(key, plan);
                return Report(key, action, plan, plan.phase);
            }
            catch (Exception)
            {
                // Read the last stored fence, never report tentative local fields as settled.
                return Report(key, action, Read()[key], "unknown", "Native settlement could not be proven. Inspect the target before another edit.");
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
            return AddressablesCapabilities.Writer("register_prefab", state.Settings.GetType(), entry, group.GetType(), null);
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
                settingsGuid = AssetDatabase.AssetPathToGUID(settingsPath),
                settingsFiles = DiskHash(settingsPath),
                stableRevision = StableRevision(state),
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

        private static JObject Conflict(Plan plan, AddressablesState state, string persistentRevision = null)
        {
            string path = PrefabPath(plan.intent, "prefab_path");
            string parentGuid = AssetDatabase.AssetPathToGUID(path.Substring(0, path.LastIndexOf('/')));
            if (parentGuid != plan.parentGuid)
                return Difference("parent_guid", plan.parentGuid, parentGuid, "Destination parent GUID changed.");
            if (plan.guid == null)
            {
                string source = PrefabPath(plan.intent, "source_prefab");
                if (AssetPathUtility.IsAssetDestinationOccupied(path))
                    return Difference("destination_vacant", true, false, "Prefab destination is now occupied.");
                string sourceGuid = AssetDatabase.AssetPathToGUID(source);
                if (sourceGuid != plan.sourceGuid)
                    return Difference("source_guid", plan.sourceGuid, sourceGuid, "Source prefab GUID changed.");
                string hash = Hash(source);
                if (hash != plan.sourceHash)
                    return Difference("source_hash", plan.sourceHash, hash, "Source prefab dependency or file hash changed.");
            }
            else
            {
                string guid = AssetDatabase.AssetPathToGUID(path);
                if (guid != plan.guid)
                    return Difference("prefab_guid", plan.guid, guid, "Created prefab GUID changed.");
                string hash = Hash(path);
                if (hash != plan.hash)
                    return Difference("prefab_hash", plan.hash, hash, "Created prefab dependency or file hash changed.");
            }
            if (plan.phase == "registered" && !MatchesEntry(plan, state))
                return Difference("registered_entry_matches", true, false, "Registered entry group, address or asset path changed.");
            if (plan.phase == "registered" && persistentRevision == null)
                return null;
            string expected = persistentRevision != null ? plan.stableRevision : plan.revision;
            string observed = persistentRevision ?? state.Revision;
            if (observed != expected)
                return Difference("settings_revision", expected, observed, "Addressables revision changed.");
            if (plan.settingsGuid != null)
            {
                string settingsPath = (string)plan.intent["settings_path"];
                string settingsGuid = AssetDatabase.AssetPathToGUID(settingsPath);
                if (settingsGuid != plan.settingsGuid)
                    return Difference("settings_guid", plan.settingsGuid, settingsGuid, "Settings asset GUID changed.");
                string files = DiskHash(settingsPath);
                if (files != plan.settingsFiles)
                    return Difference("settings_files", plan.settingsFiles, files, "Saved Addressables settings or metadata changed.");
            }
            string groupFiles = DiskHash(plan.groupPath);
            if (groupFiles != plan.groupFiles)
                return Difference("group_files", plan.groupFiles, groupFiles, "Saved Addressables group or metadata changed.");
            return null;
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

        private static object Report(
            string key,
            string action,
            Plan plan,
            string state,
            string detail = null,
            JObject conflict = null,
            JObject reconciliation = null
        )
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
                conflict,
                reconciliation,
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
                handoff = new
                {
                    version = 1,
                    action,
                    phase = plan.phase,
                    workflow_id = HandoffIdentity(AddressablesState.Hash(key, "")),
                    previous_workflow_id = HandoffIdentity(plan.approvedSource),
                    observation_id = HandoffIdentity((string)reconciliation?["observation"]),
                    approved_observation_id = HandoffIdentity(plan.approvedObservation),
                },
            };
            if (state == "unknown" || state == "conflict" || state == "blocked")
                return new ErrorResponse("prefab_" + state, data);
            return new SuccessResponse("Prefab workflow " + state, data);
        }

        private static string HandoffIdentity(string lookup) => lookup == null ? null : AddressablesState.Hash("handoff.prefab.v1\n" + lookup, "");
    }
}
