using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Bounded source replacements with durable, conditional compensation; never a filesystem-wide transaction.</summary>
    internal static class ScriptChangeSet
    {
        private const int MaxFiles = 16,
            MaxFileBytes = 256 * 1024,
            MaxCombinedBytes = 4 * 1024 * 1024,
            MaxManifestBytes = 64 * 1024,
            MaxRecords = 32;
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private static readonly string Domain = Guid.NewGuid().ToString("N");
        private static Func<string> _storageRootForTests = null;
        private static Func<string> _domainForTests = null;
        private static Action<string, int> _stageForTests = null;
        private static Func<int, bool> _continueForTests = null;

        private sealed class Entry
        {
            public string Path { get; set; } = "";
            public string Original { get; set; } = "";
            public string Candidate { get; set; } = "";
            public string Meta { get; set; } = null;
            public string State { get; set; } = "prepared";
            public string Backup { get; set; } = null;
            public string Reason { get; set; } = "";
            public bool ReplaceReturned { get; set; } = false;
            public bool ReplaceAttempted { get; set; } = false;
            public bool StageCreated { get; set; } = false;
        }

        private sealed class Plan
        {
            public int Version { get; set; } = 1;
            public string Id { get; set; } = "";
            public string Root { get; set; } = "";
            public long Created { get; set; } = 0;
            public string Phase { get; set; } = "prepared";
            public string ApplyDomain { get; set; } = "";
            public string Error { get; set; } = "";
            public List<Entry> Files { get; set; } = new();
        }

        private sealed class Refusal : Exception
        {
            internal string Code { get; }

            internal Refusal(string code, string message)
                : base(message)
            {
                Code = code;
            }
        }

        private static string CurrentDomain => _domainForTests?.Invoke() ?? Domain;
        private static string ProjectRoot => System.IO.Path.GetFullPath(System.IO.Path.Combine(Application.dataPath, ".."));
        private static string RootDigest => Digest(Utf8.GetBytes(ProjectRoot));
        private static string StoreRoot =>
            _storageRootForTests?.Invoke() ?? SafePathUtility.ResolveWithinRoot(ProjectRoot, "Library/MCPForUnity/ScriptChangeSets");

        private static bool IsId(string value) =>
            value != null && Regex.IsMatch(value, "\\A[a-f0-9]{32}\\z", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100));

        private static bool IsDigest(string value) =>
            value != null && Regex.IsMatch(value, "\\A[a-f0-9]{64}\\z", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100));

        private static string DirectoryFor(string id) => SafePathUtility.ResolveWithinRoot(StoreRoot, id);

        private static string Artifact(Plan plan, string name) => SafePathUtility.ResolveWithinRoot(DirectoryFor(plan.Id), name);

        private static string Blob(Plan plan, int index, bool candidate) => Artifact(plan, index.ToString("D2") + (candidate ? ".candidate" : ".original"));

        private static string Target(Entry entry) => AssetPathUtility.GetFullAssetPath(entry.Path);

        private static string Staged(Plan plan, int index, string suffix)
        {
            // Preserve same-volume adjacency without extending a valid source's basename past filesystem limits.
            string directory = Path.GetDirectoryName(plan.Files[index].Path);
            return AssetPathUtility.GetFullAssetPath(directory + "/.mcp-" + plan.Id + "-" + index.ToString("D2") + "." + suffix);
        }

        internal static object HandleCommand(JObject parameters)
        {
            try
            {
                string action = String(parameters, "action");
                if (action == "prepare_change_set")
                    return Prepare(parameters);
                string id = String(parameters, "change_set_id");
                if (!IsId(id))
                    throw new Refusal("invalid_change_set", "change_set_id must be a lowercase 32-hex identifier.");
                string directory = DirectoryFor(id);
                if (!Directory.Exists(directory))
                    throw new Refusal("change_set_not_found", "The retained change set is unavailable.");
                using var lease = File.Open(SafePathUtility.ResolveWithinRoot(directory, "lease"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
                Plan plan = Load(id);
                return action switch
                {
                    "get_change_set" => Reply(plan, true),
                    "apply_change_set" => Apply(plan),
                    "recover_change_set" => Recover(plan),
                    _ => throw new Refusal("invalid_change_set", "Unknown change-set action."),
                };
            }
            catch (Refusal ex)
            {
                return Error(ex.Code, ex.Message);
            }
            catch (Exception ex)
                when (ex is IOException
                    || ex is UnauthorizedAccessException
                    || ex is ArgumentException
                    || ex is InvalidOperationException
                    || ex is JsonException
                )
            {
                return Error("change_set_unavailable", Short(ex.Message));
            }
        }

        private static object Prepare(JObject parameters)
        {
            if (parameters["files"] is not JArray files || files.Count < 1 || files.Count > MaxFiles || parameters["change_set_id"] != null)
                throw new Refusal("invalid_change_set", "Preparation requires 1–16 source entries and no change_set_id.");
            string validation = parameters["validation"] == null ? "standard" : String(parameters, "validation");
            if (!new[] { "basic", "standard", "comprehensive", "strict" }.Contains(validation))
                throw new Refusal("invalid_validation", "Unknown per-file validation level.");
            var plan = new Plan
            {
                Id = Guid.NewGuid().ToString("N"),
                Root = RootDigest,
                Created = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            };
            var sources = new List<byte[]>();
            var candidates = new List<byte[]>();
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            long combined = 0;
            foreach (JToken item in files)
            {
                if (item is not JObject file || file.Properties().Any(p => p.Name != "path" && p.Name != "contents" && p.Name != "expected_bytes_sha256"))
                    throw new Refusal("invalid_change_set", "Each file requires only path, contents and expected_bytes_sha256.");
                string path = CanonicalSource(String(file, "path"));
                if (!paths.Add(path))
                    throw new Refusal("duplicate_path", "A source may occur only once in a change set.");
                string expected = String(file, "expected_bytes_sha256").ToLowerInvariant();
                if (!IsDigest(expected))
                    throw new Refusal("invalid_change_set", "Original raw-byte SHA256 must contain 64 hex characters.");
                byte[] original = ReadBounded(AssetPathUtility.GetFullAssetPath(path), MaxFileBytes);
                if (Digest(original) != expected)
                    throw new Refusal("stale_precondition", "A source no longer matches its original raw-byte digest.");
                string contents = String(file, "contents");
                bool bom = original.Length >= 3 && original[0] == 0xef && original[1] == 0xbb && original[2] == 0xbf;
                byte[] candidate = Bytes(contents, bom);
                combined += original.Length + candidate.Length;
                if (combined > MaxCombinedBytes)
                    throw new Refusal("payload_too_large", "Combined original/candidate source bytes exceed 4 MiB.");
                ValidateBuffer(path, expected, contents, candidate, validation);
                plan.Files.Add(
                    new Entry
                    {
                        Path = path,
                        Original = expected,
                        Candidate = Digest(candidate),
                        Meta = MetaDigest(path),
                    }
                );
                sources.Add(original);
                candidates.Add(candidate);
            }
            Directory.CreateDirectory(StoreRoot);
            MakeRoom();
            string directory = DirectoryFor(plan.Id);
            Directory.CreateDirectory(directory);
            try
            {
                for (int i = 0; i < plan.Files.Count; i++)
                {
                    WriteNew(Blob(plan, i, false), sources[i]);
                    WriteNew(Blob(plan, i, true), candidates[i]);
                }
                Save(plan);
            }
            catch
            {
                DeleteOwnedRecord(plan);
                throw;
            }
            return Reply(plan, true);
        }

        private static void ValidateBuffer(string path, string expected, string contents, byte[] bytes, string validation)
        {
            string name = System.IO.Path.GetFileNameWithoutExtension(path);
            string directory = System.IO.Path.GetDirectoryName(path).Replace('\\', '/');
            var parameters = new JObject
            {
                ["action"] = "preview_script_buffer",
                ["name"] = name,
                ["path"] = directory,
                ["options"] = new JObject
                {
                    ["validate"] = validation,
                    ["applyMode"] = "sequential",
                    ["preview"] = true,
                },
            };
            var snapshot = JObject.FromObject(ManageScript.HandleCommand(parameters));
            if (snapshot.Value<bool?>("success") != true || snapshot["data"]?.Value<string>("original_bytes_sha256") != expected)
                throw new Refusal("stale_precondition", "The existing source preparation could not confirm the original bytes.");
            var data = snapshot["data"];
            parameters["source_contents"] = contents;
            parameters["source_sha256"] = Digest(Utf8.GetBytes(contents));
            parameters["source_bytes_sha256"] = Digest(bytes);
            parameters["original_sha256"] = data["original_sha256"];
            parameters["original_bytes_sha256"] = expected;
            parameters["original_bom"] = data["original_bom"];
            parameters["bom"] = data["bom"];
            parameters["validate_final"] = true;
            var result = JObject.FromObject(ManageScript.HandleCommand(parameters));
            if (result.Value<bool?>("success") != true)
                throw new Refusal("validation_failed", "Per-file final source validation failed for " + path + ".");
            if (result["data"]?.Value<string>("candidate_bytes_sha256") != Digest(bytes))
                throw new Refusal("candidate_mismatch", "Existing preparation produced different candidate bytes.");
        }

        private static object Apply(Plan plan)
        {
            if (plan.Phase != "prepared")
                return Reply(plan, false, "change_set_not_prepared", "This one-attempt plan is no longer prepared; inspect or recover its retained outcomes.");
            if (DateTimeOffset.UtcNow.ToUnixTimeSeconds() - plan.Created > 600)
                return Reply(plan, false, "change_set_expired", "Prepared plans expire after ten minutes.");
            ValidateBlobs(plan);
            // Admit the complete batch before creating staging files or touching any target.
            if (plan.Files.Any(entry => !Matches(entry, entry.Original)))
            {
                plan.Phase = "refused";
                foreach (var entry in plan.Files)
                    entry.State = "not_applied";
                Save(plan);
                return Reply(plan, false, "stale_precondition", "At least one source or metadata precondition changed; no source writes were attempted.");
            }
            plan.Phase = "applying";
            plan.ApplyDomain = CurrentDomain;
            Save(plan);
            try
            {
                for (int i = 0; i < plan.Files.Count; i++)
                {
                    Entry entry = plan.Files[i];
                    if (entry.Original == entry.Candidate)
                    {
                        entry.State = "unchanged";
                        Save(plan);
                        continue;
                    }
                    string stage = Staged(plan, i, "tmp");
                    string backup = Staged(plan, i, "bak");
                    if (File.Exists(backup))
                        throw new Refusal("staging_conflict", "A replacement backup path already exists and was not overwritten.");
                    WriteNew(stage, ReadBounded(Blob(plan, i, true), MaxFileBytes));
                    entry.StageCreated = true;
                    entry.State = "writing";
                    Save(plan);
                    _stageForTests?.Invoke("before_replace", i);
                    if (!Matches(entry, entry.Original))
                        throw new Refusal("stale_during_apply", "A source or metadata changed after whole-set admission.");
                    if (File.Exists(backup))
                        throw new Refusal("staging_conflict", "A replacement backup appeared and was not overwritten.");
                    entry.ReplaceAttempted = true;
                    Save(plan);
                    File.Replace(stage, Target(entry), backup);
                    entry.ReplaceReturned = true;
                    entry.State = "confirmed";
                    entry.Backup = Digest(ReadBounded(backup, MaxFileBytes));
                    Save(plan);
                    _stageForTests?.Invoke("after_replace", i);
                    if (entry.Backup != entry.Original || !Matches(entry, entry.Candidate))
                        throw new Refusal("external_writer", "An interfering writer was observed during application.");
                    if (_continueForTests?.Invoke(i) == false)
                        return Reply(plan, false, "application_interrupted", "Application stopped at a durable boundary; explicit recovery is available.");
                }
                plan.Phase = "applied";
                Save(plan);
                CleanupStaging(plan);
                return Reply(plan, true);
            }
            catch (Exception ex)
            {
                // Replacement evidence was recorded before any postcommit callback.
                plan.Error = Short(ex.Message);
                Compensate(plan);
                return Reply(plan, false, plan.Phase == "compensated" ? "application_compensated" : "change_set_conflict", plan.Error);
            }
        }

        private static object Recover(Plan plan)
        {
            if (plan.Phase == "prepared" || plan.Phase == "applied" || plan.Phase == "compensated" || plan.Phase == "refused")
                return Reply(plan, true);
            ValidateBlobs(plan);
            Compensate(plan);
            return Reply(plan, plan.Phase == "compensated", plan.Phase == "compensated" ? null : "change_set_conflict");
        }

        private static void Compensate(Plan plan)
        {
            plan.Phase = "compensating";
            if (!TrySave(plan))
            {
                foreach (var entry in plan.Files.Where(entry => entry.State != "prepared" && entry.State != "unchanged"))
                {
                    entry.State = "uncertain";
                    entry.Reason = "recovery_manifest_unavailable";
                }
                plan.Phase = "conflict";
                return;
            }
            for (int i = plan.Files.Count - 1; i >= 0; i--)
            {
                Entry entry = plan.Files[i];
                if (entry.State == "prepared" || entry.State == "not_applied")
                {
                    entry.State = "not_applied";
                    continue;
                }
                if (entry.State == "unchanged" || entry.State == "restored")
                    continue;
                try
                {
                    if (MetaDigest(entry.Path) != entry.Meta)
                        throw new Refusal("metadata_changed", "Metadata changed externally.");
                    byte[] restore = ReadBounded(Blob(plan, i, false), MaxFileBytes);
                    string restoreDigest = entry.Original;
                    string backup = Staged(plan, i, "bak");
                    if (File.Exists(backup))
                    {
                        byte[] actualBackup = ReadBounded(backup, MaxFileBytes);
                        string actualDigest = Digest(actualBackup);
                        if (actualDigest != (entry.Backup ?? entry.Original))
                            throw new Refusal("backup_changed", "The retained replacement preimage cannot be verified.");
                        restore = actualBackup;
                        restoreDigest = actualDigest;
                    }
                    string fullPath = Target(entry);
                    using (var stream = File.Open(fullPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                    {
                        string observed = Digest(ReadStream(stream, MaxFileBytes));
                        if (observed == entry.Original)
                        {
                            entry.State = entry.ReplaceAttempted ? "original_observed" : "not_applied";
                            entry.Reason = entry.ReplaceAttempted ? "original_bytes_observed_without_restoration" : "replacement_not_attempted";
                            continue;
                        }
                        if (observed != entry.Candidate)
                            throw new Refusal("external_writer", "Current source differs from the prepared candidate; it was not overwritten.");
                        entry.State = "restoring";
                        Save(plan);
                        _stageForTests?.Invoke("before_restore", i);
                        // Keep the exclusive target handle through comparison, write, flush and verification.
                        // This is a journaled per-file restore, not a filesystem-wide or callback transaction.
                        stream.Position = 0;
                        stream.Write(restore, 0, restore.Length);
                        stream.SetLength(restore.Length);
                        stream.Flush(true);
                        stream.Position = 0;
                        if (Digest(ReadStream(stream, MaxFileBytes)) != restoreDigest)
                            throw new IOException("Restored bytes could not be confirmed.");
                    }
                    entry.State = restoreDigest == entry.Original ? "restored" : "restored_external_preimage";
                    entry.Reason = restoreDigest == entry.Original ? "original_bytes_restored" : "interfering_preimage_restored";
                }
                catch (Exception ex)
                {
                    entry.State = "uncertain";
                    entry.Reason = Short(ex.Message);
                }
                if (!TrySave(plan))
                {
                    entry.State = "uncertain";
                    entry.Reason = "recovery_manifest_unavailable";
                }
            }
            plan.Phase = plan.Files.Any(entry => entry.State == "uncertain" || entry.State == "restored_external_preimage") ? "conflict" : "compensated";
            if (!TrySave(plan))
                plan.Phase = "conflict";
            CleanupStaging(plan);
        }

        private static bool Matches(Entry entry, string digest)
        {
            try
            {
                return Digest(ReadBounded(Target(entry), MaxFileBytes)) == digest && MetaDigest(entry.Path) == entry.Meta;
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is InvalidOperationException || ex is Refusal)
            {
                return false;
            }
        }

        private static string MetaDigest(string path)
        {
            string meta = AssetPathUtility.GetFullAssetPath(path + ".meta");
            return File.Exists(meta) ? Digest(ReadBounded(meta, MaxManifestBytes)) : null;
        }

        private static string CanonicalSource(string path)
        {
            if (path.Length > 512 || !path.EndsWith(".cs", StringComparison.Ordinal))
                throw new Refusal("invalid_path", "A source must be a .cs path under Assets/.");
            string canonical = AssetPathUtility.GetContainedAssetPath(path);
            if (
                !Regex.IsMatch(
                    System.IO.Path.GetFileNameWithoutExtension(canonical),
                    "\\A[A-Za-z_][A-Za-z0-9_]*\\z",
                    RegexOptions.CultureInvariant,
                    TimeSpan.FromMilliseconds(100)
                )
            )
                throw new Refusal("invalid_path", "Source names must match the existing script-name contract.");
            return canonical;
        }

        private static string String(JObject value, string name)
        {
            if (value[name]?.Type != JTokenType.String)
                throw new Refusal("invalid_change_set", name + " must be a string.");
            return value.Value<string>(name);
        }

        private static byte[] Bytes(string contents, bool bom)
        {
            if (contents.IndexOf('\0') >= 0)
                throw new Refusal("invalid_encoding", "Source buffers must be NUL-free UTF-8.");
            byte[] text = Utf8.GetBytes(contents);
            if (text.Length > MaxFileBytes - (bom ? 3 : 0))
                throw new Refusal("payload_too_large", "Each source, including its preserved BOM, is limited to 256 KiB.");
            return bom
                ? new byte[] { 0xef, 0xbb, 0xbf }
                    .Concat(text)
                    .ToArray()
                : text;
        }

        private static byte[] ReadBounded(string path, int limit)
        {
            using var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            return ReadStream(stream, limit);
        }

        private static byte[] ReadStream(Stream stream, int limit)
        {
            if (stream.Length > limit)
                throw new Refusal("payload_too_large", "A retained source or artifact exceeds its byte budget.");
            var bytes = new byte[(int)stream.Length];
            int total = 0;
            while (total < bytes.Length)
            {
                int count = stream.Read(bytes, total, bytes.Length - total);
                if (count == 0)
                    throw new IOException("A source or artifact changed during its bounded read.");
                total += count;
            }
            if (stream.ReadByte() != -1)
                throw new IOException("A source or artifact grew during its bounded read.");
            return bytes;
        }

        private static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static void WriteNew(string path, byte[] bytes)
        {
            using var stream = File.Open(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(true);
        }

        private static void Save(Plan plan)
        {
            byte[] bytes = Utf8.GetBytes(JsonConvert.SerializeObject(plan, Formatting.None));
            if (bytes.Length > MaxManifestBytes)
                throw new Refusal("manifest_too_large", "The recovery manifest exceeds 64 KiB.");
            string path = Artifact(plan, "manifest.json");
            string temporary = Artifact(plan, Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                WriteNew(temporary, bytes);
                if (File.Exists(path))
                    File.Replace(temporary, path, null);
                else
                    File.Move(temporary, path);
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(temporary);
            }
        }

        private static bool TrySave(Plan plan)
        {
            try
            {
                Save(plan);
                return true;
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is Refusal)
            {
                plan.Error = Short(ex.Message);
                return false;
            }
        }

        private static Plan Load(string id)
        {
            string path = SafePathUtility.ResolveWithinRoot(DirectoryFor(id), "manifest.json");
            var plan = JsonConvert.DeserializeObject<Plan>(
                Utf8.GetString(ReadBounded(path, MaxManifestBytes)),
                new JsonSerializerSettings { MaxDepth = 16, MissingMemberHandling = MissingMemberHandling.Error }
            );
            if (
                plan == null
                || plan.Version != 1
                || plan.Id != id
                || plan.Root != RootDigest
                || plan.Files == null
                || plan.Files.Count < 1
                || plan.Files.Count > MaxFiles
                || !new[] { "prepared", "applying", "applied", "compensating", "compensated", "conflict", "refused" }.Contains(plan.Phase)
            )
                throw new Refusal("manifest_invalid", "The retained change-set manifest is invalid or belongs to another project.");
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (Entry entry in plan.Files)
                if (
                    entry == null
                    || entry.Path == null
                    || CanonicalSource(entry.Path) != entry.Path
                    || !paths.Add(entry.Path)
                    || !IsDigest(entry.Original)
                    || !IsDigest(entry.Candidate)
                    || (entry.Meta != null && !IsDigest(entry.Meta))
                    || (entry.Backup != null && !IsDigest(entry.Backup))
                    || !new[]
                    {
                        "prepared",
                        "writing",
                        "confirmed",
                        "unchanged",
                        "not_applied",
                        "restoring",
                        "restored",
                        "original_observed",
                        "restored_external_preimage",
                        "uncertain",
                    }.Contains(entry.State)
                )
                    throw new Refusal("manifest_invalid", "A retained source entry is invalid.");
            return plan;
        }

        private static void ValidateBlobs(Plan plan)
        {
            long total = 0;
            for (int i = 0; i < plan.Files.Count; i++)
            {
                byte[] original = ReadBounded(Blob(plan, i, false), MaxFileBytes),
                    candidate = ReadBounded(Blob(plan, i, true), MaxFileBytes);
                total += original.Length + candidate.Length;
                if (total > MaxCombinedBytes || Digest(original) != plan.Files[i].Original || Digest(candidate) != plan.Files[i].Candidate)
                    throw new Refusal("manifest_invalid", "Retained source buffers are corrupt or over budget; no source writes were attempted.");
            }
        }

        private static void CleanupStaging(Plan plan)
        {
            for (int i = 0; i < plan.Files.Count; i++)
            {
                try
                {
                    string temporary = Staged(plan, i, "tmp");
                    if (plan.Files[i].StageCreated && File.Exists(temporary) && Digest(ReadBounded(temporary, MaxFileBytes)) == plan.Files[i].Candidate)
                        File.Delete(temporary);
                    string backup = Staged(plan, i, "bak");
                    if (
                        (plan.Phase == "applied" || plan.Files[i].State == "restored")
                        && File.Exists(backup)
                        && Digest(ReadBounded(backup, MaxFileBytes)) == (plan.Files[i].Backup ?? plan.Files[i].Original)
                    )
                        File.Delete(backup);
                }
                catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is InvalidOperationException || ex is Refusal) { }
            }
        }

        private static void MakeRoom()
        {
            string[] directories = Directory.EnumerateDirectories(StoreRoot).Take(MaxRecords + 1).ToArray();
            if (directories.Length > MaxRecords)
                throw new Refusal("change_set_capacity", "The retained record directory exceeds its supported budget.");
            if (directories.Length < MaxRecords)
                return;
            foreach (string directory in directories)
            {
                string id = System.IO.Path.GetFileName(directory);
                if (!IsId(id))
                    continue;
                try
                {
                    Plan plan = Load(id);
                    bool terminal = new[] { "applied", "compensated", "refused" }.Contains(plan.Phase);
                    if (!terminal && !(plan.Phase == "prepared" && DateTimeOffset.UtcNow.ToUnixTimeSeconds() - plan.Created > 600))
                        continue;
                    DeleteOwnedRecord(plan);
                    return;
                }
                catch (Exception ex)
                    when (ex is IOException || ex is UnauthorizedAccessException || ex is Refusal || ex is JsonException || ex is InvalidOperationException) { }
            }
            throw new Refusal("change_set_capacity", "At most 32 retained plans are supported; unresolved recovery evidence is never evicted.");
        }

        private static void DeleteOwnedRecord(Plan plan)
        {
            string directory = DirectoryFor(plan.Id);
            if (!Directory.Exists(directory))
                return;
            var allowed = new HashSet<string>(StringComparer.Ordinal) { "manifest.json", "lease" };
            for (int i = 0; i < plan.Files.Count; i++)
            {
                allowed.Add(i.ToString("D2") + ".original");
                allowed.Add(i.ToString("D2") + ".candidate");
            }
            string[] entries = Directory.EnumerateFileSystemEntries(directory).Take(2 * MaxFiles + 3).ToArray();
            if (entries.Any(path => !allowed.Contains(System.IO.Path.GetFileName(path)) || Directory.Exists(path)))
                throw new Refusal("artifact_changed", "An owned record contains unexpected entries and was not removed.");
            foreach (string entry in entries)
                File.Delete(SafePathUtility.ResolveWithinRoot(directory, System.IO.Path.GetFileName(entry)));
            Directory.Delete(directory);
        }

        private static JObject Reply(Plan plan, bool success, string code = null, string message = null)
        {
            var rows = new JArray();
            foreach (Entry entry in plan.Files)
            {
                string observed = null;
                try
                {
                    observed = Digest(ReadBounded(Target(entry), MaxFileBytes));
                }
                catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is Refusal || ex is InvalidOperationException) { }
                bool uncertain =
                    entry.State == "writing"
                    || entry.State == "restoring"
                    || (entry.State == "confirmed" && observed != entry.Candidate)
                    || (entry.State == "restored" && observed != entry.Original);
                rows.Add(
                    new JObject
                    {
                        ["path"] = entry.Path,
                        ["state"] = uncertain ? "uncertain" : entry.State,
                        ["recorded_state"] = entry.State,
                        ["original_bytes_sha256"] = entry.Original,
                        ["candidate_bytes_sha256"] = entry.Candidate,
                        ["observed_bytes_sha256"] = observed,
                        ["replace_returned"] = entry.ReplaceReturned,
                        ["reason"] = entry.Reason,
                    }
                );
            }
            return new JObject
            {
                ["success"] = success,
                ["code"] = code,
                ["message"] = message ?? "Change-set state observed.",
                ["data"] = new JObject
                {
                    ["change_set_id"] = plan.Id,
                    ["phase"] = plan.Phase,
                    ["files"] = rows,
                    ["interrupted"] = (plan.Phase == "applying" || plan.Phase == "compensating") && plan.ApplyDomain != CurrentDomain,
                    ["recovery_manifest"] = "Library/MCPForUnity/ScriptChangeSets/" + plan.Id + "/manifest.json",
                    ["filesystem_transaction"] = false,
                    ["callback_rollback"] = false,
                    ["refresh_status"] = "not_requested",
                    ["project_compilation"] = false,
                    ["limitations"] = new JArray(
                        "Per-file validation does not establish cross-file semantic correctness.",
                        "Per-file atomic replacement and journaled conditional restoration are not a filesystem-wide transaction.",
                        "Unity automatic import or external uncooperative writers may act outside this request; inspect uncertain outcomes before further writes."
                    ),
                },
            };
        }

        private static JObject Error(string code, string message) =>
            new JObject
            {
                ["success"] = false,
                ["code"] = code,
                ["message"] = message,
                ["data"] = new JObject { ["source_state"] = "not_observed" },
            };

        private static string Short(string message) => message == null ? "" : message.Substring(0, Math.Min(512, message.Length));
    }
}
