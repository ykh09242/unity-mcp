using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Own file readers through hashing and the Editor-thread observation.</summary>
    [InitializeOnLoad]
    internal sealed class ReferenceFilePreparation : IDisposable
    {
        private static readonly HashSet<ReferenceFilePreparation> active = new HashSet<ReferenceFilePreparation>();
        private static readonly SemaphoreSlim slots = new SemaphoreSlim(2);
        private readonly CancellationTokenSource cancellation;
        private readonly CancellationToken token;
        private readonly FileStream source;
        private readonly FileStream metadata;
        private Task worker;
        private bool disposed;
        internal string SourceHash { get; private set; }
        internal string MetadataHash { get; private set; }
        private readonly string sourcePath;
        private readonly string metadataPath;

        static ReferenceFilePreparation()
        {
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
            EditorApplication.quitting += BeforeReload;
        }

        private ReferenceFilePreparation(string path, CancellationToken caller)
        {
            cancellation = CancellationTokenSource.CreateLinkedTokenSource(caller);
            token = cancellation.Token;
            try
            {
                sourcePath = AssetPathUtility.GetFullAssetPath(path);
                metadataPath = AssetPathUtility.GetFullAssetPath(path + ".meta");
                source = File.OpenRead(sourcePath);
                metadata = File.OpenRead(metadataPath);
                if (source.Length > 8L * 1024 * 1024 * 1024 || metadata.Length > 16L * 1024 * 1024)
                    throw new ArgumentException("Source/revision file exceeds its 8 GiB asset or 16 MiB metadata hash budget.");
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        internal static async Task<ReferenceFilePreparation> CreateAsync(string path, CancellationToken caller)
        {
            caller.ThrowIfCancellationRequested();
            var observation = new ReferenceFilePreparation(path, caller);
            active.Add(observation);
            try
            {
                await slots.WaitAsync(observation.token);
                try
                {
                    observation.token.ThrowIfCancellationRequested();
                    observation.worker = Task.Run(observation.HashFiles, observation.token);
                    // Await the real worker; no canceled-wait shortcut can dispose its readers.
                    await observation.worker;
                    observation.token.ThrowIfCancellationRequested();
                    if (
                        AssetPathUtility.GetFullAssetPath(path) != observation.sourcePath
                        || AssetPathUtility.GetFullAssetPath(path + ".meta") != observation.metadataPath
                    )
                        throw new InvalidOperationException("Source path changed during reference preparation.");
                    return observation;
                }
                finally
                {
                    slots.Release();
                }
            }
            catch
            {
                observation.Dispose();
                throw;
            }
        }

        private void HashFiles()
        {
            var buffer = new byte[128 * 1024];
            SourceHash = Hash(source, buffer);
            MetadataHash = Hash(metadata, buffer);
        }

        private string Hash(Stream stream, byte[] buffer)
        {
            using (var hash = SHA256.Create())
            {
                int count;
                while ((count = stream.Read(buffer, 0, buffer.Length)) != 0)
                {
                    token.ThrowIfCancellationRequested();
                    hash.TransformBlock(buffer, 0, count, buffer, 0);
                }
                token.ThrowIfCancellationRequested();
                hash.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
            }
        }

        private static void BeforeReload()
        {
            var pending = new List<ReferenceFilePreparation>(active);
            foreach (var observation in pending)
                observation.cancellation.Cancel();
            foreach (var observation in pending)
            {
                // Only pure file work runs on this task, so waiting does not require
                // a Unity synchronization-context continuation during assembly teardown.
                try
                {
                    observation.worker?.GetAwaiter().GetResult();
                }
                catch (OperationCanceledException) { }
                catch (Exception) { /* The awaiting request owns its failure. Teardown still closes every reader. */ }
                observation.Dispose();
            }
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            active.Remove(this);
            metadata?.Dispose();
            source?.Dispose();
            cancellation.Dispose();
        }
    }
}
