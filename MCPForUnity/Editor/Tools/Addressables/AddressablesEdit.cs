using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Addressables
{
    internal static class AddressablesEdit
    {
        internal static object Apply(AddressablesState before, JObject request)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                AddressablesState.Fail("addressables_editor_unavailable", "Authoring requires stable Edit Mode.");
            string expected = AddressablesState.Text(request["expected_revision"], "expected_revision", 80);
            if (expected != before.Revision)
                AddressablesState.Fail("stale_addressables_revision", "Settings changed; inspect again before authoring.");
            string action = request.Value<string>("action");
            JObject intended = (JObject)before.Document.DeepClone();
            object entry = null,
                group = null,
                oldGroup = null;
            string address = null,
                profileId = null,
                variableName = null,
                value = null;
            if (action == "set_profile_value")
            {
                profileId = AddressablesState.Text(request["profile_id"], "profile_id", 64);
                variableName = AddressablesState.Text(request["variable_name"], "variable_name", 128);
                value = AddressablesState.Text(request["value"], "value", 2048, true);
                JObject profile = ((JArray)intended["profiles"]).OfType<JObject>().SingleOrDefault(p => p.Value<string>("id") == profileId);
                JObject variable = ((JArray)intended["variables"]).OfType<JObject>().SingleOrDefault(p => p.Value<string>("name") == variableName);
                if (profile == null || variable == null)
                    AddressablesState.Fail("addressables_profile_not_found", "Exact existing profile and variable are required.");
                profile["values"][variable.Value<string>("id")] = value;
                var priorIssues = new JArray();
                AddressablesPreflight.ProfileIssues(before, profileId, priorIssues);
                var proposed = new AddressablesState { Document = intended };
                var nextIssues = new JArray();
                AddressablesPreflight.ProfileIssues(proposed, profileId, nextIssues);
                if (nextIssues.Any(next => !priorIssues.Any(prior => JToken.DeepEquals(prior, next))))
                    AddressablesState.Fail("invalid_profile_value", "The proposed profile value introduces unresolved tokens or a cycle.");
            }
            else
            {
                string guid = AddressablesState.Text(request["entry_guid"], "entry_guid", 32);
                if (!before.Entries.TryGetValue(guid, out entry))
                    AddressablesState.Fail("addressables_entry_not_found", "Exact existing explicit entry is required.");
                oldGroup = AddressablesState.Get(entry, "parentGroup");
                if ((bool)AddressablesState.Get(entry, "ReadOnly") || (bool)AddressablesState.Get(oldGroup, "ReadOnly"))
                    AddressablesState.Fail("addressables_read_only", "Read-only entries or groups cannot be authored.");
                JObject row = ((JArray)intended["entries"]).OfType<JObject>().Single(e => e.Value<string>("guid") == guid);
                if (action == "set_address")
                {
                    address = AddressablesState.Text(request["address"], "address");
                    if (address.Contains('[') && address.Contains(']'))
                        AddressablesState.Fail("invalid_addressables_request", "Addressables2.7.6 addresses cannot contain both '[' and ']'.");
                    if (((JArray)intended["entries"]).Any(e => e.Value<string>("guid") != guid && e.Value<string>("address") == address))
                        AddressablesState.Fail("duplicate_address", "The requested address is already assigned to another explicit entry.");
                    row["address"] = address;
                }
                else
                {
                    string groupGuid = AddressablesState.Text(request["group_guid"], "group_guid", 32);
                    if (!before.Groups.TryGetValue(groupGuid, out group))
                        AddressablesState.Fail("addressables_group_not_found", "Exact existing target group is required.");
                    if ((bool)AddressablesState.Get(group, "ReadOnly"))
                        AddressablesState.Fail("addressables_read_only", "Target group is read-only.");
                    row["group_guid"] = groupGuid;
                    foreach (JObject g in (JArray)intended["groups"])
                    {
                        var ids = ((JArray)g["entry_guids"]).Values<string>().Where(id => id != guid).ToList();
                        if (g.Value<string>("guid") == groupGuid)
                            ids.Add(guid);
                        g["entry_guids"] = new JArray(ids.OrderBy(id => id, StringComparer.Ordinal));
                    }
                }
            }
            if (JToken.DeepEquals(before.Document, intended))
                return AddressablesState.Bounded(
                    new MCPForUnity.Editor.Helpers.SuccessResponse(
                        "Addressables unchanged",
                        new
                        {
                            revision = before.Revision,
                            changed = false,
                            content_build_started = false,
                        }
                    )
                );
            AddressablesState fresh = AddressablesState.Capture(request);
            if (fresh.Revision != expected || fresh.Settings != before.Settings)
                AddressablesState.Fail("stale_addressables_revision", "Settings or native identity changed before Undo.");
            int undo = Undo.GetCurrentGroup();
            try
            {
                Undo.IncrementCurrentGroup();
                undo = Undo.GetCurrentGroup();
                var targets = new[] { before.Settings, oldGroup as UnityEngine.Object, group as UnityEngine.Object }.Where(v => v != null).Distinct().ToArray();
                Undo.RegisterCompleteObjectUndo(targets, "Edit Addressables metadata");
                if (action == "set_address")
                    AddressablesState.Call(entry, "SetAddress", address, true);
                else if (action == "move_entry")
                    AddressablesState.Call(before.Settings, "CreateOrMoveEntry", (string)AddressablesState.Get(entry, "guid"), group, false, true);
                else
                    AddressablesState.Call(before.ProfilesObject, "SetValue", profileId, variableName, value);
                AddressablesState after = AddressablesState.Capture(request);
                if (!JToken.DeepEquals(intended, after.Document))
                    AddressablesState.Fail(
                        "addressables_readback_conflict",
                        "Native readback changed state beyond the explicit metadata edit. Inspect current state; no unconditional rollback was attempted."
                    );
                foreach (UnityEngine.Object target in targets)
                    EditorUtility.SetDirty(target);
                Undo.CollapseUndoOperations(undo);
                return AddressablesState.Bounded(
                    new MCPForUnity.Editor.Helpers.SuccessResponse(
                        "Addressables metadata edited",
                        new
                        {
                            revision = after.Revision,
                            changed = true,
                            undo_registered = true,
                            content_build_started = false,
                            save_behavior = "Only edited assets are dirty; no global SaveAssets call.",
                            recovery = "Native Undo is registered. A callback/readback conflict is reported without overwriting another writer.",
                        }
                    )
                );
            }
            finally
            {
                Undo.CollapseUndoOperations(undo);
            }
        }
    }
}
