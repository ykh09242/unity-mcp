using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>
    /// Per-Editor, per-project transient disk reservations. Existing project assets are
    /// accounted for by the filesystem's free-space reading, not by this in-flight ledger.
    /// Other processes can consume disk concurrently, so writes still handle IOException.
    /// </summary>
    internal static class AssetDiskBudget
    {
        internal const long DefaultAggregateBytes = 32L * 1024 * 1024 * 1024;
        internal const long DefaultHeadroomBytes = 1L * 1024 * 1024 * 1024;
        private const string AggregateSetting = "MCPFORUNITY_ASSETGEN_TRANSIENT_BUDGET_GIB";
        private const string HeadroomSetting = "MCPFORUNITY_ASSETGEN_DISK_HEADROOM_GIB";
        private static readonly object Sync = new();
        private static readonly Dictionary<string, Totals> Projects = new(
            Path.DirectorySeparatorChar == '\\' ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal
        );
        private static readonly SemaphoreSlim DownloadSlots = new(2, 2);
        private static readonly SemaphoreSlim ExtractionSlots = new(1, 1);

        internal static Func<string, long> FreeBytesForTests = null;
        internal static long? AggregateBytesForTests = null;
        internal static long? HeadroomBytesForTests = null;

        private sealed class Totals
        {
            internal long Reserved;
            internal long Written;
            internal int ActiveLeases;
        }

        private static string ProjectKey(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
                throw new ArgumentException("An absolute project path is required for disk reservations.", nameof(path));
            string full = Path.GetFullPath(path);
            string trimmed = full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return trimmed.Length == 0 ? full : trimmed;
        }

        internal static string ProjectForExtraction(string destination)
        {
            string current = ProjectKey(destination);
            for (string parent = Path.GetDirectoryName(current); parent != null; parent = Path.GetDirectoryName(current))
            {
                if (string.Equals(Path.GetFileName(current), "Assets", StringComparison.OrdinalIgnoreCase))
                    return parent;
                current = parent;
            }
            return ProjectKey(destination);
        }

        private static long Setting(string name, long fallback)
        {
            string text = Environment.GetEnvironmentVariable(name);
            return long.TryParse(text, out long gib) && gib >= 1 && gib <= 1024 ? gib * 1024 * 1024 * 1024 : fallback;
        }

        private static long FreeBytes(string project) => FreeBytesForTests?.Invoke(project) ?? new DriveInfo(Path.GetPathRoot(project)).AvailableFreeSpace;

        internal static bool FileIsGone(string path)
        {
            if (path == null)
                return true;
            try
            {
                File.GetAttributes(path);
                return false;
            }
            catch (FileNotFoundException)
            {
                return true;
            }
            catch (DirectoryNotFoundException)
            {
                return true;
            }
            catch
            {
                return false; // Unknown cleanup state must retain its credit.
            }
        }

        internal static long ReservedBytesForTests(string project)
        {
            lock (Sync)
                return Projects.TryGetValue(ProjectKey(project), out Totals totals) ? totals.Reserved : 0;
        }

        internal static async Task<Lease> ReserveDownloadAsync(string project, long declaredBytes, CancellationToken ct)
        {
            if (declaredBytes < -1)
                throw new ArgumentOutOfRangeException(nameof(declaredBytes));
            await DownloadSlots.WaitAsync(ct).ConfigureAwait(false);
            try
            {
                var lease = new Lease(ProjectKey(project), DownloadSlots);
                lease.Increase(Math.Max(0, declaredBytes));
                return lease;
            }
            catch
            {
                DownloadSlots.Release();
                throw;
            }
        }

        internal static Lease ReserveExtraction(string project, long declaredBytes, CancellationToken ct)
        {
            if (declaredBytes < 0)
                throw new ArgumentOutOfRangeException(nameof(declaredBytes));
            ExtractionSlots.Wait(ct);
            try
            {
                var lease = new Lease(ProjectKey(project), ExtractionSlots);
                lease.Increase(declaredBytes);
                return lease;
            }
            catch
            {
                ExtractionSlots.Release();
                throw;
            }
        }

        internal sealed class Lease : IDisposable
        {
            private readonly string project;
            private SemaphoreSlim slot;
            private long reserved;
            private long written;
            private bool registered;
            private bool disposed;

            internal Lease(string project, SemaphoreSlim slot)
            {
                this.project = project;
                this.slot = slot;
            }

            internal void Increase(long bytes)
            {
                if (bytes < 0)
                    throw new ArgumentOutOfRangeException(nameof(bytes));
                lock (Sync)
                {
                    if (disposed)
                        throw new ObjectDisposedException(nameof(Lease));
                    bool existing = Projects.TryGetValue(project, out Totals totals);
                    totals ??= new Totals();
                    long aggregate = AggregateBytesForTests ?? Setting(AggregateSetting, DefaultAggregateBytes);
                    long headroom = HeadroomBytesForTests ?? Setting(HeadroomSetting, DefaultHeadroomBytes);
                    long free = FreeBytes(project);
                    if (bytes > aggregate - totals.Reserved || bytes > free - headroom - (totals.Reserved - totals.Written))
                        throw new IOException(
                            $"Insufficient transient asset disk capacity (reserved {totals.Reserved} of {aggregate} bytes; available {free} bytes; headroom {headroom} bytes). Free project disk space or adjust the Editor's asset-generation budget, then retry."
                        );
                    if (!existing)
                        Projects.Add(project, totals);
                    if (!registered)
                    {
                        totals.ActiveLeases++;
                        registered = true;
                    }
                    totals.Reserved += bytes;
                    reserved += bytes;
                }
            }

            internal void RecordWritten(long bytes)
            {
                lock (Sync)
                {
                    if (disposed || bytes < 0 || bytes > reserved - written)
                        throw new InvalidOperationException("Asset disk reservation does not cover this write.");
                    written += bytes;
                    Projects[project].Written += bytes;
                }
            }

            internal void TrimToWritten()
            {
                lock (Sync)
                {
                    if (disposed)
                        throw new ObjectDisposedException(nameof(Lease));
                    long unused = reserved - written;
                    reserved -= unused;
                    Projects[project].Reserved -= unused;
                }
            }

            // A finished download keeps its byte reservation while the staged file exists,
            // but frees the transfer slot for another job waiting on provider bytes.
            internal void CompleteTransfer()
            {
                SemaphoreSlim current = Interlocked.Exchange(ref slot, null);
                current?.Release();
            }

            public void Dispose()
            {
                lock (Sync)
                {
                    if (disposed)
                        return;
                    disposed = true;
                    if (registered)
                    {
                        Totals totals = Projects[project];
                        totals.Reserved -= reserved;
                        totals.Written -= written;
                        totals.ActiveLeases--;
                        if (totals.ActiveLeases == 0)
                            Projects.Remove(project);
                    }
                }
                CompleteTransfer();
            }
        }
    }
}
