using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioDefinition
    {
        [JsonProperty("name")]
        public string Name;

        [JsonProperty("poll_interval_ms")]
        public int PollIntervalMs = 250;

        [JsonProperty("steps")]
        public List<PlayScenarioStep> Steps = new List<PlayScenarioStep>();

        [JsonProperty("setup_steps")]
        public List<PlayScenarioStep> SetupSteps = new List<PlayScenarioStep>();

        [JsonProperty("cleanup_steps")]
        public List<PlayScenarioStep> CleanupSteps = new List<PlayScenarioStep>();

        [JsonProperty("cleanup_timeout_seconds")]
        public int CleanupTimeoutSeconds = 30;

        [JsonProperty("completion_stable_ms")]
        public int CompletionStableMs = 250;

        [JsonProperty("log_policy")]
        public PlayScenarioLogPolicy LogPolicy = new PlayScenarioLogPolicy();

        [JsonProperty("metrics")]
        public PlayScenarioMetricsOptions Metrics = new PlayScenarioMetricsOptions();

        [JsonProperty("diagnostics")]
        public PlayScenarioDiagnosticsOptions Diagnostics = new PlayScenarioDiagnosticsOptions();

        [JsonProperty("tags")]
        public List<string> Tags = new List<string>();

        [JsonProperty("resources")]
        public PlayScenarioResourceOptions Resources = new PlayScenarioResourceOptions();

        [JsonProperty("query_budget")]
        public PlayScenarioQueryBudgetOptions QueryBudget = new PlayScenarioQueryBudgetOptions();

        public static void ValidateName(string name)
        {
            if (name == null || !Regex.IsMatch(name, @"\A[a-z0-9][a-z0-9_-]{0,63}\z"))
                throw new ArgumentException("Scenario name must be a lowercase slug of 1-64 characters.");
        }

        public static PlayScenarioDefinition Parse(JObject value)
        {
            if (value == null)
                throw new ArgumentException("Scenario must be an object.");
            Fields(
                value,
                "name",
                "poll_interval_ms",
                "steps",
                "setup_steps",
                "cleanup_steps",
                "cleanup_timeout_seconds",
                "completion_stable_ms",
                "log_policy",
                "metrics",
                "diagnostics",
                "tags",
                "resources",
                "query_budget"
            );
            string name = Text(value, "name", 64);
            ValidateName(name);
            var result = new PlayScenarioDefinition
            {
                Name = name,
                PollIntervalMs = Integer(value, "poll_interval_ms", 100, 2000, 250),
                Steps = ParseSteps(value["steps"], 1, 32),
                SetupSteps = value.Property("setup_steps") == null ? new List<PlayScenarioStep>() : ParseSteps(value["setup_steps"], 0, 16),
                CleanupSteps = value.Property("cleanup_steps") == null ? new List<PlayScenarioStep>() : ParseSteps(value["cleanup_steps"], 0, 16),
                CleanupTimeoutSeconds = Integer(value, "cleanup_timeout_seconds", 1, 300, 30),
                CompletionStableMs = Integer(value, "completion_stable_ms", 0, 10000, 250),
                LogPolicy = ParseLogPolicy(value),
                Metrics = ParseMetrics(value),
                Diagnostics = ParseDiagnostics(value),
                Tags = ParseTags(value),
                Resources = ParseResources(value),
                QueryBudget = ParseQueryBudget(value),
            };
            if ((result.SetupSteps.Count > 0 ? result.SetupSteps[0] : result.Steps[0]).Action != "load_scene")
                throw new ArgumentException("The first executed setup or main step must load_scene.");
            return result;
        }

        private static List<PlayScenarioStep> ParseSteps(JToken value, int minimum, int maximum)
        {
            if (!(value is JArray steps) || steps.Count < minimum || steps.Count > maximum)
                throw new ArgumentException("Step array must contain " + minimum + "-" + maximum + " objects.");
            var result = new List<PlayScenarioStep>();
            foreach (JToken token in steps)
            {
                if (!(token is JObject step))
                    throw new ArgumentException("Each step must be an object.");
                Fields(
                    step,
                    "name",
                    "action",
                    "scene",
                    "target",
                    "timeout_seconds",
                    "count",
                    "active",
                    "component",
                    "property",
                    "stable_for_ms",
                    "target_id",
                    "click_mode",
                    "reset_ids",
                    "state_id",
                    "state_equals"
                );
                var parsed = new PlayScenarioStep
                {
                    Name = Text(step, "name", 128),
                    Action = Text(step, "action", 32),
                    TimeoutSeconds = Integer(step, "timeout_seconds", 1, 120, 30),
                };
                if (parsed.Action == "wait_state")
                {
                    Fields(step, "name", "action", "timeout_seconds", "state_id", "state_equals", "stable_for_ms");
                    parsed.StateId = Text(step, "state_id", 128);
                    if (!MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget.IsValidTargetId(parsed.StateId))
                        throw new ArgumentException("state_id must contain 1-128 permitted identifier characters.");
                    MCPForUnity.Runtime.PlayScenarios.PlayScenarioStateValue.FromJson(step["state_equals"]);
                    parsed.StateEquals = step["state_equals"].DeepClone();
                    if (step.Property("stable_for_ms") != null)
                    {
                        parsed.StableForMs = Integer(step, "stable_for_ms", 0, 60000, 0);
                        if (parsed.StableForMs >= parsed.TimeoutSeconds * 1000)
                            throw new ArgumentException("stable_for_ms must be less than the step timeout.");
                    }
                    result.Add(parsed);
                    continue;
                }
                if (step.Property("state_id") != null || step.Property("state_equals") != null)
                    throw new ArgumentException("state_id and state_equals are permitted only on wait_state.");
                if (parsed.Action == "reset_state")
                {
                    Fields(step, "name", "action", "timeout_seconds", "reset_ids");
                    if (!(step["reset_ids"] is JArray ids) || ids.Count < 1 || ids.Count > 16)
                        throw new ArgumentException("reset_ids must contain 1-16 unique stable identifiers.");
                    parsed.ResetIds = new List<string>();
                    foreach (JToken id in ids)
                    {
                        if (
                            id.Type != JTokenType.String
                            || !MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget.IsValidTargetId((string)id)
                            || parsed.ResetIds.Contains((string)id, StringComparer.Ordinal)
                        )
                            throw new ArgumentException("reset_ids must contain unique case-sensitive stable identifiers.");
                        parsed.ResetIds.Add((string)id);
                    }
                    result.Add(parsed);
                    continue;
                }
                if (step.Property("reset_ids") != null)
                    throw new ArgumentException("reset_ids is permitted only on reset_state.");
                switch (parsed.Action)
                {
                    case "load_scene":
                    case "wait_scene":
                        if (step.Property("target") != null || step.Property("target_id") != null)
                            throw new ArgumentException("Scene actions cannot specify target.");
                        parsed.Scene = Text(step, "scene", 4096);
                        ValidateScene(parsed.Scene);
                        break;
                    case "click_ui":
                    case "wait_object":
                        if (step.Property("scene") != null)
                            throw new ArgumentException("Object actions cannot specify scene.");
                        if ((step.Property("target") != null) == (step.Property("target_id") != null))
                            throw new ArgumentException("Exactly one target or target_id is required.");
                        if (step.Property("target") != null)
                        {
                            parsed.Target = Text(step, "target", 4096);
                            ValidateHierarchy(parsed.Target);
                        }
                        else
                        {
                            parsed.TargetId = Text(step, "target_id", 128);
                            if (!Regex.IsMatch(parsed.TargetId, @"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\z"))
                                throw new ArgumentException("target_id must contain 1-128 permitted identifier characters.");
                        }
                        break;
                    default:
                        throw new ArgumentException("Unsupported step action.");
                }
                if (step.Property("click_mode") != null)
                {
                    if (parsed.Action != "click_ui")
                        throw new ArgumentException("click_mode is permitted only on click_ui.");
                    parsed.ClickMode = Text(step, "click_mode", 16);
                    if (parsed.ClickMode != "direct" && parsed.ClickMode != "raycast")
                        throw new ArgumentException("click_mode must be direct or raycast.");
                }
                else if (parsed.Action == "click_ui")
                    parsed.ClickMode = "direct";
                if (step.Property("stable_for_ms") != null)
                {
                    if (parsed.Action != "wait_object" && parsed.Action != "wait_scene")
                        throw new ArgumentException("stable_for_ms is permitted only on wait actions.");
                    parsed.StableForMs = Integer(step, "stable_for_ms", 0, 60000, 0);
                    if (parsed.StableForMs >= parsed.TimeoutSeconds * 1000)
                        throw new ArgumentException("stable_for_ms must be less than the step timeout.");
                }
                string[] conditionKeys = { "count", "active", "component", "property" };
                if (parsed.Action != "wait_object" && conditionKeys.Any(key => step.Property(key) != null))
                    throw new ArgumentException("Object conditions are permitted only on wait_object.");
                if (parsed.Action == "wait_object")
                {
                    if (step.Property("count") != null)
                        parsed.Count = Integer(step, "count", 0, 10000, 1);
                    if (step.Property("active") != null)
                        parsed.Active = Boolean(step, "active", true);
                    if (step.Property("component") != null)
                        parsed.Component = Text(step, "component", 256);
                    if (step.Property("property") != null)
                    {
                        JObject condition = Object(step, "property");
                        Fields(condition, "path", "equals");
                        JToken expected = condition["equals"];
                        if (!Scalar(expected))
                            throw new ArgumentException("property.equals must be a bounded boolean, signed integer, finite number or string.");
                        parsed.Property = new PlayScenarioPropertyCondition { Path = Text(condition, "path", 256), Equals = expected.DeepClone() };
                        if (parsed.Component == null)
                            throw new ArgumentException("property requires component.");
                    }
                    if (parsed.TargetId != null && (parsed.Count ?? 1) > 1)
                        throw new ArgumentException("ID selectors permit only count 0 or 1.");
                    if (parsed.Component != null && (parsed.Count ?? 1) != 1)
                        throw new ArgumentException("component and property require count 1.");
                    if (parsed.Count == 0 && parsed.Active.HasValue)
                        throw new ArgumentException("count 0 cannot specify active.");
                }
                result.Add(parsed);
            }
            return result;
        }

        private static PlayScenarioLogPolicy ParseLogPolicy(JObject value)
        {
            var policy = new PlayScenarioLogPolicy();
            if (value.Property("log_policy") == null)
                return policy;
            JObject options = Object(value, "log_policy");
            Fields(options, "mode", "allowed_messages");
            if (options.Property("mode") != null)
                policy.Mode = Text(options, "mode", 16);
            if (policy.Mode != "strict" && policy.Mode != "log_only")
                throw new ArgumentException("log_policy.mode must be strict or log_only.");
            if (options.Property("allowed_messages") != null)
            {
                if (!(options["allowed_messages"] is JArray allowed) || allowed.Count > 32)
                    throw new ArgumentException("allowed_messages must contain at most 32 unique literal messages.");
                foreach (JToken message in allowed)
                {
                    if (message.Type != JTokenType.String || ((string)message).Length < 1 || ((string)message).Length > 1024)
                        throw new ArgumentException("Allowed messages must be nonempty strings of at most 1024 characters.");
                    if (policy.AllowedMessages.Contains((string)message, StringComparer.Ordinal))
                        throw new ArgumentException("Allowed messages must be unique.");
                    policy.AllowedMessages.Add((string)message);
                }
            }
            return policy;
        }

        private static PlayScenarioMetricsOptions ParseMetrics(JObject value)
        {
            if (value.Property("metrics") == null)
                return new PlayScenarioMetricsOptions();
            JObject options = Object(value, "metrics");
            Fields(options, "enabled", "warmup_iterations", "consecutive_increases", "managed_growth_bytes", "allocated_growth_bytes", "object_growth_count");
            return new PlayScenarioMetricsOptions
            {
                Enabled = Boolean(options, "enabled", false),
                WarmupIterations = Integer(options, "warmup_iterations", 0, 9, 1),
                ConsecutiveIncreases = Integer(options, "consecutive_increases", 2, 9, 2),
                ManagedGrowthBytes = Integer(options, "managed_growth_bytes", 0, int.MaxValue, 1048576),
                AllocatedGrowthBytes = Integer(options, "allocated_growth_bytes", 0, int.MaxValue, 1048576),
                ObjectGrowthCount = Integer(options, "object_growth_count", 0, 10000, 0),
            };
        }

        private static PlayScenarioDiagnosticsOptions ParseDiagnostics(JObject value)
        {
            if (value.Property("diagnostics") == null)
                return new PlayScenarioDiagnosticsOptions();
            JObject options = Object(value, "diagnostics");
            Fields(options, "screenshot_on_failure", "record_timeline");
            return new PlayScenarioDiagnosticsOptions
            {
                ScreenshotOnFailure = Boolean(options, "screenshot_on_failure", false),
                RecordTimeline = Boolean(options, "record_timeline", false),
            };
        }

        internal static List<string> ParseTags(JObject value)
        {
            if (value.Property("tags") == null)
                return new List<string>();
            if (!(value["tags"] is JArray tags) || tags.Count > 16)
                throw new ArgumentException("tags must contain at most 16 unique lowercase slugs.");
            var result = new List<string>();
            foreach (JToken tag in tags)
            {
                if (tag.Type != JTokenType.String)
                    throw new ArgumentException("Each tag must be a lowercase slug.");
                string text = (string)tag;
                ValidateName(text);
                if (result.Contains(text, StringComparer.Ordinal))
                    throw new ArgumentException("tags must be unique.");
                result.Add(text);
            }
            return result;
        }

        private static PlayScenarioResourceOptions ParseResources(JObject value)
        {
            if (value.Property("resources") == null)
                return new PlayScenarioResourceOptions();
            JObject options = Object(value, "resources");
            Fields(options, "enabled", "max_scriptable_objects", "max_subscriptions", "max_handles");
            return new PlayScenarioResourceOptions
            {
                Enabled = Boolean(options, "enabled", false),
                MaxScriptableObjects = Integer(options, "max_scriptable_objects", 0, 4096, 0),
                MaxSubscriptions = Integer(options, "max_subscriptions", 0, 4096, 0),
                MaxHandles = Integer(options, "max_handles", 0, 4096, 0),
            };
        }

        private static PlayScenarioQueryBudgetOptions ParseQueryBudget(JObject value)
        {
            if (value.Property("query_budget") == null)
                return new PlayScenarioQueryBudgetOptions();
            JObject options = Object(value, "query_budget");
            Fields(options, "enabled", "max_target_searches", "max_hierarchy_visits");
            return new PlayScenarioQueryBudgetOptions
            {
                Enabled = Boolean(options, "enabled", false),
                MaxTargetSearches = Integer(options, "max_target_searches", 0, 1000000, 4096),
                MaxHierarchyVisits = Integer(options, "max_hierarchy_visits", 0, 10000000, 1000000),
            };
        }

        private static JObject Object(JObject value, string key) => value[key] as JObject ?? throw new ArgumentException(key + " must be an object.");

        internal static bool Boolean(JObject value, string key, bool fallback)
        {
            if (value.Property(key) == null)
                return fallback;
            if (value[key].Type != JTokenType.Boolean)
                throw new ArgumentException(key + " must be a boolean.");
            return (bool)value[key];
        }

        internal static bool Scalar(JToken token)
        {
            if (token == null)
                return false;
            switch (token.Type)
            {
                case JTokenType.Boolean:
                    return true;
                case JTokenType.Integer:
                    return long.TryParse(token.ToString(), out _);
                case JTokenType.Float:
                    double number = (double)token;
                    return !double.IsNaN(number) && !double.IsInfinity(number);
                case JTokenType.String:
                    return ((string)token).Length <= 1024;
                default:
                    return false;
            }
        }

        private static void ValidateScene(string scene)
        {
            if (!scene.StartsWith("Assets/", StringComparison.Ordinal) || !scene.EndsWith(".unity", StringComparison.Ordinal))
                throw new ArgumentException("Scene must be a canonical Assets/*.unity path.");
            ValidateHierarchy(scene);
            foreach (string part in scene.Split('/'))
            {
                if (
                    part.Equals("GameData", StringComparison.OrdinalIgnoreCase)
                    || part.IndexOfAny(new[] { ':', '*', '?', '"', '<', '>', '|' }) >= 0
                    || part.EndsWith(".", StringComparison.Ordinal)
                    || part.EndsWith(" ", StringComparison.Ordinal)
                )
                    throw new ArgumentException("Scene path is not permitted.");
            }
        }

        private static void ValidateHierarchy(string target)
        {
            string[] parts = target.Split('/');
            if (target.IndexOf('\\') >= 0 || parts.Length > 128 || parts.Any(part => part.Length == 0 || part == "." || part == ".."))
                throw new ArgumentException("Target must be an exact relative hierarchy path with at most 128 levels.");
        }

        internal static void Fields(JObject value, params string[] allowed)
        {
            foreach (JProperty property in value.Properties())
                if (!allowed.Contains(property.Name, StringComparer.Ordinal))
                    throw new ArgumentException("Unknown field: " + property.Name);
        }

        internal static string Text(JObject value, string key, int maximum)
        {
            if (value[key]?.Type != JTokenType.String)
                throw new ArgumentException(key + " must be a string.");
            string text = (string)value[key];
            if (string.IsNullOrWhiteSpace(text) || text.Length > maximum || text.Any(char.IsControl) || text.IndexOf('\\') >= 0)
                throw new ArgumentException(key + " has an invalid length or character.");
            return text;
        }

        internal static int Integer(JObject value, string key, int minimum, int maximum, int fallback)
        {
            JToken token = value[key];
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int number) || number < minimum || number > maximum)
                throw new ArgumentException(key + " must be an integer between " + minimum + " and " + maximum + ".");
            return number;
        }
    }

    public sealed class PlayScenarioStep
    {
        [JsonProperty("name")]
        public string Name;

        [JsonProperty("action")]
        public string Action;

        [JsonProperty("scene", NullValueHandling = NullValueHandling.Ignore)]
        public string Scene;

        [JsonProperty("target", NullValueHandling = NullValueHandling.Ignore)]
        public string Target;

        [JsonProperty("target_id", NullValueHandling = NullValueHandling.Ignore)]
        public string TargetId;

        [JsonProperty("state_id", NullValueHandling = NullValueHandling.Ignore)]
        public string StateId;

        [JsonProperty("state_equals", NullValueHandling = NullValueHandling.Ignore)]
        public JToken StateEquals;

        [JsonProperty("reset_ids", NullValueHandling = NullValueHandling.Ignore)]
        public List<string> ResetIds;

        [JsonProperty("click_mode", NullValueHandling = NullValueHandling.Ignore)]
        public string ClickMode;

        [JsonProperty("timeout_seconds")]
        public int TimeoutSeconds = 30;

        [JsonProperty("count", NullValueHandling = NullValueHandling.Ignore)]
        public int? Count;

        [JsonProperty("active", NullValueHandling = NullValueHandling.Ignore)]
        public bool? Active;

        [JsonProperty("component", NullValueHandling = NullValueHandling.Ignore)]
        public string Component;

        [JsonProperty("property", NullValueHandling = NullValueHandling.Ignore)]
        public PlayScenarioPropertyCondition Property;

        [JsonProperty("stable_for_ms", NullValueHandling = NullValueHandling.Ignore)]
        public int? StableForMs;
    }

    public sealed class PlayScenarioPropertyCondition
    {
        [JsonProperty("path")]
        public string Path;

        [JsonProperty("equals")]
        public new JToken Equals;
    }

    public sealed class PlayScenarioLogPolicy
    {
        [JsonProperty("mode")]
        public string Mode = "strict";

        [JsonProperty("allowed_messages")]
        public List<string> AllowedMessages = new List<string>();

        public bool IsUnexpected(string type, string message) =>
            Mode == "strict" && (type == "Error" || type == "Assert" || type == "Exception") && !AllowedMessages.Contains(message, StringComparer.Ordinal);

        public bool IsUnexpectedError(string message, string type) => IsUnexpected(type, message);
    }

    public sealed class PlayScenarioMetricsOptions
    {
        [JsonProperty("enabled")]
        public bool Enabled;

        [JsonProperty("warmup_iterations")]
        public int WarmupIterations = 1;

        [JsonProperty("consecutive_increases")]
        public int ConsecutiveIncreases = 2;

        [JsonProperty("managed_growth_bytes")]
        public int ManagedGrowthBytes = 1048576;

        [JsonProperty("allocated_growth_bytes")]
        public int AllocatedGrowthBytes = 1048576;

        [JsonProperty("object_growth_count")]
        public int ObjectGrowthCount;
    }

    public sealed class PlayScenarioDiagnosticsOptions
    {
        [JsonProperty("screenshot_on_failure")]
        public bool ScreenshotOnFailure;

        [JsonProperty("record_timeline")]
        public bool RecordTimeline;
    }
}
