using System;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.SimulationProfiles
{
    [McpForUnityTool("manage_simulation_profiles", AutoRegister = false, Group = "testing")]
    public static class ManageSimulationProfiles
    {
        public static object HandleCommand(JObject p)
        {
            if (p == null)
                return new ErrorResponse("Simulation profile parameters are required.");
            try
            {
                switch ((string)p["action"])
                {
                    case "capabilities":
                        return new SuccessResponse("Simulation profile applicability and explicit supported settings.", SimulationSettings.Capabilities());
                    case "capture":
                        return Capture(p);
                    case "list":
                        return List(p);
                    case "get":
                        return new SuccessResponse("Named simulation profile.", SimulationProfileStore.Profile(p).DeepClone());
                    case "diff":
                    case "validate":
                        return new SuccessResponse(
                            "Simulation profile validation and current-state diff.",
                            new SimulationProfileReview(SimulationProfileStore.Profile(p)).Report()
                        );
                    case "apply":
                        return SimulationProfileApply.Apply(p);
                    case "restore":
                        return SimulationProfileApply.Restore(p);
                    case "receipt":
                        return new SuccessResponse("Retained simulation profile outcomes.", SimulationProfileStore.Receipt(p).DeepClone());
                    case "delete":
                        return Delete(p);
                    default:
                        return new ErrorResponse(
                            "Unknown simulation profile action. Use capabilities, capture, list, get, diff, validate, apply, restore, receipt or delete."
                        );
                }
            }
            catch (TargetInvocationException ex)
            {
                return new ErrorResponse("Simulation setting API failed: " + (ex.InnerException?.Message ?? ex.Message));
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse(ex.Message);
            }
            catch (InvalidOperationException ex)
            {
                return new ErrorResponse(ex.Message);
            }
            catch (JsonException)
            {
                return new ErrorResponse("Simulation profile metadata is corrupt or malformed; no further writes attempted.");
            }
            catch (IOException)
            {
                return new ErrorResponse(
                    "Simulation profile storage is unavailable; inspect retained receipt state before retrying. No global transaction is claimed."
                );
            }
            catch (UnauthorizedAccessException)
            {
                return new ErrorResponse("Simulation profile storage access was refused; no further writes attempted.");
            }
        }

        private static object Capture(JObject p)
        {
            SimulationSettings.StableMode();
            string name = SimulationProfileStore.Name(p);
            JObject previous = (JObject)SimulationProfileStore.Profiles[name];
            if (previous != null && (string)p["profile_revision"] != (string)previous["revision"])
                return new ErrorResponse("stale_profile: replacing a named capture requires its current profile_revision.");
            if (previous == null && SimulationProfileStore.Profiles.Count >= 32)
                throw new InvalidOperationException("Simulation profile capacity reached (32). Delete an owned profile explicitly first.");
            JObject settings = SimulationSettings.Capture(p["domains"]);
            if (p["settings"] != null)
            {
                if (!(p["settings"] is JObject overrides) || overrides.Count > 32)
                    throw new ArgumentException("settings must be an object with at most 32 supported values.");
                foreach (JProperty item in overrides.Properties())
                {
                    if (settings[item.Name] == null)
                        throw new ArgumentException("Setting is outside captured/applicable domains: " + item.Name);
                    settings[item.Name] = SimulationSettings.Get(item.Name).Normalize(item.Value);
                }
            }
            settings = SimulationSettings.Normalize(settings);
            JObject profile = new JObject
            {
                ["name"] = name,
                ["settings"] = settings,
                ["domains"] = new JArray(settings.Properties().Select(s => SimulationSettings.Get(s.Name).Domain).Distinct()),
                ["created_utc"] = DateTime.UtcNow.ToString("O"),
                ["format"] = 1,
            };
            profile["revision"] = SimulationProfileStore.Hash(new JObject { ["name"] = name, ["settings"] = settings });
            SimulationProfileStore.Profiles[name] = profile;
            SimulationProfileStore.Save();
            return new SuccessResponse(
                "Named configuration captured. Optional replacements changed only this stored profile; no global settings were applied.",
                profile.DeepClone()
            );
        }

        private static object List(JObject p)
        {
            int offset = PageInteger(p, "offset", 0, 0, int.MaxValue);
            int limit = PageInteger(p, "limit", 20, 1, 32);
            JObject[] profiles = SimulationProfileStore
                .Profiles.Properties()
                .OrderBy(row => row.Name, StringComparer.Ordinal)
                .Select(row => (JObject)row.Value)
                .ToArray();
            return new SuccessResponse(
                "Named simulation profiles (project-local Library metadata).",
                new
                {
                    total = profiles.Length,
                    items = profiles
                        .Skip(offset)
                        .Take(limit)
                        .Select(profile => new
                        {
                            name = (string)profile["name"],
                            profile_revision = (string)profile["revision"],
                            domains = profile["domains"].DeepClone(),
                        })
                        .ToArray(),
                    next_offset = (long)offset + limit < profiles.Length ? (int?)(offset + limit) : null,
                }
            );
        }

        private static int PageInteger(JObject p, string key, int fallback, int minimum, int maximum)
        {
            if (p[key] == null)
                return fallback;
            if (p[key].Type != JTokenType.Integer || !long.TryParse(p[key].ToString(), out long number) || number < minimum || number > maximum)
                throw new ArgumentException(key + " must be an integer in the supported page range.");
            return (int)number;
        }

        private static object Delete(JObject p)
        {
            string name = SimulationProfileStore.Name(p);
            bool removed = SimulationProfileStore.Profiles.Remove(name);
            string[] receipts = SimulationProfileStore.Receipts.Properties().Where(row => (string)row.Value["name"] == name).Select(row => row.Name).ToArray();
            foreach (string id in receipts)
                SimulationProfileStore.Receipts.Remove(id);
            SimulationProfileStore.Save();
            return new SuccessResponse(
                "Named profile and its receipts deleted. This does not restore global settings; restore first when needed.",
                new
                {
                    name,
                    removed,
                    receipts_removed = receipts.Length,
                }
            );
        }
    }
}
