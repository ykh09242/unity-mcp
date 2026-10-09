using System;
using System.Collections.Generic;

namespace WindowActions;

// Explicit test boundaries: UI value notifications model an attached panel; no real preferences/files are read.
public sealed class TextField
{
    private string current;
    private Action<Change> changed;
    public bool Attached = true;
    public string value
    {
        get => current;
        set
        {
            if (current == value)
                return;
            current = value;
            if (Attached)
                changed?.Invoke(new Change { newValue = value });
        }
    }

    public void SetValueWithoutNotify(string value) => current = value;

    public void RegisterValueChangedCallback(Action<Change> callback) => changed += callback;
}

public sealed class Change
{
    public string newValue;
}

public sealed class Button
{
    public event Action clicked;

    public void Click() => clicked?.Invoke();
}

public static class EditorPrefKeys
{
    public const string GitUrlOverride = "synthetic-git-url";
}

public static class EditorPrefs
{
    public static string Stored;
    public static int Deletes;
    public static int Sets;
    public static bool ThrowOnSet;

    public static void DeleteKey(string key)
    {
        Stored = null;
        Deletes++;
    }

    public static void SetString(string key, string value)
    {
        Sets++;
        Trace.Steps.Add("persist");
        if (ThrowOnSet)
            throw new InvalidOperationException("persist");
        Stored = value;
    }
}

public static class File
{
    public static bool Present = true;

    public static Func<string, bool> Handler;
    public static int Queries;

    public static bool Exists(string path)
    {
        Queries++;
        Trace.Steps.Add("query");
        return Handler != null ? Handler(path) : Present;
    }
}

public static class EditorUtility
{
    public static int Dialogs;
    public static string Picked;

    public static string OpenFolderPanel(string title, string path, string defaultName) => Picked;

    public static void DisplayDialog(string title, string message, string ok) => Dialogs++;
}

public static class McpLog
{
    public static int Errors;

    public static void Info(string message) => Trace.Steps.Add("info");

    public static void Error(string message) => Errors++;
}

public sealed class ProcessStartInfo
{
    public string FileName;
    public bool UseShellExecute;
}

public sealed class Process : IDisposable
{
    // Strong references deliberately expose prompt ownership rather than timing-dependent finalizers.
    public static readonly List<Process> Started = new();
    public static bool ReturnNull;
    public static bool Throw;
    public static ProcessStartInfo Last;
    public bool Disposed;

    public static Process Start(ProcessStartInfo info)
    {
        Last = info;
        if (Throw)
            throw new InvalidOperationException("synthetic-start-failure");
        if (ReturnNull)
            return null;
        var process = new Process();
        Started.Add(process);
        return process;
    }

    public void Dispose()
    {
        if (Disposed)
            throw new InvalidOperationException("double-dispose");
        Disposed = true;
    }

    public static void Reset()
    {
        Started.Clear();
        ReturnNull = Throw = false;
        Last = null;
        File.Present = true;
        EditorUtility.Dialogs = McpLog.Errors = 0;
    }
}

public static class Trace
{
    public static readonly List<string> Steps = new();
}
