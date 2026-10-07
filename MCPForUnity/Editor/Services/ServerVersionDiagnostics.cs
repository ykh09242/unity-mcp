using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Bounded observations from this editor's stdio command route, independent of HTTP health.</summary>
    internal static class ServerVersionDiagnostics
    {
        private sealed class Observation
        {
            internal string ServerId;
            internal string Version;
            internal string Commit;
            internal DateTime LastSeenUtc;
        }

        private const int MaximumServers = 8;
        private static readonly Dictionary<string, Observation> Observations = new();

        internal static void Observe(JToken metadata, TransportMode? origin, DateTime observedUtc)
        {
            // The origin is supplied by the transport callsite, never by the envelope.
            // Missing legacy metadata is accepted and never affects command execution.
            if (origin != TransportMode.Stdio || !(metadata is JObject info) || info.Count > 3)
                return;
            if (info["version"]?.Type != JTokenType.String || info["server_id"]?.Type != JTokenType.String)
                return;
            string version = (string)info["version"];
            string serverId = (string)info["server_id"];
            if (
                version.Length > 80
                || !Regex.IsMatch(version, @"\A[0-9A-Za-z+_.-]{1,80}\z")
                || serverId.Length != 32
                || !Regex.IsMatch(serverId, @"\A[0-9a-f]{32}\z")
            )
                return;
            string commit = null;
            if (info["source_commit"] != null && info["source_commit"].Type != JTokenType.Null)
            {
                if (info["source_commit"].Type != JTokenType.String)
                    return;
                commit = (string)info["source_commit"];
                if (commit.Length > 64 || !Regex.IsMatch(commit, @"\A[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\z"))
                    return;
                commit = commit.ToLowerInvariant();
            }
            if (!Observations.ContainsKey(serverId) && Observations.Count == MaximumServers)
                Observations.Remove(Observations.Values.OrderBy(observation => observation.LastSeenUtc).First().ServerId);
            Observations[serverId] = new Observation
            {
                ServerId = serverId,
                Version = version,
                Commit = commit,
                LastSeenUtc = observedUtc,
            };
        }

        internal static string Describe(string expectedSource, DateTime nowUtc)
        {
            string expectedCommit = PinnedCommit(expectedSource);
            string expectation =
                expectedCommit == null
                    ? "Selected server build: comparison unavailable (source is not an immutable Git commit)."
                    : $"Selected server build: {expectedCommit}.";
            if (Observations.Count == 0)
                return "Observed stdio servers: no build metadata received. Run a tool from the client to observe its server; older servers may not report metadata.\n"
                    + expectation
                    + "\nPython server and Unity package versions are independent. Restart the client's MCP connection after an update.";

            var lines = new List<string> { "Observed stdio servers (server-reported; separate from HTTP health):", expectation };
            foreach (var observation in Observations.Values.OrderByDescending(value => value.LastSeenUtc))
            {
                double age = Math.Max(0, (nowUtc - observation.LastSeenUtc).TotalSeconds);
                string freshness = age > 120 ? "stale observation; current process state unknown" : "recent observation";
                string comparison =
                    expectedCommit == null || observation.Commit == null ? "build comparison unavailable"
                    : expectedCommit == observation.Commit ? "matches selected build"
                    : "build mismatch with selected source";
                lines.Add(
                    $"STDIO {observation.ServerId.Substring(0, 8)}: Python v{observation.Version}, commit {observation.Commit ?? "unknown"}; {comparison}; "
                        + $"last seen {observation.LastSeenUtc:yyyy-MM-dd HH:mm:ss} UTC ({freshness})."
                );
            }
            lines.Add(
                "After updating, restart the affected client's MCP connection to load the selected server. Reconfigure only to change its source; preserve intentional manual pins."
            );
            return string.Join("\n", lines);
        }

        internal static string PinnedCommit(string source)
        {
            if (string.IsNullOrEmpty(source) || source.Length > 4096)
                return null;
            var archive = Regex.Match(
                source,
                @"\Ahttps://github\.com/[^/]+/[^/]+/archive/([0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?)\.(?:zip|tar\.gz)(?:#subdirectory=[^#?]+)?\z"
            );
            if (archive.Success)
                return archive.Groups[1].Value.ToLowerInvariant();
            var vcs = Regex.Match(source, @"\Agit\+https://[^\s?#@]+/[^\s?#@]+@([0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?)(?:#subdirectory=[^#?]+)?\z");
            return vcs.Success ? vcs.Groups[1].Value.ToLowerInvariant() : null;
        }

        internal static void ResetForTests() => Observations.Clear();
    }
}
