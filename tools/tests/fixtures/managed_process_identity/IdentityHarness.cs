using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services.Server;

namespace UnityEngine
{
    internal enum RuntimePlatform
    {
        WindowsEditor,
    }

    internal static class Application
    {
        internal static RuntimePlatform platform => RuntimePlatform.WindowsEditor;
        internal static string dataPath => Environment.CurrentDirectory;
    }
}

namespace UnityEditor
{
    internal static class EditorPrefs
    {
        private static readonly Dictionary<string, object> Values = new();

        internal static void SetString(string key, string value) => Values[key] = value;

        internal static void SetInt(string key, int value) => Values[key] = value;

        internal static string GetString(string key, string fallback) => Values.TryGetValue(key, out object value) ? (string)value : fallback;

        internal static int GetInt(string key, int fallback) => Values.TryGetValue(key, out object value) ? (int)value : fallback;

        internal static void DeleteKey(string key) => Values.Remove(key);
    }
}

internal sealed class RecoveryBoundary
{
    private readonly IPidFileManager _pidFileManager;
    private readonly IProcessDetector _processDetector = new Detector();
    private Process _lastLaunchedProcess;

    internal RecoveryBoundary(PidFileManager pids) => _pidFileManager = pids;

    internal bool Prepare() => TryPreparePreviousManagedLaunch(out _);

    internal bool LauncherExited(string path, string token) => IsOwnedLauncherExitConfirmed(path, token);

    private bool TryGetLocalHttpServerHandshake(out string path, out string token) => _pidFileManager.TryGetHandshake(out path, out token);

    private bool TryGetPortFromPidFilePath(string path, out int port) => _pidFileManager.TryGetPortFromPidFilePath(path, out port);

    private bool TryReadPidFromPidFile(string path, out int pid) => _pidFileManager.TryReadPid(path, out pid);

    private void DeletePidFile(string path) => _pidFileManager.DeletePidFile(path);

    private void ClearLocalServerPidTracking() => _pidFileManager.ClearTracking();

    // PRODUCTION_RECOVERY

    private sealed class Detector : IProcessDetector
    {
        public bool ProcessExists(int pid) => true;

        public bool LooksLikeMcpServerProcess(int pid) => false;

        public bool TryGetProcessCommandLine(int pid, out string args)
        {
            args = null;
            return false;
        }

        public List<int> GetListeningProcessIdsForPort(int port) => new();

        public int GetCurrentProcessId() => -1;

        public string NormalizeForMatch(string input) => input;
    }
}

internal static class IdentityHarness
{
    private static void Main(string[] args)
    {
        const int port = 59981;
        const string token = "owned-identity-token";
        var pids = new PidFileManager(args[0]);
        string path = pids.GetPidFilePath(port);
        pids.StoreHandshake(path, token);
        using var process = Process.GetCurrentProcess();
        int pid = args[2] == "unknown" ? int.MaxValue : process.Id;
        if (args[1] == "launcher")
            pids.StoreOwnedLaunchProcess(pid, port, token);
        else
            File.WriteAllText(path, pid.ToString(CultureInfo.InvariantCulture));
        if (args[2] != "legacy")
        {
            long ticks = process.StartTime.ToUniversalTime().Ticks;
            if (args[2] == "reused")
                ticks -= TimeSpan.FromDays(1).Ticks;
            string key = args[1] == "launcher" ? EditorPrefKeys.LastLocalHttpServerLauncherCreation : EditorPrefKeys.LastLocalHttpServerCreation;
            string identity = string.Join(
                ":",
                pid.ToString(CultureInfo.InvariantCulture),
                port.ToString(CultureInfo.InvariantCulture),
                pids.ComputeShortHash("managed-launch:" + token),
                ticks.ToString(CultureInfo.InvariantCulture)
            );
            UnityEditor.EditorPrefs.SetString(pids.PreferenceKey(key), identity);
        }
        var recovered = new RecoveryBoundary(new PidFileManager(args[0]));
        bool expected = args[2] == "reused";
        if (args[1] == "launcher" && recovered.LauncherExited(path, token) != expected)
            throw new Exception("Launcher recovery must distinguish a proven new creation from current or unknown identity.");
        if (recovered.Prepare() != expected)
            throw new Exception("Startup must retire a proven reused PID while preserving current, unknown and legacy identities.");
        bool retained = pids.TryGetHandshake(out _, out _);
        if (retained == expected)
            throw new Exception("Only proven exit may retire the managed launch identity.");
        if (process.HasExited)
            throw new Exception("The unrelated live process must remain alive.");
        Console.WriteLine("PASS " + args[1] + " " + args[2]);
    }
}
