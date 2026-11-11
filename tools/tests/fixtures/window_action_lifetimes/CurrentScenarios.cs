#if CURRENT_SERVER_SOURCE
using System;
using System.IO;

namespace WindowActions;

public static partial class Program
{
    private static int CurrentMain(string[] args)
    {
        currentGroup = "clear";
        foreach (string initial in new[] { "synthetic-url", "", "   ", null })
        {
            string captured = initial;
            Run($"explicit clear initial={initial ?? "<null>"} commits once", () => Clear(captured, true));
        }
        Run("detached clear commits explicitly", () => Clear("synthetic-url", false));
        Run("1000 clears emit exactly one event pair per click", () =>
        {
            EditorPrefs.Deletes = 0;
            var action = new AdvancedAction();
            action.Register();
            int git = 0,
                http = 0;
            action.OnGitUrlChanged += () => git++;
            action.OnHttpServerCommandUpdateRequested += () => http++;
            for (int i = 0; i < 1000; i++)
                action.clearGitUrlButton.Click();
            Equal(1000, git);
            Equal(1000, http);
            Equal(1000, EditorPrefs.Deletes);
        });
        Run("draft is inert until Apply and repeated Apply is inert", () =>
        {
            EditorPrefs.Stored = "previous";
            EditorPrefs.Sets = 0;
            var action = new AdvancedAction();
            action.Register();
            int git = 0,
                http = 0;
            action.OnGitUrlChanged += () => git++;
            action.OnHttpServerCommandUpdateRequested += () => http++;
            action.gitUrlOverride.value = "  synthetic-url  ";
            Equal("previous", EditorPrefs.Stored);
            Equal(0, EditorPrefs.Sets);
            Equal(0, git);
            Equal(0, http);
            action.Apply();
            action.Apply();
            Equal("synthetic-url", EditorPrefs.Stored);
            Equal("synthetic-url", action.gitUrlOverride.value);
            Equal(1, EditorPrefs.Sets);
            Equal(1, git);
            Equal(1, http);
        });
        Run("caught subscriber failure preserves write and stops HTTP notification", () =>
        {
            var action = new AdvancedAction();
            action.Register();
            int git = 0,
                http = 0;
            action.OnGitUrlChanged += () =>
            {
                git++;
                throw new InvalidOperationException("subscriber");
            };
            action.OnHttpServerCommandUpdateRequested += () => http++;
            action.clearGitUrlButton.Click();
            Equal(1, git);
            Equal(0, http);
            Equal(null, EditorPrefs.Stored);
            Equal(true, action.StatusError);
        });
        foreach (var key in new[] { KeyCode.Return, KeyCode.KeypadEnter })
        {
            Run($"{key} commits draft once", () =>
            {
                EditorPrefs.Sets = 0;
                var action = new AdvancedAction();
                action.Register();
                action.gitUrlOverride.value = "synthetic-url";
                var keyEvent = new KeyDownEvent { keyCode = key };
                action.gitUrlOverride.Send(keyEvent);
                action.Apply();
                Equal(true, keyEvent.Stopped);
                Equal(1, EditorPrefs.Sets);
            });
        }
        Run("focus inside field preserves draft and external focus commits", () =>
        {
            EditorPrefs.Sets = 0;
            var action = new AdvancedAction();
            action.Register();
            action.gitUrlOverride.value = "synthetic-url";
            action.gitUrlOverride.Send(new FocusOutEvent { relatedTarget = action.gitUrlOverride });
            Equal(0, EditorPrefs.Sets);
            action.gitUrlOverride.Send(new FocusOutEvent());
            Equal(1, EditorPrefs.Sets);
        });
        currentGroup = "browse";
        string selectedServer = Path.GetFullPath("synthetic-server");
        Run("browse changed selection commits once", () => CurrentBrowse(selectedServer, "old-server"));
        Run("browse same selection still commits once", () => CurrentBrowse(selectedServer, selectedServer));
        Run("empty picker preserves state", () => CurrentBrowse("", "old-server"));
        Run("null picker preserves state", () => CurrentBrowse(null, "old-server"));
        Run("parent correction commits normalized source once", () => CurrentBrowse(Path.GetFullPath("synthetic-checkout"), "old-server", normalize: true));
        Run("resolver failure preserves committed value and retains draft", () =>
            CurrentBrowse(Path.GetFullPath("synthetic-server"), "old-server", failure: "resolve")
        );
        Run("persistence failure preserves committed value and can retry", () => CurrentBrowse(selectedServer, "old-server", failure: "persist"));
        Run("subscriber failure preserves write and stops HTTP notification", () => CurrentBrowse(selectedServer, "old-server", failure: "subscriber"));
        currentGroup = "process";
        Run("real BCL wrapper disposal closes handle while child remains alive", () => RealProcess(args[0]));
        int expected =
            selectedGroup == "process" ? 5
            : selectedGroup == "clear" ? 11
            : selectedGroup == "browse" ? 8
            : 24;
        if (passed + failed != expected)
        {
            failed++;
            Console.WriteLine($"FAIL expected {expected} cases");
        }
        Console.WriteLine($"RESULT passed={passed} failed={failed}");
        return failed == 0 ? 0 : 1;
    }

    private static void CurrentBrowse(string picked, string initial, bool normalize = false, string failure = null)
    {
        Trace.Steps.Clear();
        File.Queries = 0;
        File.Handler = path =>
        {
            if (failure == "resolve")
                throw new InvalidOperationException("resolve");
            return !normalize || path == Path.Combine(picked, "Server", "pyproject.toml");
        };
        EditorUtility.Picked = picked;
        EditorPrefs.Stored = initial;
        EditorPrefs.Sets = 0;
        EditorPrefs.ThrowOnSet = failure == "persist";
        var action = new AdvancedAction();
        action.gitUrlOverride.SetValueWithoutNotify(initial);
        action.Register();
        string expected = normalize ? Path.Combine(picked, "Server") : picked;
        int git = 0,
            http = 0;
        action.OnGitUrlChanged += () =>
        {
            git++;
            Trace.Steps.Add("git");
            Equal(expected, EditorPrefs.Stored);
            Equal(expected, action.gitUrlOverride.value);
            if (failure == "subscriber")
                throw new InvalidOperationException("subscriber");
        };
        action.OnHttpServerCommandUpdateRequested += () =>
        {
            http++;
            Trace.Steps.Add("http");
        };
        try
        {
            action.Browse();
            if (string.IsNullOrEmpty(picked))
            {
                Equal(initial, EditorPrefs.Stored);
                Equal(initial, action.gitUrlOverride.value);
                Equal(0, File.Queries);
                Equal(0, EditorPrefs.Sets);
                Equal(0, git);
                Equal(0, http);
                return;
            }
            Equal(failure != null, action.StatusError);
            Equal(failure is "resolve" or "persist" ? initial : expected, EditorPrefs.Stored);
            Equal(failure == "resolve" ? picked : expected, action.gitUrlOverride.value);
            Equal(failure == "resolve" ? 0 : 1, EditorPrefs.Sets);
            Equal(failure is "resolve" or "persist" ? 0 : 1, git);
            Equal(failure == null ? 1 : 0, http);
            string queryTrace = normalize ? "query,query,query," : "query,query,";
            Equal(
                failure == "resolve" ? "query"
                    : failure == "persist" ? queryTrace + "persist"
                    : failure == "subscriber" ? queryTrace + "persist,git"
                    : queryTrace + "persist,git,http",
                string.Join(",", Trace.Steps)
            );
            if (failure == "persist")
            {
                EditorPrefs.ThrowOnSet = false;
                action.Apply();
                Equal(expected, EditorPrefs.Stored);
                Equal(1, git);
                Equal(1, http);
                Equal(false, action.StatusError);
            }
        }
        finally
        {
            File.Handler = null;
            EditorPrefs.ThrowOnSet = false;
        }
    }
}
#endif
