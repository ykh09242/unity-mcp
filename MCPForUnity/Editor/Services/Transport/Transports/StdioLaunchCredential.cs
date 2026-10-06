using System;
using System.IO;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Security;

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    /// <summary>Publish a dedicated per-launch credential without Windows ACL assumptions.</summary>
    internal sealed class StdioLaunchCredential : IDisposable
    {
        private readonly string generation;
        private readonly WindowsCredentialKeyStore windows = null;
        private int parentFd = -1;
        private int directoryFd = -1;
        private bool createdDirectory = false;

        [DllImport("libc", SetLastError = true)]
        private static extern int open(string path, int flags);
        [DllImport("libc", SetLastError = true)]
        private static extern int openat(int parent, string path, int flags, uint mode);
        [DllImport("libc", SetLastError = true)]
        private static extern int mkdirat(int parent, string path, uint mode);
        [DllImport("libc", SetLastError = true)]
        private static extern int unlinkat(int parent, string path, int flags);
        [DllImport("libc", SetLastError = true)]
        private static extern IntPtr write(int descriptor, byte[] bytes, UIntPtr length);
        [DllImport("libc")]
        private static extern int close(int descriptor);

        // Official Linux asm-generic/fcntl.h and Apple xnu bsd/sys/fcntl.h.
#if UNITY_EDITOR_OSX
        private const int DirectoryFlags = 0x00100000 | 0x00000100 | 0x01000000;
        private const int TokenFlags = 1 | 0x00000200 | 0x00000800 | 0x00000100 | 0x01000000;
        private const int RemoveDirectory = 0x0080;
#else
        private const int DirectoryFlags = (1 << 16) | (1 << 17) | (1 << 19);
        private const int TokenFlags = 1 | (1 << 6) | (1 << 7) | (1 << 17) | (1 << 19);
        private const int RemoveDirectory = 0x200;
#endif

        internal StdioLaunchCredential(string generation, string token, string ownedHome = null)
        {
            if (generation == null || !System.Text.RegularExpressions.Regex.IsMatch(generation, "\\A[0-9a-f]{32}\\z"))
                throw new ArgumentException("Invalid stdio launch generation");
            this.generation = generation;
#if UNITY_EDITOR_WIN
            windows = new WindowsCredentialKeyStore("MCPForUnity.Stdio");
            windows.Set(generation, token);
#else
#if UNITY_EDITOR_OSX
            if (!RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) throw new PlatformNotSupportedException();
#elif UNITY_EDITOR_LINUX
            if (!RuntimeInformation.IsOSPlatform(OSPlatform.Linux)
                || RuntimeInformation.ProcessArchitecture != Architecture.X64)
                throw new PlatformNotSupportedException();
#else
            throw new PlatformNotSupportedException("No secure stdio credential storage for this platform");
#endif
            try
            {
                parentFd = open(ownedHome ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), DirectoryFlags);
                if (parentFd < 0) throw new IOException("Cannot open private stdio credential home");
                foreach (string part in new[] { ".unity-mcp", "stdio-auth" })
                {
                    int child = openat(parentFd, part, DirectoryFlags, 0);
                    if (child < 0 && Marshal.GetLastWin32Error() == 2)
                    {
                        if (mkdirat(parentFd, part, 448) != 0 && Marshal.GetLastWin32Error() != 17)
                            throw new IOException("Cannot create stdio credential directory");
                        child = openat(parentFd, part, DirectoryFlags, 0);
                    }
                    if (child < 0) throw new IOException("Cannot open stdio credential directory without following links");
                    close(parentFd);
                    parentFd = child;
                }
                if (mkdirat(parentFd, generation, 448) != 0)
                    throw new IOException("Cannot create private stdio launch credential directory");
                createdDirectory = true;
                directoryFd = openat(parentFd, generation, DirectoryFlags, 0);
                if (directoryFd < 0) throw new IOException("Cannot open private stdio launch credential directory");
                int tokenFd = openat(directoryFd, "token", TokenFlags, 384);
                if (tokenFd < 0) throw new IOException("Cannot create private stdio launch credential file");
                try
                {
                    byte[] bytes = System.Text.Encoding.UTF8.GetBytes(token);
                    if (bytes.Length < 32 || bytes.Length > 256 || write(tokenFd, bytes, (UIntPtr)bytes.Length).ToInt64() != bytes.Length)
                        throw new IOException("Cannot publish private stdio launch credential");
                }
                finally
                {
                    close(tokenFd);
                }
            }
            catch
            {
                Dispose();
                throw;
            }
#endif
        }

        public void Dispose()
        {
            if (windows != null) windows.Delete(generation);
            if (directoryFd >= 0)
            {
                unlinkat(directoryFd, "token", 0);
                close(directoryFd);
                directoryFd = -1;
            }
            if (parentFd >= 0)
            {
                if (createdDirectory) unlinkat(parentFd, generation, RemoveDirectory);
                close(parentFd);
                parentFd = -1;
            }
        }
    }
}
