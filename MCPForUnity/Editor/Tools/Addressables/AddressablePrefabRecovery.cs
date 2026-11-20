using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Addressables
{
    internal static partial class AddressablePrefabWorkflow
    {
        // Only the server derives checkpoint keys from the authenticated receipt principal and project.
        // Store a one-way lookup and a signed snapshot. Never serialize the key or restore write
        // authority from this journal. Active writes still require a current SessionState plan.
        private static string EvidencePath =>
            GuardedFileSystem.CheckPath(Path.Combine(Application.dataPath, "..", "Library", "MCPForUnity", "Addressables", "prefab-evidence-v1.json"));

        private static string HexField(JObject request, string name)
        {
            string value = AddressablesState.Text(request[name], name, 64);
            if (value.Length != 64 || !value.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))
                AddressablesState.Fail("invalid_prefab_checkpoint", name + " must be a lowercase 64-character digest.");
            return value;
        }

        private static JObject ReadJournal()
        {
            string path = EvidencePath;
            if (!File.Exists(path))
                return new JObject();
            using var stream = File.OpenRead(path);
            if (stream.Length > 1024 * 1024)
                AddressablesState.Fail("prefab_evidence_unavailable", "The reconciliation journal exceeds its size bound.");
            using var reader = new StreamReader(stream, Encoding.UTF8);
            var journal = JObject.Parse(reader.ReadToEnd());
            if (journal.Count > 64)
                AddressablesState.Fail("prefab_evidence_unavailable", "The reconciliation journal exceeds its record bound.");
            return journal;
        }

        private static string Signature(string key, string purpose, string value)
        {
            using var mac = new HMACSHA256(Encoding.ASCII.GetBytes(key));
            return BitConverter.ToString(mac.ComputeHash(Encoding.UTF8.GetBytes(purpose + "\n" + value))).Replace("-", "").ToLowerInvariant();
        }

        private static Plan ReadEvidence(string key) => VerifyEvidence(key, ReadJournal()[AddressablesState.Hash(key, "")]);

        private static Plan VerifyEvidence(string key, JToken record)
        {
            if (record == null)
                return null;
            string payload = (string)record["payload"];
            if (payload == null || Signature(key, "evidence.v1", payload) != (string)record["signature"])
                AddressablesState.Fail("prefab_evidence_unavailable", "The reconciliation record failed its integrity check.");
            var plan = JsonConvert.DeserializeObject<Plan>(payload);
            return plan != null && plan.expires > DateTimeOffset.UtcNow.ToUnixTimeSeconds() ? plan : null;
        }

        private static JObject SignedRecord(string key, Plan plan)
        {
            string payload = JsonConvert.SerializeObject(plan, Formatting.None);
            return new JObject
            {
                ["payload"] = payload,
                ["signature"] = Signature(key, "evidence.v1", payload),
                ["expires"] = plan.expires,
            };
        }

        private static void PersistEvidence(string key, Plan plan, string previous, string expectedSignature)
        {
            string id = AddressablesState.Hash(key, "");
            JObject journal = ReadJournal();
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            foreach (var property in journal.Properties().Where(p => (long?)p.Value["expires"] <= now).ToArray())
                property.Remove();
            if (journal[id] == null && journal.Count >= 64)
                AddressablesState.Fail("prefab_checkpoint_capacity", "64 unexpired reconciliation records are retained.");
            if (previous != null)
            {
                string previousId = AddressablesState.Hash(previous, "");
                var record = journal[previousId];
                Plan source = VerifyEvidence(previous, record);
                if (source == null || source.successor != null || (string)record["signature"] != expectedSignature || journal[id] != null)
                    AddressablesState.Fail("prefab_replan_conflict", "The observed evidence is unavailable, changed, or already has an approved continuation.");
                // Both records are published by one file replacement before the new plan acquires session authority.
                source.successor = id;
                journal[previousId] = SignedRecord(previous, source);
            }
            journal[id] = SignedRecord(key, plan);
            string json = journal.ToString(Formatting.None);
            if (Encoding.UTF8.GetByteCount(json) > 1024 * 1024)
                AddressablesState.Fail("prefab_evidence_unavailable", "The reconciliation journal exceeds its size bound.");
            GuardedFileSystem.WriteAllTextAtomic(EvidencePath, json);
        }

        private static JObject Difference(string field, JToken expected, JToken observed, string detail) =>
            new JObject
            {
                ["field"] = field,
                ["expected"] = expected,
                ["observed"] = observed,
                ["detail"] = detail,
            };

        private static string StableRevision(AddressablesState state)
        {
            // Native object instance IDs legitimately change after reopen. Keep persistent GUID,
            // local file ID, provider, target, entry metadata, and dependency digests in the comparison.
            var document = (JObject)state.Document.DeepClone();
            ((JObject)document["settings"]).Remove("instance_id");
            foreach (var group in document["groups"])
            {
                ((JObject)group["asset"]).Remove("instance_id");
                foreach (var schema in group["schemas"])
                    ((JObject)schema["asset"]).Remove("instance_id");
            }
            return AddressablesState.Hash(document.ToString(Formatting.None), "addr-persist1:");
        }

        private static ErrorResponse Unavailable() =>
            new ErrorResponse(
                "prefab_checkpoint_unavailable",
                new { state = "unknown", message = "No active checkpoint in this Editor. Reconcile the original operation with its receipt owner." }
            );

        private static object Recover(JObject request, string key, string action, Plan active)
        {
            bool approving = action == "replan_prefab";
            string previous = approving ? HexField(request, DeclaredActionContracts.PrefabPreviousCheckpoint) : key;
            string expected = approving ? HexField(request, DeclaredActionContracts.PrefabExpectedObservation) : null;
            if (approving && previous == key)
                AddressablesState.Fail("prefab_intent_conflict", "Approval requires a fresh operation ID.");
            var record = ReadJournal()[AddressablesState.Hash(previous, "")];
            Plan prior = VerifyEvidence(previous, record);
            if (prior == null || prior.stableRevision == null || prior.settingsGuid == null || prior.settingsFiles == null)
                return new ErrorResponse(
                    "prefab_evidence_unavailable",
                    new
                    {
                        state = "unknown",
                        message = "No complete, unexpired evidence for this receipt owner, project and server. Inspect the original assets.",
                    }
                );
            if (prior.session == CommandResumeState.SessionId)
                return Report(previous, action, prior, "blocked", "This checkpoint belongs to the current Editor. Use prefab_status.");
            if (approving && active != null)
            {
                if (active.approvedObservation != expected || active.approvedSource != AddressablesState.Hash(previous, ""))
                    AddressablesState.Fail("prefab_intent_conflict", "This operation ID already belongs to a different plan.");
                return Handle(new JObject { ["action"] = "prefab_status", [DeclaredActionContracts.PrefabCheckpoint] = key }, "prefab_status");
            }
            if (approving && ReadEvidence(key) != null)
                AddressablesState.Fail("prefab_intent_conflict", "Approval requires an unused operation ID.");

            var observation = new JObject
            {
                ["recorded_phase"] = prior.phase,
                ["recorded_editor_session_id"] = prior.session,
                ["observing_editor_session_id"] = CommandResumeState.SessionId,
                ["expected_prefab_guid"] = prior.guid,
                ["expected_prefab_hash"] = prior.hash,
                ["expected_settings_guid"] = prior.settingsGuid,
                ["expected_settings_revision"] = prior.stableRevision,
                ["replan_available"] = false,
                ["continuation_claimed"] = prior.successor != null,
            };
            bool unsettled = prior.phase == "creating" || prior.phase == "registering";
            try
            {
                string path = PrefabPath(prior.intent, "prefab_path");
                string guid = AssetDatabase.AssetPathToGUID(path);
                observation["observed_prefab_guid"] = guid;
                if (!string.IsNullOrEmpty(guid) && AssetDatabase.LoadAssetAtPath<GameObject>(path) != null)
                    observation["observed_prefab_hash"] = Hash(path);
                var settings = AddressablesState.Capture(prior.intent);
                observation["observed_settings_guid"] = AssetDatabase.AssetPathToGUID((string)prior.intent["settings_path"]);
                observation["observed_settings_revision"] = StableRevision(settings);
                var entries = settings.Document["entries"].Where(row => (string)row["guid"] == guid).ToArray();
                observation["observed_entry_count"] = entries.Length;
                var entry = entries.FirstOrDefault();
                observation["observed_entry_group_guid"] = entry?["group_guid"];
                observation["observed_entry_address"] = entry?["address"];
                if (unsettled)
                    return Report(previous, action, prior, "unknown", "The original native step has no confirmed settlement.", reconciliation: observation);
                if (prior.phase == "cancelled")
                    return Report(
                        previous,
                        action,
                        prior,
                        approving ? "blocked" : "cancelled",
                        "The original operation was cancelled.",
                        reconciliation: observation
                    );
                var conflict = Conflict(prior, settings, (string)observation["observed_settings_revision"]);
                if (
                    conflict == null
                    && settings.Groups.TryGetValue((string)prior.intent["group_guid"], out var group)
                    && EditorUtility.IsDirty((UnityEngine.Object)group)
                )
                    conflict = Difference("group_dirty", false, true, "Target group has unsaved edits.");
                if (conflict != null)
                    return Report(previous, action, prior, "conflict", (string)conflict["detail"], conflict, observation);
                if (prior.phase == "registered")
                    return Report(
                        previous,
                        action,
                        prior,
                        approving ? "blocked" : "registered",
                        "Registration already completed.",
                        reconciliation: observation
                    );
                if (prior.phase != "created" && prior.phase != "planned")
                    return Report(previous, action, prior, "unknown", "No confirmed continuation exists for this phase.", reconciliation: observation);
                if (prior.successor != null)
                    return Report(
                        previous,
                        action,
                        prior,
                        approving ? "blocked" : prior.phase,
                        "An approved continuation already owns this work. Inspect its operation ID.",
                        reconciliation: observation
                    );
                if (prior.phase == "created" && (string)observation["observed_prefab_hash"] != prior.hash)
                    return Report(previous, action, prior, "conflict", "The created prefab could not be verified.", reconciliation: observation);

                string digest = Signature(
                    previous,
                    "observe.v1",
                    prior.session + "\n" + prior.phase + "\n" + CommandResumeState.SessionId + "\n" + settings.Revision + "\n" + prior.hash
                );
                observation["replan_available"] = true;
                observation["observation"] = digest;
                if (!approving)
                    return Report(previous, action, prior, prior.phase, reconciliation: observation);
                if (digest != expected)
                    return Report(
                        previous,
                        action,
                        prior,
                        "conflict",
                        "Approval does not match the current observation.",
                        Difference("observation", expected, digest, "Observe again before issuing a new approval."),
                        observation
                    );

                // Only the new ID obtains a current plan. The prior journal and old SessionState
                // checkpoint cannot authorize an edit. A confirmed copy remains a dependency result.
                prior.session = CommandResumeState.SessionId;
                prior.expires = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 7200;
                prior.revision = settings.Revision;
                prior.approvedObservation = digest;
                prior.approvedSource = AddressablesState.Hash(previous, "");
                Write(key, prior, previous, (string)record["signature"]);
                observation["replan_available"] = false;
                observation["continuation_claimed"] = true;
                return Report(key, action, prior, prior.phase, reconciliation: observation);
            }
            catch (AddressablesException error)
            {
                return Report(previous, action, prior, unsettled ? "unknown" : "conflict", error.Code + ": " + error.Message, reconciliation: observation);
            }
            catch (IOException)
            {
                return Report(
                    previous,
                    action,
                    prior,
                    unsettled ? "unknown" : "conflict",
                    "A recorded asset or metadata file could not be read.",
                    reconciliation: observation
                );
            }
        }
    }
}
