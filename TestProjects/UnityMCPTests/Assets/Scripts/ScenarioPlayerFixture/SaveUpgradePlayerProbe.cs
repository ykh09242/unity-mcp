using System;
using System.IO;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    /// <summary>Test-game save writer, migrator and disk-backed Player observation.</summary>
    public sealed class SaveUpgradePlayerProbe : MonoBehaviour, IPlayScenarioStateProvider
    {
        public const string StateId = "verified-save-upgrade.ready";
        private const int SaveLimit = 8192;
        private const string SaveEnvironment = "UNITY_MCP_VERIFICATION_SAVE_DIRECTORY";

        public enum Variant
        {
            Previous,
            Candidate,
            MigrationFailure,
            DuplicateOnRestart,
        }

        public Variant Version;
        private IDisposable registration;
        private bool completed;

        private void Awake()
        {
            string directory = Environment.GetEnvironmentVariable(SaveEnvironment);
            string[] args = Environment.GetCommandLineArgs();
            int requestIndex = Array.IndexOf(args, "--mcp-scenario-request");
            if (string.IsNullOrEmpty(directory) || requestIndex < 0 || requestIndex + 1 >= args.Length)
                return;
            string request = PlayScenarioPlayerFiles.CheckedAbsolute(args[requestIndex + 1]);
            string evidenceRoot = new FileInfo(request).Directory?.Parent?.Parent?.FullName;
            if (evidenceRoot == null || !File.Exists(request))
                throw new IOException("Save fixture requires the owned scenario run directory.");
            string expected = Path.Combine(evidenceRoot, "storage");
            StringComparison comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (!string.Equals(expected, directory, comparison) || !Directory.Exists(directory))
                throw new IOException("Save fixture requires the new slot beside its run directories.");
            string path = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, "progress.json"));
            registration = PlayScenarioStateRegistry.Register(StateId, this);
            string outcome = "failed";
            string failure = null;
            try
            {
                outcome = Version == Variant.Previous ? SavePrevious(path) : OpenCandidate(path);
            }
            catch (Exception error) when (error is IOException || error is JsonException)
            {
                failure = error.GetType().Name;
            }

            JObject saved = File.Exists(path) ? ReadSave(path) : new JObject();
            var observation = new JObject
            {
                ["outcome"] = outcome,
                ["migration_error"] = failure,
                ["schema"] = saved["schema"],
                ["profile"] = saved["profile"],
                ["coins"] = saved["coins"],
                ["migration_count"] = (saved["history"] as JArray)?.Count ?? 0,
            };
            Debug.Log("SAVE_UPGRADE " + observation.ToString(Formatting.None));
            completed = failure == null;
        }

        private static string SavePrevious(string path)
        {
            var save = new JObject
            {
                ["schema"] = 1,
                ["profile"] = "fixture-profile",
                ["coins"] = 37,
            };
            PlayScenarioPlayerFiles.WriteNew(path, save.ToString(Formatting.None), SaveLimit);
            return "written";
        }

        private string OpenCandidate(string path)
        {
            JObject save = ReadSave(path);
            int schema = save.Value<int>("schema");
            if (schema == 1)
            {
                if (Version == Variant.MigrationFailure)
                    throw new IOException("Fixture rejected the legacy save during migration.");
                save["schema"] = 2;
                save["history"] = new JArray("v1-to-v2");
                PlayScenarioPlayerFiles.ReplaceExisting(path, save.ToString(Formatting.None), SaveLimit);
                return "migrated";
            }
            if (schema != 2 || save["history"] is not JArray history)
                throw new IOException("Fixture save schema is unsupported.");
            if (Version == Variant.DuplicateOnRestart)
            {
                history.Add("v1-to-v2");
                PlayScenarioPlayerFiles.ReplaceExisting(path, save.ToString(Formatting.None), SaveLimit);
            }
            return "reopened";
        }

        private static JObject ReadSave(string path) => JObject.Parse(PlayScenarioPlayerFiles.Read(path, SaveLimit));

        public bool TryRead(out PlayScenarioStateValue value)
        {
            value = PlayScenarioStateValue.FromBoolean(completed);
            return true;
        }

        private void OnDestroy() => registration?.Dispose();
    }
}
