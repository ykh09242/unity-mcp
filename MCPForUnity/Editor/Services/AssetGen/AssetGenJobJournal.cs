using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.AssetGen
{
    /// <summary>Durable continuation evidence; never stores provider credentials or opaque responses.</summary>
    internal sealed class AssetGenJobJournal : IDisposable
    {
        private const int EnvelopeBytes = AssetGenJobCheckpoint.MaxRecordBytes + 4096;
        private readonly string root;
        private readonly string owner;
        private readonly string instance;
        private readonly Func<DateTimeOffset> utc;
        private ExclusiveFileLease lease;
        private bool loaded;
        private string indexHash;
        private string[] ids = Array.Empty<string>();
        private readonly Dictionary<string, string> hashes = new(StringComparer.Ordinal);
        private readonly Dictionary<string, string> fingerprints = new(StringComparer.Ordinal);

        internal AssetGenJobJournal(string root, string owner, string instance, Func<DateTimeOffset> utc)
        {
            this.root = Path.GetFullPath(root);
            if (!AssetGenJobCheckpoint.Hex(owner, 64) || !AssetGenJobCheckpoint.Hex(instance, 32))
                throw new IOException("Invalid journal ownership.");
            this.owner = owner;
            this.instance = instance;
            this.utc = utc;
        }

        private string PathFor(string name) => SafePathUtility.ResolveWithinRoot(root, name);

        private static string Hash(string text) => AtomicAssetFile.Digest(Encoding.UTF8.GetBytes(text));

        private static bool Terminal(int state) =>
            state == (int)AssetGenJobState.Done || state == (int)AssetGenJobState.Failed || state == (int)AssetGenJobState.Canceled;

        private static string Fingerprint(JObject job) =>
            ((string)job["_resume_v1"]?["Revision"] ?? "") + "/" + (int)job["State"] + "/" + (string)job["RecoveryCode"];

        private static string ReadText(string path, int maximum)
        {
            using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
            if (input.Length > maximum)
                throw new IOException("Journal metadata exceeds its schema bound.");
            using var reader = new StreamReader(input, new UTF8Encoding(false, true));
            return reader.ReadToEnd();
        }

        private JObject Parse(string text)
        {
            using var source = new StringReader(text);
            using var reader = new JsonTextReader(source) { MaxDepth = AssetGenJobCheckpoint.MaxDepth + 2, DateParseHandling = DateParseHandling.None };
            var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read() || value["schema"]?.Type != JTokenType.Integer || (int)value["schema"] != 1 || (string)value["owner"] != owner)
                throw new IOException("Journal identity or schema is unverified.");
            return value;
        }

        private bool Initialize(bool create)
        {
            if (loaded)
            {
                lease.Verify();
                return true;
            }
            if (!Directory.Exists(root) && !create)
                return false;
            if (create)
                Directory.CreateDirectory(root);
            lease ??= ExclusiveFileLease.Acquire(PathFor("owner.lock"));
            try
            {
                string path = PathFor("index.json");
                if (File.Exists(path))
                {
                    string text = ReadText(path, AssetGenJobCheckpoint.MaxIndexBytes);
                    var index = Parse(text);
                    if (
                        index["ids"] is not JArray values
                        || values.Count > AssetGenJobCheckpoint.MaxActive + AssetGenJobManager.MaxTerminalJobs
                        || values.Any(v => v.Type != JTokenType.String || !AssetGenJobCheckpoint.Hex((string)v, 32))
                    )
                        throw new IOException("Journal index is unverified.");
                    ids = values.Values<string>().ToArray();
                    if (ids.Distinct(StringComparer.Ordinal).Count() != ids.Length)
                        throw new IOException("Duplicate journal identity.");
                    indexHash = Hash(text);
                }
                else if (Directory.EnumerateFiles(root, "*.json").Any(p => AssetGenJobCheckpoint.Hex(Path.GetFileNameWithoutExtension(p), 32)))
                    throw new IOException("Journal index is missing; retained work must be inspected before new admission.");
                loaded = true;
                return true;
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        internal List<JObject> Read(double now)
        {
            if (!Initialize(false) || indexHash == null)
                return null; // Same-process legacy SessionState can still migrate.
            var records = new List<JObject>(ids.Length);
            foreach (string id in ids)
            {
                try
                {
                    string text = ReadText(PathFor(id + ".json"), EnvelopeBytes);
                    var envelope = Parse(text);
                    if (
                        envelope["job"] is not JObject job
                        || (string)job["JobId"] != id
                        || job["State"]?.Type != JTokenType.Integer
                        || !Enum.IsDefined(typeof(AssetGenJobState), (int)job["State"])
                    )
                        throw new IOException("Journal job identity is unverified.");
                    hashes[id] = Hash(text);
                    fingerprints[id] = Fingerprint(job);
                    if (!Terminal((int)job["State"]))
                    {
                        if (
                            !AssetGenJobCheckpoint.Hex((string)envelope["instance"], 32)
                            || envelope["monotonic"]?.Type is not (JTokenType.Float or JTokenType.Integer)
                            || envelope["utc"]?.Type is not (JTokenType.Float or JTokenType.Integer)
                        )
                            throw new IOException("Missing execution clock evidence.");
                        double observed = (double)envelope["monotonic"],
                            savedUtc = (double)envelope["utc"];
                        if (!AssetGenJobCheckpoint.Finite(observed) || observed < 0 || !AssetGenJobCheckpoint.Finite(savedUtc) || savedUtc < 0)
                            throw new IOException("Invalid execution clock evidence.");
                        var cp = AssetGenJobCheckpoint.Read(job, id, observed) ?? throw new IOException("Missing continuation checkpoint.");
                        if ((string)envelope["instance"] != instance)
                        {
                            double elapsed = utc().ToUnixTimeMilliseconds() / 1000d - savedUtc;
                            if (elapsed < 0)
                            {
                                records.Add(Failure(id, "checkpoint_clock_uncertain"));
                                continue;
                            }
                            double remaining = Math.Max(0, cp.Deadline - observed - elapsed);
                            cp.StartedAt = now;
                            cp.Deadline = now + remaining;
                            job["_resume_v1"] = JObject.FromObject(cp);
                        }
                        if (!cp.Validate(id, now))
                            throw new IOException("Execution clock could not be reconciled.");
                    }
                    records.Add(job);
                }
                catch (Exception ex)
                    when (ex is IOException
                        || ex is UnauthorizedAccessException
                        || ex is JsonException
                        || ex is ArgumentException
                        || ex is InvalidCastException
                        || ex is FormatException
                        || ex is OverflowException
                        || ex is InvalidOperationException
                    )
                {
                    hashes.Remove(id);
                    fingerprints.Remove(id);
                    records.Add(Failure(id, "checkpoint_invalid"));
                }
            }
            return records;
        }

        private static JObject Failure(string id, string code) =>
            new JObject
            {
                ["JobId"] = id,
                ["State"] = (int)AssetGenJobState.Failed,
                ["RecoveryState"] = "unavailable",
                ["RecoveryCode"] = code,
                ["Error"] = AssetGenJobCheckpoint.ErrorSummary(code),
            };

        internal void Save(JObject job, IEnumerable<string> order, double now, bool required)
        {
            string id = (string)job["JobId"];
            if (!AssetGenJobCheckpoint.Hex(id, 32))
                throw new IOException("Invalid journal job identity.");
            Initialize(true);
            lease.Verify();
            string fingerprint = Fingerprint(job);
            if (!required && fingerprints.TryGetValue(id, out string previous) && previous == fingerprint)
                return;
            string[] next = order.Where(value => AssetGenJobCheckpoint.Hex(value, 32)).ToArray();
            if (
                next.Length > AssetGenJobCheckpoint.MaxActive + AssetGenJobManager.MaxTerminalJobs
                || !next.Contains(id)
                || next.Any(value => !AssetGenJobCheckpoint.Hex(value, 32))
                || next.Distinct(StringComparer.Ordinal).Count() != next.Length
            )
                throw new IOException("Invalid journal index.");
            if (indexHash == null || !ids.SequenceEqual(next))
            {
                var index = new JObject
                {
                    ["schema"] = 1,
                    ["owner"] = owner,
                    ["ids"] = new JArray(next),
                };
                // Index first: interrupted first admission remains discoverable as missing evidence.
                // No provider or import side effect is admitted until the job write also completes.
                indexHash = Publish("index.json", index.ToString(Formatting.None), indexHash, AssetGenJobCheckpoint.MaxIndexBytes);
                foreach (string old in ids.Except(next).ToArray())
                    PruneTerminal(old);
                ids = next;
            }
            else if (Hash(ReadText(PathFor("index.json"), AssetGenJobCheckpoint.MaxIndexBytes)) != indexHash)
                throw new IOException("Journal index changed outside this owner.");
            var envelope = new JObject
            {
                ["schema"] = 1,
                ["owner"] = owner,
                ["instance"] = instance,
                ["monotonic"] = now,
                ["utc"] = utc().ToUnixTimeMilliseconds() / 1000d,
                ["job"] = job,
            };
            hashes.TryGetValue(id, out string expected);
            hashes[id] = Publish(id + ".json", envelope.ToString(Formatting.None), expected, EnvelopeBytes);
            fingerprints[id] = fingerprint;
        }

        private void PruneTerminal(string id)
        {
            try
            {
                if (hashes.TryGetValue(id, out string expected))
                {
                    string text = ReadText(PathFor(id + ".json"), EnvelopeBytes);
                    var envelope = Parse(text);
                    if (Hash(text) == expected && envelope["job"] is JObject job && (string)job["JobId"] == id && Terminal((int)job["State"]))
                        File.Delete(PathFor(id + ".json"));
                }
            }
            catch { /* Retain unverified or undeletable evidence; never delete generated assets. */ }
            hashes.Remove(id);
            fingerprints.Remove(id);
        }

        private string Publish(string name, string text, string expected, int maximum)
        {
            if (Encoding.UTF8.GetByteCount(text) > maximum)
                throw new IOException("Journal metadata exceeds its schema bound.");
            lease.Verify();
            string path = PathFor(name);
            string previous = File.Exists(path) ? ReadText(path, maximum) : null;
            if ((previous == null ? null : Hash(previous)) != expected)
                throw new IOException("Journal evidence changed outside this owner.");
            string stage = PathFor(".journal-" + Guid.NewGuid().ToString("N") + ".tmp"),
                backup = stage + ".bak";
            byte[] bytes = Encoding.UTF8.GetBytes(text);
            bool confirmed = false;
            try
            {
                AtomicFilePublication.StageNew(stage, stream => stream.Write(bytes, 0, bytes.Length));
                if (previous == null)
                    AtomicFilePublication.Create(stage, path);
                else
                {
                    AtomicFilePublication.Replace(stage, path, backup);
                    if (ReadText(backup, maximum) != previous)
                        throw new IOException("Journal preimage changed during publication.");
                }
                if (ReadText(path, maximum) != text)
                    throw new IOException("Journal publication is unverified.");
                confirmed = true;
                return Hash(text);
            }
            finally
            {
                if (confirmed)
                {
                    try
                    {
                        File.Delete(stage);
                        File.Delete(backup);
                    }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                }
            }
        }

        public void Dispose()
        {
            lease?.Dispose();
            lease = null;
            loaded = false;
            ids = Array.Empty<string>();
            indexHash = null;
            hashes.Clear();
            fingerprints.Clear();
        }
    }
}
