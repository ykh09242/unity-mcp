using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Finite scalar bindings only. Every effective case passes the ordinary scenario parser before storage.</summary>
    public static class PlayScenarioParameters
    {
        private static readonly string[] Metadata = { "parameters", "parameter_bindings", "datasets" };

        public static PlayScenarioDefinition Materialize(PlayScenarioDefinition definition, string dataset = null)
        {
            if (definition == null)
                throw new ArgumentNullException(nameof(definition));
            JObject source = JObject.FromObject(definition);
            Validate(source);
            if (dataset != null)
                PlayScenarioDefinition.ValidateName(dataset);
            JObject selected = null;
            if (dataset != null)
            {
                selected = (source["datasets"] as JArray)?.OfType<JObject>().SingleOrDefault(item => (string)item["name"] == dataset);
                if (selected == null)
                    throw new ArgumentException("Unknown dataset: " + dataset);
            }
            return PlayScenarioDefinition.Parse(Expand(source, selected));
        }

        internal static void Validate(JObject source)
        {
            if (Encoding.UTF8.GetByteCount(source.ToString(Formatting.None)) > PlayScenarioDefinition.MaximumDefinitionBytes)
                throw new ArgumentException("Scenario definition exceeds 1 MiB.");
            bool any = Metadata.Any(key => source.Property(key) != null);
            if (!any)
                return;
            if (!(source["parameters"] is JObject parameters) || parameters.Count < 1 || parameters.Count > 64)
                throw new ArgumentException("parameters must contain one to sixty-four declarations.");
            if (!(source["parameter_bindings"] is JArray bindings) || bindings.Count < 1 || bindings.Count > 256)
                throw new ArgumentException("parameter_bindings must contain one to 256 bindings.");
            foreach (JProperty parameter in parameters.Properties())
            {
                PlayScenarioDefinition.ValidateName(parameter.Name);
                if (!(parameter.Value is JObject declaration))
                    throw new ArgumentException("Parameter declaration must be an object.");
                PlayScenarioDefinition.Fields(declaration, "type", "default");
                ValidateValue(declaration, declaration["default"]);
            }
            var paths = new HashSet<string>(StringComparer.Ordinal);
            var used = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken item in bindings)
            {
                if (!(item is JObject binding))
                    throw new ArgumentException("Binding must be an object.");
                PlayScenarioDefinition.Fields(binding, "path", "parameter");
                string parameter = PlayScenarioDefinition.Text(binding, "parameter", 64);
                if (parameters.Property(parameter) == null)
                    throw new ArgumentException("Binding names an undeclared parameter.");
                string path = PlayScenarioDefinition.Text(binding, "path", 256);
                if (!paths.Add(path))
                    throw new ArgumentException("Binding paths must be unique.");
                Destination(source, path);
                used.Add(parameter);
            }
            if (used.Count != parameters.Count)
                throw new ArgumentException("Every parameter must have a binding.");
            PlayScenarioDefinition.Parse(Expand(source, null));
            if (source.Property("datasets") == null)
                return;
            if (!(source["datasets"] is JArray datasets) || datasets.Count < 1 || datasets.Count > 64)
                throw new ArgumentException("datasets must contain one to sixty-four cases.");
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken item in datasets)
            {
                if (!(item is JObject dataset))
                    throw new ArgumentException("Dataset must be an object.");
                PlayScenarioDefinition.Fields(dataset, "name", "values");
                string name = PlayScenarioDefinition.Text(dataset, "name", 64);
                PlayScenarioDefinition.ValidateName(name);
                if (!names.Add(name))
                    throw new ArgumentException("Dataset names must be unique.");
                if (!(dataset["values"] is JObject values) || values.Count > 64)
                    throw new ArgumentException("Dataset values must be a bounded object.");
                foreach (JProperty value in values.Properties())
                {
                    if (!(parameters[value.Name] is JObject declaration))
                        throw new ArgumentException("Dataset names an undeclared parameter.");
                    ValidateValue(declaration, value.Value);
                }
                PlayScenarioDefinition.Parse(Expand(source, dataset));
            }
        }

        private static void ValidateValue(JObject declaration, JToken value)
        {
            string type = PlayScenarioDefinition.Text(declaration, "type", 16);
            bool matches =
                value != null
                && (
                    type == "boolean" ? value.Type == JTokenType.Boolean
                    : type == "integer" ? value.Type == JTokenType.Integer
                    : type == "number" ? value.Type == JTokenType.Integer || value.Type == JTokenType.Float
                    : type == "string" && value.Type == JTokenType.String
                );
            if (!matches || !PlayScenarioDefinition.Scalar(value))
                throw new ArgumentException("Parameter value must match its bounded scalar type.");
        }

        private static JObject Expand(JObject source, JObject dataset)
        {
            var expanded = (JObject)source.DeepClone();
            foreach (string key in Metadata)
                expanded.Remove(key);
            if (dataset != null)
            {
                string name = (string)source["name"] + "--" + (string)dataset["name"];
                PlayScenarioDefinition.ValidateName(name);
                expanded["name"] = name;
            }
            if (source["parameter_bindings"] is JArray bindings)
                foreach (JObject binding in bindings)
                {
                    string parameter = (string)binding["parameter"];
                    JToken value = (dataset?["values"] as JObject)?.Property(parameter)?.Value ?? source["parameters"][parameter]["default"];
                    Destination(expanded, (string)binding["path"]).Value = value.DeepClone();
                }
            return expanded;
        }

        private static JProperty Destination(JObject source, string path)
        {
            // Exact grammar deliberately excludes identifiers, paths, actions, names and all output configuration.
            Match match = Regex.Match(
                path,
                @"\A/(steps|setup_steps|cleanup_steps)/(0|[1-9][0-9]*)/(state_equals|count|active|timeout_seconds|stable_for_ms|wait_frames|event_count|property/equals|(state_compare|property/compare)/(value|min|max|absolute_tolerance|relative_tolerance))\z"
            );
            if (
                !match.Success
                || !int.TryParse(match.Groups[2].Value, out int index)
                || !(source[match.Groups[1].Value] is JArray steps)
                || index >= steps.Count
            )
                throw new ArgumentException("Binding path is outside the permitted existing scalar destinations.");
            JToken current = steps[index];
            string[] parts = path.Split('/');
            for (int part = 3; part < parts.Length - 1; part++)
                current = (current as JObject)?[parts[part]];
            JProperty destination = (current as JObject)?.Property(parts[parts.Length - 1]);
            if (destination == null || !PlayScenarioDefinition.Scalar(destination.Value))
                throw new ArgumentException("Binding requires an existing scalar destination.");
            return destination;
        }
    }
}
