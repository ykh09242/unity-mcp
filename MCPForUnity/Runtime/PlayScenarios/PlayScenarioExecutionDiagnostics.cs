using System.Collections.Generic;
using Newtonsoft.Json;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioQueryBudgetOptions
    {
        [JsonProperty("enabled")]
        public bool Enabled;

        [JsonProperty("max_target_searches")]
        public int MaxTargetSearches = 4096;

        [JsonProperty("max_hierarchy_visits")]
        public int MaxHierarchyVisits = 1000000;
    }

    /// <summary>Cumulative scalar counts; capture must not resolve targets or allocate metadata.</summary>
    public interface IPlayScenarioQueryHost
    {
        PlayScenarioQueryCounts CaptureQueryCounts();
    }

    public struct PlayScenarioQueryCounts
    {
        [JsonProperty("target_searches")]
        public long TargetSearches;

        [JsonProperty("hierarchy_visits")]
        public long HierarchyVisits;
    }

    public sealed class PlayScenarioTimelineEvent
    {
        [JsonProperty("sequence")]
        public long Sequence;

        [JsonProperty("timestamp_unix_ms")]
        public long TimestampUnixMs;

        [JsonProperty("stage")]
        public string Stage;

        [JsonProperty("iteration")]
        public int Iteration;

        [JsonProperty("step_index")]
        public int StepIndex;

        [JsonProperty("event")]
        public string Event;

        [JsonProperty("detail")]
        public string Detail;
    }

    public sealed class PlayScenarioRetainedResource
    {
        [JsonProperty("id")]
        public long Id;

        [JsonProperty("kind")]
        public string Kind;

        [JsonProperty("owner")]
        public string Owner;

        [JsonProperty("type_name")]
        public string TypeName;

        [JsonProperty("resource_name")]
        public string ResourceName;

        [JsonProperty("source_file")]
        public string SourceFile;

        [JsonProperty("source_member")]
        public string SourceMember;

        [JsonProperty("source_line")]
        public int SourceLine;
    }
}
