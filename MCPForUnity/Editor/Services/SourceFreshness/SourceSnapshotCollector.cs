using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.SourceFreshness
{
    internal static class SourceSnapshotCollector
    {
        internal static Task<SourceSnapshot> CaptureAsync(SourceScope scope, CancellationToken cancellation) =>
            Task.Run(() => Capture(scope, cancellation), cancellation);

        private static SourceSnapshot Capture(SourceScope scope, CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            var reasons = new HashSet<string>(scope.Reasons, StringComparer.Ordinal);
            string source = string.Empty;
            int fileCount = 0;
            long size = 0;
            // A metadata-limited scope has no compiler input list. A file scan cannot complete it.
            if (!reasons.Contains("metadata_limit"))
            {
                var first = Sample(scope, cancellation, reasons, out size);
                // Two content scans also check membership. File timestamps alone are not evidence.
                var second = Sample(scope, cancellation, reasons, out _);
                if (first.Count != second.Count || first.Any(pair => !second.TryGetValue(pair.Key, out string hash) || hash != pair.Value))
                    reasons.Add("inputs_changed_during_scan");
                source = string.Join("\n", first.Select(p => p.Key + "\0" + p.Value));
                fileCount = first.Count;
            }
            return new SourceSnapshot(
                SourceSnapshot.Hash(scope.ProjectRoot.Replace('\\', '/')),
                scope.SessionId,
                SourceSnapshot.Hash(source),
                SourceSnapshot.Hash(scope.Context),
                reasons.Count == 0,
                reasons.OrderBy(r => r).ToArray(),
                fileCount,
                size,
                unityVersion: scope.UnityVersion
            );
        }

        private static SortedDictionary<string, string> Sample(SourceScope scope, CancellationToken cancellation, HashSet<string> reasons, out long bytes)
        {
            var files = new SortedDictionary<string, string>(StringComparer.Ordinal);
            var content = new Dictionary<string, string>(Path.DirectorySeparatorChar == '\\' ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal);
            long total = 0;
            int entries = 0;
            bool limit = false;
            bool Excluded(string name, string path)
            {
                bool Blocked(string value)
                {
                    string normalized = "/" + value.Replace('\\', '/').Trim('/') + "/";
                    return normalized.IndexOf("/Assets/Resources/GameData/", StringComparison.OrdinalIgnoreCase) >= 0
                        || normalized.Split('/').Any(part => part.StartsWith(".env", StringComparison.OrdinalIgnoreCase));
                }
                if (!Blocked(name) && !Blocked(Path.GetFullPath(path)))
                    return false;
                reasons.Add("excluded_compilation_input");
                return true;
            }
            void Add(string name, string path)
            {
                cancellation.ThrowIfCancellationRequested();
                if (limit || Excluded(name, path) || files.ContainsKey(name))
                    return;
                if (files.Count >= scope.MaxFiles)
                {
                    limit = true;
                    reasons.Add("input_limit");
                    return;
                }
                try
                {
                    string absolute = Path.GetFullPath(path);
                    if (content.TryGetValue(absolute, out string existing))
                    {
                        files[name] = existing;
                        return;
                    }
                    // Refuse links, including linked ancestors of explicitly supplied references.
                    for (string item = absolute; item != null; item = Path.GetDirectoryName(item))
                        if ((scope.FileSystem.Attributes(item) & FileAttributes.ReparsePoint) != 0)
                            throw new IOException("Linked compiler input");
                    using var input = scope.FileSystem.Open(path);
                    using var hash = SHA256.Create();
                    var buffer = new byte[65536];
                    while (true)
                    {
                        long remaining = scope.MaxBytes - total;
                        // Probe for EOF at the boundary without counting or hashing another byte.
                        int read = input.Read(buffer, 0, (int)Math.Min(buffer.Length, Math.Max(1, remaining)));
                        cancellation.ThrowIfCancellationRequested();
                        if (read == 0)
                            break;
                        if (remaining <= 0)
                        {
                            limit = true;
                            reasons.Add("input_limit");
                            return;
                        }
                        total += read;
                        hash.TransformBlock(buffer, 0, read, buffer, 0);
                    }
                    hash.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                    files[name] = SourceSnapshot.Hex(hash.Hash);
                    content[absolute] = files[name];
                }
                catch (Exception error) when (error is IOException || error is UnauthorizedAccessException || error is System.Security.SecurityException)
                {
                    reasons.Add("unreadable_or_linked_input");
                }
            }
            void Walk(string name, string path)
            {
                cancellation.ThrowIfCancellationRequested();
                if (limit || Excluded(name, path) || !scope.FileSystem.DirectoryExists(path))
                    return;
                try
                {
                    if ((scope.FileSystem.Attributes(path) & FileAttributes.ReparsePoint) != 0)
                    {
                        reasons.Add("linked_input_root");
                        return;
                    }
                    foreach (string child in scope.FileSystem.Entries(path))
                    {
                        cancellation.ThrowIfCancellationRequested();
                        if (++entries > scope.MaxFiles * 4L || limit)
                        {
                            reasons.Add("input_limit");
                            limit = true;
                            return;
                        }
                        string leaf = Path.GetFileName(child);
                        string relative = name + "/" + leaf;
                        if (Excluded(relative, child))
                            continue;
                        if (scope.FileSystem.DirectoryExists(child))
                        {
                            if (
                                !leaf.StartsWith(".", StringComparison.Ordinal)
                                && !leaf.EndsWith("~", StringComparison.Ordinal)
                                && leaf != "Library"
                                && leaf != "Temp"
                                && leaf != "obj"
                                && leaf != "bin"
                                && leaf != "node_modules"
                            )
                                Walk(relative, child);
                        }
                        else if (Relevant(relative))
                            Add(relative, child);
                    }
                }
                catch (Exception error) when (error is IOException || error is UnauthorizedAccessException || error is System.Security.SecurityException)
                {
                    reasons.Add("input_enumeration_failed");
                }
            }
            foreach (var root in scope.Roots.OrderBy(p => p.Key, StringComparer.Ordinal))
                Walk(root.Key, root.Value);
            foreach (string name in new[] { ".editorconfig", ".globalconfig", "csc.rsp" })
                if (scope.FileSystem.FileExists(Path.Combine(scope.ProjectRoot, name)))
                    Add(name, Path.Combine(scope.ProjectRoot, name));
            foreach (var input in scope.Inputs.OrderBy(p => p.Key, StringComparer.Ordinal))
                Add(input.Key, input.Value);
            bytes = total;
            return files;
        }

        private static bool Relevant(string path)
        {
            string name = Path.GetFileName(path);
            if (name.StartsWith(".env", StringComparison.OrdinalIgnoreCase))
                return false;
            string extension = Path.GetExtension(path).ToLowerInvariant();
            return extension == ".cs"
                || extension == ".asmdef"
                || extension == ".asmref"
                || extension == ".rsp"
                || extension == ".dll"
                || extension == ".ruleset"
                || extension == ".globalconfig"
                || extension == ".meta"
                || name == ".editorconfig"
                || name == "manifest.json"
                || name == "packages-lock.json"
                || name == "package.json"
                || (path.StartsWith("ProjectSettings/", StringComparison.Ordinal) && (extension == ".asset" || extension == ".txt"));
        }
    }
}
