using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Setup
{
    // The lock coordinates installer instances. Content checks also preserve edits made
    // by non-participating writers; only this transaction's private staging is recursive.
    internal sealed class RoslynFileSet : IDisposable
    {
        private const string Record = ".mcp-roslyn-owned.json";
        private readonly string folder,
            stage,
            lockPath;
        private readonly HashSet<string> allowed;
        private readonly FileStream gate;
        private readonly Dictionary<string, string> owned;
        private string recordHash;
        private bool retainRecovery;

        internal RoslynFileSet(string folder, IEnumerable<string> names)
        {
            this.folder = GuardedFileSystem.CheckPath(folder);
            Directory.CreateDirectory(this.folder);
            allowed = new HashSet<string>(names, StringComparer.Ordinal);
            foreach (string name in names)
                allowed.Add(name + ".meta");
            lockPath = PathFor(".mcp-roslyn.lock");
            gate = new FileStream(lockPath, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
            stage = PathFor(".mcp-roslyn-stage-" + Guid.NewGuid().ToString("N"));
            try
            {
                Directory.CreateDirectory(stage);
                string recordPath = PathFor(Record);
                recordHash = HashOrNull(recordPath);
                owned = new Dictionary<string, string>(StringComparer.Ordinal);
                if (recordHash != null)
                {
                    var record = JObject.Parse(File.ReadAllText(recordPath));
                    if (record.Value<int>("version") != 1 || !(record["files"] is JObject files))
                        throw new IOException("Unsupported Roslyn ownership record.");
                    foreach (var property in files.Properties())
                    {
                        string hash = property.Value.Type == JTokenType.String ? (string)property.Value : null;
                        if (!allowed.Contains(property.Name) || hash == null || hash.Length != 64 || hash.Any(c => !Uri.IsHexDigit(c)))
                            throw new IOException("Invalid Roslyn ownership entry.");
                        owned.Add(property.Name, hash);
                    }
                }
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        internal string PathFor(string name) => GuardedFileSystem.CheckPath(Path.Combine(folder, name));

        internal string StagePath(string name) => GuardedFileSystem.CheckPath(Path.Combine(stage, name));

        internal static string HashOrNull(string path)
        {
            GuardedFileSystem.CheckPath(path);
            if (!File.Exists(path))
                return null;
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "");
        }

        internal void Install(IReadOnlyList<string> names)
        {
            var next = new Dictionary<string, string>(owned, StringComparer.Ordinal);
            var changes = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (string name in names)
            {
                string old = HashOrNull(PathFor(name));
                if (old != null && (!owned.TryGetValue(name, out var expected) || old != expected))
                    throw new IOException($"Preserving unowned or modified Roslyn file: {name}. Move it explicitly before installing.");
                // Existing unowned metadata is retained but never adopted.
                PathFor(name + ".meta");
                string staged = StagePath(name);
                next[name] = HashOrNull(staged) ?? throw new IOException($"Missing staged Roslyn DLL: {name}");
                changes[name] = staged;
            }
            Apply(changes, next);
        }

        internal void RecordCreatedMetadata(IEnumerable<string> names)
        {
            var next = new Dictionary<string, string>(owned, StringComparer.Ordinal);
            foreach (string name in names)
            {
                if (!owned.TryGetValue(name, out var dllHash) || HashOrNull(PathFor(name)) != dllHash)
                    continue;
                string hash = HashOrNull(PathFor(name + ".meta"));
                if (hash != null)
                    next[name + ".meta"] = hash;
            }
            if (next.Any(pair => !owned.TryGetValue(pair.Key, out var value) || value != pair.Value))
                Apply(new Dictionary<string, string>(), next);
        }

        internal int Uninstall(IReadOnlyList<string> names)
        {
            if (recordHash == null)
                return 0;
            var next = new Dictionary<string, string>(owned, StringComparer.Ordinal);
            var changes = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (string name in names)
            {
                if (!owned.TryGetValue(name, out var expected) || HashOrNull(PathFor(name)) != expected)
                    continue;
                changes[name] = null;
                next.Remove(name);
                string meta = name + ".meta";
                if (owned.TryGetValue(meta, out var metaHash) && HashOrNull(PathFor(meta)) == metaHash)
                {
                    changes[meta] = null;
                    next.Remove(meta);
                }
            }
            Apply(changes, next);
            return changes.Keys.Count(name => name.EndsWith(".dll", StringComparison.Ordinal));
        }

        private sealed class Replacement
        {
            internal string Name,
                Backup,
                NewHash;
            internal bool HadPrevious,
                Installed;
        }

        private void Apply(Dictionary<string, string> changes, Dictionary<string, string> next)
        {
            if (HashOrNull(PathFor(Record)) != recordHash)
                throw new IOException("Roslyn ownership changed during this operation. Retry with the current files.");
            var snapshot = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (string name in changes.Keys)
            {
                string current = HashOrNull(PathFor(name));
                if (current != null && (!owned.TryGetValue(name, out var expected) || expected != current))
                    throw new IOException($"Roslyn file changed before publication: {name}");
                snapshot[name] = current;
            }
            string manifestStage = StagePath("record-" + Guid.NewGuid().ToString("N"));
            File.WriteAllText(manifestStage, new JObject { ["version"] = 1, ["files"] = JObject.FromObject(next) }.ToString(), new UTF8Encoding(false));
            changes[Record] = manifestStage;
            snapshot[Record] = recordHash;
            var completed = new List<Replacement>();
            try
            {
                foreach (var pair in changes)
                {
                    string live = PathFor(pair.Key);
                    if (HashOrNull(live) != snapshot[pair.Key])
                        throw new IOException($"Roslyn file changed during publication: {pair.Key}");
                    var item = new Replacement
                    {
                        Name = pair.Key,
                        Backup = StagePath("previous-" + Guid.NewGuid().ToString("N")),
                        NewHash = pair.Value == null ? null : HashOrNull(pair.Value),
                    };
                    completed.Add(item);
                    if (snapshot[pair.Key] != null)
                    {
                        File.Move(live, item.Backup);
                        item.HadPrevious = true;
                    }
                    if (pair.Value != null)
                    {
                        File.Move(GuardedFileSystem.CheckPath(pair.Value), PathFor(pair.Key));
                        item.Installed = true;
                    }
                }
                owned.Clear();
                foreach (var pair in next)
                    owned.Add(pair.Key, pair.Value);
                recordHash = HashOrNull(PathFor(Record));
            }
            catch (Exception original)
            {
                var failures = new List<Exception> { original };
                foreach (var item in completed.AsEnumerable().Reverse())
                {
                    try
                    {
                        string live = PathFor(item.Name);
                        if (item.Installed)
                        {
                            if (HashOrNull(live) != item.NewHash)
                                throw new IOException($"Modified replacement retained: {item.Name}");
                            File.Delete(live);
                        }
                        if (item.HadPrevious)
                            File.Move(GuardedFileSystem.CheckPath(item.Backup), PathFor(item.Name));
                    }
                    catch (Exception rollback)
                    {
                        retainRecovery = true;
                        failures.Add(rollback);
                    }
                }
                if (retainRecovery)
                    throw new AggregateException($"Roslyn rollback requires recovery from {stage}. Existing files were preserved.", failures);
                throw;
            }
        }

        public void Dispose()
        {
            // Failure to clean private staging must not mask an installation error or
            // discard the only recoverable copy after a blocked rollback.
            try
            {
                if (!retainRecovery && Directory.Exists(GuardedFileSystem.CheckPath(stage)))
                {
                    GuardedFileSystem.CheckTree(stage);
                    Directory.Delete(stage, true);
                }
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[MCP] Roslyn staging retained at {stage}: {ex.Message}");
            }
            finally
            {
                gate.Dispose();
                try
                {
                    File.Delete(GuardedFileSystem.CheckPath(lockPath));
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[MCP] Roslyn lock retained at {lockPath}: {ex.Message}");
                }
            }
        }
    }
}
