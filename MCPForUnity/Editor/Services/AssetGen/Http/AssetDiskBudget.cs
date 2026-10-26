using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
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
        internal static Func<string, long> ProbeFreeBytesForTests = null;
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

        private static long Setting(string name, long fallback)
        {
            string text = Environment.GetEnvironmentVariable(name);
            return long.TryParse(text, out long gib) && gib >= 1 && gib <= 1024 ? gib * 1024 * 1024 * 1024 : fallback;
        }

        // DriveInfo(Path.GetPathRoot(path)) probes / on Unix even when the project is
        // mounted elsewhere. On Windows DriveInfo also resolves mounted folders to the
        // parent drive and rejects UNC paths, so query the actual existing directory.
        private static long FreeBytes(string project)
        {
            if (FreeBytesForTests != null)
                return FreeBytesForTests(project);
            string directory = ExistingDirectory(project);
            string probe = Path.DirectorySeparatorChar == '\\' ? WithTrailingSeparator(directory) : directory;
            if (ProbeFreeBytesForTests != null)
                return ProbeFreeBytesForTests(probe);
            if (Path.DirectorySeparatorChar != '\\')
            {
                // Unity Mono's DriveInfo(string) accepts an arbitrary path but maps it to
                // a mount with a case-insensitive prefix check that ignores path boundaries.
                // Select a listed drive ourselves, then query that DriveInfo object directly.
                DriveInfo[] drives = DriveInfo.GetDrives();
                string[] mounts = new string[drives.Length];
                for (int i = 0; i < drives.Length; i++)
                    mounts[i] = drives[i].Name;
                return drives[SelectUnixDriveIndex(probe, mounts, RuntimeInformation.IsOSPlatform(OSPlatform.OSX))].AvailableFreeSpace;
            }
            if (!GetDiskFreeSpaceEx(probe, out ulong available, IntPtr.Zero, IntPtr.Zero))
                throw new IOException($"Unable to inspect project disk capacity (Windows error {Marshal.GetLastWin32Error()}).");
            return available > long.MaxValue ? long.MaxValue : (long)available;
        }

        private static string ExistingDirectory(string path)
        {
            string current = Path.GetFullPath(path);
            while (true)
            {
                try
                {
                    if ((File.GetAttributes(current) & FileAttributes.Directory) == 0)
                        throw new IOException("The asset project path is not a directory.");
                    return current;
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                string parent = Path.GetDirectoryName(current);
                if (string.IsNullOrEmpty(parent) || parent == current)
                    throw new IOException("Unable to find an existing directory for the asset disk capacity check.");
                current = parent;
            }
        }

        private static string WithTrailingSeparator(string path) =>
            path.EndsWith(Path.DirectorySeparatorChar.ToString(), StringComparison.Ordinal) ? path : path + Path.DirectorySeparatorChar;

        internal static int SelectUnixDriveIndex(string directory, string[] mountNames, bool isMacOS = false)
        {
            int best = -1;
            int length = -1;
            int caseOnlyLength = -1;
            for (int i = 0; i < mountNames.Length; i++)
            {
                string mount = mountNames[i];
                if (string.IsNullOrEmpty(mount) || mount[0] != '/')
                    continue;
                mount = mount.TrimEnd('/');
                if (mount.Length == 0)
                    mount = "/";
                if (ContainsUnixPath(directory, mount, StringComparison.Ordinal) && mount.Length > length)
                {
                    best = i;
                    length = mount.Length;
                }
                else if (isMacOS && ContainsUnixPath(directory, mount, StringComparison.OrdinalIgnoreCase))
                    caseOnlyLength = Math.Max(caseOnlyLength, mount.Length);
            }
            if (best < 0)
                throw new IOException("Unable to identify the project filesystem for the asset disk capacity check.");
            if (isMacOS && caseOnlyLength > length)
                throw new IOException(
                    "Project path casing differs from a mounted filesystem. Open the project using its canonical mount path before generating assets."
                );
            return best;
        }

        private static bool ContainsUnixPath(string directory, string mount, StringComparison comparison) =>
            string.Equals(directory, mount, comparison)
            || (mount == "/" && directory.StartsWith("/", comparison))
            || (directory.Length > mount.Length && directory.StartsWith(mount, comparison) && directory[mount.Length] == '/');

        [DllImport("kernel32.dll", EntryPoint = "GetDiskFreeSpaceExW", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetDiskFreeSpaceEx(string directoryName, out ulong available, IntPtr totalBytes, IntPtr totalFreeBytes);

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
