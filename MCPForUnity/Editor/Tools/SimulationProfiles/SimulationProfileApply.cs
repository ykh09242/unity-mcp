using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.SimulationProfiles
{
    internal static class SimulationProfileApply
    {
        // Owned native fault fixtures inject an exception before a real setter; no public request can set this hook.
        internal static Action<string> BeforeWrite = null;

        internal static object Apply(JObject p)
        {
            SimulationSettings.StableMode();
            JObject profile = SimulationProfileStore.Profile(p);
            if (SimulationProfileStore.Required(p, "profile_revision") != (string)profile["revision"])
                return new ErrorResponse("stale_profile: inspect the named profile again before applying.");
            var review = new SimulationProfileReview(profile);
            if (!review.Applicable)
                return new ErrorResponse("Profile has inapplicable or invalid settings; no writes attempted.", review.Report());
            if (SimulationProfileStore.Required(p, "current_revision") != review.CurrentRevision)
                return new ErrorResponse("stale_current: validate again; selected current settings or owners changed. No writes attempted.");
            if (SimulationProfileStore.Receipts.Count >= 64)
                throw new InvalidOperationException("Simulation receipt capacity reached. Delete a completed owned profile/receipt group explicitly.");
            string id = Guid.NewGuid().ToString("N");
            var outcomes = new JArray();
            foreach (string key in SimulationSettings.OrderedKeys(review.Desired))
            {
                SimulationSetting setting = SimulationSettings.Get(key);
                outcomes.Add(
                    new JObject
                    {
                        ["key"] = key,
                        ["domain"] = setting.Domain,
                        ["before"] = review.Current[key].DeepClone(),
                        ["desired"] = review.Desired[key].DeepClone(),
                        ["owner"] = review.Owners[key],
                        ["status"] = setting.Equal(review.Current[key], review.Desired[key]) ? "unchanged" : "pending",
                    }
                );
            }
            JObject receipt = new JObject
            {
                ["receipt_id"] = id,
                ["name"] = profile["name"],
                ["profile_revision"] = profile["revision"],
                ["state"] = "applying",
                ["is_playing"] = EditorApplication.isPlaying,
                ["editor_session"] = SimulationProfileStore.SessionId,
                ["created_utc"] = DateTime.UtcNow.ToString("O"),
                ["outcomes"] = outcomes,
                ["global_transaction"] = false,
                ["limitations"] =
                    "Confirmed settings can be conditionally restored while the same owner and observed applied value remain. Matching values cannot prove writer ownership. Native callbacks/side effects are not rolled back; interrupted or uncertain writes are never overwritten automatically.",
            };
            SimulationProfileStore.Receipts[id] = receipt;
            SimulationProfileStore.Save(); // Durable intent precedes every global setter.
            foreach (JObject row in outcomes.OfType<JObject>().Where(row => (string)row["status"] == "pending"))
            {
                string key = (string)row["key"];
                SimulationSetting setting = SimulationSettings.Get(key);
                try
                {
                    SimulationSettings.StableMode();
                    BeforeWrite?.Invoke(key);
                    if (setting.OwnerIdentity() != (string)row["owner"] || !setting.Equal(setting.Read(), row["before"]))
                        throw new InvalidOperationException("External setting/owner drift before write; preserved without applying this setting.");
                    row["status"] = "in_flight";
                    SimulationProfileStore.Save();
                    setting.Write(row["desired"]);
                    JToken observed = setting.Read();
                    row["observed"] = observed;
                    if (!setting.Equal(observed, row["desired"]))
                        throw new InvalidOperationException("Native readback differs from the requested value; this setting's ownership is uncertain.");
                    row["status"] = "applied";
                    SimulationProfileStore.Save();
                }
                catch (Exception ex)
                {
                    row["status"] =
                        (string)row["status"] == "in_flight" ? "uncertain_after_exception"
                        : (string)row["status"] == "applied" ? "applied_persistence_uncertain"
                        : "failed_before_write";
                    row["error"] = Detail(ex);
                    receipt["state"] = outcomes.Any(r =>
                        (string)r["status"] == "applied"
                        || (string)r["status"] == "applied_persistence_uncertain"
                        || (string)r["status"] == "uncertain_after_exception"
                    )
                        ? "partial"
                        : "failed";
                    Finish(receipt);
                    return new ErrorResponse(
                        "Simulation profile application stopped; inspect retained outcomes before restoring confirmed changes.",
                        receipt.DeepClone()
                    );
                }
            }
            receipt["state"] = "applied";
            if (!Finish(receipt))
                return new ErrorResponse(
                    "Native application was observed but receipt persistence failed; inspect outcomes before retrying.",
                    receipt.DeepClone()
                );
            return new SuccessResponse("Simulation profile applied with native readback; no cross-domain transaction is claimed.", receipt.DeepClone());
        }

        internal static object Restore(JObject p)
        {
            SimulationSettings.StableMode();
            JObject receipt = SimulationProfileStore.Receipt(p);
            if ((string)receipt["editor_session"] != SimulationProfileStore.SessionId)
                return new ErrorResponse(
                    "Receipt belongs to an earlier editor process. Inspect it as evidence; no settings were restored.",
                    receipt.DeepClone()
                );
            if ((bool?)receipt["is_playing"] != EditorApplication.isPlaying)
                return new ErrorResponse("Receipt belongs to a different Play/Edit context. No settings were restored.", receipt.DeepClone());
            var outcomes = (JArray)receipt["outcomes"];
            receipt["state"] = "restoring";
            SimulationProfileStore.Save();
            bool failed = false;
            bool preserved = outcomes
                .OfType<JObject>()
                .Any(row =>
                {
                    string status = (string)row["status"] ?? "";
                    return status.StartsWith("uncertain_", StringComparison.Ordinal)
                        || status == "applied_persistence_uncertain"
                        || status == "external_change_preserved"
                        || status == "domain_or_owner_unavailable_preserved";
                });
            foreach (JObject row in outcomes.OfType<JObject>().Where(r => (string)r["status"] == "applied"))
            {
                string key = (string)row["key"];
                try
                {
                    SimulationSetting setting = SimulationSettings.Get(key);
                    if (setting.Availability() != null || setting.OwnerIdentity() != (string)row["owner"])
                    {
                        row["status"] = "domain_or_owner_unavailable_preserved";
                        preserved = true;
                    }
                    else if (!setting.Equal(setting.Read(), row["observed"]))
                    {
                        row["status"] = "external_change_preserved";
                        preserved = true;
                    }
                    else
                    {
                        SimulationSettings.StableMode();
                        BeforeWrite?.Invoke(key);
                        // Recheck after callbacks/fault seam: restore must not overwrite an interfering writer.
                        if (setting.OwnerIdentity() != (string)row["owner"] || !setting.Equal(setting.Read(), row["observed"]))
                        {
                            row["status"] = "external_change_preserved";
                            preserved = true;
                        }
                        else
                        {
                            row["status"] = "restore_in_flight";
                            SimulationProfileStore.Save();
                            setting.Write(row["before"]);
                            row["restored_observed"] = setting.Read();
                            if (!setting.Equal(row["restored_observed"], row["before"]))
                                throw new InvalidOperationException("Restoration readback differed; ownership is uncertain.");
                            row["status"] = "restored";
                        }
                    }
                    SimulationProfileStore.Save();
                }
                catch (Exception ex)
                {
                    if ((string)row["status"] == "restore_in_flight")
                        row["status"] = "uncertain_restore";
                    row["restore_error"] = Detail(ex);
                    failed = true;
                    break;
                }
            }
            receipt["state"] =
                failed ? "partial_restore"
                : preserved ? "restored_with_preserved_changes"
                : "restored";
            bool persisted = Finish(receipt);
            if (!persisted)
                return new ErrorResponse(
                    "Native restoration outcomes were observed but receipt persistence failed; inspect outcomes before retrying.",
                    receipt.DeepClone()
                );
            return failed
                ? (object)new ErrorResponse("Conditional restoration stopped; inspect confirmed, preserved and uncertain outcomes.", receipt.DeepClone())
                : new SuccessResponse("Conditional restoration processed; externally changed and uncertain settings are preserved.", receipt.DeepClone());
        }

        private static string Detail(Exception ex)
        {
            string detail = ex.InnerException?.Message ?? ex.Message;
            return detail.Length <= 256 ? detail : detail.Substring(0, 256);
        }

        private static bool Finish(JObject receipt)
        {
            receipt["updated_utc"] = DateTime.UtcNow.ToString("O");
            try
            {
                receipt["receipt_persisted"] = true;
                SimulationProfileStore.Receipts[(string)receipt["receipt_id"]] = receipt;
                SimulationProfileStore.Save();
                return true;
            }
            catch (Exception ex)
            {
                receipt["receipt_persisted"] = false;
                receipt["persistence_error"] = Detail(ex);
                return false;
            }
        }
    }
}
