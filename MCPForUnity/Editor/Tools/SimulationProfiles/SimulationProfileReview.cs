using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.SimulationProfiles
{
    internal sealed class SimulationProfileReview
    {
        internal readonly JObject Desired = new JObject();
        internal readonly JObject Current = new JObject();
        internal readonly JObject Owners = new JObject();
        internal readonly JArray Errors = new JArray();
        internal readonly JArray Changes = new JArray();
        internal readonly JObject Profile;
        internal bool Applicable => Errors.Count == 0;
        internal string CurrentRevision =>
            SimulationProfileStore.Hash(
                new JObject
                {
                    ["values"] = Current,
                    ["owners"] = Owners,
                    ["is_playing"] = EditorApplication.isPlaying,
                    ["editor_session"] = SimulationProfileStore.SessionId,
                }
            );

        internal SimulationProfileReview(JObject profile)
        {
            Profile = profile;
            if (!(profile["settings"] is JObject settings) || settings.Count < 1 || settings.Count > 32)
                throw new ArgumentException("Stored profile settings are invalid or exceed the setting limit.");
            foreach (JProperty item in settings.Properties())
            {
                var row = new JObject { ["key"] = item.Name, ["desired"] = item.Value.DeepClone() };
                try
                {
                    SimulationSetting setting = SimulationSettings.Get(item.Name);
                    string reason = setting.Availability();
                    if (reason != null)
                        throw new InvalidOperationException(reason);
                    Desired[item.Name] = setting.Normalize(item.Value);
                    Current[item.Name] = setting.Read();
                    Owners[item.Name] = setting.OwnerIdentity();
                    row["domain"] = setting.Domain;
                    row["applicable"] = true;
                    row["current"] = Current[item.Name].DeepClone();
                    row["changed"] = !setting.Equal(Current[item.Name], Desired[item.Name]);
                }
                catch (Exception ex)
                {
                    row["applicable"] = false;
                    row["reason"] = ex.InnerException?.Message ?? ex.Message;
                    Errors.Add(item.Name + ": " + row["reason"]);
                }
                Changes.Add(row);
            }
            try
            {
                if (Applicable)
                    SimulationSettings.CheckRelations(Desired);
            }
            catch (ArgumentException ex)
            {
                Errors.Add(ex.Message);
            }
        }

        internal JObject Report() =>
            new JObject
            {
                ["name"] = Profile["name"],
                ["profile_revision"] = Profile["revision"],
                ["current_revision"] = CurrentRevision,
                ["applicable"] = Applicable,
                ["errors"] = Errors.DeepClone(),
                ["changes"] = Changes.DeepClone(),
                ["domains"] = SimulationSettings.Capabilities()["domains"],
                ["global_transaction"] = false,
                ["restore_condition"] =
                    "Only confirmed values still equal to the observed applied value and same setting owner are restored. Equal values cannot establish writer identity. Intervening changes are preserved.",
            };
    }
}
