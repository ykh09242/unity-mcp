using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Threading;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>Owns only files created by this staging operation, and their project-volume reservation.</summary>
    internal sealed class LocalAssetCopy : IDisposable
    {
        private readonly AssetDiskBudget.Lease lease;
        private readonly List<string> created = new();
        private readonly CancellationToken token;
        private bool complete;
        internal static Action<string> DeleteForTests = null;
        internal IReadOnlyList<string> CreatedFiles => created;

        internal LocalAssetCopy(string project, long bytes, CancellationToken token)
        {
            this.token = token;
            lease = AssetDiskBudget.ReserveExtraction(project, bytes, token);
        }

        internal void Copy(string source, string destination, long expectedBytes, byte[] expectedHash = null, Action<long> progress = null)
        {
            token.ThrowIfCancellationRequested();
            using var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (input.Length != expectedBytes)
                throw new IOException("Local asset changed length after inspection.");
            using var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            created.Add(destination);
            using var hash = expectedHash == null ? null : SHA256.Create();
            var buffer = new byte[81920];
            long written = 0;
            int count;
            while ((count = input.Read(buffer, 0, buffer.Length)) != 0)
            {
                token.ThrowIfCancellationRequested();
                if (count > expectedBytes - written)
                    throw new IOException("Local asset grew beyond its inspected byte budget.");
                output.Write(buffer, 0, count);
                lease.RecordWritten(count);
                hash?.TransformBlock(buffer, 0, count, buffer, 0);
                written += count;
                progress?.Invoke(count);
            }
            token.ThrowIfCancellationRequested();
            if (written != expectedBytes)
                throw new IOException("Local asset ended before its inspected byte length.");
            hash?.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            if (hash != null)
            {
                bool equal = hash.Hash.Length == expectedHash.Length;
                for (int i = 0; equal && i < expectedHash.Length; i++)
                    equal = expectedHash[i] == hash.Hash[i];
                if (!equal)
                    throw new IOException("Local model metadata changed after dependency inspection.");
            }
        }

        internal void Complete() => complete = true;

        public void Dispose()
        {
            if (!complete)
                foreach (string path in created)
                    try
                    {
                        if (DeleteForTests == null)
                            File.Delete(path);
                        else
                            DeleteForTests(path);
                    }
                    catch (IOException) { /* Retain the reservation for surviving owned bytes. */ }
                    catch (UnauthorizedAccessException) { /* Retain the reservation for surviving owned bytes. */ }
            if (complete || created.TrueForAll(AssetDiskBudget.FileIsGone))
                lease.Dispose();
            else
                lease.RetainUntilFilesRemoved(created);
        }
    }
}
