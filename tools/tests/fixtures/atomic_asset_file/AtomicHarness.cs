using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Helpers;

namespace UnityEngine
{
    public static class Application
    {
        public static string dataPath;
    }
}

internal static class AtomicHarness
{
    private static readonly BindingFlags Flags = BindingFlags.Static | BindingFlags.NonPublic;
    private static int failures;
    private static int checks;

    private static object Invoke(string type, string method, params object[] args)
    {
        Type implementation = typeof(AtomicAssetFile).Assembly.GetType("MCPForUnity.Editor.Helpers." + type);
        Require(implementation != null, "Missing OS-safe publication: " + type);
        MethodInfo member = implementation.GetMethod(method, Flags);
        Require(member != null, "Missing publication boundary: " + method);
        try
        {
            return member.Invoke(null, args);
        }
        catch (TargetInvocationException ex)
        {
            ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
            throw;
        }
    }

    private static void Require(bool value, string reason)
    {
        if (!value)
            throw new Exception(reason);
    }

    private static void Check(string name, Action body)
    {
        checks++;
        try
        {
            body();
            Console.WriteLine("PASS " + name);
        }
        catch (Exception ex)
        {
            failures++;
            Console.Error.WriteLine("FAIL " + name + ": " + ex.Message);
        }
    }

    private static void Refused(Action body)
    {
        try
        {
            body();
        }
        catch (IOException)
        {
            return;
        }
        throw new Exception("Operation should have refused publication");
    }

    private static string Fresh(string root, string name)
    {
        string path = Path.Combine(root, name + "_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        UnityEngine.Application.dataPath = path;
        return path;
    }

    private static void Stage(string path, byte[] value) =>
        Invoke("AtomicFilePublication", "StageNew", path, (Action<Stream>)(stream => stream.Write(value, 0, value.Length)));

    private static byte[] Bytes(string value) => Encoding.UTF8.GetBytes(value);

    private static void Ready(string path)
    {
        File.WriteAllText(Path.Combine(path, "ready"), "ready");
        Thread.Sleep(Timeout.Infinite);
    }

    private static int Child(string[] args)
    {
        string path = args[2],
            target = Path.Combine(path, "source.txt"),
            stage = Path.Combine(path, ".stage"),
            backup = Path.Combine(path, ".backup");
        UnityEngine.Application.dataPath = path;
        if (args[1] == "recover")
        {
            AtomicAssetFile.Replace(target, Bytes("original"), AtomicAssetFile.Digest(Bytes("candidate")));
            return 0;
        }
        if (args[1] == "partial")
            Invoke(
                "AtomicFilePublication",
                "StageNew",
                stage,
                (Action<Stream>)(
                    stream =>
                    {
                        stream.Write(Bytes("partial"), 0, 7);
                        stream.Flush();
                        Ready(path);
                    }
                )
            );
        Stage(stage, Bytes("candidate"));
        if (args[1] == "prepared")
            Ready(path);
        if (args[1] == "exchange")
        {
            // Windows retains the backup in ReplaceFile; Unix exchanges both names in one syscall.
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                Invoke("AtomicFilePublication", "Replace", stage, target, backup);
            else
                Invoke("AtomicFilePublication", "Exchange", stage, target);
            Ready(path);
        }
        Invoke("AtomicFilePublication", "Replace", stage, target, backup);
        Ready(path);
        return 1;
    }

    private static Process Start(string mode, string root)
    {
        var info = new ProcessStartInfo(Environment.ProcessPath)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
        };
        if (Path.GetFileNameWithoutExtension(info.FileName).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
            info.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
        info.ArgumentList.Add("--child");
        info.ArgumentList.Add(mode);
        info.ArgumentList.Add(root);
        return Process.Start(info);
    }

    private static void Crash(string root, string mode)
    {
        string path = Fresh(root, mode),
            target = Path.Combine(path, "source.txt");
        File.WriteAllText(target, "original");
        File.WriteAllText(target + ".meta", "metadata");
        using (Process child = Start(mode, path))
        {
            try
            {
                var watch = Stopwatch.StartNew();
                while (!File.Exists(Path.Combine(path, "ready")) && !child.HasExited && watch.Elapsed < TimeSpan.FromMinutes(2))
                    Thread.Sleep(10);
                Require(!child.HasExited && File.Exists(Path.Combine(path, "ready")), "Owned child did not reach " + mode);
                // Kill only the Process object created by this scenario. No PID discovery or external termination.
                child.Kill(true);
                Require(child.WaitForExit(30000), "Owned child did not exit");
            }
            finally
            {
                if (!child.HasExited)
                {
                    child.Kill(true);
                    child.WaitForExit(30000);
                }
            }
        }
        bool published = mode == "exchange" || mode == "published";
        Require(File.ReadAllText(target) == (published ? "candidate" : "original"), "Source missing or incomplete at " + mode);
        Require(File.ReadAllText(target + ".meta") == "metadata", "Metadata changed");
        if (published)
        {
            string preimage = File.Exists(Path.Combine(path, ".backup")) ? ".backup" : ".stage";
            Require(File.ReadAllText(Path.Combine(path, preimage)) == "original", "Displaced original lost");
            using Process recovery = Start("recover", path);
            try
            {
                Require(recovery.WaitForExit(120000) && recovery.ExitCode == 0, "Restarted recovery failed");
            }
            finally
            {
                if (!recovery.HasExited)
                {
                    recovery.Kill(true);
                    recovery.WaitForExit(30000);
                }
            }
            Require(File.ReadAllText(target) == "original", "Restarted recovery did not restore complete original");
        }
    }

    public static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--child")
            return Child(args);
        string root = Path.GetFullPath(args[0]);
        Directory.CreateDirectory(root);
        Check(
            "no-replace preserves a concurrent creator and stage",
            () =>
            {
                string path = Fresh(root, "create_새😀"),
                    stage = Path.Combine(path, ".stage"),
                    target = Path.Combine(path, "source.txt");
                Stage(stage, Bytes("candidate"));
                File.WriteAllText(target, "external");
                Refused(() => Invoke("AtomicFilePublication", "Create", stage, target));
                Require(File.ReadAllText(target) == "external" && File.ReadAllText(stage) == "candidate", "Creator clobbered");
                File.Delete(target);
                Invoke("AtomicFilePublication", "Create", stage, target);
                Require(File.ReadAllText(target) == "candidate" && !File.Exists(stage), "Creation incomplete");
            }
        );
        Check(
            "post-guard conflict keeps the actual displaced bytes",
            () =>
            {
                string path = Fresh(root, "conflict"),
                    stage = Path.Combine(path, ".stage"),
                    target = Path.Combine(path, "source.txt"),
                    backup = Path.Combine(path, ".backup");
                // A writer replaces the source after caller admission, immediately before the commit boundary.
                Stage(stage, Bytes("candidate"));
                File.WriteAllText(target, "external");
                File.WriteAllText(target + ".meta", "metadata");
                Refused(() =>
                    Invoke(
                        "AtomicAssetFile",
                        "CommitPrepared",
                        stage,
                        target,
                        backup,
                        AtomicAssetFile.Digest(Bytes("original")),
                        AtomicAssetFile.Digest(Bytes("metadata")),
                        AtomicAssetFile.Digest(Bytes("candidate"))
                    )
                );
                Require(File.ReadAllText(target) == "candidate" && File.ReadAllText(backup) == "external", "Conflict preimage was discarded");
            }
        );
        Check(
            "partial stage disk-full exception leaves source intact",
            () =>
            {
                string path = Fresh(root, "disk_full"),
                    target = Path.Combine(path, "source.txt"),
                    stage = Path.Combine(path, ".stage");
                File.WriteAllText(target, "original");
                Refused(() =>
                    Invoke(
                        "AtomicFilePublication",
                        "StageNew",
                        stage,
                        (Action<Stream>)(
                            stream =>
                            {
                                stream.Write(Bytes("partial"), 0, 7);
                                throw new IOException("Injected disk-full write failure");
                            }
                        )
                    )
                );
                Require(File.ReadAllText(target) == "original" && File.ReadAllText(stage) == "partial", "Failed stage changed source");
            }
        );
        foreach (string mode in new[] { "partial", "prepared", "exchange", "published" })
            Check("owned process termination at " + mode, () => Crash(root, mode));
        Console.WriteLine("Atomic publication: " + (checks - failures) + "/" + checks + " passed on " + RuntimeInformation.OSDescription);
        return failures == 0 ? 0 : 1;
    }
}
