using System;
using UnityEditor;

namespace MCPForUnityTests.PlayScenarios.Player
{
    /// <summary>Applies explicit reload selection before the command-line PlayMode runner starts.</summary>
    [InitializeOnLoad]
    public static class PlayModeEvidenceSettings
    {
        private const string Snapshot = "MCP.PlayModeEvidence.Settings";
        private const string Enabled = Snapshot + ".Enabled";
        private const string Options = Snapshot + ".Options";
        private static readonly string DomainInstance = Guid.NewGuid().ToString("N");

        static PlayModeEvidenceSettings()
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "--mcp-playmode-reload");
            if (index < 0)
                return;
            if (index + 1 >= args.Length || Array.LastIndexOf(args, "--mcp-playmode-reload") != index || (args[index + 1] != "on" && args[index + 1] != "off"))
                throw new ArgumentException("Provide exactly one --mcp-playmode-reload on|off.");
            if (!SessionState.GetBool(Snapshot, false))
            {
                SessionState.SetBool(Enabled, EditorSettings.enterPlayModeOptionsEnabled);
                SessionState.SetInt(Options, (int)EditorSettings.enterPlayModeOptions);
                SessionState.SetBool(Snapshot, true);
            }
            EditorSettings.enterPlayModeOptionsEnabled = true;
            // Scene reload is retained: only domain reload varies between the two executions.
            EditorSettings.enterPlayModeOptions = args[index + 1] == "off" ? EnterPlayModeOptions.DisableDomainReload : EnterPlayModeOptions.None;
            EditorApplication.quitting += Restore;
            EditorApplication.playModeStateChanged += ObserveDomain;
        }

        private static void ObserveDomain(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.ExitingEditMode)
                SessionState.SetString("MCP.PlayModeEvidence.BeforeDomain", DomainInstance);
            if (state == PlayModeStateChange.EnteredPlayMode)
                SessionState.SetString("MCP.PlayModeEvidence.AfterDomain", DomainInstance);
        }

        private static void Restore()
        {
            if (!SessionState.GetBool(Snapshot, false))
                return;
            EditorSettings.enterPlayModeOptionsEnabled = SessionState.GetBool(Enabled, false);
            EditorSettings.enterPlayModeOptions = (EnterPlayModeOptions)SessionState.GetInt(Options, 0);
            SessionState.EraseBool(Snapshot);
        }
    }
}
