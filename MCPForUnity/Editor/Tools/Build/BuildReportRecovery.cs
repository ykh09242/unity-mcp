using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Build
{
    internal static partial class BuildReportStore
    {
        private static readonly Regex TemporaryPattern = new Regex("\\Awrite-v2-([0-9a-f]{32})-[0-9a-f]{32}\\.tmp\\z", RegexOptions.CultureInvariant);
        private static readonly HashSet<string> PublishingGenerations = new HashSet<string>(StringComparer.Ordinal);
        private static string recoveryRoot;
        private static string recoverySession;
        private static DateTime nextRecovery;
        private static int temporaryPosition;
        private static int markerPosition;
        private static int detailPosition;
        private static int generationPosition;
        private static string detailCandidate;
        private static bool detailCycleEnded;

        // Keep only scalar continuation positions. Every directory enumerator is closed
        // during its pass, including idle stores and domain reloads.
        private static List<string> RecoveryCandidates(string root, string pattern, bool directories, ref int position, int maximum, out bool completed)
        {
            var result = new List<string>();
            using (var paths = (directories ? Directory.EnumerateDirectories(root, pattern) : Directory.EnumerateFiles(root, pattern)).GetEnumerator())
            {
                for (int skipped = 0; skipped < position && paths.MoveNext(); skipped++) { }
                while (result.Count < maximum && paths.MoveNext())
                    result.Add(paths.Current);
                completed = result.Count < maximum || !paths.MoveNext();
            }
            position = completed ? 0 : position + result.Count;
            return result;
        }

        // Incremental optional storage maintenance, never a native job timeout or cancellation.
        private static void RecoverArtifacts()
        {
            string root = Root;
            DateTime now = UtcNow();
            bool sameStore = root == recoveryRoot && recoverySession == sessionId;
            if (sameStore && now < nextRecovery)
                return;
            if (!sameStore)
            {
                temporaryPosition = markerPosition = detailPosition = generationPosition = 0;
                detailCandidate = null;
            }
            recoveryRoot = root;
            recoverySession = sessionId;
            nextRecovery = now.AddSeconds(5);
            try
            {
                RetireMarkers();
            }
            catch { /* Keep original evidence if archival fails. */ }
            int remaining = 256;
            foreach (string path in RecoveryCandidates(root, "write-v2-*.tmp", false, ref temporaryPosition, 256, out _))
            {
                var match = TemporaryPattern.Match(Path.GetFileName(path));
                if (!match.Success || match.Groups[1].Value == sessionId)
                    continue;
                try
                {
                    File.Delete(Location(Path.GetFileName(path)));
                    remaining--;
                }
                catch { /* Linked, locked or unavailable artifacts remain untouched. */ }
            }
            int roots = 0,
                inspected = 0;
            while (remaining > 0 && roots < 128 && inspected < 128)
            {
                if (detailCandidate == null)
                {
                    var candidates = RecoveryCandidates(root, "br-*.details", true, ref detailPosition, 1, out detailCycleEnded);
                    if (candidates.Count == 0)
                        break;
                    detailCandidate = Path.GetFileName(candidates[0]);
                    generationPosition = 0;
                }
                roots++;
                bool completed = true;
                try
                {
                    string id = detailCandidate.Substring(0, detailCandidate.Length - ".details".Length);
                    if (!IdPattern.IsMatch(id))
                        throw new ReportFailure("build_report_corrupt");
                    string directory = Location(detailCandidate);
                    string keep = null;
                    if (File.Exists(Location(id + ".json")))
                        keep = (string)Load(id, allowExpired: true)["detail_index"]?["generation"];
                    string active = Location(id + ".active.json");
                    if (File.Exists(active) && (string)Read(active)["session"] == sessionId)
                        throw new ReportFailure("active_report_observation");
                    foreach (string child in RecoveryCandidates(directory, "*", true, ref generationPosition, 128 - inspected, out completed))
                    {
                        inspected++;
                        if (remaining == 0)
                            break;
                        string generation = Path.GetFileName(child);
                        if (!GenerationPattern.IsMatch(generation) || generation == keep)
                            continue;
                        try
                        {
                            string checkedDirectory = DetailsDirectory(id, generation);
                            if (PublishingGenerations.Contains(checkedDirectory))
                                continue;
                            var owner = Read(SafePathUtility.ResolveWithinRoot(checkedDirectory, "owner.json"));
                            if (!OwnsGeneration(owner, id, generation) || (string)owner["session"] == sessionId)
                                continue;
                            remaining -= DeleteGenerationFiles(id, generation, remaining);
                        }
                        catch { /* Unknown ownership or corrupt publication cannot authorize deletion. */ }
                    }
                    if (!Directory.EnumerateFileSystemEntries(directory).Any())
                        Directory.Delete(directory);
                }
                catch { /* One foreign/invalid directory cannot block another report. */ }
                if (completed)
                {
                    detailCandidate = null;
                    if (detailCycleEnded)
                        break;
                }
            }
        }

        private static bool OwnsGeneration(JObject owner, string id, string generation) =>
            owner != null
            && (owner.Count == 2 || owner.Count == 3 && GenerationPattern.IsMatch((string)owner["session"] ?? ""))
            && (string)owner["report_id"] == id
            && (string)owner["generation"] == generation;

        private static void ValidateMarker(JObject marker)
        {
            if (
                marker.Count != 6
                || !Regex.IsMatch((string)marker["job_id"] ?? "", "\\A[A-Za-z0-9_-]{1,128}\\z")
                || !GenerationPattern.IsMatch((string)marker["session"] ?? "")
                || !new[] { "build", "batch" }.Contains((string)marker["kind"])
                || !new[] { "pending", "building" }.Contains((string)marker["state"])
                || marker["platform"]?.Type != JTokenType.String
                || SafeText((string)marker["platform"]) != (string)marker["platform"]
                || !DateTime.TryParseExact((string)marker["observed_at"], "O", CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out _)
            )
                throw new ReportFailure("build_report_corrupt");
        }

        private static List<JObject> ReadInterrupted()
        {
            string path = Location("interrupted.json");
            if (!File.Exists(path))
                return new List<JObject>();
            var archive = Read(path);
            if (archive.Count != 2 || (int?)archive["schema_version"] != 1 || !(archive["observations"] is JArray rows) || rows.Count > MaxRecords)
                throw new ReportFailure("build_report_corrupt");
            var observations = new List<JObject>();
            foreach (JToken row in rows)
            {
                if (!(row is JObject marker))
                    throw new ReportFailure("build_report_corrupt");
                ValidateMarker(marker);
                if (DateTime.ParseExact((string)marker["observed_at"], "O", CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind) > UtcNow().AddDays(-30))
                    observations.Add(marker);
            }
            return observations;
        }

        private static void RetireMarkers()
        {
            var previous = new List<KeyValuePair<string, JObject>>();
            // A bounded pass can recover legacy directories already above admission capacity.
            foreach (string path in RecoveryCandidates(Root, "br-*.active.json", false, ref markerPosition, 128, out _))
            {
                try
                {
                    string checkedPath = Location(Path.GetFileName(path));
                    var marker = Read(checkedPath);
                    ValidateMarker(marker);
                    if (Path.GetFileName(path) == Identity((string)marker["job_id"]) + ".active.json" && (string)marker["session"] != sessionId)
                        previous.Add(new KeyValuePair<string, JObject>(checkedPath, marker));
                }
                catch { /* Unrecognized evidence is never deleted. */ }
            }
            if (previous.Count == 0)
                return;
            List<JObject> archived;
            try
            {
                archived = ReadInterrupted();
            }
            catch
            {
                // Preserve unreadable bytes before publishing a new valid archive.
                // A failed move/write keeps the old active markers for a later pass.
                File.Move(Location("interrupted.json"), Location("interrupted-" + Guid.NewGuid().ToString("N") + ".corrupt.json"));
                archived = new List<JObject>();
            }
            var merged = archived
                .Concat(previous.Select(pair => pair.Value))
                .Where(marker =>
                    DateTime.ParseExact((string)marker["observed_at"], "O", CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind) > UtcNow().AddDays(-30)
                )
                .OrderByDescending(marker => (string)marker["observed_at"], StringComparer.Ordinal)
                .ThenBy(marker => (string)marker["job_id"], StringComparer.Ordinal)
                .GroupBy(marker => (string)marker["job_id"], StringComparer.Ordinal)
                .Select(group => group.First())
                .Take(MaxRecords);
            var archive = new JObject { ["schema_version"] = 1, ["observations"] = new JArray(merged) };
            byte[] bytes = Utf8.GetBytes(archive.ToString(Formatting.None));
            if (bytes.Length > MaxFileBytes)
                throw new IOException("Interrupted observation archive exceeds its byte budget.");
            Atomic(Location("interrupted.json"), bytes);
            // Publication precedes deletion: a crash can duplicate an observation, never lose it.
            foreach (var pair in previous)
                File.Delete(pair.Key);
        }

        private static JObject MarkerObservation(JObject marker, bool interrupted)
        {
            ValidateMarker(marker);
            return new JObject
            {
                ["job_id"] = marker["job_id"],
                ["kind"] = marker["kind"],
                ["state"] = interrupted ? "interrupted" : (string)marker["state"],
                ["resumed"] = false,
            };
        }
    }
}
