using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Runtime;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioResourceOptions
    {
        [JsonProperty("enabled")]
        public bool Enabled;

        [JsonProperty("max_scriptable_objects")]
        public int MaxScriptableObjects;

        [JsonProperty("max_subscriptions")]
        public int MaxSubscriptions;

        [JsonProperty("max_handles")]
        public int MaxHandles;
    }

    public sealed class PlayScenarioFailure
    {
        [JsonProperty("code")]
        public string Code;

        [JsonProperty("stage")]
        public string Stage;

        [JsonProperty("iteration")]
        public int? Iteration;

        [JsonProperty("step_index")]
        public int? StepIndex;

        [JsonProperty("target")]
        public string Target;

        [JsonProperty("component")]
        public string Component;

        [JsonProperty("property_path")]
        public string PropertyPath;

        [JsonProperty("expected")]
        public string Expected;

        [JsonProperty("actual")]
        public string Actual;

        [JsonProperty("message")]
        public string Message;
    }

    public sealed class PlayScenarioException : InvalidOperationException
    {
        public PlayScenarioFailure Failure { get; }

        public PlayScenarioException(PlayScenarioFailure failure)
            : base(failure?.Message ?? "Scenario capability failed.")
        {
            Failure = failure ?? throw new ArgumentNullException(nameof(failure));
        }
    }

    public sealed class PlayScenarioReproduction
    {
        [JsonProperty("definition_hash")]
        public string DefinitionHash;

        [JsonProperty("unity_version")]
        public string UnityVersion;

        [JsonProperty("package_version")]
        public string PackageVersion;

        [JsonProperty("source_revision")]
        public string SourceRevision;

        internal static void ValidateSourceRevision(string sourceRevision)
        {
            if (sourceRevision != null && (string.IsNullOrWhiteSpace(sourceRevision) || sourceRevision.Length > 128 || sourceRevision.Any(char.IsControl)))
                throw new ArgumentException("source_revision must be a nonblank control-free label of at most 128 characters.");
        }

        public static string Hash(PlayScenarioDefinition definition)
        {
            JToken normalized = Sort(JObject.FromObject(definition));
            using (SHA256 hash = SHA256.Create())
                return string.Concat(hash.ComputeHash(Encoding.UTF8.GetBytes(normalized.ToString(Formatting.None))).Select(value => value.ToString("x2")));
        }

        private static JToken Sort(JToken value)
        {
            if (value is JObject objectValue)
                return new JObject(
                    objectValue
                        .Properties()
                        .OrderBy(property => property.Name, StringComparer.Ordinal)
                        .Select(property => new JProperty(property.Name, Sort(property.Value)))
                );
            if (value is JArray array)
                return new JArray(array.Select(Sort));
            return value.DeepClone();
        }
    }

    public sealed class PlayScenarioResourceCheck
    {
        [JsonProperty("iteration")]
        public int Iteration;

        [JsonProperty("baseline_unix_ms")]
        public long BaselineUnixMs;

        [JsonProperty("checked_unix_ms")]
        public long CheckedUnixMs;

        [JsonProperty("new_scriptable_objects")]
        public int? NewScriptableObjects;

        [JsonProperty("new_subscriptions")]
        public int? NewSubscriptions;

        [JsonProperty("new_handles")]
        public int? NewHandles;

        [JsonProperty("passed")]
        public bool Passed;

        [JsonProperty("error")]
        public string Error;
    }

    public interface IPlayScenarioResourceHost
    {
        PlayScenarioRegisteredResources CaptureResources();
    }

    internal sealed class UnityPlayScenarioResources : IPlayScenarioResourceHost
    {
        public PlayScenarioRegisteredResources CaptureResources() => PlayScenarioResourceTracker.Capture();
    }
}
