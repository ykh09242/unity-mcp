using System;
using System.IO;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Finite native storage, cooperative byte leases; retained evidence is never evicted.</summary>
    internal static class ReferenceGraphRuntimeSpool
    {
        internal const long CaptureBytes = 64L * 1024 * 1024;
        internal const long TotalBytes = 512L * 1024 * 1024;
        internal const int RecordBytes = 1024 * 1024;
        internal const int CaptureCount = 256;
        internal const int MetadataBytes = 64 * 1024;
        internal const int PendingRecords = 128;
        internal const int PendingBytes = 4 * 1024 * 1024;
        internal const int DrainRecords = 32;
        internal const int DrainBytes = 1024 * 1024 + 4;
        internal const int InventoryEntries = 4096;
        internal static readonly object Gate = new object();

        internal static FileStream Lock(string path)
        {
            if (TryLock(path, out FileStream stream))
                return stream;
            throw new IOException("Native spool ownership is temporarily busy.");
        }

        internal static bool TryLock(string path, out FileStream stream)
        {
            path = SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(path), path);
            stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
            try
            {
                stream.Lock(0, 1);
                return true;
            }
            catch (IOException error) when (IsContention(error))
            {
                stream.Dispose();
                stream = null;
                return false;
            }
            catch
            {
                stream.Dispose();
                throw;
            }
        }

        private static bool IsContention(IOException error)
        {
            int code = error.HResult & 0xFFFF;
            return code == 11 || code == 13 || code == 32 || code == 33;
        }

        internal static string Root(string location) => Path.GetDirectoryName(Path.GetDirectoryName(location));

        internal static void PruneEmptyOwner(string root, string directory)
        {
            // The caller holds Gate and the common spool lease. Never recurse:
            // unknown contents, including a competing producer's files, survive.
            directory = SafePathUtility.ResolveWithinRoot(root, directory);
            if (!Regex.IsMatch(Path.GetFileName(directory), "\\A[a-f0-9]{32}\\z") || !Directory.Exists(directory))
                return;
            using (var entries = Directory.EnumerateFileSystemEntries(directory).GetEnumerator())
                if (entries.MoveNext())
                    return;
            SafePathUtility.ResolveWithinRoot(root, directory);
            try
            {
                Directory.Delete(directory, false);
            }
            catch (IOException)
            {
                // Empty-directory housekeeping is best effort. A noncooperating
                // filesystem writer may have filled or removed the path.
            }
        }

        internal static void PruneEmptyOwners(string root, int limit = InventoryEntries)
        {
            if (limit < 1 || limit > InventoryEntries)
                throw new ArgumentOutOfRangeException(nameof(limit));
            // Bound enumeration as well as removals. Repeated starts can drain
            // an old over-cap inventory without scanning it all in one call.
            using (var entries = Directory.EnumerateFileSystemEntries(root).GetEnumerator())
                for (int scanned = 0; scanned < limit && entries.MoveNext(); scanned++)
                {
                    string directory = entries.Current;
                    if (Regex.IsMatch(Path.GetFileName(directory), "\\A[a-f0-9]{32}\\z") && Directory.Exists(directory))
                        PruneEmptyOwner(root, directory);
                }
        }

        internal static string Pressure(string location, long additional, bool starting, int inventoryLimit = InventoryEntries)
        {
            if (inventoryLimit < 1 || inventoryLimit > InventoryEntries)
                throw new ArgumentOutOfRangeException(nameof(inventoryLimit));
            string root = Root(location);
            long bytes = 0;
            int captures = 0,
                entries = 0;
            foreach (string directory in Directory.EnumerateDirectories(root))
            {
                if (++entries > inventoryLimit)
                    return "inventory_limit";
                SafePathUtility.ResolveWithinRoot(root, directory);
                if (!Regex.IsMatch(Path.GetFileName(directory), "\\A[a-f0-9]{32}\\z"))
                    return "unknown_spool_layout";
                foreach (string file in Directory.EnumerateFileSystemEntries(directory))
                {
                    if (++entries > inventoryLimit)
                        return "inventory_limit";
                    SafePathUtility.ResolveWithinRoot(root, file);
                    string name = Path.GetFileName(file);
                    if (!Regex.IsMatch(name, "\\A[a-f0-9]{64}\\.(?:events|json(?:\\.pending)?|lease)\\z") || Directory.Exists(file))
                        return "unknown_spool_layout";
                    bytes = checked(bytes + new FileInfo(file).Length);
                    if (name.EndsWith(".json", StringComparison.Ordinal))
                        captures++;
                }
            }
            if (starting && captures >= CaptureCount)
                return "max_capture_count";
            // Reserve one bounded metadata rewrite per admitted capture. The
            // global lock covers this census through the actual event write.
            if (bytes + additional + (captures + (starting ? 1 : 0)) * (long)MetadataBytes > TotalBytes)
                return "max_total_bytes";
            return null;
        }
    }
}
