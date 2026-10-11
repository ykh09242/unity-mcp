using System;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.PlayScenarios
{
    public sealed class PlayScenarioPlayerSoakOptions
    {
        public int Iterations { get; private set; }
        public int MaxRuntimeSeconds { get; private set; }
        public string FailurePolicy { get; private set; }
        public int RetainReports { get; private set; }

        public static PlayScenarioPlayerSoakOptions Parse(JObject value)
        {
            PlayScenarioPlayerBundle.Fields(value, "iterations", "max_runtime_seconds", "failure_policy", "retain_reports");
            string policy = PlayScenarioPlayerBundle.Text(value, "failure_policy", 8);
            if (policy != "stop" && policy != "continue")
                throw new ArgumentException("Player soak failure_policy must be stop or continue.");
            return new PlayScenarioPlayerSoakOptions
            {
                Iterations = Integer(value, "iterations", 2, 1000),
                MaxRuntimeSeconds = Integer(value, "max_runtime_seconds", 1, 86400),
                FailurePolicy = policy,
                RetainReports = Integer(value, "retain_reports", 1, 64),
            };
        }

        private static int Integer(JObject value, string field, int min, int max)
        {
            if (value[field]?.Type != JTokenType.Integer || !int.TryParse(value[field].ToString(), out int result) || result < min || result > max)
                throw new ArgumentException("Player soak " + field + " is outside its bounds.");
            return result;
        }
    }
}
