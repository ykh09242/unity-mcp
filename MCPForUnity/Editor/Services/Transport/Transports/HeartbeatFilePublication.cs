using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    internal static class HeartbeatFilePublication
    {
        internal static void Publish(string path, string json)
        {
            // Discovery readers can keep the previous snapshot open while the Editor publishes the next one.
            bool replacing = IsRegularFile(path);
#if !UNITY_EDITOR_LINUX && !UNITY_EDITOR_OSX
            // After an interrupted replacement, only its original writer can identify the backup.
            if (!replacing && Directory.EnumerateFiles(Path.GetDirectoryName(path), Path.GetFileName(path) + ".*.pending*").Any())
                throw new IOException("Heartbeat recovery files exist for a missing status file.");
#endif
            string stage = path + "." + Guid.NewGuid().ToString("N") + (replacing ? ".pending" : ".tmp");
            bool created = false;
            try
            {
#if UNITY_EDITOR_OSX || UNITY_EDITOR_LINUX
                // Mode 000 prevents a retained read descriptor via mode bits before setting the existing policy.
                int fd = CreateUnixStage(stage, replacing ? 0 : 438); // First publication uses 0666 subject to umask.
                if (fd < 0)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                created = true;
                try
                {
                    if (replacing)
                    {
                        // A replacement requires write access to the original. Read its policy through that same descriptor.
                        int original = OpenWritableHeartbeat(path);
                        if (original < 0)
                            throw new Win32Exception(Marshal.GetLastWin32Error());
                        try
                        {
                            PreserveHeartbeatAccess(original, fd);
                        }
                        finally
                        {
                            if (close(original) != 0)
                                throw new Win32Exception(Marshal.GetLastWin32Error());
                        }
                    }
                    var bytes = System.Text.Encoding.UTF8.GetBytes(json);
                    if (write(fd, bytes, (UIntPtr)bytes.Length).ToInt64() != bytes.Length)
                        throw new IOException("Incomplete heartbeat stage write.");
                }
                finally
                {
                    if (close(fd) != 0)
                        throw new Win32Exception(Marshal.GetLastWin32Error());
                }
#else
                using (var handle = replacing ? CreateProtectedStage(path, stage) : null)
                {
                    // Unity Mono's FileStream(SafeFileHandle, ...) leaves the supplied handle open.
                    created = handle != null;
                    using var stream = created
                        ? new FileStream(handle, FileAccess.Write)
                        : new FileStream(stage, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                    created = true;
                    using var writer = new StreamWriter(stream, new System.Text.UTF8Encoding(false));
                    writer.Write(json);
                }
#endif
#if UNITY_EDITOR_OSX || UNITY_EDITOR_LINUX
                if (replacing)
                    File.Replace(stage, path, null);
                else
                {
                    // link(2) publishes an already complete file without replacing an existing name or copying its contents.
                    if (link(stage, path) != 0)
                        throw new Win32Exception(Marshal.GetLastWin32Error());
                    created = false;
                    File.Delete(stage);
                }
#else
                if (replacing)
                {
                    created = false; // The replacement owns cleanup, including documented partial-failure states.
                    ReplaceWindowsSnapshot(path, stage, File.Replace);
                }
                else
                    File.Move(stage, path);
#endif
            }
            catch
            {
                if (created)
                    File.Delete(stage);
                throw;
            }
        }

        private static bool IsRegularFile(string path)
        {
            try
            {
                if ((File.GetAttributes(path) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
                    throw new IOException("Expected a regular heartbeat file.");
                return true;
            }
            catch (FileNotFoundException)
            {
                return false;
            }
        }

#if UNITY_EDITOR_OSX || UNITY_EDITOR_LINUX
        // Each runtime's fixed-signature shim handles the native variadic open ABI, including on Apple Silicon.
#if ENABLE_CORECLR
        [DllImport("libSystem.Native", EntryPoint = "SystemNative_Open", SetLastError = true)]
        private static extern IntPtr OpenStage(string path, int flags, int mode);

        private static int CreateUnixStage(string path, int mode) => OpenStage(path, 0x1 | 0x10 | 0x20 | 0x40, mode).ToInt32(); // PAL_O_WRONLY | CLOEXEC | CREAT | EXCL
#else
        [DllImport("MonoPosixHelper", EntryPoint = "Mono_Posix_Syscall_open_mode", SetLastError = true)]
        private static extern int OpenStage(string path, int flags, uint mode);

        private static int CreateUnixStage(string path, int mode) => OpenStage(path, 1 | 0x40 | 0x80 | 0x80000, (uint)mode); // Mono.Posix O_WRONLY | CREAT | EXCL | CLOEXEC
#endif

        [DllImport("libc", SetLastError = true)]
        private static extern IntPtr write(int fd, byte[] bytes, UIntPtr count);

        [DllImport("libc", SetLastError = true)]
        private static extern int close(int fd);

        [DllImport("libc", SetLastError = true)]
        private static extern int link(string source, string destination);
#endif

#if UNITY_EDITOR_OSX
        [DllImport("libc", SetLastError = true)]
        private static extern int openx_np(string path, int flags, IntPtr security);

        private static int OpenWritableHeartbeat(string path) => openx_np(path, 1 | 4 | 0x100 | 0x1000000, IntPtr.Zero); // O_WRONLY | NONBLOCK | NOFOLLOW | CLOEXEC

        [DllImport("libc", SetLastError = true)]
        private static extern int fgetattrlist(int fd, uint[] attributes, [Out] byte[] buffer, UIntPtr size, UIntPtr options);

        [DllImport("libc", SetLastError = true)]
        private static extern int fchmod(int fd, ushort mode);

        [DllImport("libc", SetLastError = true)]
        private static extern int fsetattrlist(int fd, uint[] attributes, byte[] buffer, UIntPtr size, UIntPtr options);

        private static byte[] MacHeartbeatAccess(int fd)
        {
            // attr.h: vnode type, owner, group, access mask and extended security (ACL).
            var attributes = new uint[] { 5, 0x438008, 0, 0, 0, 0 };
            var buffer = new byte[4096];
            if (fgetattrlist(fd, attributes, buffer, (UIntPtr)buffer.Length, (UIntPtr)4) != 0)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            uint size = BitConverter.ToUInt32(buffer, 0);
            if (size < 28 || size > buffer.Length || BitConverter.ToUInt32(buffer, 4) != 1) // VREG
                throw new IOException("Unsupported heartbeat access metadata.");
            Array.Resize(ref buffer, (int)size);
            return buffer;
        }

        private static void PreserveHeartbeatAccess(int original, int fd)
        {
            const int modeOffset = 16;
            const int aclReference = 20;
            var expected = MacHeartbeatAccess(original);
            var initial = MacHeartbeatAccess(fd);
            if (BitConverter.ToUInt32(initial, aclReference + 4) == 0 && BitConverter.ToUInt32(expected, aclReference + 4) != 0)
            {
                // Install just the ACL while mode is still 000. Darwin's packed attrreference offset is relative to itself.
                var acl = new byte[expected.Length - aclReference];
                Buffer.BlockCopy(expected, aclReference, acl, 0, acl.Length);
                if (fsetattrlist(fd, new uint[] { 5, 0x400000, 0, 0, 0, 0 }, acl, (UIntPtr)acl.Length, UIntPtr.Zero) != 0)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                initial = MacHeartbeatAccess(fd);
            }
            // An inherited ACL could have admitted early readers. Never replace a different one.
            Buffer.BlockCopy(expected, modeOffset, initial, modeOffset, sizeof(uint)); // Compare everything except the initial mode.
            if (!expected.SequenceEqual(initial))
                throw new IOException("Cannot preserve the heartbeat owner, group or ACL.");
            if (fchmod(fd, (ushort)(BitConverter.ToUInt32(expected, modeOffset) & 0x1ff)) != 0)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if (!expected.SequenceEqual(MacHeartbeatAccess(fd)))
                throw new IOException("Cannot preserve the heartbeat owner, group or ACL.");
        }
#elif UNITY_EDITOR_LINUX
        // glibc's fixed two-argument open entry point is available on supported Linux Editors, including Ubuntu 18.04.
        [DllImport("libc", EntryPoint = "__open_2", SetLastError = true)]
        private static extern int OpenExisting(string path, int flags);

        private static int OpenWritableHeartbeat(string path) => OpenExisting(path, 1 | 0x800 | 0x20000 | 0x80000); // O_WRONLY | NONBLOCK | NOFOLLOW | CLOEXEC
#if ENABLE_CORECLR
        // dotnet/runtime System.Native/pal_io.h FileStatus on 64-bit Unix.
        [StructLayout(LayoutKind.Explicit, Size = 120)]
        private struct HeartbeatStat
        {
            [FieldOffset(4)]
            internal int Mode;

            [FieldOffset(8)]
            internal uint Owner;

            [FieldOffset(12)]
            internal uint Group;
        }

        [DllImport("libSystem.Native", EntryPoint = "SystemNative_FStat", SetLastError = true)]
        private static extern int FStatNative(IntPtr fd, out HeartbeatStat stat);

        private static int FStat(int fd, out HeartbeatStat stat) => FStatNative((IntPtr)fd, out stat);
#else
        // Unity's bundled Mono.Unix.Native.Stat layout (support/map.h) also works on Ubuntu 18.04.
        [StructLayout(LayoutKind.Explicit, Size = 120)]
        private struct HeartbeatStat
        {
            [FieldOffset(16)]
            internal uint Mode;

            [FieldOffset(32)]
            internal uint Owner;

            [FieldOffset(36)]
            internal uint Group;
        }

        [DllImport("MonoPosixHelper", EntryPoint = "Mono_Posix_Syscall_fstat", SetLastError = true)]
        private static extern int FStat(int fd, out HeartbeatStat stat);
#endif

        [DllImport("libacl.so.1", SetLastError = true)]
        private static extern IntPtr acl_get_fd(int fd);

        [DllImport("libacl.so.1", SetLastError = true)]
        private static extern int acl_set_fd(int fd, IntPtr acl);

        [DllImport("libacl.so.1")]
        private static extern int acl_free(IntPtr acl);

        private static HeartbeatStat LinuxHeartbeatAccess(int fd)
        {
            if (FStat(fd, out var stat) != 0)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if ((stat.Mode & 0xf000) != 0x8000 || (stat.Mode & 0xe00) != 0)
                throw new IOException("Unsupported heartbeat access metadata.");
            return stat;
        }

        private static void PreserveHeartbeatAccess(int original, int fd)
        {
            var expected = LinuxHeartbeatAccess(original);
            var created = LinuxHeartbeatAccess(fd);
            if (expected.Owner != created.Owner || expected.Group != created.Group)
                throw new IOException("Cannot preserve the heartbeat owner or group.");
            IntPtr acl = acl_get_fd(original); // The access ACL includes the mode bits and any named entries.
            if (acl == IntPtr.Zero)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            try
            {
                if (acl_set_fd(fd, acl) != 0)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            finally
            {
                acl_free(acl);
            }
        }
#else
        private static readonly object BackupLock = new object();
        private static readonly Dictionary<string, string> DeferredBackupCleanup = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        internal static void ReplaceWindowsSnapshot(string path, string stage, Action<string, string, string> replace)
        {
            lock (BackupLock)
            {
                string backup = stage + ".bak";
                try
                {
                    // A completed publication may still have an open backup reader. Retry once per heartbeat before making another backup.
                    if (DeferredBackupCleanup.TryGetValue(path, out var previous))
                    {
                        DeleteCompletedBackup(path, previous);
                        DeferredBackupCleanup.Remove(path);
                    }
                    if (IsRegularFile(backup))
                        throw new IOException("The heartbeat backup name is already in use.");
                    replace(stage, path, backup);
                }
                catch (IOException ex) when (ex.HResult == unchecked((int)0x80070499)) // ERROR_UNABLE_TO_MOVE_REPLACEMENT_2
                {
                    // ReplaceFileW moved this invocation's original to the named backup. Never overwrite a newer publication.
                    File.Move(backup, path);
                    File.Delete(stage);
                    throw;
                }
                catch
                {
                    // With a named backup, error 1176 retains both original names. Keep all evidence if the target disappeared.
                    if (File.Exists(path))
                        File.Delete(stage);
                    throw;
                }
                try
                {
                    DeleteCompletedBackup(path, backup);
                }
                catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
                {
                    // Only a successful replacement makes this particular backup eligible for later cleanup.
                    DeferredBackupCleanup[path] = backup;
                }
            }
        }

        private static void DeleteCompletedBackup(string path, string backup)
        {
            // Keep a canonical record in place while deleting its predecessor.
            using var current = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            File.Delete(backup);
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SecurityAttributes
        {
            internal int Length;
            internal IntPtr Descriptor;
            internal int InheritHandle;
        }

        // WinNT.h SECURITY_DESCRIPTOR in absolute form. The protected DACL is borrowed from the source descriptor.
        [StructLayout(LayoutKind.Sequential)]
        private struct AbsoluteSecurityDescriptor
        {
            internal byte Revision;
            internal byte Reserved;
            internal ushort Control;
            internal IntPtr Owner;
            internal IntPtr Group;
            internal IntPtr Sacl;
            internal IntPtr Dacl;
        }

        private sealed class LocalSecurityDescriptor : SafeHandleZeroOrMinusOneIsInvalid
        {
            public LocalSecurityDescriptor()
                : base(true) { }

            protected override bool ReleaseHandle() => LocalFree(handle) == IntPtr.Zero;
        }

        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr memory);

        [DllImport("advapi32.dll")]
        private static extern uint GetSecurityInfo(
            SafeFileHandle file,
            uint objectType,
            uint sections,
            out IntPtr owner,
            out IntPtr group,
            out IntPtr dacl,
            IntPtr sacl,
            out LocalSecurityDescriptor descriptor
        );

        [DllImport("advapi32.dll")]
        private static extern bool EqualSid(IntPtr first, IntPtr second);

        private static LocalSecurityDescriptor ReadWindowsSecurity(SafeFileHandle file, uint sections, out IntPtr owner, out IntPtr group, out IntPtr dacl)
        {
            uint error = GetSecurityInfo(file, 1, sections, out owner, out group, out dacl, IntPtr.Zero, out var descriptor); // SE_FILE_OBJECT
            if (error == 0 && descriptor != null && !descriptor.IsInvalid && owner != IntPtr.Zero && group != IntPtr.Zero)
                return descriptor;
            descriptor?.Dispose();
            if (error != 0)
                throw new Win32Exception((int)error);
            throw new IOException("Cannot read the heartbeat owner or group.");
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(
            string path,
            uint access,
            uint share,
            ref SecurityAttributes security,
            uint disposition,
            uint attributes,
            IntPtr template
        );

        internal static SafeFileHandle CreateProtectedStage(string path, string stage)
        {
            // Replacing a name also requires the original writer's permission to change this file's data.
            using var writable = File.Open(path, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
            const uint identity = 1 | 2; // OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION
            using var original = ReadWindowsSecurity(writable.SafeFileHandle, identity | 4, out var owner, out var group, out var dacl);
            // Supply the original DACL in CREATE_NEW. Omit owner/group so the OS assigns them without WRITE_OWNER.
            var access = new AbsoluteSecurityDescriptor
            {
                Revision = 1,
                Control = 0x1004,
                Dacl = dacl,
            }; // SE_DACL_PROTECTED | SE_DACL_PRESENT
            var pinned = GCHandle.Alloc(access, GCHandleType.Pinned);
            SafeFileHandle handle;
            try
            {
                var security = new SecurityAttributes { Length = Marshal.SizeOf<SecurityAttributes>(), Descriptor = pinned.AddrOfPinnedObject() };
                handle = CreateFileW(stage, 0x40000000, 0, ref security, 1, 0x80, IntPtr.Zero); // GENERIC_WRITE, CREATE_NEW, FILE_ATTRIBUTE_NORMAL
                if (handle.IsInvalid)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            finally
            {
                pinned.Free();
            }
            try
            {
                using var created = ReadWindowsSecurity(handle, identity, out var stageOwner, out var stageGroup, out _);
                if (!EqualSid(owner, stageOwner) || !EqualSid(group, stageGroup))
                    throw new IOException("Cannot preserve the heartbeat owner or group.");
                return handle;
            }
            catch
            {
                handle.Dispose();
                File.Delete(stage);
                throw;
            }
        }
#endif
    }
}
