using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// Only the Unity-facing DTO and unused import-bound constants are stubbed. Journal, checkpoint
// validation, publication, path containment and OS lease source are linked directly by the project.
namespace UnityEngine
{
    public static class Application
    {
        public static string dataPath;
    }
}

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    internal static class AssetDownloadTransport
    {
        internal const long MaxDownloadBytes = 8L * 1024 * 1024 * 1024;
    }
}

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    internal static class ModelArchiveSelection
    {
        internal const int MaxCandidates = 1024;
    }
}

namespace MCPForUnity.Editor.Services.AssetGen
{
    public enum AssetGenJobState
    {
        Queued,
        Running,
        Importing,
        Done,
        Failed,
        Canceled,
    }

    internal static class AssetGenJobManager
    {
        internal const int MaxTerminalJobs = 100;

        internal static bool IsAllowedResultExtension(string kind, string extension) => kind == "model" && extension == "glb";
    }

    public sealed class AssetGenJob
    {
        public string JobId,
            Kind,
            Provider,
            Action,
            Format,
            AnimationType,
            AssetPath,
            AssetGuid,
            ModelImportStage,
            ModelImportErrorCode;
        public string ArchiveModelPath,
            Error,
            RecoveryState,
            RecoveryCode,
            ProviderTaskId,
            ProviderPhase;
        public AssetGenJobState State;
        public float Progress;
        public float TargetSize = 1;
        public JObject ArchiveSelection;
    }
}

internal static class JournalHarness
{
    private static readonly string Owner = new string('a', 64);
    private const string JobId = "0123456789abcdef0123456789abcdef";
    private static readonly DateTimeOffset WrittenAt = DateTimeOffset.FromUnixTimeSeconds(1800000000);

    private static void Require(bool value, string message)
    {
        if (!value)
            throw new Exception(message);
    }

    private static string Fresh(string root, string name)
    {
        string path = Path.Combine(root, name + "_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }

    private static AssetGenJobJournal Journal(string root, DateTimeOffset at) => new AssetGenJobJournal(root, Owner, Guid.NewGuid().ToString("N"), () => at);

    private static JObject Record(string phase, string id = JobId)
    {
        var cp = new AssetGenJobCheckpoint
        {
            JobId = id,
            Kind = "model",
            Provider = "tripo",
            Phase = phase,
            Revision = 1,
            StartedAt = 1000,
            Deadline = 1600,
            OutputFolder = "Assets/Generated",
            Name = "owned",
            Extension = "glb",
            Subfolder = "Models",
            Receipt = phase == "preparing" || phase == "submitting" ? null : "owned-provider-receipt",
        };
        if (phase == "writing" || phase == "importing")
        {
            cp.SavedPath = "Assets/Generated/owned.glb";
            cp.SavedLength = 10;
            cp.SavedHash = new string('b', 64);
        }
        var job = new AssetGenJob
        {
            JobId = id,
            Kind = "model",
            Provider = "tripo",
            Action = "generate",
            Format = "glb",
            State = phase == "terminal" ? AssetGenJobState.Done : AssetGenJobState.Running,
        };
        return AssetGenJobCheckpoint.Project(job, cp);
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
        throw new Exception("Unverified ownership should have been refused");
    }

    private static Process Start(string operation, string phase, string root)
    {
        var info = new ProcessStartInfo(Environment.ProcessPath)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
        };
        if (Path.GetFileNameWithoutExtension(info.FileName).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
            info.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
        foreach (string argument in new[] { operation, phase, root })
            info.ArgumentList.Add(argument);
        return Process.Start(info);
    }

    private static void Reap(Process child)
    {
        if (!child.HasExited)
        {
            child.Kill(true);
            Require(child.WaitForExit(30000), "Owned child did not exit");
        }
    }

    private static int Child(string operation, string phase, string root)
    {
        using var journal = Journal(root, operation == "verify" ? WrittenAt.AddSeconds(20) : WrittenAt);
        if (operation == "prepare")
        {
            journal.Save(Record(phase), new[] { JobId }, 1000, true);
            File.WriteAllText(Path.Combine(root, "ready"), phase);
            Thread.Sleep(Timeout.Infinite);
            return 9;
        }
        if (operation == "contend")
        {
            Refused(() => journal.Read(5));
            return 0;
        }
        var rows = journal.Read(5);
        Require(rows.Count == 1 && (string)rows[0]["JobId"] == JobId, "Restart lost recorded work");
        var cp = AssetGenJobCheckpoint.Read(rows[0], JobId, 5);
        Require(cp.Phase == phase && cp.StartedAt == 5 && cp.Deadline == 585, "Restart changed intent or extended execution budget");
        return 0;
    }

    private static void Crash(string root, string phase)
    {
        string path = Fresh(root, phase);
        using (var child = Start("prepare", phase, path))
        {
            try
            {
                var elapsed = Stopwatch.StartNew();
                while (!child.HasExited && !File.Exists(Path.Combine(path, "ready")) && elapsed.Elapsed < TimeSpan.FromMinutes(2))
                    Thread.Sleep(10);
                Require(!child.HasExited && File.Exists(Path.Combine(path, "ready")), "Writer did not reach checkpoint");
                using var competitor = Start("contend", phase, path);
                try
                {
                    Require(competitor.WaitForExit(120000) && competitor.ExitCode == 0, "Another process obtained live ownership");
                }
                finally
                {
                    Reap(competitor);
                }
                Reap(child);
            }
            finally
            {
                Reap(child);
            }
        }
        using var reader = Start("verify", phase, path);
        try
        {
            Require(reader.WaitForExit(120000) && reader.ExitCode == 0, "Fresh-process recovery failed");
        }
        finally
        {
            Reap(reader);
        }
    }

    public static int Main(string[] args)
    {
        if (args.Length == 3)
            return Child(args[0], args[1], args[2]);
        string root = Path.GetFullPath(args[0]);
        Directory.CreateDirectory(root);
        UnityEngine.Application.dataPath = Path.Combine(root, "Assets");
        int passed = 0;
        foreach (string phase in new[] { "preparing", "submitting", "polling", "downloading", "writing", "importing" })
        {
            Crash(root, phase);
            passed++;
            Console.WriteLine("PASS process ownership, kill and restart: " + phase);
        }
        string foreign = Fresh(root, "foreign");
        using (var original = Journal(foreign, WrittenAt))
            original.Save(Record("polling"), new[] { JobId }, 1000, true);
        using (var other = new AssetGenJobJournal(foreign, new string('f', 64), Guid.NewGuid().ToString("N"), () => WrittenAt))
            Refused(() => other.Read(5));
        passed++;
        Console.WriteLine("PASS foreign owner cannot restore or replace index");
        string replacement = Fresh(root, "replacement");
        using (var journal = Journal(replacement, WrittenAt))
        {
            journal.Save(Record("polling"), new[] { JobId }, 1000, true);
            string path = Path.Combine(replacement, JobId + ".json");
            File.WriteAllText(path, "external evidence");
            Refused(() => journal.Save(Record("polling"), new[] { JobId }, 1001, true));
            Require(File.ReadAllText(path) == "external evidence", "Displaced evidence was overwritten");
        }
        passed++;
        Console.WriteLine("PASS unexpected replacement preserved");
        string retention = Fresh(root, "retention");
        using (var journal = Journal(retention, WrittenAt))
        {
            var ids = new List<string>();
            for (int i = 0; i < 105; i++)
            {
                string id = Guid.NewGuid().ToString("N");
                ids.Add(id);
                if (ids.Count > 100)
                    ids.RemoveAt(0);
                journal.Save(Record("terminal", id), ids, 1000, true);
            }
            Require(Directory.GetFiles(retention, "*.json").Length == 101, "Terminal metadata was not bounded");
        }
        passed++;
        Console.WriteLine("PASS existing terminal retention bound prunes only confirmed records");
        Console.WriteLine("Generation journal: " + passed + "/9 passed on " + System.Runtime.InteropServices.RuntimeInformation.OSDescription);
        return 0;
    }
}
