using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    internal sealed class PlayScenarioSoakComparisonSnapshot
    {
        internal static readonly string[] Metrics = { "managed_bytes", "allocated_bytes", "object_count" };
        internal static readonly string[] EnvironmentFields =
        {
            "operating_system",
            "platform",
            "architecture",
            "scripting_backend",
            "build_mode",
            "runtime_version",
        };
        internal string Source;
        internal PlayScenarioSoakEvidence Evidence;
        internal JObject Environment;
        internal string UnityVersion;
        internal string PackageVersion;
        internal int Warmup;
        internal bool MetricsKnown;
        internal int Planned;
        internal readonly Dictionary<string, Point[]> Series = new Dictionary<string, Point[]>();

        internal sealed class Point
        {
            internal int Iteration;
            internal long? Value;
            internal string State;
            internal bool Usable => State == "measured";
            internal string Display =>
                (Value.HasValue ? Value.Value.ToString(CultureInfo.InvariantCulture) : "unavailable") + (State == "measured" ? "" : " (" + State + ")");
        }

        internal static PlayScenarioSoakComparisonSnapshot Read(string file, CancellationToken cancellation)
        {
            var result = new PlayScenarioSoakComparisonSnapshot
            {
                Source = Path.GetFullPath(file),
                Evidence = PlayScenarioSoakEvidence.Read(file, cancellation),
            };
            result.Initialize(cancellation);
            return result;
        }

        internal void Initialize(CancellationToken cancellation = default)
        {
            JObject summary = Evidence.Summary;
            Planned = (int?)((summary ?? Evidence.ClientSummary)?["requested_iterations"]) ?? 0;
            if (Planned < 0 || Planned > 1000)
                throw new InvalidDataException("Comparison iteration bound exceeded.");
            MetricsKnown = summary?["metrics_summary"] is JObject;
            Warmup = MetricsKnown ? (int)summary["metrics_summary"]["warmup_iterations"] : 0;
            JObject reproduction = Evidence.VerifiedReproduction;
            UnityVersion = OptionalText(reproduction?["unity_version"], 64, "unity_version");
            PackageVersion = OptionalText(reproduction?["package_version"], 64, "package_version");
            Environment = ValidateEnvironment(reproduction?["environment"]);
            foreach (string metric in Metrics)
            {
                var points = new Point[Planned];
                for (int index = 0; index < points.Length; index++)
                {
                    cancellation.ThrowIfCancellationRequested();
                    JObject row = index < Evidence.Rows.Count ? Evidence.Rows[index] : null;
                    JObject sample = (row?["metrics"] as JArray)?.OfType<JObject>().FirstOrDefault();
                    bool warmup = index + 1 <= Warmup || !MetricsKnown && (bool?)sample?["ignored_for_trend"] == true;
                    bool error = sample?["error"]?.Type == JTokenType.String;
                    long? value = error ? null : (long?)sample?[metric];
                    points[index] = new Point
                    {
                        Iteration = index + 1,
                        Value = value,
                        State =
                            warmup ? "warmup"
                            : row == null ? "unrecorded"
                            : error ? "error"
                            : value.HasValue ? "measured"
                            : "missing",
                    };
                }
                Series[metric] = points;
            }
        }

        internal static JObject ValidateEnvironment(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (!(token is JObject value) || value["schema_version"]?.Type != JTokenType.Integer || (long)value["schema_version"] != 1)
                throw new InvalidDataException("Unknown comparison environment schema.");
            if (
                value.Properties().Count() != EnvironmentFields.Length + 1
                || value.Properties().Any(p => p.Name != "schema_version" && !EnvironmentFields.Contains(p.Name))
            )
                throw new InvalidDataException("Comparison environment fields differ from schema 1.");
            int[] bounds = { 256, 64, 32, 32, 32, 64 };
            for (int index = 0; index < EnvironmentFields.Length; index++)
            {
                string field = EnvironmentFields[index];
                if (value[field] == null)
                    throw new InvalidDataException("Missing comparison environment field: " + field);
                string text = OptionalText(value[field], bounds[index], field);
                string[] choices =
                    field == "architecture" ? new[] { "x86", "x64", "arm", "arm64" }
                    : field == "scripting_backend" ? new[] { "mono", "il2cpp" }
                    : field == "build_mode" ? new[] { "editor", "development", "release" }
                    : null;
                if (text != null && choices != null && !choices.Contains(text))
                    throw new InvalidDataException("Invalid comparison environment " + field);
            }
            return (JObject)value.DeepClone();
        }

        private static string OptionalText(JToken token, int bound, string field)
        {
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (
                token.Type != JTokenType.String
                || string.IsNullOrWhiteSpace((string)token)
                || ((string)token).Length > bound
                || ((string)token).Any(char.IsControl)
                || !ValidUnicode((string)token)
            )
                throw new InvalidDataException("Invalid bounded comparison " + field);
            return (string)token;
        }

        private static bool ValidUnicode(string text)
        {
            for (int index = 0; index < text.Length; index++)
            {
                if (char.IsHighSurrogate(text[index]))
                {
                    if (index + 1 == text.Length || !char.IsLowSurrogate(text[++index]))
                        return false;
                }
                else if (char.IsLowSurrogate(text[index]))
                    return false;
            }
            return true;
        }
    }

    internal sealed class PlayScenarioSoakComparison
    {
        internal PlayScenarioSoakComparisonSnapshot Baseline;
        internal PlayScenarioSoakComparisonSnapshot Candidate;
        internal readonly List<string> Differences = new List<string>();
        internal readonly List<string> Unknown = new List<string>();
        internal bool AllowDeltas => Differences.Count == 0;

        internal static PlayScenarioSoakComparison Create(PlayScenarioSoakComparisonSnapshot baseline, PlayScenarioSoakComparisonSnapshot candidate)
        {
            var result = new PlayScenarioSoakComparison { Baseline = baseline, Candidate = candidate };
            foreach (var side in new[] { baseline, candidate })
                if (side.Evidence.Summary == null || !side.Evidence.Completeness.StartsWith("Complete:", StringComparison.Ordinal))
                    result.Differences.Add((side == baseline ? "Baseline" : "Candidate") + " native evidence is incomplete or unavailable.");
            foreach (string field in new[] { "definition_hash", "failure_policy" })
                result.Match(field, (string)baseline.Evidence.Summary?[field], (string)candidate.Evidence.Summary?[field]);
            result.Match("unity_version", baseline.UnityVersion, candidate.UnityVersion);
            result.Match("package_version", baseline.PackageVersion, candidate.PackageVersion);
            foreach (string field in PlayScenarioSoakComparisonSnapshot.EnvironmentFields)
                result.Match("environment." + field, (string)baseline.Environment?[field], (string)candidate.Environment?[field]);
            if (!baseline.MetricsKnown || !candidate.MetricsKnown)
                result.Differences.Add("Metrics configuration is disabled or unavailable.");
            else if (baseline.Warmup != candidate.Warmup)
                result.Differences.Add("Warmup iteration settings differ.");
            return result;
        }

        private void Match(string field, string baseline, string candidate)
        {
            if (baseline == null || candidate == null)
                Unknown.Add(field);
            else if (!string.Equals(baseline, candidate, StringComparison.Ordinal))
                Differences.Add(field + " differs: " + baseline + " → " + candidate);
        }

        internal string Delta(string metric, int iteration)
        {
            if (!AllowDeltas || iteration > Baseline.Planned || iteration > Candidate.Planned)
                return "unavailable";
            var left = Baseline.Series[metric][iteration - 1];
            var right = Candidate.Series[metric][iteration - 1];
            return left.Usable && right.Usable ? ((decimal)right.Value.Value - left.Value.Value).ToString(CultureInfo.InvariantCulture) : "unavailable";
        }

        internal static string Counts(PlayScenarioSoakComparisonSnapshot snapshot, string metric) =>
            string.Join(
                "; ",
                new[] { "measured", "warmup", "missing", "error", "unrecorded" }.Select(state =>
                    state + " " + snapshot.Series[metric].Count(point => point.State == state)
                )
            );

        internal string PairingText(string metric)
        {
            int paired = Enumerable
                .Range(0, Math.Min(Baseline.Planned, Candidate.Planned))
                .Count(index => Baseline.Series[metric][index].Usable && Candidate.Series[metric][index].Usable);
            return "Paired postwarmup measurements "
                + paired
                + "; unpaired baseline "
                + (Baseline.Series[metric].Count(point => point.Usable) - paired)
                + "; unpaired candidate "
                + (Candidate.Series[metric].Count(point => point.Usable) - paired)
                + ".";
        }

        internal IEnumerable<string> Intervals(string metric)
        {
            int start = 0;
            int total = Math.Min(Baseline.Planned, Candidate.Planned);
            for (int iteration = 1; iteration <= total + 1; iteration++)
            {
                bool usable = iteration <= total && Baseline.Series[metric][iteration - 1].Usable && Candidate.Series[metric][iteration - 1].Usable;
                if (usable && start == 0)
                    start = iteration;
                if (usable || start == 0)
                    continue;
                int end = iteration - 1;
                decimal leftChange = (decimal)Baseline.Series[metric][end - 1].Value.Value - Baseline.Series[metric][start - 1].Value.Value;
                decimal rightChange = (decimal)Candidate.Series[metric][end - 1].Value.Value - Candidate.Series[metric][start - 1].Value.Value;
                yield return "Iterations "
                    + start
                    + "–"
                    + end
                    + "; baseline endpoint change "
                    + leftChange.ToString(CultureInfo.InvariantCulture)
                    + "; candidate endpoint change "
                    + rightChange.ToString(CultureInfo.InvariantCulture)
                    + ".";
                start = 0;
            }
        }
    }
}
