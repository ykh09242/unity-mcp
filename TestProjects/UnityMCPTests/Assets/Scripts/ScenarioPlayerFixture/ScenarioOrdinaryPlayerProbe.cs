using System;
using System.IO;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ScenarioOrdinaryPlayerProbe : MonoBehaviour
    {
        private sealed class ScalarProvider : IPlayScenarioStateProvider
        {
            public bool TryRead(out PlayScenarioStateValue value)
            {
                value = PlayScenarioStateValue.FromBoolean(true);
                return true;
            }
        }

        [Serializable]
        private sealed class Witness
        {
            public int schema_version = 1;
            public string session_id;
            public int process_id;
            public long completed_unix_ms;
            public bool runner_absent = true;
            public bool input_backend_absent = true;
            public bool tracker_disabled = true;
            public bool registration_disposal_verified = true;
        }

        private static string Argument(string[] args, string name)
        {
            int first = Array.IndexOf(args, name);
            if (first < 0)
                return null;
            if (first + 1 >= args.Length || Array.LastIndexOf(args, name) != first)
                throw new ArgumentException("Provide exactly one " + name + " value.");
            return args[first + 1];
        }

        private void Start()
        {
#if !UNITY_EDITOR && !MCP_FOR_UNITY_PLAY_SCENARIOS
            for (int index = 0; index < PlayScenarioResourceTracker.RegistrationLimit + 1; index++)
            {
                PlayScenarioResourceTracker.RegisterHandle().Dispose();
                PlayScenarioResourceTracker.RegisterSubscription().Dispose();
            }
            if (
                typeof(PlayScenarioPlayerHost).Assembly.GetType("MCPForUnity.Runtime.PlayScenarios.PlayScenarioPlayerRunner") != null
                || PlayScenarioPlayerInput.Backend != null
                || string.IsNullOrEmpty(PlayScenarioResourceTracker.Capture().Error)
            )
                throw new InvalidOperationException("Ordinary Player activated scenario tracking or bootstrap.");
            var provider = new ScalarProvider();
            using (PlayScenarioStateRegistry.Register("ordinary-probe", provider)) { }
            using (PlayScenarioStateRegistry.Register("ordinary-probe", provider)) { }
            string[] args = Environment.GetCommandLineArgs();
            string witnessPath = Argument(args, "--mcp-fixture-witness");
            string session = Argument(args, "--mcp-fixture-session");
            if (witnessPath != null || session != null)
            {
                if (session == null || session.Length != 32 || !System.Text.RegularExpressions.Regex.IsMatch(session, "^[0-9a-f]{32}$") || witnessPath == null)
                    throw new ArgumentException("Ordinary fixture witness requires an absolute path and lowercase 32-hex session identity.");
                witnessPath = PlayScenarioPlayerFiles.CheckedAbsolute(witnessPath);
                if (PlayScenarioPlayerFiles.IsWithin(Path.GetDirectoryName(Application.dataPath), witnessPath))
                    throw new ArgumentException("Ordinary fixture witness must be outside the immutable build.");
                int processId;
                using (var process = System.Diagnostics.Process.GetCurrentProcess())
                    processId = process.Id;
                var witness = new Witness
                {
                    session_id = session,
                    process_id = processId,
                    completed_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                };
                PlayScenarioPlayerFiles.WriteNew(witnessPath, JsonUtility.ToJson(witness), 4096);
            }
            Debug.Log("ORDINARY_PLAYER_BOUNDARY_VERIFIED");
            Application.Quit(0);
#endif
        }
    }
}
