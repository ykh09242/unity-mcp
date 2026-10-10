using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioSuiteDefinition
    {
        [JsonProperty("schema_version")]
        public int SchemaVersion = 1;

        [JsonProperty("name")]
        public string Name;

        [JsonProperty("scenarios")]
        public List<string> Scenarios = new List<string>();

        [JsonProperty("tags")]
        public List<string> Tags = new List<string>();

        [JsonProperty("failure_policy")]
        public string FailurePolicy = "stop";

        public static PlayScenarioSuiteDefinition Parse(JObject value)
        {
            if (value == null)
                throw new ArgumentException("suite must be an object.");
            PlayScenarioDefinition.Fields(value, "schema_version", "name", "scenarios", "tags", "failure_policy");
            var result = new PlayScenarioSuiteDefinition
            {
                SchemaVersion = PlayScenarioDefinition.Integer(value, "schema_version", 1, 1, 1),
                Name = PlayScenarioDefinition.Text(value, "name", 64),
                Scenarios = Selectors(value, "scenarios"),
                Tags = Selectors(value, "tags"),
                FailurePolicy = value.Property("failure_policy") == null ? "stop" : PlayScenarioDefinition.Text(value, "failure_policy", 8),
            };
            PlayScenarioDefinition.ValidateName(result.Name);
            if (result.FailurePolicy != "stop" && result.FailurePolicy != "continue")
                throw new ArgumentException("failure_policy must be stop or continue.");
            if (result.Scenarios.Count == 0 && result.Tags.Count == 0)
                throw new ArgumentException("A suite requires at least one scenario or tag selector.");
            return result;
        }

        private static List<string> Selectors(JObject value, string key)
        {
            if (value.Property(key) == null)
                return new List<string>();
            if (!(value[key] is JArray array) || array.Count > 16)
                throw new ArgumentException(key + " must contain at most 16 unique lowercase slugs.");
            var result = new List<string>();
            foreach (JToken token in array)
            {
                if (token.Type != JTokenType.String)
                    throw new ArgumentException(key + " entries must be strings.");
                string text = (string)token;
                PlayScenarioDefinition.ValidateName(text);
                if (result.Contains(text, StringComparer.Ordinal))
                    throw new ArgumentException(key + " entries must be unique.");
                result.Add(text);
            }
            return result;
        }

        public IReadOnlyList<PlayScenarioDefinition> Resolve(PlayScenarioStore store)
        {
            PlayScenarioSuiteDefinition suite = Parse(JObject.FromObject(this));
            var selected = new List<PlayScenarioDefinition>();
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (string name in suite.Scenarios)
            {
                selected.Add(store.Get(name));
                names.Add(name);
            }
            if (suite.Tags.Count > 0)
                foreach (string name in store.List())
                {
                    if (names.Contains(name))
                        continue;
                    PlayScenarioDefinition definition = store.Get(name);
                    if (definition.Tags.Any(tag => suite.Tags.Contains(tag, StringComparer.Ordinal)))
                    {
                        selected.Add(definition);
                        names.Add(name);
                    }
                }
            if (selected.Count < 1 || selected.Count > 16)
                throw new ArgumentException("Suite selection must resolve to 1-16 scenarios.");
            return selected.AsReadOnly();
        }
    }

    public sealed class PlayScenarioSuiteReport
    {
        [JsonProperty("suite_id")]
        public string SuiteId;

        [JsonProperty("suite")]
        public PlayScenarioSuiteDefinition Suite;

        [JsonProperty("status")]
        public string Status = "running";

        [JsonProperty("repeat_count")]
        public int RepeatCount;

        [JsonProperty("timeout_seconds")]
        public int TimeoutSeconds;

        [JsonProperty("started_unix_ms")]
        public long StartedUnixMs;

        [JsonProperty("finished_unix_ms")]
        public long? FinishedUnixMs;

        [JsonProperty("scenarios")]
        public List<PlayScenarioSuiteChild> Scenarios = new List<PlayScenarioSuiteChild>();

        [JsonProperty("error")]
        public string Error;

        [JsonProperty("report_path")]
        public string ReportPath;

        [JsonProperty("report_error")]
        public string ReportError;

        [JsonProperty("source_revision")]
        public string SourceRevision;
    }

    public sealed class PlayScenarioSuiteChild
    {
        [JsonProperty("name")]
        public string Name;

        [JsonProperty("status")]
        public string Status = "pending";

        [JsonProperty("definition_hash")]
        public string DefinitionHash;

        [JsonProperty("job_id")]
        public string JobId;

        [JsonProperty("report")]
        public JObject Report;

        [JsonProperty("skip_reason")]
        public string SkipReason;
    }
}
