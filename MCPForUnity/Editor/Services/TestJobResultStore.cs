using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Immutable, byte-bounded project-local test evidence. Never stores Unity adaptors.</summary>
    internal sealed class TestJobResultStore
    {
        internal const long JobLimit = 32 * 1024 * 1024;
        private const long StoreLimit = 128 * 1024 * 1024;
        private const int ShardLimit = 1024 * 1024;
        private static readonly UTF8Encoding Utf8 = new(false, true);
        private readonly string root;
        private readonly string session;
        private readonly object gate = new();
        public long MaximumReadBytes { get; private set; }
        public long MaximumExportTemporaryBytes { get; private set; }

        internal void ObserveExportTemporaryBytes(long bytes) => MaximumExportTemporaryBytes = Math.Max(MaximumExportTemporaryBytes, bytes);

        internal Func<long> Clock { get; set; } = () => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        public TestJobResultStore(string projectRoot, string sessionId)
        {
            ValidateId(sessionId);
            root = Path.GetFullPath(projectRoot);
            session = sessionId;
            WindowsPathBudget(root);
            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), root);
            Checked(root);
            Cleanup(null, 0, null);
        }

        internal object Gate => gate;

        internal string CheckPath(string path) => Checked(path);

        internal string ExportPath(string id, JObject descriptor, string format) =>
            Checked(Path.Combine(DirectoryFor(id, (string)descriptor["generation"]), "export." + format));

        internal long RemainingExportBudget(string id, JObject descriptor)
        {
            string directory = DirectoryFor(id, (string)descriptor["generation"]);
            if (!OwnedFiles(directory, out var files, out _))
                throw new InvalidDataException("corrupt");
            long bytes = files.Sum(file => new FileInfo(Checked(file)).Length);
            if (bytes > JobLimit)
                throw new InvalidDataException("quota_exceeded");
            Cleanup(id, bytes, (string)descriptor["generation"]);
            return JobLimit - bytes;
        }

        internal void ValidateExportBudget(string id, JObject descriptor, long incoming)
        {
            string directory = DirectoryFor(id, (string)descriptor["generation"]);
            if (!OwnedFiles(directory, out var files, out _))
                throw new InvalidDataException("corrupt");
            long bytes = files.Sum(file => new FileInfo(Checked(file)).Length);
            if (bytes > JobLimit || incoming > JobLimit)
                throw new InvalidDataException("quota_exceeded");
            Cleanup(id, bytes, (string)descriptor["generation"]);
        }

        private static void WindowsPathBudget(string fullPath)
        {
            // Portable Unity/Mono compatibility policy, not a claim about every Windows filesystem.
            if (Path.DirectorySeparatorChar == '\\' && fullPath.Length >= 260)
                throw new InvalidDataException("path_limit_exceeded");
        }

        private string Checked(string path)
        {
            WindowsPathBudget(Path.GetFullPath(Path.Combine(root, path)));
            return SafePathUtility.ResolveWithinRoot(root, path);
        }

        internal string DirectoryFor(string id, string generation)
        {
            ValidateId(id);
            ValidateId(generation);
            return Checked($"Library/MCPForUnity/TestJobResults/{generation}");
        }

        private static void ValidateId(string value)
        {
            if (value == null || !Regex.IsMatch(value, "\\A[a-f0-9]{32}\\z"))
                throw new ArgumentException("Invalid test artifact identity.");
        }

        internal static bool ValidDescriptor(JToken token, string jobId)
        {
            try
            {
                if (token is not JObject value || Utf8.GetByteCount(value.ToString(Formatting.None)) > 2048)
                    return false;
                ValidateId((string)value["session"]);
                ValidateId((string)value["generation"]);
                if ((string)value["job_id"] != jobId)
                    return false;
                ValidateId(jobId);
                if (!Regex.IsMatch((string)value["sha256"] ?? "", "\\A[a-f0-9]{64}\\z"))
                    return false;
                foreach (string key in new[] { "completed_unix_ms", "count", "truncated_result_count" })
                    if (value[key]?.Type != JTokenType.Integer || (long)value[key] < 0)
                        return false;
                if ((long)value["count"] > 10000 || (long)value["truncated_result_count"] > (long)value["count"])
                    return false;
                if (value["exports"] != null)
                {
                    if (value["exports"] is not JObject exports || exports.Count > 2)
                        return false;
                    foreach (var entry in exports.Properties())
                        if (
                            (entry.Name != "json" && entry.Name != "junit")
                            || entry.Value is not JObject evidence
                            || !Regex.IsMatch((string)evidence["sha256"] ?? "", "\\A[a-f0-9]{64}\\z")
                            || (string)evidence["source_sha256"] != (string)value["sha256"]
                            || evidence["total_bytes"]?.Type != JTokenType.Integer
                            || (long)evidence["total_bytes"] < 0
                            || (long)evidence["total_bytes"] > JobLimit
                        )
                            return false;
                }
                return true;
            }
            catch (Exception error) when (error is ArgumentException || error is InvalidCastException || error is OverflowException)
            {
                return false;
            }
        }

        internal static string Hash(byte[] bytes)
        {
            using var hash = SHA256.Create();
            return string.Concat(hash.ComputeHash(bytes).Select(value => value.ToString("x2")));
        }

        internal static string BoundedText(string text, int maximum, out int originalBytes)
        {
            text ??= string.Empty;
            originalBytes = Encoding.UTF8.GetByteCount(text);
            var output = new StringBuilder();
            int bytes = 0;
            for (int index = 0; index < text.Length; index++)
            {
                char character = text[index];
                string unit;
                if (char.IsHighSurrogate(character) && index + 1 < text.Length && char.IsLowSurrogate(text[index + 1]))
                    unit = new string(new[] { character, text[++index] });
                else
                    unit = char.IsSurrogate(character) ? "\uFFFD" : character.ToString();
                int size = Utf8.GetByteCount(unit);
                if (bytes + size > maximum)
                    break;
                output.Append(unit);
                bytes += size;
            }
            return output.ToString();
        }

        private static JObject Row(TestRunTestResult result)
        {
            var row = JObject.FromObject(result.ToSerializable());
            var truncated = new JArray();
            var original = new JObject();
            foreach (string key in new[] { "name", "fullName", "state", "message", "stackTrace", "output" })
            {
                int cap =
                    key == "state" ? 128
                    : key == "name" || key == "fullName" ? 2048
                    : 12000;
                string source = (string)row[key];
                string bounded = BoundedText(source, cap, out int bytes);
                if (source != null)
                    row[key] = bounded;
                if (bytes > cap)
                {
                    truncated.Add(key);
                    original[key] = bytes;
                }
            }
            if ((double.IsNaN(result.DurationSeconds) || double.IsInfinity(result.DurationSeconds)) || result.DurationSeconds < 0)
            {
                row["durationSeconds"] = 0;
                truncated.Add("durationSeconds");
            }
            row["truncated_fields"] = truncated;
            row["original_field_bytes"] = original;
            return row;
        }

        public JObject Save(string jobId, string mode, TestRunResult result, long completed) =>
            SaveCapture(jobId, mode, result.Summary, result.Results, completed);

        internal JObject SaveCapture(string jobId, string mode, TestRunSummary summary, IEnumerable<TestRunTestResult> rows, long completed)
        {
            lock (gate)
            {
                ValidateId(jobId);
                string generation = Guid.NewGuid().ToString("N");
                string directory = DirectoryFor(jobId, generation);
                if (Directory.Exists(Checked(directory)))
                    throw new IOException("Artifact generation already exists.");
                Directory.CreateDirectory(Checked(directory));
                Checked(directory);
                var shards = new JArray();
                var states = new JObject
                {
                    ["passed"] = 0,
                    ["failed"] = 0,
                    ["skipped"] = 0,
                    ["error"] = 0,
                };
                var buffer = new MemoryStream();
                int truncatedCount = 0;
                int count = 0,
                    shardCount = 0,
                    failures = 0,
                    shardFailures = 0;
                byte[] ownerBytes = Utf8.GetBytes(
                    new JObject
                    {
                        ["version"] = 1,
                        ["session"] = session,
                        ["job_id"] = jobId,
                        ["generation"] = generation,
                    }.ToString(Formatting.None)
                );
                long bytes = ownerBytes.Length;
                void Flush()
                {
                    if (shardCount == 0)
                        return;
                    if (shards.Count >= 100)
                        throw new InvalidDataException("quota_exceeded");
                    byte[] content = buffer.ToArray();
                    string name = shards.Count.ToString("D4") + ".jsonl";
                    WriteNew(Checked(Path.Combine(directory, name)), content);
                    shards.Add(
                        new JObject
                        {
                            ["name"] = name,
                            ["count"] = shardCount,
                            ["failed"] = shardFailures,
                            ["bytes"] = content.Length,
                            ["sha256"] = Hash(content),
                        }
                    );
                    buffer.SetLength(0);
                    shardCount = shardFailures = 0;
                }
                try
                {
                    WriteNew(Checked(Path.Combine(directory, ".owner.json")), ownerBytes);
                    foreach (var result in rows)
                    {
                        if (++count > 10000)
                            throw new InvalidDataException("quota_exceeded");
                        JObject row = Row(result);
                        if (((JArray)row["truncated_fields"]).Count > 0)
                            truncatedCount++;
                        string state = ((string)row["state"] ?? "").ToLowerInvariant();
                        string category =
                            state == "passed" ? "passed"
                            : state == "failed" ? "failed"
                            : state == "skipped" || state == "ignored" || state == "inconclusive" ? "skipped"
                            : "error";
                        states[category] = (int)states[category] + 1;
                        byte[] line = Utf8.GetBytes(row.ToString(Formatting.None) + "\n");
                        if (line.Length > 65536 || bytes + line.Length > JobLimit)
                            throw new InvalidDataException("quota_exceeded");
                        if (shardCount == 100 || buffer.Length + line.Length > ShardLimit)
                            Flush();
                        buffer.Write(line, 0, line.Length);
                        bytes += line.Length;
                        shardCount++;
                        if (!string.Equals((string)row["state"], "Passed", StringComparison.OrdinalIgnoreCase))
                        {
                            shardFailures++;
                            failures++;
                        }
                    }
                    Flush();
                    var manifest = new JObject
                    {
                        ["version"] = 1,
                        ["session"] = session,
                        ["job_id"] = jobId,
                        ["generation"] = generation,
                        ["mode"] = mode,
                        ["completed_unix_ms"] = completed,
                        ["summary"] = JObject.FromObject(summary.ToSerializable()),
                        ["truncated_result_count"] = truncatedCount,
                        ["state_counts"] = states,
                        ["count"] = count,
                        ["failed_count"] = failures,
                        ["shards"] = shards,
                    };
                    byte[] encoded = Utf8.GetBytes(manifest.ToString(Formatting.None));
                    if (encoded.Length > 32768 || bytes + encoded.Length > JobLimit)
                        throw new InvalidDataException("quota_exceeded");
                    Cleanup(jobId, bytes + encoded.Length, generation);
                    WriteNew(Checked(Path.Combine(directory, "manifest.json")), encoded);
                    return new JObject
                    {
                        ["session"] = session,
                        ["job_id"] = jobId,
                        ["generation"] = generation,
                        ["sha256"] = Hash(encoded),
                        ["completed_unix_ms"] = completed,
                        ["count"] = count,
                        ["truncated_result_count"] = truncatedCount,
                        ["status"] = summary.Failed > 0 ? "failed" : "succeeded",
                        ["finished_unix_ms"] = completed,
                    };
                }
                catch
                {
                    if (Directory.Exists(Checked(directory)))
                        DeleteOwned(directory);
                    throw;
                }
                finally
                {
                    buffer.Dispose();
                }
            }
        }

        internal void WriteNew(string path, byte[] content)
        {
            string temporary = Checked(Path.Combine(Path.GetDirectoryName(path), "." + Guid.NewGuid().ToString("N") + ".tmp"));
            try
            {
                using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    file.Write(content, 0, content.Length);
                    file.Flush();
                }
                File.Move(Checked(temporary), Checked(path));
            }
            finally
            {
                if (File.Exists(Checked(temporary)))
                    File.Delete(Checked(temporary));
            }
        }

        private FileStream OpenRead(string path)
        {
            try
            {
                return new FileStream(Checked(path), FileMode.Open, FileAccess.Read, FileShare.Read);
            }
            catch (DirectoryNotFoundException error)
            {
                throw new FileNotFoundException("missing", error);
            }
        }

        internal byte[] ReadBytes(string path, int limit)
        {
            Checked(path);
            using var file = OpenRead(path);
            if (file.Length > limit)
                throw new InvalidDataException("corrupt");
            using var output = new MemoryStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = file.Read(buffer, 0, Math.Min(buffer.Length, limit + 1 - (int)output.Length))) > 0)
            {
                if (output.Length + read > limit)
                    throw new InvalidDataException("corrupt");
                output.Write(buffer, 0, read);
            }
            MaximumReadBytes = Math.Max(MaximumReadBytes, output.Length);
            return output.ToArray();
        }

        private static JObject Parse(string text)
        {
            using var reader = new JsonTextReader(new StringReader(text)) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 };
            var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read())
                throw new InvalidDataException("corrupt");
            return value;
        }

        internal JObject Manifest(string jobId, JObject descriptor)
        {
            if ((string)descriptor?["session"] != session || (string)descriptor?["job_id"] != jobId)
                throw new ArgumentException("Test result descriptor belongs to another job or session.");
            if (!ValidDescriptor(descriptor, jobId))
                throw new InvalidDataException("corrupt");
            string generation = (string)descriptor?["generation"];
            JObject owner = Owner(DirectoryFor(jobId, generation));
            if ((string)owner["session"] != session || (string)owner["job_id"] != jobId)
                throw new InvalidDataException("corrupt");
            byte[] bytes = ReadBytes(Checked(Path.Combine(DirectoryFor(jobId, generation), "manifest.json")), 32768);
            if (Hash(bytes) != (string)descriptor["sha256"])
                throw new InvalidDataException("corrupt");
            JObject value = Parse(Utf8.GetString(bytes));
            ValidateManifest(value, session, jobId, generation);
            if (Clock() - (long)value["completed_unix_ms"] > 86400000)
                throw new FileNotFoundException("evicted");
            return value;
        }

        private static void ValidateManifest(JObject value, string session, string jobId, string generation)
        {
            if (
                (int?)value["version"] != 1
                || (string)value["session"] != session
                || (string)value["job_id"] != jobId
                || (string)value["generation"] != generation
                || value["shards"] is not JArray shards
                || shards.Count > 100
                || (int?)value["count"] < 0
                || (int?)value["count"] > 10000
            )
                throw new InvalidDataException("corrupt");
            int total = 0,
                failed = 0,
                index = 0;
            if (value["count"]?.Type != JTokenType.Integer || value["failed_count"]?.Type != JTokenType.Integer)
                throw new InvalidDataException("corrupt");
            foreach (JToken token in shards)
            {
                if (
                    token is not JObject shard
                    || (string)shard["name"] != (index++).ToString("D4") + ".jsonl"
                    || shard["count"]?.Type != JTokenType.Integer
                    || (int)shard["count"] < 1
                    || (int)shard["count"] > 100
                    || shard["failed"]?.Type != JTokenType.Integer
                    || (int)shard["failed"] < 0
                    || (int)shard["failed"] > (int)shard["count"]
                    || shard["bytes"]?.Type != JTokenType.Integer
                    || (int)shard["bytes"] < 1
                    || (int)shard["bytes"] > ShardLimit
                    || !Regex.IsMatch((string)shard["sha256"] ?? "", "\\A[a-f0-9]{64}\\z")
                )
                    throw new InvalidDataException("corrupt");
                total += (int)shard["count"];
                failed += (int)shard["failed"];
            }
            if (total != (int)value["count"] || failed != (int)value["failed_count"])
                throw new InvalidDataException("corrupt");
        }

        internal IEnumerable<JObject> Rows(string jobId, JObject descriptor)
        {
            JObject manifest = Manifest(jobId, descriptor);
            foreach (JObject shard in manifest["shards"])
            foreach (JObject row in ReadShard(jobId, descriptor, shard))
                yield return row;
        }

        private List<JObject> ReadShard(string id, JObject descriptor, JObject shard)
        {
            string name = (string)shard["name"];
            if (name == null || !Regex.IsMatch(name, "\\A[0-9]{4}\\.jsonl\\z"))
                throw new InvalidDataException("corrupt");
            byte[] bytes = ReadBytes(Checked(Path.Combine(DirectoryFor(id, (string)descriptor["generation"]), name)), ShardLimit);
            if (bytes.Length != (int)shard["bytes"] || Hash(bytes) != (string)shard["sha256"])
                throw new InvalidDataException("corrupt");
            var rows = new List<JObject>();
            using var reader = new StringReader(Utf8.GetString(bytes));
            string line;
            while ((line = reader.ReadLine()) != null)
            {
                if (rows.Count >= 100 || Utf8.GetByteCount(line) > 65536)
                    throw new InvalidDataException("corrupt");
                JObject row = Parse(line);
                if (
                    row["name"]?.Type != JTokenType.String
                    || row["fullName"]?.Type != JTokenType.String
                    || row["state"]?.Type != JTokenType.String
                    || row["durationSeconds"] == null
                    || row["durationSeconds"].Type != JTokenType.Float && row["durationSeconds"].Type != JTokenType.Integer
                    || double.IsNaN((double)row["durationSeconds"])
                    || double.IsInfinity((double)row["durationSeconds"])
                    || (double)row["durationSeconds"] < 0
                )
                    throw new InvalidDataException("corrupt");
                rows.Add(row);
            }
            if (rows.Count != (int)shard["count"])
                throw new InvalidDataException("corrupt");

            return rows;
        }

        public JObject ReadPage(string jobId, JObject descriptor, int pageSize, string cursor, bool failedOnly)
        {
            lock (gate)
            {
                if (pageSize < 1 || pageSize > 100)
                    throw new ArgumentException("page_size must be 1..100.");
                JObject manifest = Manifest(jobId, descriptor);
                string identity = jobId + ":" + (string)descriptor["generation"] + ":" + (failedOnly ? "f" : "a") + ":";
                int offset = 0;
                if (
                    cursor != null
                    && (
                        !cursor.StartsWith(identity, StringComparison.Ordinal)
                        || !int.TryParse(cursor.Substring(identity.Length), out offset)
                        || offset < 0
                        || offset > (int)manifest["count"]
                    )
                )
                    throw new ArgumentException("Invalid or stale test result cursor.");
                int position = 0,
                    emittedBytes = 0;
                var items = new JArray();
                bool more = false;
                foreach (JObject shard in manifest["shards"])
                {
                    int count = (int)shard["count"];
                    if (position + count <= offset || failedOnly && (int)shard["failed"] == 0)
                    {
                        position += count;
                        continue;
                    }
                    foreach (JObject row in ReadShard(jobId, descriptor, shard))
                    {
                        int index = position++;
                        if (index < offset || failedOnly && string.Equals((string)row["state"], "Passed", StringComparison.OrdinalIgnoreCase))
                            continue;
                        int size = Utf8.GetByteCount(row.ToString(Formatting.None));
                        if (items.Count >= pageSize || emittedBytes + size > 500000)
                        {
                            more = true;
                            offset = index;
                            break;
                        }
                        items.Add(row);
                        emittedBytes += size;
                        offset = index + 1;
                    }
                    if (more)
                        break;
                }
                return new JObject
                {
                    ["items"] = items,
                    ["total_count"] = manifest[failedOnly ? "failed_count" : "count"],
                    ["has_more"] = more,
                    ["next_cursor"] = more ? identity + offset : null,
                    ["generation"] = descriptor["generation"],
                };
            }
        }

        internal IReadOnlyList<TestRunTestResult> Results(string id, JObject descriptor) => new StoredResults(this, id, descriptor);

        private sealed class StoredResults : IReadOnlyList<TestRunTestResult>
        {
            private readonly TestJobResultStore owner;
            private readonly string id;
            private readonly JObject descriptor;

            public StoredResults(TestJobResultStore owner, string id, JObject descriptor)
            {
                this.owner = owner;
                this.id = id;
                this.descriptor = descriptor;
            }

            public int Count => (int)descriptor["count"];
            public TestRunTestResult this[int index] => this.Skip(index).First();

            public IEnumerator<TestRunTestResult> GetEnumerator()
            {
                foreach (JObject row in owner.Rows(id, descriptor))
                    yield return new TestRunTestResult(
                        (string)row["name"],
                        (string)row["fullName"],
                        (string)row["state"],
                        (double)row["durationSeconds"],
                        (string)row["message"],
                        (string)row["stackTrace"],
                        (string)row["output"]
                    );
            }

            IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
        }

        private JObject Owner(string directory)
        {
            JObject value = Parse(Utf8.GetString(ReadBytes(Checked(Path.Combine(directory, ".owner.json")), 512)));
            if (
                value.Count != 4
                || value["version"]?.Type != JTokenType.Integer
                || (int?)value["version"] != 1
                || (string)value["generation"] != Path.GetFileName(directory)
            )
                throw new InvalidDataException("corrupt");
            ValidateId((string)value["session"]);
            ValidateId((string)value["job_id"]);
            ValidateId((string)value["generation"]);
            return value;
        }

        // A whole generation is preflighted before mutation. Unknown/unmarked/link-bearing
        // directories are never adopted merely because their names resemble our identifiers.
        private bool OwnedFiles(string directory, out List<string> files, out long completed)
        {
            files = new List<string>();
            completed = 0;
            try
            {
                Checked(directory);
                JObject owner = Owner(directory);
                var allowed = new HashSet<string>(StringComparer.Ordinal) { ".owner.json", "manifest.json", "export.json", "export.junit" };
                string manifestPath = Checked(Path.Combine(directory, "manifest.json"));
                if (File.Exists(manifestPath))
                {
                    byte[] bytes = ReadBytes(manifestPath, 32768);
                    JObject manifest = Parse(Utf8.GetString(bytes));
                    var descriptor = new JObject
                    {
                        ["session"] = owner["session"],
                        ["job_id"] = owner["job_id"],
                        ["generation"] = owner["generation"],
                        ["sha256"] = Hash(bytes),
                        ["completed_unix_ms"] = manifest["completed_unix_ms"],
                        ["count"] = manifest["count"],
                        ["truncated_result_count"] = manifest["truncated_result_count"],
                    };
                    if (!ValidDescriptor(descriptor, (string)owner["job_id"]))
                        return false;
                    ValidateManifest(manifest, (string)owner["session"], (string)owner["job_id"], (string)owner["generation"]);
                    int index = 0;
                    foreach (JToken shard in (JArray)manifest["shards"])
                    {
                        string name = (index++).ToString("D4") + ".jsonl";
                        if ((string)shard["name"] != name)
                            return false;
                        allowed.Add(name);
                    }
                    completed = (long)manifest["completed_unix_ms"];
                }
                foreach (string entry in Directory.EnumerateFileSystemEntries(Checked(directory)))
                {
                    if (files.Count >= 105)
                        return false;
                    string safe = Checked(entry);
                    if ((File.GetAttributes(safe) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
                        return false;
                    string name = Path.GetFileName(safe);
                    bool stagingShard = !File.Exists(manifestPath) && Regex.IsMatch(name, "\\A[0-9]{4}\\.jsonl\\z");
                    if (!allowed.Contains(name) && !stagingShard && !Regex.IsMatch(name, "\\A\\.[a-f0-9]{32}\\.tmp\\z"))
                        return false;
                    files.Add(safe);
                }
                return true;
            }
            catch (Exception error)
                when (error is IOException
                    || error is JsonException
                    || error is ArgumentException
                    || error is UnauthorizedAccessException
                    || error is InvalidCastException
                    || error is OverflowException
                )
            {
                return false;
            }
        }

        private void DeleteOwned(string directory)
        {
            if (!OwnedFiles(directory, out var files, out _))
                return;
            foreach (string file in files)
                File.Delete(Checked(file));
            Directory.Delete(Checked(directory));
        }

        private void Cleanup(string protectedId, long incoming, string protectedGeneration)
        {
            string basePath = Checked("Library/MCPForUnity/TestJobResults");
            if (!Directory.Exists(basePath))
                return;
            var entries = new List<(string Path, long Time, long Bytes)>();
            int visited = 0;
            foreach (string generation in Directory.EnumerateDirectories(Checked(basePath)))
            {
                if (++visited > 512)
                    throw new InvalidDataException("quota_exceeded");
                if (!Regex.IsMatch(Path.GetFileName(generation), "\\A[a-f0-9]{32}\\z") || Path.GetFileName(generation) == protectedGeneration)
                    continue;
                if (!OwnedFiles(generation, out var files, out long completed))
                    continue;
                entries.Add((generation, completed, files.Sum(file => new FileInfo(Checked(file)).Length)));
            }
            long total = entries.Sum(e => e.Bytes) + incoming;
            int count = entries.Count + (protectedGeneration == null ? 0 : 1);
            foreach (var entry in entries.OrderBy(e => e.Time).ThenBy(e => e.Path, StringComparer.Ordinal))
            {
                if (Clock() - entry.Time <= 86400000 && total <= StoreLimit && count <= 10)
                    continue;
                DeleteOwned(entry.Path);
                total -= entry.Bytes;
                count--;
            }
            if (total > StoreLimit)
                throw new InvalidDataException("quota_exceeded");
        }
    }
}
