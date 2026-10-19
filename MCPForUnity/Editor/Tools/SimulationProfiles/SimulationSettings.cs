using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.SimulationProfiles
{
    internal static class SimulationSettings
    {
        internal static readonly string[] Domains = { "time", "physics3d", "physics2d", "audio", "input" };
        private static readonly SimulationSetting[] Core =
        {
            new SimulationSetting("time.timeScale", typeof(Time), "timeScale", 0, 100),
            new SimulationSetting("time.fixedDeltaTime", typeof(Time), "fixedDeltaTime", 0.000001, 1),
            new SimulationSetting("time.maximumDeltaTime", typeof(Time), "maximumDeltaTime", 0.000001, 10),
            new SimulationSetting("physics3d.gravity", typeof(UnityEngine.Physics), "gravity", -100000, 100000),
            new SimulationSetting("physics3d.defaultSolverIterations", typeof(UnityEngine.Physics), "defaultSolverIterations", 1, 255),
            new SimulationSetting("physics3d.queriesHitTriggers", typeof(UnityEngine.Physics), "queriesHitTriggers", 0, 1),
            new SimulationSetting("physics3d.queriesHitBackfaces", typeof(UnityEngine.Physics), "queriesHitBackfaces", 0, 1),
            new SimulationSetting("physics2d.gravity", typeof(Physics2D), "gravity", -100000, 100000),
            new SimulationSetting("physics2d.velocityIterations", typeof(Physics2D), "velocityIterations", 1, 255),
            new SimulationSetting("physics2d.positionIterations", typeof(Physics2D), "positionIterations", 1, 255),
            new SimulationSetting("physics2d.queriesHitTriggers", typeof(Physics2D), "queriesHitTriggers", 0, 1),
            new SimulationSetting("physics2d.queriesStartInColliders", typeof(Physics2D), "queriesStartInColliders", 0, 1),
            new SimulationSetting("audio.volume", typeof(AudioListener), "volume", 0, 1),
            new SimulationSetting("audio.pause", typeof(AudioListener), "pause", 0, 1),
        };

        // Input descriptors resolve current settings objects per request, not captured stale references.
        internal static SimulationSetting[] All() => Core.Concat(SimulationInputSettings.Create()).ToArray();

        internal static SimulationSetting Get(string key) =>
            All().FirstOrDefault(s => s.Key == key) ?? throw new ArgumentException("Unsupported simulation setting: " + key);

        internal static JObject Capabilities()
        {
            JObject domains = new JObject();
            SimulationSetting[] all = All();
            foreach (string domain in Domains)
            {
                var settings = new JArray();
                foreach (SimulationSetting setting in all.Where(s => s.Domain == domain))
                {
                    string reason;
                    try
                    {
                        reason = setting.Availability();
                    }
                    catch (Exception ex)
                    {
                        reason = ex.InnerException?.Message ?? ex.Message;
                    }
                    settings.Add(
                        new JObject
                        {
                            ["key"] = setting.Key,
                            ["applicable"] = reason == null,
                            ["reason"] = reason,
                        }
                    );
                }
                domains[domain] = new JObject { ["applicable"] = settings.Any(s => (bool)s["applicable"]), ["settings"] = settings };
            }
            return new JObject
            {
                ["domains"] = domains,
                ["setting_limit"] = 32,
                ["profile_limit"] = 32,
                ["receipt_limit"] = 64,
                ["global_transaction"] = false,
                ["limitations"] =
                    "Settings are explicit process/project configuration observations. No simulation, input injection, listener audibility or global transaction is guaranteed. Undo is not a cross-domain rollback. Values do not imply persistence across entering/exiting Play Mode; settings assets are never saved automatically.",
            };
        }

        internal static JObject Capture(JToken requested)
        {
            string[] selected;
            if (requested == null)
                selected = Domains;
            else
            {
                if (!(requested is JArray array) || array.Count < 1 || array.Count > Domains.Length || array.Any(t => t.Type != JTokenType.String))
                    throw new ArgumentException("domains must contain 1..5 supported domain names.");
                selected = array.Values<string>().Distinct().ToArray();
                if (selected.Any(d => !Domains.Contains(d)))
                    throw new ArgumentException("Unknown simulation profile domain.");
            }
            JObject result = new JObject();
            foreach (SimulationSetting setting in All().Where(s => selected.Contains(s.Domain)))
            {
                if (setting.Availability() == null)
                    result[setting.Key] = setting.Read();
            }
            foreach (string domain in selected)
                if (!result.Properties().Any(p => Get(p.Name).Domain == domain) && requested != null)
                    throw new InvalidOperationException("Domain is unavailable: " + domain + ". Inspect capabilities; nothing was changed.");
            return result;
        }

        internal static JObject Normalize(JObject settings)
        {
            if (settings == null || settings.Count < 1 || settings.Count > 32)
                throw new ArgumentException("A profile requires 1..32 supported settings.");
            var normalized = new JObject();
            foreach (JProperty value in settings.Properties())
                normalized[value.Name] = Get(value.Name).Normalize(value.Value);
            CheckRelations(normalized);
            return normalized;
        }

        internal static void CheckRelations(JObject settings)
        {
            if (
                settings["time.fixedDeltaTime"] != null
                && settings["time.maximumDeltaTime"] != null
                && (float)settings["time.fixedDeltaTime"] > (float)settings["time.maximumDeltaTime"]
            )
                throw new ArgumentException("time.maximumDeltaTime must be at least time.fixedDeltaTime.");
            if (
                settings["input.system.defaultDeadzoneMin"] != null
                && settings["input.system.defaultDeadzoneMax"] != null
                && (float)settings["input.system.defaultDeadzoneMin"] >= (float)settings["input.system.defaultDeadzoneMax"]
            )
                throw new ArgumentException("Input System deadzone minimum must be less than maximum.");
        }

        internal static JObject Current(JObject desired)
        {
            var current = new JObject();
            foreach (SimulationSetting setting in All().Where(s => desired[s.Key] != null))
                current[setting.Key] = setting.Read();
            return current;
        }

        internal static IEnumerable<string> OrderedKeys(JObject desired) => All().Select(s => s.Key).Where(key => desired[key] != null);

        internal static void StableMode()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode && !EditorApplication.isPlaying)
                throw new InvalidOperationException("Simulation profile writes are refused during a Play Mode transition.");
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
                throw new InvalidOperationException("Wait for compilation/import before changing simulation profiles.");
        }
    }
}
