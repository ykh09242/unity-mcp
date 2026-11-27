using System;
using System.ComponentModel;
using MCPForUnity.Editor.Services.Transport.Transports;
#if UNITY_EDITOR_WIN || ENABLE_CORECLR
using System.Runtime.InteropServices;
#endif
#if UNITY_EDITOR_WIN
using System.IO;
using System.Linq;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
#endif

internal static class Publisher
{
    private static int Main(string[] args)
    {
        try
        {
#if ENABLE_CORECLR
            if (typeof(object).Assembly.GetName().Name != "System.Private.CoreLib" || Type.GetType("Mono.Runtime") != null)
                throw new InvalidOperationException("The fixture requires CoreCLR.");
            if (RuntimeInformation.ProcessArchitecture != RuntimeInformation.OSArchitecture)
                throw new InvalidOperationException("The fixture requires a native CoreCLR process architecture.");
            Console.WriteLine($"CoreCLR {RuntimeInformation.FrameworkDescription}, {RuntimeInformation.ProcessArchitecture}");
#elif UNITY_EDITOR_WIN
            Console.WriteLine($".NET Framework CLR {Environment.Version}, {IntPtr.Size * 8}-bit");
#endif
#if UNITY_EDITOR_WIN
            return CheckWindows(args);
#else
            HeartbeatFilePublication.Publish(args[0], args[1]);
            return 0;
#endif
        }
#if !UNITY_EDITOR_WIN
        catch (Win32Exception ex) when (ex.NativeErrorCode == 13)
        {
            Console.Error.WriteLine($"Write access denied (errno {ex.NativeErrorCode}).");
            return 13;
        }
        catch (UnauthorizedAccessException ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 13;
        }
#endif
        catch (Exception ex)
        {
            Console.Error.WriteLine($"{ex.GetType().FullName} (0x{ex.HResult:X8}): {ex.Message}");
            return 1;
        }
    }

#if UNITY_EDITOR_WIN
    [DllImport("kernel32.dll")]
    private static extern uint GetErrorMode();

    private static void Check(bool condition, string name)
    {
        if (!condition)
            throw new InvalidOperationException(name);
        Console.WriteLine("PASS " + name);
    }

    private static byte[] ProtectedDacl(string path)
    {
        var policy = new FileInfo(path).GetAccessControl();
        using (var current = WindowsIdentity.GetCurrent())
            Console.WriteLine(
                $"DACL {Path.GetFileName(path)} {policy.GetSecurityDescriptorSddlForm(AccessControlSections.Access).Replace(current.User.Value, "CURRENT_USER")}"
            );
        if (!policy.AreAccessRulesProtected)
            throw new InvalidOperationException($"Unprotected DACL on {Path.GetFileName(path)}.");
        var descriptor = new RawSecurityDescriptor(policy.GetSecurityDescriptorBinaryForm(), 0);
        var acl = descriptor.DiscretionaryAcl;
        var bytes = new byte[acl.BinaryLength];
        acl.GetBinaryForm(bytes, 0);
        return bytes;
    }

    private static int CheckWindows(string[] args)
    {
        Check((GetErrorMode() & 3) == 3, "child inherited noninteractive error mode");
        string path = args[0];
        HeartbeatFilePublication.Publish(path, args[1]);
        Check(File.ReadAllBytes(path).SequenceEqual(Encoding.UTF8.GetBytes(args[1])), "initial complete UTF-8");
        var access = new FileSecurity();
        access.SetAccessRuleProtection(true, false);
        access.AddAccessRule(
            new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.AnonymousSid, null), FileSystemRights.WriteData, AccessControlType.Deny)
        );
        using (var current = WindowsIdentity.GetCurrent())
            access.AddAccessRule(new FileSystemAccessRule(current.User, FileSystemRights.FullControl, AccessControlType.Allow));
        new FileInfo(path).SetAccessControl(access);
        byte[] expected = ProtectedDacl(path);

        string empty = path + ".empty";
        using (var handle = HeartbeatFilePublication.CreateProtectedStage(path, empty))
        using (var stage = new FileStream(handle, FileAccess.Write))
        {
            Check(stage.Length == 0 && ProtectedDacl(empty).SequenceEqual(expected), "protected DACL before the first byte");
        }
        File.Delete(empty);
        using (var snapshot = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        using (var reader = new StreamReader(snapshot))
        {
            HeartbeatFilePublication.Publish(path, args[2]);
            Check(reader.ReadToEnd() == args[1], "reader opened before replacement");
        }
        byte[] second = Encoding.UTF8.GetBytes(args[2]);
        Check(File.ReadAllBytes(path).SequenceEqual(second) && ProtectedDacl(path).SequenceEqual(expected), "replacement content and protected DACL");
        using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            try
            {
                HeartbeatFilePublication.Publish(path, args[3]);
                throw new InvalidOperationException("Publication succeeded while delete sharing was denied.");
            }
            catch (IOException ex) when (ex.HResult == unchecked((int)0x80070020))
            {
                Console.WriteLine("PASS native sharing violation");
            }
            Check(
                File.ReadAllBytes(path).SequenceEqual(second) && ProtectedDacl(path).SequenceEqual(expected),
                "blocked publication retained content and protected DACL"
            );
            Check(Directory.GetFileSystemEntries(Path.GetDirectoryName(path)).Length == 1, "blocked publication left no stage or backup");
        }
        HeartbeatFilePublication.Publish(path, args[3]);
        Check(ProtectedDacl(path).SequenceEqual(expected), "recovery retained protected DACL");
        return 0;
    }
#endif
}
