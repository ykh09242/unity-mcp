using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Same-directory publication without Mono Unix's multi-rename replacement fallback.</summary>
    internal static class AtomicFilePublication
    {
        internal static void StageNew(string path, Action<Stream> write) => StageNewWithFlush(path, write, null);

        internal static void StageNewWithFlush(string path, Action<Stream> write, Action<FileStream> flushForTests)
        {
            using var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            write(output);
            if (flushForTests == null)
                output.Flush(true);
            else
                flushForTests(output);
        }

        internal static void Create(string stage, string target)
        {
            Adjacent(stage, target);
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                File.Move(stage, target); // Windows MoveFile refuses an existing target atomically.
            else
                RenameUnix(stage, target, false);
        }

        internal static void Replace(string stage, string target, string backup)
        {
            Adjacent(stage, target);
            Adjacent(stage, backup);
            if (File.Exists(backup))
                throw new IOException("Replacement backup already exists.");
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                File.Replace(stage, target, backup);
            else
            {
                Exchange(stage, target);
                // The source is already complete. A crash before this rename leaves the actual
                // displaced preimage at stage; callers must retain and reconcile that file too.
                Create(stage, backup);
            }
        }

        internal static void Exchange(string stage, string target)
        {
            Adjacent(stage, target);
            RenameUnix(stage, target, true);
        }

        private static void Adjacent(string source, string target)
        {
            var comparison = RuntimeInformation.IsOSPlatform(OSPlatform.Windows) ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (
                !Path.IsPathRooted(source)
                || !Path.IsPathRooted(target)
                || string.Equals(Path.GetFullPath(source), Path.GetFullPath(target), comparison)
                || !string.Equals(Path.GetDirectoryName(Path.GetFullPath(source)), Path.GetDirectoryName(Path.GetFullPath(target)), comparison)
            )
                throw new IOException("Atomic publication requires distinct adjacent absolute paths.");
        }

        private static void RenameUnix(string source, string target, bool exchange)
        {
            int result;
            try
            {
                if (RuntimeInformation.IsOSPlatform(OSPlatform.Linux))
                    result = RenameAt2(-100, source, -100, target, exchange ? 2u : 1u); // RENAME_EXCHANGE / RENAME_NOREPLACE
                else if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
                    result = RenameX(source, target, exchange ? 2u : 4u); // RENAME_SWAP / RENAME_EXCL
                else
                    throw new IOException("This platform does not provide supported atomic publication.");
            }
            catch (Exception ex) when (ex is DllNotFoundException || ex is EntryPointNotFoundException)
            {
                throw new IOException("Atomic publication is unavailable on this platform; no non-atomic fallback was attempted.", ex);
            }
            if (result != 0)
            {
                int error = Marshal.GetLastWin32Error();
                throw new IOException("Atomic publication refused by the filesystem (errno " + error + ").", new Win32Exception(error));
            }
        }

        [DllImport("libc", EntryPoint = "renameat2", SetLastError = true)]
        private static extern int RenameAt2(
            int oldDirectory,
            [MarshalAs(UnmanagedType.LPUTF8Str)] string source,
            int newDirectory,
            [MarshalAs(UnmanagedType.LPUTF8Str)] string target,
            uint flags
        );

        [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "renamex_np", SetLastError = true)]
        private static extern int RenameX([MarshalAs(UnmanagedType.LPUTF8Str)] string source, [MarshalAs(UnmanagedType.LPUTF8Str)] string target, uint flags);
    }
}
