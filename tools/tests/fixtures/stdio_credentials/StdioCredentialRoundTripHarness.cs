using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.Transport.Transports;

// Compiled beside the complete production writer sources. No mock Cred* functions.
internal static class StdioCredentialRoundTripHarness
{
    private const string TargetNamespace = "MCPForUnity.Stdio";
    private const int ErrorNotFound = 1168;
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "CredReadW")]
    private static extern bool CredRead(string target, int type, int flags, out IntPtr value);
    [DllImport("advapi32.dll", EntryPoint = "CredFree")]
    private static extern void CredFree(IntPtr value);

    private static bool Exists(string ownedTarget, out int error)
    {
        bool found = CredRead(ownedTarget, 1, 0, out IntPtr pointer);
        error = found ? 0 : Marshal.GetLastWin32Error();
        if (found) CredFree(pointer); // Existence check only: never inspect the blob here.
        return found;
    }
    private static string Quote(string path) => "\"" + path.Replace("\"", "\\\"") + "\"";
    public static int Main(string[] args)
    {
        if (Environment.OSVersion.Platform != PlatformID.Win32NT || args.Length != 3)
        {
            Console.WriteLine("FAIL: Windows and three owned fixture paths are required");
            return 1;
        }
        byte[] generationBytes = new byte[16];
        byte[] tokenBytes = new byte[32];
        using (var random = RandomNumberGenerator.Create())
        {
            random.GetBytes(generationBytes);
            random.GetBytes(tokenBytes);
        }
        string generation = BitConverter.ToString(generationBytes).Replace("-", "").ToLowerInvariant();
        string ownedTarget = TargetNamespace + ":" + generation;
        string token = BitConverter.ToString(tokenBytes).Replace("-", "").ToLowerInvariant();
        Array.Clear(generationBytes, 0, generationBytes.Length);
        Array.Clear(tokenBytes, 0, tokenBytes.Length);
        StdioLaunchCredential published = null;
        Process child = null;
        bool matched = false, cleanup = false;
        string stage = "production_writer";
        try
        {
            // Only this fresh random launch entry is written or subsequently inspected.
            published = new StdioLaunchCredential(generation, token);
            stage = "fixed_namespace_presence";
            if (!Exists(ownedTarget, out int presenceError))
                throw new InvalidOperationException("Owned fixed-namespace credential missing; Win32 " + presenceError);
            Console.WriteLine("PASS: production writer published one owned fixed-namespace entry");
            stage = "production_python_reader";
            child = Process.Start(new ProcessStartInfo
            {
                FileName = args[0],
                Arguments = "-B " + Quote(args[1]) + " " + Quote(args[2]),
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true
            });
            var stdout = child.StandardOutput.ReadToEndAsync();
            var stderr = child.StandardError.ReadToEndAsync();
            // The expected token travels exclusively through this owned anonymous stdin pipe.
            // It never enters process arguments, environment variables, logs, or plain files.
            child.StandardInput.WriteLine(generation);
            child.StandardInput.WriteLine(token);
            child.StandardInput.Close();
            token = null;
            if (!child.WaitForExit(15000)) throw new TimeoutException("Owned Python reader timed out");
            matched = child.ExitCode == 0 && stdout.Result.Trim() == "PASS: production Python reader matched; malformed generations rejected before native access";
            string readerStatus = stdout.Result.Trim();
            if (readerStatus == "FAIL: owned channel framing" || readerStatus == "FAIL: malformed generation rejection" ||
                readerStatus == "FAIL: production native credential match")
                Console.WriteLine(readerStatus);
            if (child.ExitCode != 0) Console.WriteLine("FAIL: Python child exit=" + child.ExitCode);
            // Do not forward arbitrary child error/output: credential-bearing locals stay private.
            if (!matched || stderr.Result.Length != 0) throw new InvalidOperationException("Owned Python reader check failed");
            Console.WriteLine("PASS: production Python reader matched; malformed generations rejected before native access");
        }
        catch (Exception error)
        {
            matched = false;
            // Stage, exception type and native status are non-secret diagnostics.
            Console.WriteLine("FAIL: owned credential roundtrip stage=" + stage + " exception=" +
                error.GetType().Name + " win32=" + Marshal.GetLastWin32Error());
        }
        finally
        {
            token = null;
            try { if (child != null && !child.HasExited) child.Kill(); } catch { }
            child?.Dispose();
            try { published?.Dispose(); } catch { }
            try
            {
                // Unconditionally attempt exact-owned-entry removal, including constructor failure.
                new WindowsCredentialKeyStore(TargetNamespace).Delete(generation);
                cleanup = !Exists(ownedTarget, out int cleanupError) && cleanupError == ErrorNotFound;
            }
            catch { cleanup = false; }
            Console.WriteLine(cleanup ? "PASS: exact owned credential cleanup confirmed (ERROR_NOT_FOUND)" :
                "FAIL: cleanup not confirmed for owned target " + ownedTarget);
        }
        return matched && cleanup ? 0 : 1;
    }
}
