using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using BclFile = System.IO.File;
using BclProcess = System.Diagnostics.Process;
using BclProcessStartInfo = System.Diagnostics.ProcessStartInfo;

namespace WindowActions;

public static class Program
{
    private static int passed;
    private static int failed;
    private static string selectedGroup;
    private static string currentGroup;

    public static int Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--child")
        {
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (!BclFile.Exists(args[1]) && DateTime.UtcNow < deadline)
                Thread.Sleep(20);
            return 0;
        }
        selectedGroup = args.Length > 1 ? args[1] : "all";
        currentGroup = "process";
        Run(
            "1000 successful launches release every returned wrapper",
            () =>
            {
                Process.Reset();
                var action = new ClientAction();
                action.configPathField.value = "synthetic-config.json";
                for (int i = 0; i < 1000; i++)
                    action.Open();
                int outstanding = Process.Started.Count(x => !x.Disposed);
                Console.WriteLine($"METRIC returned={Process.Started.Count} undisposed={outstanding}");
                Equal(1000, Process.Started.Count);
                Equal(0, outstanding);
                Equal("synthetic-config.json", Process.Last.FileName);
                Equal(true, Process.Last.UseShellExecute);
            }
        );
        Run(
            "shell association may return null",
            () =>
            {
                Process.Reset();
                Process.ReturnNull = true;
                new ClientAction().Open();
                Equal(0, McpLog.Errors);
                Equal(0, Process.Started.Count);
            }
        );
        Run(
            "missing config displays existing dialog without launching",
            () =>
            {
                Process.Reset();
                File.Present = false;
                new ClientAction().Open();
                Equal(1, EditorUtility.Dialogs);
                Equal(null, Process.Last);
            }
        );
        Run(
            "start failure keeps existing error handling",
            () =>
            {
                Process.Reset();
                Process.Throw = true;
                new ClientAction().Open();
                Equal(1, McpLog.Errors);
                Equal(0, Process.Started.Count);
            }
        );
        currentGroup = "clear";
        foreach (string initial in new[] { "synthetic-url", "", "   ", null })
        {
            string captured = initial;
            Run($"attached clear initial={initial ?? "<null>"} and repeated click notify once each", () => Clear(captured, true));
        }
        Run("detached clear preserves explicit notifications", () => Clear("synthetic-url", false));
        Run(
            "1000 repeated clears retain one subscription and one notification per click",
            () =>
            {
                EditorPrefs.Deletes = 0;
                var action = new AdvancedAction();
                action.gitUrlOverride.SetValueWithoutNotify("synthetic-url");
                action.Register();
                int git = 0,
                    http = 0;
                action.OnGitUrlChanged += () => git++;
                action.OnHttpServerCommandUpdateRequested += () => http++;
                for (int i = 0; i < 1000; i++)
                    action.clearGitUrlButton.Click();
                Console.WriteLine($"METRIC clicks=1000 git_notifications={git} http_notifications={http} deletes={EditorPrefs.Deletes}");
                Equal(1000, git);
                Equal(1000, http);
                Equal(1000, EditorPrefs.Deletes);
            }
        );
        Run(
            "ordinary text edit still persists and notifies",
            () =>
            {
                var action = new AdvancedAction();
                action.Register();
                int git = 0,
                    http = 0;
                action.OnGitUrlChanged += () => git++;
                action.OnHttpServerCommandUpdateRequested += () => http++;
                action.gitUrlOverride.value = "  synthetic-url  ";
                Equal("synthetic-url", EditorPrefs.Stored);
                Equal(1, git);
                Equal(1, http);
            }
        );
        Run(
            "subscriber exception still propagates and stops later notification",
            () =>
            {
                var action = new AdvancedAction();
                action.gitUrlOverride.SetValueWithoutNotify("synthetic-url");
                action.Register();
                int git = 0,
                    http = 0;
                action.OnGitUrlChanged += () =>
                {
                    git++;
                    throw new InvalidOperationException("subscriber");
                };
                action.OnHttpServerCommandUpdateRequested += () => http++;
                bool threw = false;
                try
                {
                    action.clearGitUrlButton.Click();
                }
                catch (InvalidOperationException ex)
                {
                    threw = ex.Message == "subscriber";
                }
                Equal(true, threw);
                Equal(1, git);
                Equal(0, http);
                Equal("", action.gitUrlOverride.value);
            }
        );
        currentGroup = "process";
        Run("real BCL wrapper disposal closes handle while child remains alive", () => RealProcess(args[0]));
        int expectedCases =
            selectedGroup == "process" ? 5
            : selectedGroup == "clear" ? 8
            : 13;
        if (passed + failed != expectedCases)
        {
            failed++;
            Console.WriteLine($"FAIL expected {expectedCases} executed cases, actual={passed + failed - 1}");
        }
        Console.WriteLine($"RESULT passed={passed} failed={failed}");
        return failed == 0 ? 0 : 1;
    }

    private static void Clear(string initial, bool attached)
    {
        EditorPrefs.Deletes = 0;
        EditorPrefs.Stored = "synthetic-stored-value";
        var action = new AdvancedAction();
        action.gitUrlOverride.Attached = attached;
        action.gitUrlOverride.SetValueWithoutNotify(initial);
        action.Register();
        int git = 0,
            http = 0;
        action.OnGitUrlChanged += () => git++;
        action.OnHttpServerCommandUpdateRequested += () => http++;
        for (int click = 1; click <= 2; click++)
        {
            action.clearGitUrlButton.Click();
            Console.WriteLine($"METRIC attached={attached} initial={initial ?? "<null>"} clicks={click} git={git} http={http} deletes={EditorPrefs.Deletes}");
            Equal(click, git);
            Equal(click, http);
            Equal(click, EditorPrefs.Deletes);
            Equal("", action.gitUrlOverride.value);
            Equal(null, EditorPrefs.Stored);
        }
    }

    private static void RealProcess(string output)
    {
        string signal = Path.Combine(output, $"child-exit-{Guid.NewGuid():N}.signal");
        var info = new BclProcessStartInfo
        {
            FileName = Environment.ProcessPath,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden,
        };
        info.ArgumentList.Add("--child");
        info.ArgumentList.Add(signal);
        using var child = BclProcess.Start(info);
        using var observer = BclProcess.GetProcessById(child.Id);
        var handle = child.SafeHandle;
        try
        {
            child.Dispose();
            bool closed = handle.IsClosed;
            bool alive = !observer.HasExited;
            Console.WriteLine($"BCL handle_closed={closed} child_alive_after_dispose={alive}");
            Equal(true, closed);
            Equal(true, alive);
        }
        finally
        {
            BclFile.WriteAllText(signal, "finish naturally");
            bool exited = observer.WaitForExit(12000);
            Console.WriteLine($"BCL natural_exit={exited}");
            Equal(true, exited);
            BclFile.Delete(signal);
        }
    }

    private static void Run(string name, Action action)
    {
        if (selectedGroup != "all" && selectedGroup != currentGroup)
            return;
        try
        {
            action();
            passed++;
            Console.WriteLine($"PASS {name}");
        }
        catch (Exception ex)
        {
            failed++;
            Console.WriteLine($"FAIL {name}: {ex.Message}");
        }
    }

    private static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
            throw new InvalidOperationException($"expected={expected}, actual={actual}");
    }
}
