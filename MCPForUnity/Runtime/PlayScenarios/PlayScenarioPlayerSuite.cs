using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Frozen ordered definitions share one payload, with a fresh process for every child.</summary>
    public static class PlayScenarioPlayerSuite
    {
        public static JObject Create(JObject request)
        {
            if (request == null || Encoding.UTF8.GetByteCount(request.ToString(Formatting.None)) > PlayScenarioPlayerBundle.Limit)
                throw new ArgumentException("Player suite request exceeds its manifest byte budget.");
            PlayScenarioPlayerBundle.Fields(
                request,
                "name",
                "children",
                "unity_version",
                "package_version",
                "build_id",
                "build_source_revision",
                "failure_policy",
                "isolation"
            );
            string name = PlayScenarioPlayerBundle.Text(request, "name", 64);
            PlayScenarioDefinition.ValidateName(name);
            string isolation = request.Property("isolation") == null ? "fresh-process" : PlayScenarioPlayerBundle.Text(request, "isolation", 32);
            if (isolation != "fresh-process")
                throw new ArgumentException("Player suites require fresh-process isolation; shared-process reset is unsupported.");
            string policy = request.Property("failure_policy") == null ? "stop" : PlayScenarioPlayerBundle.Text(request, "failure_policy", 8);
            if (policy != "stop" && policy != "continue")
                throw new ArgumentException("Player suite failure_policy must be stop or continue.");
            if (!(request["children"] is JArray inputs) || inputs.Count < 1 || inputs.Count > PlayScenarioDefinition.MaximumSuiteScenarios)
                throw new ArgumentException("Player suite requires 1-64 distinct frozen children.");
            string buildId = request.Property("build_id") == null ? Guid.NewGuid().ToString("N") : PlayScenarioPlayerBundle.Text(request, "build_id", 32);
            string revision = PlayScenarioPlayerBundle.OptionalRevision(request, "build_source_revision");
            var children = new JArray();
            var scenes = new SortedSet<string>(StringComparer.Ordinal);
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken token in inputs)
            {
                if (!(token is JObject input) || !(input["definition"] is JObject definitionJson))
                    throw new ArgumentException("Every Player suite child requires a definition object.");
                PlayScenarioPlayerBundle.Fields(input, "definition", "dataset");
                string dataset = input.Property("dataset") == null ? null : PlayScenarioPlayerBundle.Text(input, "dataset", 64);
                PlayScenarioDefinition definition = PlayScenarioParameters.Materialize(PlayScenarioDefinition.Parse(definitionJson), dataset);
                if (!names.Add(definition.Name))
                    throw new ArgumentException("Player suite frozen child names must be unique.");
                JObject single = PlayScenarioPlayerBundle.Create(
                    definition,
                    PlayScenarioPlayerBundle.Text(request, "unity_version", 128),
                    PlayScenarioPlayerBundle.Text(request, "package_version", 128),
                    buildId,
                    revision
                );
                children.Add(
                    new JObject
                    {
                        ["scenario_name"] = single["scenario_name"],
                        ["definition_hash"] = single["definition_hash"],
                        ["definition_json"] = single["definition_json"],
                        ["definition"] = single["definition"],
                    }
                );
                scenes.UnionWith(PlayScenarioPlayerBundle.Scenes(definition));
            }
            if (scenes.Count > 64)
                throw new ArgumentException("Player suite scene union exceeds 64 scenes.");
            var manifest = new JObject
            {
                ["schema_version"] = 3,
                ["build_id"] = buildId,
                ["build_source_revision"] = revision,
                ["suite_name"] = name,
                ["failure_policy"] = policy,
                ["isolation"] = isolation,
                ["children"] = children,
                ["scene_paths"] = new JArray(scenes),
                ["executable"] = PlayScenarioPlayerBundle.ExecutableName,
                ["unity_version"] = request["unity_version"],
                ["package_version"] = request["package_version"],
            };
            manifest["suite_hash"] = Hash(manifest);
            PlayScenarioPlayerBundle.Parse(manifest.ToString(Formatting.None));
            return manifest;
        }

        internal static PlayScenarioPlayerBundle Parse(JObject value)
        {
            PlayScenarioPlayerBundle.Fields(
                value,
                "schema_version",
                "build_id",
                "build_source_revision",
                "suite_name",
                "failure_policy",
                "isolation",
                "children",
                "scene_paths",
                "executable",
                "unity_version",
                "package_version",
                "suite_hash",
                "build_target",
                "scripting_backend",
                "payload_inventory",
                "payload_inventory_json",
                "payload_hash"
            );
            PlayScenarioDefinition.ValidateName(PlayScenarioPlayerBundle.Text(value, "suite_name", 64));
            string policy = PlayScenarioPlayerBundle.Text(value, "failure_policy", 8);
            if (PlayScenarioPlayerBundle.Text(value, "isolation", 32) != "fresh-process" || (policy != "stop" && policy != "continue"))
                throw new ArgumentException("Player suites require fresh-process isolation and stop/continue failure policy.");
            if (!(value["children"] is JArray inputs) || inputs.Count < 1 || inputs.Count > PlayScenarioDefinition.MaximumSuiteScenarios)
                throw new ArgumentException("Player suite requires 1-64 children.");
            if (PlayScenarioPlayerBundle.Text(value, "suite_hash", 64) != Hash(value))
                throw new ArgumentException("Player suite frozen identity does not match.");
            PlayScenarioPlayerPayload.ValidateManifest(value);
            var children = new List<PlayScenarioPlayerBundle>();
            var names = new HashSet<string>(StringComparer.Ordinal);
            var scenes = new SortedSet<string>(StringComparer.Ordinal);
            foreach (JToken token in inputs)
            {
                if (!(token is JObject child) || !(child["definition"] is JObject definitionJson))
                    throw new ArgumentException("Frozen Player suite child must be an object.");
                PlayScenarioPlayerBundle.Fields(child, "scenario_name", "definition_hash", "definition_json", "definition");
                var single = (JObject)child.DeepClone();
                foreach (
                    string key in new[]
                    {
                        "build_id",
                        "build_source_revision",
                        "executable",
                        "unity_version",
                        "package_version",
                        "build_target",
                        "scripting_backend",
                    }
                )
                    if (value.Property(key) != null)
                        single[key] = value[key].DeepClone();
                single["schema_version"] = 2;
                single["scene_paths"] = new JArray(PlayScenarioPlayerBundle.Scenes(PlayScenarioDefinition.Parse(definitionJson)));
                PlayScenarioPlayerBundle parsed = PlayScenarioPlayerBundle.Parse(single.ToString(Formatting.None));
                if (!names.Add(parsed.Definition.Name))
                    throw new ArgumentException("Frozen Player suite child names must be unique.");
                scenes.UnionWith(parsed.ScenePaths);
                children.Add(parsed);
            }
            if (
                !(value["scene_paths"] is JArray paths)
                || scenes.Count > 64
                || !paths.SequenceEqual(scenes.Select(scene => (JToken)new JValue(scene)), JToken.EqualityComparer)
            )
                throw new ArgumentException("Player suite scene inventory does not match the exact frozen union.");
            return PlayScenarioPlayerBundle.FromSuite(value, children.AsReadOnly(), scenes.ToArray());
        }

        private static string Hash(JObject value)
        {
            if (!(value["children"] is JArray children))
                throw new ArgumentException("Player suite children must be an array.");
            var identity = new StringBuilder("MCPForUnity.PlayerSuite/3\n");
            AppendIdentity(identity, PlayScenarioPlayerBundle.Text(value, "suite_name", 64));
            AppendIdentity(identity, PlayScenarioPlayerBundle.Text(value, "failure_policy", 8));
            AppendIdentity(identity, PlayScenarioPlayerBundle.Text(value, "isolation", 32));
            AppendIdentity(identity, children.Count.ToString(CultureInfo.InvariantCulture));
            foreach (JToken token in children)
            {
                if (!(token is JObject child))
                    throw new ArgumentException("Frozen Player suite child must be an object.");
                AppendIdentity(identity, PlayScenarioPlayerBundle.Text(child, "scenario_name", 64));
                AppendIdentity(identity, PlayScenarioPlayerBundle.Text(child, "definition_hash", 64));
                AppendIdentity(identity, PlayScenarioPlayerBundle.Text(child, "definition_json", PlayScenarioDefinition.MaximumDefinitionBytes));
            }
            return PlayScenarioPlayerPayload.HashText(identity.ToString());
        }

        private static void AppendIdentity(StringBuilder identity, string value)
        {
            // Byte lengths, invariant decimal digits and exact UTF8 strings define the cross-language contract.
            identity.Append(Encoding.UTF8.GetByteCount(value).ToString(CultureInfo.InvariantCulture)).Append(':').Append(value);
        }
    }
}
