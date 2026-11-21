using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Helpers;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Prefabs
{
    // Unity state is collected on the Editor thread. Only immutable paths and file I/O
    // cross the worker boundary; readers remain owned until compare-before-save.
    internal sealed class PrefabSourceRevision : IDisposable
    {
        private const long MaxBytes = 256L * 1024 * 1024;
        private const long DirectCallBytes = 1024 * 1024;
        private static readonly SemaphoreSlim Workers = new SemaphoreSlim(2);
        private static readonly HashSet<PrefabSourceRevision> Active = new();
        private static readonly CancellationTokenSource Lifetime = new CancellationTokenSource();
        private readonly string path;
        private readonly string assetsRoot;
        private readonly string imports;
        private readonly string[] dependencies;
        private readonly bool windows;
        private readonly List<(FileStream stream, long bytes, DateTime written, (ulong, ulong, long, long)? unixId)> readers = new();
        private bool lacksChangeIdentity;
        internal string Hash { get; private set; }

        static PrefabSourceRevision()
        {
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
        }

        private static void Stop()
        {
            Lifetime.Cancel();
            foreach (var revision in Active.ToArray())
                revision.Dispose();
        }

        private PrefabSourceRevision(string path)
        {
            this.path = path;
            assetsRoot = Application.dataPath;
            windows = Application.platform == RuntimePlatform.WindowsEditor;
            imports = ImportIdentity();
            dependencies = Dependencies();
            Active.Add(this);
        }

        private string ImportIdentity() => AssetDatabase.AssetPathToGUID(path) + ":" + AssetDatabase.GetAssetDependencyHash(path);

        private string[] Dependencies()
        {
            string[] found = AssetDatabase
                .GetDependencies(path, true)
                .Append(path)
                .Distinct(StringComparer.Ordinal)
                .OrderBy(p => p, StringComparer.Ordinal)
                .ToArray();
            if (found.Length > 4096)
                throw new ArgumentException("Safe prefab plans are limited to 4096 dependencies.");
            return found;
        }

        internal static async Task<PrefabSourceRevision> Observe(string path, bool background, CancellationToken cancellationToken)
        {
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, Lifetime.Token);
            CancellationToken token = linked.Token;
            token.ThrowIfCancellationRequested();
            var revision = new PrefabSourceRevision(path);
            try
            {
                if (background)
                {
                    await Workers.WaitAsync(token);
                    try
                    {
                        await Task.Run(() => revision.ReadFiles(MaxBytes, token), token);
                    }
                    finally
                    {
                        Workers.Release();
                    }
                }
                else
                    revision.ReadFiles(DirectCallBytes, token);
                token.ThrowIfCancellationRequested();
                revision.VerifyCurrent();
                return revision;
            }
            catch
            {
                revision.Dispose();
                throw;
            }
        }

        private void ReadFiles(long limit, CancellationToken token)
        {
            long remaining = limit;
            var fingerprint = new StringBuilder(imports);
            var buffer = new byte[65536];
            using var sha = SHA256.Create();
            foreach (string dependency in dependencies.Where(p => p.StartsWith("Assets/", StringComparison.Ordinal)))
            {
                token.ThrowIfCancellationRequested();
                string asset = SafePathUtility.ResolveWithinRoot(assetsRoot, dependency.Substring(7));
                if (Directory.Exists(asset))
                    continue;
                foreach (bool meta in new[] { false, true })
                {
                    string file = meta ? SafePathUtility.ResolveWithinRoot(assetsRoot, dependency.Substring(7) + ".meta") : asset;
                    var stamp = new FileInfo(file);
                    if (!stamp.Exists)
                        throw new ArgumentException("Prefab source/dependency file is unavailable.");
                    long length = stamp.Length;
                    if (length > remaining)
                        throw new ArgumentException(
                            limit == DirectCallBytes
                                ? "Use the asynchronous Editor command for prefab source/dependency bytes above 1 MiB."
                                : "Safe prefab source/dependency bytes exceed 256 MiB."
                        );
                    var unixId = windows ? null : UnixFileIdentity.Read(file);
                    lacksChangeIdentity |= !windows && unixId == null;
                    FileStream stream;
                    lock (readers)
                    {
                        token.ThrowIfCancellationRequested();
                        stream = File.OpenRead(file);
                        readers.Add((stream, length, stamp.LastWriteTimeUtc, unixId));
                    }
                    string digest = HashFile(stream, length, sha, buffer, token);
                    fingerprint.Append('\n').Append(dependency).Append(meta ? ":meta:" : ":asset:").Append(digest);
                    remaining -= length;
                }
            }
            sha.Initialize();
            Hash = Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(fingerprint.ToString())));
        }

        internal static string HashFile(Stream stream, long length, SHA256 sha, byte[] buffer, CancellationToken token)
        {
            long left = length;
            sha.Initialize();
            while (left > 0)
            {
                token.ThrowIfCancellationRequested();
                int count = stream.Read(buffer, 0, (int)Math.Min(left, buffer.Length));
                if (count == 0)
                    throw Drift();
                sha.TransformBlock(buffer, 0, count, buffer, 0);
                left -= count;
            }
            if (stream.Length != length)
                throw Drift();
            sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            return Convert.ToBase64String(sha.Hash);
        }

        internal async Task VerifyBeforeSave(bool background, CancellationToken token)
        {
            if (lacksChangeIdentity)
            {
                // A Mono profile without a reliable native change identity gets another
                // complete byte comparison after preparing the isolated prefab contents.
                using var final = await Observe(path, background, token);
                if (final.Hash != Hash)
                    throw Drift();
            }
            VerifyCurrent();
        }

        internal void VerifyCurrent()
        {
            Lifetime.Token.ThrowIfCancellationRequested();
            if (imports != ImportIdentity() || !dependencies.SequenceEqual(Dependencies(), StringComparer.Ordinal))
                throw Drift();
            if (windows)
                return; // File.OpenRead denies writers and deletion until the final comparison.
            foreach (var entry in readers)
            {
                if (entry.unixId.HasValue)
                {
                    if (UnixFileIdentity.Read(entry.stream.Name) != entry.unixId)
                        throw Drift();
                    continue;
                }
                var stamp = new FileInfo(entry.stream.Name);
                if (!stamp.Exists || stamp.Length != entry.bytes || stamp.LastWriteTimeUtc != entry.written)
                    throw Drift();
            }
        }

        private static InvalidOperationException Drift() => new InvalidOperationException("Prefab source or dependency drifted. Preview again.");

        public void Dispose()
        {
            Active.Remove(this);
            lock (readers)
            {
                foreach (var entry in readers)
                    entry.stream.Dispose();
                readers.Clear();
            }
        }

        // Mono.Posix is optional in Editor profiles. Use the runtime's portable stat
        // mapping rather than duplicating the different Darwin and Linux struct layouts.
        private static class UnixFileIdentity
        {
            private static readonly Type Stat = Type.GetType("Mono.Unix.Native.Stat, Mono.Posix", false);
            private static readonly MethodInfo ReadStat = Stat
                ?.Assembly.GetType("Mono.Unix.Native.Syscall")
                ?.GetMethod("stat", new[] { typeof(string), Stat.MakeByRefType() });
            private static readonly FieldInfo Device = Stat?.GetField("st_dev");
            private static readonly FieldInfo Inode = Stat?.GetField("st_ino");
            private static readonly FieldInfo Changed = Stat?.GetField("st_ctime");
            private static readonly FieldInfo Nanoseconds = Stat?.GetField("st_ctime_nsec");
            private static volatile bool unavailable;

            internal static (ulong, ulong, long, long)? Read(string file)
            {
                if (unavailable || ReadStat == null || Device == null || Inode == null || Changed == null || Nanoseconds == null)
                    return null;
                object[] args = { file, null };
                try
                {
                    if ((int)ReadStat.Invoke(null, args) != 0)
                        throw Drift();
                }
                catch (TargetInvocationException ex)
                    when (ex.GetBaseException() is DllNotFoundException || ex.GetBaseException() is EntryPointNotFoundException)
                {
                    unavailable = true;
                    return null;
                }
                object stat = args[1];
                long nanoseconds = (long)Nanoseconds.GetValue(stat);
                // A low-resolution filesystem may silently return whole seconds.
                if (nanoseconds == 0)
                    return null;
                return ((ulong)Device.GetValue(stat), (ulong)Inode.GetValue(stat), (long)Changed.GetValue(stat), nanoseconds);
            }
        }
    }
}
