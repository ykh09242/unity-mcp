using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;

namespace MCPForUnity.Editor.Tools.Build
{
    internal static partial class BuildReportStore
    {
        private const int RowsPerShard = 256;
        private const int MaxDetailShards = 4096;
        private const long MaxDetailBytes = 256L * 1024 * 1024;
        private static readonly string[] DetailSections = { "steps", "packed_assets" };
        private static readonly Regex GenerationPattern = new Regex("\\A[0-9a-f]{32}\\z", RegexOptions.CultureInvariant);
        private static readonly Regex ShardNamePattern = new Regex("\\A(?:steps|packed_assets)-(?:index|[0-9]{6})\\.json\\z", RegexOptions.CultureInvariant);

        private static string DetailsDirectory(string id, string generation)
        {
            if (!IdPattern.IsMatch(id ?? "") || !GenerationPattern.IsMatch(generation ?? ""))
                throw new ReportFailure("build_report_corrupt");
            return Location(id + ".details/" + generation);
        }

        private static string DetailFile(JObject record, string name)
        {
            if (!ShardNamePattern.IsMatch(name))
                throw new ReportFailure("build_report_corrupt");
            return SafePathUtility.ResolveWithinRoot(DetailsDirectory((string)record["report_id"], (string)record["detail_index"]["generation"]), name);
        }

        private static string WriteSnapshot(JObject source, IEnumerable<JObject> steps, IEnumerable<JObject> packed)
        {
            Validate(source);
            var record = (JObject)source.DeepClone();
            record["schema_version"] = 2;
            record["steps"] = new JArray();
            record["packed_assets"] = new JArray();
            string id = (string)record["report_id"];
            string generation = Guid.NewGuid().ToString("N");
            record["detail_index"] = new JObject { ["generation"] = generation };
            Directory.CreateDirectory(Root);
            Prune(id);
            string directory = DetailsDirectory(id, generation);
            if (Directory.Exists(directory))
                throw new IOException("Report generation already exists.");
            Directory.CreateDirectory(directory);
            bool published = false;
            long bytes = 0;
            try
            {
                Atomic(
                    SafePathUtility.ResolveWithinRoot(directory, "owner.json"),
                    Utf8.GetBytes(new JObject { ["report_id"] = id, ["generation"] = generation }.ToString(Formatting.None))
                );
                WriteSection(record, "steps", steps, ref bytes);
                WriteSection(record, "packed_assets", packed, ref bytes);
                Validate(record);
                string payload = record.ToString(Formatting.None);
                var envelope = new JObject
                {
                    ["storage_version"] = 2,
                    ["revision"] = Hash(payload),
                    ["payload"] = payload,
                };
                byte[] encoded = Utf8.GetBytes(envelope.ToString(Formatting.None));
                if (encoded.Length > MaxFileBytes)
                    throw new IOException("Report summary exceeds its byte budget.");
                Atomic(Location(id + ".json"), encoded);
                published = true;
                // Publication is already complete. Optional stale-generation cleanup cannot invalidate it.
                try
                {
                    DeleteDetails(id, generation);
                }
                catch { }
                return id;
            }
            finally
            {
                if (!published)
                {
                    try
                    {
                        DeleteGeneration(id, generation);
                    }
                    catch { }
                }
            }
        }

        private static void WriteSection(JObject record, string section, IEnumerable<JObject> rows, ref long bytes)
        {
            var hashes = new JArray();
            var batch = new JArray();
            int count = 0;
            bool exhausted = true;
            foreach (JObject row in rows)
            {
                batch.Add(row);
                if (batch.Count != RowsPerShard)
                    continue;
                if (!WriteBatch(record, section, batch, hashes, ref bytes))
                {
                    exhausted = false;
                    break;
                }
                count += batch.Count;
                batch = new JArray();
            }
            if (exhausted && batch.Count > 0)
            {
                if (WriteBatch(record, section, batch, hashes, ref bytes))
                    count += batch.Count;
                else
                    exhausted = false;
            }
            if (!exhausted)
            {
                record[section + "_complete"] = false;
                record["details_note"] = "Optional detail capture reached its 256 MiB / 4096 shards per section budget; build outcome is unchanged.";
            }
            var index = new JObject
            {
                ["row_count"] = count,
                ["rows_per_shard"] = RowsPerShard,
                ["shards"] = hashes,
            };
            byte[] encoded = Utf8.GetBytes(index.ToString(Formatting.None));
            if (encoded.Length > MaxFileBytes)
                throw new IOException("Detail index exceeds its byte budget.");
            Atomic(DetailFile(record, section + "-index.json"), encoded);
            record["detail_index"][section] = new JObject
            {
                ["row_count"] = count,
                ["shards"] = hashes.Count,
                ["sha256"] = Hash(index.ToString(Formatting.None)),
            };
        }

        private static bool WriteBatch(JObject record, string section, JArray batch, JArray hashes, ref long bytes)
        {
            if (hashes.Count >= MaxDetailShards)
                return false;
            ValidateRows(record, section, batch);
            string text = new JObject { ["rows"] = batch }.ToString(Formatting.None);
            byte[] encoded = Utf8.GetBytes(text);
            if (encoded.Length > MaxFileBytes || bytes + encoded.Length > MaxDetailBytes)
                return false;
            Atomic(DetailFile(record, section + "-" + hashes.Count.ToString("D6", CultureInfo.InvariantCulture) + ".json"), encoded);
            hashes.Add(Hash(text));
            bytes += encoded.Length;
            return true;
        }

        private static void ValidateRows(JObject record, string section, JArray rows)
        {
            var ordinary = (JObject)record.DeepClone();
            ordinary["schema_version"] = 1;
            ordinary.Remove("detail_index");
            ordinary.Remove("report_revision");
            ordinary[section] = rows;
            Validate(ordinary);
        }

        private static void ValidateIndex(JObject record)
        {
            if ((int?)record["schema_version"] == 1)
            {
                if (record["detail_index"] != null)
                    throw new ReportFailure("build_report_corrupt");
                return;
            }
            if (!(record["detail_index"] is JObject index) || index.Count != 3 || !GenerationPattern.IsMatch((string)index["generation"] ?? ""))
                throw new ReportFailure("build_report_corrupt");
            foreach (string section in DetailSections)
            {
                if (
                    ((JArray)record[section]).Count != 0
                    || !(index[section] is JObject item)
                    || item.Count != 3
                    || item["row_count"]?.Type != JTokenType.Integer
                    || item["shards"]?.Type != JTokenType.Integer
                    || !HashPattern.IsMatch((string)item["sha256"] ?? "")
                )
                    throw new ReportFailure("build_report_corrupt");
                long count = item.Value<long>("row_count"),
                    shards = item.Value<long>("shards");
                if (count < 0 || count > (long)MaxDetailShards * RowsPerShard || shards != (count + RowsPerShard - 1) / RowsPerShard)
                    throw new ReportFailure("build_report_corrupt");
            }
        }

        private static JObject ReadVerified(string path, string expected)
        {
            try
            {
                byte[] bytes = ReadBytes(path);
                string text = Utf8.GetString(bytes);
                if (Hash(text) != expected)
                    throw new ReportFailure("build_report_corrupt");
                return Parse(text);
            }
            catch (ReportFailure)
            {
                throw;
            }
            catch
            {
                throw new ReportFailure("build_report_corrupt");
            }
        }

        private static JObject ReadIndex(JObject record, string section)
        {
            var manifest = record["detail_index"][section];
            var index = ReadVerified(DetailFile(record, section + "-index.json"), (string)manifest["sha256"]);
            if (
                index.Count != 3
                || (int?)index["rows_per_shard"] != RowsPerShard
                || !JToken.DeepEquals(index["row_count"], manifest["row_count"])
                || !(index["shards"] is JArray hashes)
                || hashes.Count != (int)manifest["shards"]
                || hashes.Any(h => h.Type != JTokenType.String || !HashPattern.IsMatch((string)h))
            )
                throw new ReportFailure("build_report_corrupt");
            return index;
        }

        private static IEnumerable<JObject> ReadDetailRows(JObject record, string section, long start, long length)
        {
            if (record["detail_index"] == null)
            {
                foreach (JObject row in ((JArray)record[section]).Skip((int)Math.Min(start, int.MaxValue)).Take((int)Math.Min(length, int.MaxValue)))
                    yield return row;
                yield break;
            }
            int total = record["detail_index"][section].Value<int>("row_count");
            if (start >= total || length == 0)
                yield break;
            JObject index = ReadIndex(record, section);
            long end = Math.Min(total, start + length);
            for (int shard = (int)(start / RowsPerShard); shard <= (end - 1) / RowsPerShard; shard++)
            {
                string name = section + "-" + shard.ToString("D6", CultureInfo.InvariantCulture) + ".json";
                var content = ReadVerified(DetailFile(record, name), (string)index["shards"][shard]);
                if (content.Count != 1 || !(content["rows"] is JArray rows) || rows.Count != Math.Min(RowsPerShard, total - shard * RowsPerShard))
                    throw new ReportFailure("build_report_corrupt");
                ValidateRows(record, section, rows);
                for (int i = 0; i < rows.Count; i++)
                {
                    long offset = (long)shard * RowsPerShard + i;
                    if (offset >= start && offset < end)
                        yield return (JObject)rows[i];
                }
            }
        }

        private static void PrepareComparison(JObject record, string section)
        {
            if (section == "summary" || record["detail_index"] == null)
                return;
            int count = record["detail_index"][section].Value<int>("row_count");
            int maximum = section == "steps" ? MaxSteps : MaxPackedAssets;
            var rows = new JArray(ReadDetailRows(record, section, 0, maximum));
            if (count > maximum)
                record[section + "_complete"] = false;
            record[section] = rows;
        }

        private static void DeleteGeneration(string id, string generation)
        {
            string directory = DetailsDirectory(id, generation);
            if (!Directory.Exists(directory))
                return;
            string owner = SafePathUtility.ResolveWithinRoot(directory, "owner.json");
            var marker = Read(owner);
            if (marker.Count != 2 || (string)marker["report_id"] != id || (string)marker["generation"] != generation)
                return;
            var entries = Directory.EnumerateFileSystemEntries(directory).Take(MaxDetailShards * 2 + 4).ToArray();
            if (
                entries.Length > MaxDetailShards * 2 + 3
                || entries.Any(path => Path.GetFileName(path) != "owner.json" && !ShardNamePattern.IsMatch(Path.GetFileName(path)))
            )
                return;
            // Inspect every candidate before deleting any; unknown entries/links stay untouched.
            var checkedFiles = entries.Select(path => SafePathUtility.ResolveWithinRoot(directory, Path.GetFileName(path))).ToArray();
            if (checkedFiles.Any(path => !File.Exists(path)))
                return;
            foreach (string path in checkedFiles.Where(path => Path.GetFileName(path) != "owner.json"))
                File.Delete(path);
            File.Delete(owner);
            if (!Directory.EnumerateFileSystemEntries(directory).Any())
                Directory.Delete(directory);
        }

        private static void DeleteDetails(string id, string keep = null)
        {
            string root = Location(id + ".details");
            if (!Directory.Exists(root))
                return;
            foreach (string directory in Directory.EnumerateDirectories(root).Take(128))
            {
                string generation = Path.GetFileName(directory);
                if (generation != keep && GenerationPattern.IsMatch(generation))
                    DeleteGeneration(id, generation);
            }
            if (!Directory.EnumerateFileSystemEntries(root).Any())
                Directory.Delete(root);
        }

        private static string PersistNativeSnapshot(JObject record, BuildReport report)
        {
            if (report == null || (report.summary.options & BuildOptions.DetailedBuildReport) == 0)
                return PersistSnapshot(record);
            record["steps_complete"] = true;
            record["packed_assets_complete"] = true;
            record["details_note"] =
                "Complete detail capture requested: all scalar rows exposed by Unity, subject to the optional storage budget. Native APIs materialize their own arrays; message text is excluded.";
            try
            {
                return WriteSnapshot(record, BuildReportCapture.AllSteps(report), BuildReportCapture.AllPackedAssets(report));
            }
            catch
            {
                // The native build has already completed; optional detail failure cannot replace its result.
                record["steps"] = new JArray();
                record["packed_assets"] = new JArray();
                record["steps_complete"] = false;
                record["packed_assets_complete"] = false;
                record["details_note"] = "Optional complete detail capture unavailable; authoritative summary retained.";
                return PersistSnapshot(record);
            }
        }
    }
}
