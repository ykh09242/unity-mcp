using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace MCPForUnity.Editor.Services.SourceFreshness
{
    internal sealed class SourceFileSystem
    {
        internal Func<string, bool> DirectoryExists = Directory.Exists;
        internal Func<string, bool> FileExists = File.Exists;
        internal Func<string, IEnumerable<string>> Entries = Directory.EnumerateFileSystemEntries;
        internal Func<string, FileAttributes> Attributes = File.GetAttributes;
        internal Func<string, Stream> Open = path => new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
    }

    // Captured on the Editor thread. The collector only consumes this detached data.
    internal sealed class SourceScope
    {
        internal readonly string ProjectRoot;
        internal readonly string SessionId;
        internal readonly string Context;
        internal readonly Dictionary<string, string> Roots = new(StringComparer.Ordinal);
        internal readonly Dictionary<string, string> Inputs = new(StringComparer.Ordinal);
        internal readonly Dictionary<string, string> Outputs = new(StringComparer.Ordinal);
        internal readonly List<string> Reasons = new();
        internal long MaxBytes = 256L * 1024 * 1024;
        internal int MaxFiles = 100000;
        internal SourceFileSystem FileSystem = new();
        internal string UnityVersion;

        internal SourceScope(string root, string sessionId, string context)
        {
            ProjectRoot = System.IO.Path.GetFullPath(root);
            SessionId = sessionId;
            Context = context;
            foreach (string name in new[] { "Assets", "Packages", "ProjectSettings" })
                Roots.Add(name, System.IO.Path.Combine(ProjectRoot, name));
        }
    }

    internal sealed class SourceSnapshot
    {
        public string ProjectId { get; }
        public string SessionId { get; }
        public string SourceDigest { get; }
        public string ContextDigest { get; }
        public bool Complete { get; }
        public string[] Reasons { get; }
        public int FileCount { get; }
        public long InputBytes { get; }
        public long ObservedAtUnixMs { get; }
        public string UnityVersion { get; }
        public string Token => Hash(ProjectId + "\n" + SessionId + "\n" + SourceDigest + "\n" + ContextDigest);

        [Newtonsoft.Json.JsonConstructor]
        public SourceSnapshot(
            string projectId,
            string sessionId,
            string sourceDigest,
            string contextDigest,
            bool complete,
            string[] reasons,
            int fileCount,
            long inputBytes,
            long observedAtUnixMs = 0,
            string unityVersion = null
        )
        {
            ProjectId = projectId;
            SessionId = sessionId;
            SourceDigest = sourceDigest;
            ContextDigest = contextDigest;
            Complete = complete;
            Reasons = reasons ?? Array.Empty<string>();
            FileCount = fileCount;
            InputBytes = inputBytes;
            ObservedAtUnixMs = observedAtUnixMs == 0 ? DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() : observedAtUnixMs;
            UnityVersion = unityVersion;
        }

        internal static string Hash(string value)
        {
            using var hash = SHA256.Create();
            return Hex(hash.ComputeHash(Encoding.UTF8.GetBytes(value)));
        }

        internal static string Hex(byte[] bytes) => BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
    }
}
