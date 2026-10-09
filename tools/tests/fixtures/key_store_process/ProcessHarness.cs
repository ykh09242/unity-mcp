using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Security;

internal static class ProcessHarness
{
    private static int failures;
    private static int checks;

    public static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--child")
            return Child(args);
        string workRoot = args[0];
        Directory.CreateDirectory(workRoot);
        Check(
            "concurrent stdout/stderr and stdin completion",
            () =>
            {
                string input = "fixture-value with spaces\r\n";
                var result = KeyStoreProcess.Run(Start("echo"), input, 5000);
                Require(result.code == 0 && result.stdout == input && result.stderr == "fixture diagnostic\n", "Successful pipe result changed");
            }
        );
        Check(
            "large stderr before stdout cannot block output draining",
            () =>
            {
                var result = KeyStoreProcess.Run(Start("both"), timeoutMs: 5000);
                Require(result.code == 7 && result.stdout.Length == 262144 && result.stderr.Length == 262144, "Concurrent full pipe or exit status failed");
            }
        );
        foreach (string mode in new[] { "hang", "blocked-input", "closed-pipes" })
            Check(
                mode + " timeout reaps the owned child",
                () =>
                {
                    string pidPath = Path.Combine(workRoot, mode + ".pid");
                    if (File.Exists(pidPath))
                        File.Delete(pidPath);
                    var watch = Stopwatch.StartNew();
                    var result = KeyStoreProcess.Run(Start(mode, pidPath), mode == "blocked-input" ? new string('x', 4 * 1024 * 1024) : null, 1500);
                    long elapsed = watch.ElapsedMilliseconds;
                    Require(File.Exists(pidPath), "Test child never signaled startup");
                    int pid = int.Parse(File.ReadAllText(pidPath));
                    bool alive = false;
                    try
                    {
                        using var child = Process.GetProcessById(pid);
                        alive = !child.HasExited;
                        // This is the just-started test child, retained only to clean a failing fixture.
                        if (alive)
                        {
                            child.Kill();
                            child.WaitForExit(1000);
                        }
                    }
                    catch (ArgumentException) { }
                    Console.WriteLine("TIMEOUT " + mode + ": elapsed_ms=" + elapsed + " child_alive=" + alive);
                    Require(result.code == -1 && result.stdout == null, "Timed-out process reported a completed result");
                    Require(!alive, "Owned test child survived timeout");
                    Require(elapsed < 4500, "Pipe IO bypassed timeout");
                }
            );
        Check(
            "failed process start returns failure",
            () =>
            {
                var info = Start("echo");
                info.FileName = Path.Combine(workRoot, "does-not-exist.exe");
                var result = KeyStoreProcess.Run(info, timeoutMs: 100);
                Require(result.code == -1 && result.stdout == null, "Start failure changed result");
            }
        );
        Check(
            "repeated completed children release pipe handles promptly",
            () =>
            {
                for (int i = 0; i < 3; i++)
                    KeyStoreProcess.Run(Start("echo"), "warmup");
                using var self = Process.GetCurrentProcess();
                int before = self.HandleCount;
                for (int i = 0; i < 50; i++)
                    Require(KeyStoreProcess.Run(Start("echo"), "small input").code == 0, "Repeated child failed");
                self.Refresh();
                int growth = self.HandleCount - before;
                Console.WriteLine("HANDLES: growth_after_50=" + growth);
                Require(growth < 20, "Completed commands retained pipe handles before GC");
            }
        );
        Console.WriteLine("RESULT: " + (checks - failures) + "/" + checks + " passed (real OS child processes)");
        return failures == 0 ? 0 : 1;
    }

    private static ProcessStartInfo Start(string mode, string pidPath = "")
    {
        var info = new ProcessStartInfo(Environment.ProcessPath)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = mode == "echo" || mode == "blocked-input",
        };
        if (string.Equals(Path.GetFileNameWithoutExtension(Environment.ProcessPath), "dotnet", StringComparison.OrdinalIgnoreCase))
            info.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
        info.ArgumentList.Add("--child");
        info.ArgumentList.Add(mode);
        info.ArgumentList.Add(pidPath);
        return info;
    }

    private static int Child(string[] args)
    {
        switch (args[1])
        {
            case "echo":
                string input = Console.In.ReadToEnd();
                Console.Out.Write(input);
                Console.Error.Write("fixture diagnostic\n");
                return 0;
            case "both":
                Console.Error.Write(new string('e', 262144));
                Console.Out.Write(new string('o', 262144));
                return 7;
            case "hang":
            case "blocked-input":
            case "closed-pipes":
                File.WriteAllText(args[2], Environment.ProcessId.ToString());
                if (args[1] == "closed-pipes")
                {
                    Console.Out.Close();
                    Console.Error.Close();
                }
                Thread.Sleep(20000);
                return 0;
            default:
                return 9;
        }
    }

    private static void Check(string name, Action action)
    {
        checks++;
        try
        {
            action();
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception ex)
        {
            failures++;
            Console.WriteLine("FAIL: " + name + " -- " + ex.Message);
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}
