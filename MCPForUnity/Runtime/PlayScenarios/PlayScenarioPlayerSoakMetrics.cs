using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Constant-size rolling diagnostics; missing samples break an increasing sequence.</summary>
    public sealed class PlayScenarioPlayerSoakMetrics
    {
        private readonly PlayScenarioMetricsOptions options;
        private readonly Queue<JObject> samples = new Queue<JObject>();
        private readonly HashSet<string> warnings = new HashSet<string>(StringComparer.Ordinal);
        private readonly long?[] previous = new long?[3];
        private readonly int[] increases = new int[3];
        private int count;
        private int usable;

        public PlayScenarioPlayerSoakMetrics(PlayScenarioMetricsOptions options) => this.options = options;

        public JArray Boundary(PlayScenarioRun run, int iteration)
        {
            var rows = new JArray();
            if (!options.Enabled)
                return rows;
            // A completed one-repeat engine has at most one boundary snapshot.
            if (run.Metrics.Count > 1)
                throw new InvalidOperationException("A soak iteration cannot supply multiple boundary samples.");
            JObject sample = run.Metrics.Count == 0 ? null : JObject.FromObject(run.Metrics[0]);
            if (sample != null)
            {
                sample["iteration"] = iteration;
                sample["ignored_for_trend"] = iteration <= options.WarmupIterations;
                rows.Add(sample);
            }
            return rows;
        }

        public void Observe(JArray rows, int iteration)
        {
            if (!options.Enabled)
                return;
            JObject sample = rows.Count == 0 ? null : (JObject)rows[0];
            if (sample != null)
            {
                samples.Enqueue(sample);
                if (samples.Count > 10)
                    samples.Dequeue();
                count++;
            }
            bool valid = sample != null && iteration > options.WarmupIterations && sample["error"]?.Type == JTokenType.Null;
            if (valid)
                usable++;
            ObserveMetric(0, "managed_bytes", valid ? (long?)sample["managed_bytes"] : null, options.ManagedGrowthBytes);
            ObserveMetric(1, "allocated_bytes", valid ? (long?)sample["allocated_bytes"] : null, options.AllocatedGrowthBytes);
            ObserveMetric(2, "object_count", valid ? (long?)sample["object_count"] : null, options.ObjectGrowthCount);
        }

        private void ObserveMetric(int index, string name, long? current, long threshold)
        {
            increases[index] =
                previous[index].HasValue && current.HasValue && current > previous[index] && current - previous[index] > threshold ? increases[index] + 1 : 0;
            if (increases[index] >= options.ConsecutiveIncreases)
                warnings.Add(name + " sustained post-warmup growth; diagnostic only, not proof of a leak.");
            previous[index] = current;
        }

        public JObject Snapshot() =>
            !options.Enabled
                ? null
                : new JObject
                {
                    ["sample_count"] = count,
                    ["usable_samples"] = usable,
                    ["warmup_iterations"] = options.WarmupIterations,
                    ["bounded_samples"] = new JArray(samples),
                    ["warnings"] = new JArray(warnings),
                    ["summary"] =
                        usable < options.ConsecutiveIncreases + 1
                            ? "Insufficient post-warmup samples; this does not establish a leak."
                            : "Sustained growth is diagnostic only; measurements do not establish a leak.",
                };
    }
}
