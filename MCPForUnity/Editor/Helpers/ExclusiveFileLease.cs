using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>A process-lifetime lock for cooperating journal owners; released by the OS on exit.</summary>
    internal sealed class ExclusiveFileLease : IDisposable
    {
        private FileStream stream;
        private readonly string path;

        private ExclusiveFileLease(string path, FileStream stream)
        {
            this.path = path;
            this.stream = stream;
        }

        internal static ExclusiveFileLease Acquire(string path)
        {
            if (!Path.IsPathRooted(path))
                throw new IOException("Lease path must be absolute.");
            var stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
            try
            {
                // Mono's Unix sharing policy is not a cross-process ownership guarantee. Ask
                // the OS explicitly and fail closed on unsupported filesystems or platforms.
                if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                {
                    int fd = stream.SafeFileHandle.DangerousGetHandle().ToInt32();
                    int result =
                        RuntimeInformation.IsOSPlatform(OSPlatform.Linux) ? FlockLinux(fd, 2 | 4)
                        : RuntimeInformation.IsOSPlatform(OSPlatform.OSX) ? FlockMac(fd, 2 | 4)
                        : throw new IOException("Exclusive journal ownership is unavailable on this platform.");
                    if (result != 0)
                        throw new IOException("Journal is owned by another instance or cannot be locked.", new Win32Exception(Marshal.GetLastWin32Error()));
                }
                return new ExclusiveFileLease(path, stream);
            }
            catch
            {
                stream.Dispose();
                throw;
            }
        }

        internal void Verify()
        {
            if (stream == null || !File.Exists(path))
                throw new IOException("Journal ownership was lost.");
        }

        public void Dispose()
        {
            stream?.Dispose();
            stream = null;
            // Never delete the lock file: another process may already be acquiring its inode.
        }

        [DllImport("libc", EntryPoint = "flock", SetLastError = true)]
        private static extern int FlockLinux(int fd, int operation);

        [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "flock", SetLastError = true)]
        private static extern int FlockMac(int fd, int operation);
    }
}
