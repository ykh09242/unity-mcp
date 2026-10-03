using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Threading;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    /// <summary>
    /// Extracts a .zip into a destination directory while rejecting Zip-Slip path traversal:
    /// every entry's resolved target must stay inside <c>destDir</c>. Directory entries are
    /// created; file entries are written by copying the entry stream (no reliance on the
    /// ZipFileExtensions helper). Used to unpack marketplace model archives (e.g. Sketchfab).
    ///
    /// When <paramref name="allowedExtensions"/> is supplied, file entries whose extension is not
    /// on the allowlist are SKIPPED (not written). Callers that extract UNTRUSTED archives into the
    /// Assets tree MUST pass an allowlist of inert asset types so executable content (.cs/.dll/
    /// .asmdef) can never land under Assets/ and be compiled/loaded by the Editor.
    /// </summary>
    public static class SafeZipExtractor
    {
        public static void ExtractTo(string zipPath, string destDir, ISet<string> allowedExtensions = null,
            CancellationToken cancellationToken = default)
            => ExtractTo(zipPath, destDir, allowedExtensions, cancellationToken, 4096,
                512L * 1024 * 1024, 2L * 1024 * 1024 * 1024, 200);

        internal static void ExtractTo(string zipPath, string destDir, ISet<string> allowedExtensions,
            CancellationToken cancellationToken, int maxEntries, long maxEntryBytes, long maxTotalBytes, int maxRatio)
        {
            if (string.IsNullOrEmpty(zipPath)) throw new ArgumentException("zipPath required", nameof(zipPath));
            if (string.IsNullOrEmpty(destDir)) throw new ArgumentException("destDir required", nameof(destDir));

            cancellationToken.ThrowIfCancellationRequested();
            string destFull = Path.GetFullPath(destDir);
            string prefix = destFull.EndsWith(Path.DirectorySeparatorChar.ToString())
                ? destFull
                : destFull + Path.DirectorySeparatorChar;

            using (FileStream fs = File.OpenRead(zipPath))
            using (var archive = OpenBoundedArchive(fs, maxEntries, cancellationToken))
            {
                if (archive.Entries.Count > maxEntries)
                    throw new IOException("Archive exceeds the entry count limit.");
                long declaredTotal = 0;
                // Validate all advertised sizes before creating any output files.
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    if (entry.Length > maxEntryBytes || entry.Length > maxTotalBytes - declaredTotal)
                        throw new IOException("Archive exceeds the uncompressed size limit.");
                    if (entry.Length > Math.Max(1L, entry.CompressedLength) * maxRatio)
                        throw new IOException("Archive exceeds the compression ratio limit.");
                    declaredTotal += entry.Length;
                }
                long totalWritten = 0;
                var createdFiles = new List<string>();
                try
                {
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        string name = entry.FullName.Replace('\\', '/');
                        if (string.IsNullOrEmpty(name)) continue;

                        // Reject traversal / absolute paths up front.
                        if (name.Contains("..") || name.Contains(":") || Path.IsPathRooted(name))
                            throw new IOException($"Unsafe zip entry rejected: {name}");

                        string target = Path.GetFullPath(Path.Combine(destDir, name));
                        if (!target.StartsWith(prefix, StringComparison.Ordinal))
                            throw new IOException($"Unsafe zip entry escapes destination: {name}");
                        target = SafePathUtility.ResolveWithinRoot(destFull, target);

                        // A directory entry has an empty Name (FullName ends with a separator).
                        if (string.IsNullOrEmpty(entry.Name))
                        {
                            Directory.CreateDirectory(target);
                            continue;
                        }

                        // Allowlist gate: skip anything that isn't an inert asset type the caller permits.
                        if (allowedExtensions != null && allowedExtensions.Count > 0
                            && !allowedExtensions.Contains(Path.GetExtension(entry.Name).ToLowerInvariant()))
                        {
                            continue;
                        }

                        string parent = Path.GetDirectoryName(target);
                        if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);

                        using (Stream src = entry.Open())
                        using (FileStream dst = new FileStream(target, FileMode.CreateNew, FileAccess.Write))
                        {
                            createdFiles.Add(target);
                            var buffer = new byte[81920];
                            long entryWritten = 0;
                            int count;
                            while ((count = src.Read(buffer, 0, buffer.Length)) != 0)
                            {
                                cancellationToken.ThrowIfCancellationRequested();
                                if (count > maxEntryBytes - entryWritten || count > maxTotalBytes - totalWritten ||
                                    count > entry.Length - entryWritten)
                                    throw new IOException("Archive exceeds the actual uncompressed size limit.");
                                dst.Write(buffer, 0, count);
                                entryWritten += count;
                                totalWritten += count;
                            }
                            if (entryWritten != entry.Length)
                                throw new IOException("Archive entry size does not match its metadata.");
                        }
                    }
                }
                catch
                {
                    foreach (string created in createdFiles)
                        File.Delete(created);
                    throw;
                }
            }
        }

        private static ZipArchive OpenBoundedArchive(Stream stream, int maxEntries, CancellationToken cancellationToken)
        {
            ZipMetadataPreflight.Validate(stream, maxEntries, cancellationToken);
            return new ZipArchive(stream, ZipArchiveMode.Read);
        }
    }
}
