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
        int failures = 0;
        foreach (
            var test in new (string Name, Action<string> Run)[]
            {
                ("concurrent-first-use", ConcurrentFirstUse),
                ("malformed-material", MalformedMaterial),
                ("missing-material-read", MissingMaterialRead),
                ("atomic-ciphertext", AtomicCiphertext),
            }
        )
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
        string secret = Path.Combine(root, "secret.bin");
        File.Delete(secret);
        Require(!store.TryGet("fixture", out _), "missing secret accepted");
        Require(!File.Exists(secret), "read recreated missing material");
    }

    private static void AtomicCiphertext(string root)
    {
        var store = new EncryptedFileKeyStore(root);
        string value = new string('x', 1024 * 1024);
        store.Set("fixture", value);
        string path = Path.Combine(root, "key_fixture.bin");
        int expectedLength = Convert.FromBase64String(File.ReadAllText(path)).Length;
        var writer = Task.Run(() =>
        {
            for (int i = 0; i < 12; i++)
                new EncryptedFileKeyStore(root).Set("fixture", value);
        });
        int observations = 0;
        while (!writer.IsCompleted)
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            Require(Convert.FromBase64String(reader.ReadToEnd()).Length == expectedLength, "partial ciphertext visible");
            observations++;
        }
        writer.GetAwaiter().GetResult();
        Require(observations > 0, "no concurrent observations");
        Require(new EncryptedFileKeyStore(root).TryGet("fixture", out string actual) && actual == value, "roundtrip failed");
        Require(Directory.GetFiles(root, "*.tmp").Length == 0, "temporary publication files leaked");
        Console.WriteLine("Concurrent ciphertext observations: " + observations);
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
    }

    internal static class Application
    {
        internal static RuntimePlatform platform => RuntimePlatform.WindowsEditor;
    }

    internal static class SystemInfo
    {
        internal static string deviceUniqueIdentifier => "synthetic-fixture-machine";
        internal static string unsupportedIdentifier => "unsupported";
    }
}
