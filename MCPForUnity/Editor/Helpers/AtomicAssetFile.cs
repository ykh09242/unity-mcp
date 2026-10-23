using System;
using System.IO;
using System.Security.Cryptography;
using MCPForUnity.Runtime.Helpers;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Publishes complete asset bytes without truncating an existing source or its metadata.</summary>
    internal static class AtomicAssetFile
    {
        internal static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return Hex(sha.ComputeHash(bytes));
        }

        internal static string DigestFile(string path)
        {
            using var stream = OpenSource(Checked(path));
            return DigestStream(stream);
        }

        internal static void Create(string path, byte[] contents) => Publish(path, contents, null, true, null, true);

        // Texture generation intentionally supports updating an existing target.
        internal static void Write(string path, byte[] contents) => Publish(path, contents, null, true, null);

        internal static void Replace(string path, byte[] contents, string expectedDigest = null, Action beforeCommit = null) =>
            Publish(path, contents, expectedDigest, false, beforeCommit);

        private static FileStream OpenSource(string path) => new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);

        private static string Checked(string path) => SafePathUtility.ResolveWithinRoot(Application.dataPath, Path.GetFullPath(path));

        private static string Hex(byte[] value) => BitConverter.ToString(value).Replace("-", "").ToLowerInvariant();

        private static string DigestStream(Stream stream)
        {
            using var sha = SHA256.Create();
            return Hex(sha.ComputeHash(stream));
        }

        private static string Metadata(string path) => File.Exists(Checked(path + ".meta")) ? DigestFile(path + ".meta") : null;

        private static void Publish(string path, byte[] contents, string expectedDigest, bool allowCreate, Action beforeCommit, bool createOnly = false)
        {
            if (contents == null)
                throw new ArgumentNullException(nameof(contents));
            path = Checked(path);
            string directory = Path.GetDirectoryName(path);
            string attempt = ".mcp-" + Guid.NewGuid().ToString("N");
            string stage = Path.Combine(directory, attempt + ".tmp");
            string backup = Path.Combine(directory, attempt + ".bak");
            bool staged = false;
            bool attempted = false;
            bool confirmed = false;
            try
            {
                // Deny in-place writers while comparing and publishing. Delete sharing is necessary
                // for atomic replacement and means an uncooperative rename is still possible.
                if (createOnly && File.Exists(path))
                    throw new IOException("Destination already exists; creation did not replace it.");
                using var original = createOnly || allowCreate && !File.Exists(path) ? null : OpenSource(path);
                string observed = original == null ? null : DigestStream(original);
                if (expectedDigest != null && observed != expectedDigest)
                    throw new IOException("Source changed since preparation; no bytes were replaced.");
                string metadata = Metadata(path);
                using (var output = new FileStream(Checked(stage), FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    staged = true;
                    output.Write(contents, 0, contents.Length);
                    output.Flush(true);
                }
                beforeCommit?.Invoke();
                Checked(path);
                if (Metadata(path) != metadata || (original != null ? DigestFile(path) != observed : File.Exists(path)))
                    throw new IOException("Source or metadata changed before publication; no bytes were replaced.");
                if (File.Exists(Checked(backup)))
                    throw new IOException("An unexpected replacement backup already exists.");
                attempted = true;
                if (original == null)
                    File.Move(stage, path);
                else
                    File.Replace(stage, path, backup);
                // File.Replace is not compare-and-swap. Preserve its actual preimage on a rename
                // race or uncertain postcommit outcome; never blindly restore over another writer.
                if (original != null && DigestFile(backup) != observed || DigestFile(path) != Digest(contents) || Metadata(path) != metadata)
                    throw new IOException("Concurrent source replacement detected; inspect the retained preimage before retrying.");
                confirmed = true;
            }
            catch (Exception ex) when (attempted && File.Exists(backup))
            {
                throw new IOException("Asset publication is uncertain. Replacement preimage retained at: " + backup, ex);
            }
            finally
            {
                if (staged)
                    TryDelete(stage);
                if (confirmed)
                    TryDelete(backup);
            }
        }

        private static void TryDelete(string path)
        {
            try
            {
                File.Delete(Checked(path));
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            catch (InvalidOperationException) { }
        }
    }
}
