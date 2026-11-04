using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;

internal static class StoreHarness
{
    private static int Main(string[] args)
    {
        if (args.Length == 4 && args[0] == "worker")
        {
            var store = new EncryptedFileKeyStore(args[1]);
            File.WriteAllText(args[3] + "." + args[2], "ready");
            Require(SpinWait.SpinUntil(() => File.Exists(args[3]), TimeSpan.FromSeconds(20)), "gate timeout");
            store.Set("fixture" + args[2], "synthetic-value-" + args[2]);
            return 0;
        }
        if (args.Length != 1)
            throw new ArgumentException("Pass one owned fixture root.");
        string root = Path.GetFullPath(args[0]);
        Directory.CreateDirectory(root);
        Console.WriteLine("HOST " + Environment.OSVersion.Platform + ": Unity platform=" + UnityEngine.Application.platform);
        var tests = new List<(string Name, Action<string> Run)>
        {
            ("concurrent-first-use", ConcurrentFirstUse),
            ("malformed-material", MalformedMaterial),
            ("missing-material-read", MissingMaterialRead),
            ("atomic-ciphertext", AtomicCiphertext),
            ("publication-failure-preserves-winner", PublicationFailurePreservesWinner),
        };
        if (OperatingSystem.IsWindows())
            tests.Add(("ciphertext-open-contention", CiphertextOpenContention));
        else
            Console.WriteLine("NOT APPLICABLE ciphertext-open-contention: requires Windows sharing semantics");
        int failures = 0;
        foreach (var test in tests)
        {
            string path = Path.Combine(root, test.Name + "-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(path);
            try
            {
                test.Run(path);
                Console.WriteLine("PASS " + test.Name);
            }
            catch (Exception error)
            {
                failures++;
                Console.WriteLine("FAIL " + test.Name + ": " + error);
            }
        }
        Console.WriteLine("RESULT: " + (tests.Count - failures) + "/" + tests.Count + " applicable cases passed");
        return failures == 0 ? 0 : 1;
    }

    private static void ConcurrentFirstUse(string root)
    {
        string gate = Path.Combine(root, "gate");
        var children = new List<Process>();
        try
        {
            for (int i = 0; i < 8; i++)
            {
                var start = new ProcessStartInfo("dotnet")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                foreach (string argument in new[] { Assembly.GetExecutingAssembly().Location, "worker", root, i.ToString(), gate })
                    start.ArgumentList.Add(argument);
                children.Add(Process.Start(start));
            }
            Require(
                SpinWait.SpinUntil(
                    () =>
                    {
                        for (int i = 0; i < children.Count; i++)
                            if (!File.Exists(gate + "." + i))
                                return false;
                        return true;
                    },
                    TimeSpan.FromSeconds(20)
                ),
                "children not ready"
            );
            File.WriteAllText(gate, "go");
            foreach (var child in children)
            {
                Require(child.WaitForExit(20000), "child timeout");
                Require(child.ExitCode == 0, "child exit " + child.ExitCode + ": " + child.StandardError.ReadToEnd());
            }
            var reopened = new EncryptedFileKeyStore(root);
            for (int i = 0; i < children.Count; i++)
                Require(reopened.TryGet("fixture" + i, out string value) && value == "synthetic-value-" + i, "race lost credential " + i);
            Require(File.ReadAllBytes(Path.Combine(root, "secret.bin")).Length == 32, "secret size");
            Require(File.ReadAllBytes(Path.Combine(root, "salt.bin")).Length == 16, "salt size");
            byte[] secret = File.ReadAllBytes(Path.Combine(root, "secret.bin"));
            byte[] salt = File.ReadAllBytes(Path.Combine(root, "salt.bin"));
            reopened.Set("fixture-extra", "synthetic-extra");
            Require(Convert.ToBase64String(File.ReadAllBytes(Path.Combine(root, "secret.bin"))) == Convert.ToBase64String(secret), "secret rotated");
            Require(Convert.ToBase64String(File.ReadAllBytes(Path.Combine(root, "salt.bin"))) == Convert.ToBase64String(salt), "salt rotated");
            Require(Directory.GetFiles(root, "*.tmp").Length == 0, "initializer temporary files leaked");
        }
        finally
        {
            foreach (var child in children)
            {
                if (!child.HasExited)
                {
                    child.Kill(true);
                    child.WaitForExit(5000);
                }
                child.Dispose();
            }
        }
    }

    private static void MalformedMaterial(string root)
    {
        foreach (string file in new[] { "secret.bin", "salt.bin" })
        {
            string path = Path.Combine(root, file);
            byte[] malformed = { 1, 2, 3 };
            File.WriteAllBytes(path, malformed);
            bool refused = false;
            try
            {
                new EncryptedFileKeyStore(root).Set("fixture", "synthetic");
            }
            catch (CryptographicException)
            {
                refused = true;
            }
            Require(refused, "malformed " + file + " was accepted");
            Require(Convert.ToBase64String(File.ReadAllBytes(path)) == Convert.ToBase64String(malformed), "material rotated");
            Require(!File.Exists(Path.Combine(root, "key_fixture.bin")), "failed write published key");
            File.Delete(path);
        }
    }

    private static void MissingMaterialRead(string root)
    {
        var store = new EncryptedFileKeyStore(root);
        store.Set("fixture", "synthetic");
        foreach (string file in new[] { "secret.bin", "salt.bin" })
        {
            string material = Path.Combine(root, file);
            byte[] original = File.ReadAllBytes(material);
            File.Delete(material);
            Require(!store.TryGet("fixture", out _), "missing " + file + " accepted");
            Require(!File.Exists(material), "read recreated missing material");
            File.WriteAllBytes(material, original);
        }
    }

    private static void AtomicCiphertext(string root)
    {
        var store = new EncryptedFileKeyStore(root);
        string value = new string('x', 1024 * 1024);
        store.Set("fixture", value);
        string path = Path.Combine(root, "key_fixture.bin");
        int expectedLength = Convert.FromBase64String(File.ReadAllText(path)).Length;
        object[] keys = { null, null, false };
        typeof(EncryptedFileKeyStore).GetMethod("DeriveKeys", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(store, keys);
        using var hmac = new HMACSHA256((byte[])keys[1]);
        var writer = Task.Run(() =>
        {
            for (int i = 0; i < 12; i++)
                new EncryptedFileKeyStore(root).Set("fixture", value);
        });
        int observations = 0;
        try
        {
            while (!writer.IsCompleted)
            {
                using var stream = OpenPublishedCiphertext(path);
                using var reader = new StreamReader(stream, Encoding.UTF8);
                byte[] blob = Convert.FromBase64String(reader.ReadToEnd());
                Require(blob.Length == expectedLength, "partial ciphertext visible");
                byte[] authenticated = new byte[blob.Length - 32];
                Buffer.BlockCopy(blob, 0, authenticated, 0, 16);
                Buffer.BlockCopy(blob, 48, authenticated, 16, blob.Length - 48);
                Require(CryptographicOperations.FixedTimeEquals(hmac.ComputeHash(authenticated), blob.AsSpan(16, 32)), "mixed ciphertext visible");
                observations++;
            }
        }
        finally
        {
            writer.GetAwaiter().GetResult();
        }
        Require(observations > 0, "no concurrent observations");
        Require(new EncryptedFileKeyStore(root).TryGet("fixture", out string actual) && actual == value, "roundtrip failed");
        Require(Directory.GetFiles(root, "*.tmp").Length == 0, "temporary publication files leaked");
        Console.WriteLine("Concurrent ciphertext observations: " + observations);
    }

    private static void CiphertextOpenContention(string root)
    {
        var store = new EncryptedFileKeyStore(root);
        store.Set("fixture", "synthetic");
        string path = Path.Combine(root, "key_fixture.bin");
        using (var held = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None))
        {
            var releasing = Task.Run(() =>
            {
                Thread.Sleep(100);
                held.Dispose();
            });
            try
            {
                Require(store.TryGet("fixture", out string value) && value == "synthetic", "released ciphertext lock was not retried");
            }
            finally
            {
                releasing.GetAwaiter().GetResult();
            }
        }
        byte[] original = File.ReadAllBytes(path);
        using (var held = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None))
        {
            var elapsed = Stopwatch.StartNew();
            Require(!store.TryGet("fixture", out _), "held ciphertext lock was accepted");
            Require(elapsed.Elapsed < TimeSpan.FromSeconds(2), "ciphertext open retry was unbounded");
        }
        Require(Convert.ToBase64String(File.ReadAllBytes(path)) == Convert.ToBase64String(original), "failed read changed ciphertext");
        File.WriteAllText(path, "not base64");
        Require(!store.TryGet("fixture", out _), "malformed ciphertext was accepted");
        Require(File.ReadAllText(path) == "not base64", "malformed ciphertext changed");
    }

    private static FileStream OpenPublishedCiphertext(string path)
    {
        var elapsed = Stopwatch.StartNew();
        while (true)
        {
            try
            {
                return new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
            }
            catch (IOException error)
                when (OperatingSystem.IsWindows() && ((error.HResult & 0xffff) == 32 || (error.HResult & 0xffff) == 2) && elapsed.ElapsedMilliseconds < 250)
            {
                Thread.Sleep(10);
            }
        }
    }

    private static void PublicationFailurePreservesWinner(string root)
    {
        var store = new EncryptedFileKeyStore(root);
        store.Set("fixture", "synthetic-original");
        string path = Path.Combine(root, "key_fixture.bin");
        byte[] original = File.ReadAllBytes(path);
        if (OperatingSystem.IsWindows())
        {
            File.SetAttributes(path, FileAttributes.ReadOnly);
            bool refused = false;
            try
            {
                store.Set("fixture", "synthetic-replacement");
            }
            catch (UnauthorizedAccessException)
            {
                refused = true;
            }
            finally
            {
                File.SetAttributes(path, FileAttributes.Normal);
            }
            Require(refused, "access-denied publication was suppressed");
        }
        else
        {
            Console.WriteLine("NOT APPLICABLE windows-access-denied-publication: requires Windows file attributes");
        }
        // Real partial content must fail, rather than being replaced with fresh material.
        string secret = Path.Combine(root, "secret.bin");
        File.WriteAllBytes(secret, new byte[] { 1, 2, 3 });
        bool malformedRefused = false;
        try
        {
            store.Set("fixture", "synthetic-replacement");
        }
        catch (CryptographicException)
        {
            malformedRefused = true;
        }
        Require(malformedRefused, "partial derivation material was accepted");
        Require(File.ReadAllBytes(secret).Length == 3, "partial material was rotated");
        Require(Convert.ToBase64String(File.ReadAllBytes(path)) == Convert.ToBase64String(original), "failed publication replaced winner");
        Require(Directory.GetFiles(root, "*.tmp").Length == 0, "failed publication leaked temporary files");
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}

namespace UnityEngine
{
    internal enum RuntimePlatform
    {
        WindowsEditor,
        LinuxEditor,
        OSXEditor,
    }

    internal static class Application
    {
        internal static RuntimePlatform platform =>
            OperatingSystem.IsWindows() ? RuntimePlatform.WindowsEditor
            : OperatingSystem.IsLinux() ? RuntimePlatform.LinuxEditor
            : OperatingSystem.IsMacOS() ? RuntimePlatform.OSXEditor
            : throw new PlatformNotSupportedException("The fixture supports Windows, Linux and macOS hosts.");
    }

    internal static class SystemInfo
    {
        internal static string deviceUniqueIdentifier => "synthetic-fixture-machine";
        internal static string unsupportedIdentifier => "unsupported";
    }
}
