using System;
using System.IO;
using System.Threading;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.SourceFreshness;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Services
{
    // Only the explicitly launched owned GUI fixture enables this observer.
    [InitializeOnLoad]
    public static class SourceFreshnessBackgroundProbe
    {
        private const string PhaseKey = "MCPForUnity.FreshnessQA.Phase";
        private const string CompileKey = "MCPForUnity.FreshnessQA.CompileId";
        private static bool _running;
        private static string Root => Path.GetFullPath(Application.dataPath + "/..");
        private static string ProbeDirectory =>
            Path.Combine(Root, "Library", "MCPFreshnessQA", Environment.GetEnvironmentVariable("UNITY_MCP_FRESHNESS_QA_ID"));
        private static string StatePath => Path.Combine(ProbeDirectory, "state.json");
        private static bool Owned =>
            string.Equals(Environment.GetEnvironmentVariable("UNITY_MCP_FRESHNESS_QA_PROJECT"), Root, StringComparison.Ordinal)
            && Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_FRESHNESS_QA_ID"), "N", out _);

        static SourceFreshnessBackgroundProbe()
        {
            if (Owned)
                EditorApplication.update += Tick;
        }

        public static void Begin()
        {
            if (!Owned || Application.isBatchMode)
                throw new InvalidOperationException("A separately owned GUI Editor is required.");
            Directory.CreateDirectory(ProbeDirectory);
            SessionState.SetString(PhaseKey, "await_unfocused");
        }

        private static JObject Sample() =>
            new JObject
            {
                ["value"] = TestNamespace.SourceFreshnessProbeValue.Read(),
                ["focused"] = UnityEditorInternal.InternalEditorUtility.isApplicationActive,
                ["batch_mode"] = Application.isBatchMode,
                ["pid"] = System.Diagnostics.Process.GetCurrentProcess().Id,
            };

        private static void Write(string phase, JObject sample = null)
        {
            var state = File.Exists(StatePath) ? JObject.Parse(File.ReadAllText(StatePath)) : new JObject();
            state[phase] = sample ?? Sample();
            string pending = StatePath + ".tmp";
            File.WriteAllText(pending, state.ToString());
            if (File.Exists(StatePath))
                File.Delete(StatePath);
            File.Move(pending, StatePath);
        }

        private static async void Tick()
        {
            if (!Owned || _running || EditorApplication.isCompiling || EditorApplication.isUpdating)
                return;
            string phase = SessionState.GetString(PhaseKey, "");
            if (phase == "await_unfocused")
            {
                if (UnityEditorInternal.InternalEditorUtility.isApplicationActive)
                    return;
                SessionState.SetString(PhaseKey, "await_write");
                Write("before");
                return;
            }
            if (phase == "await_write" && !File.Exists(Path.Combine(ProbeDirectory, "written")))
                return;
            if (phase != "await_write" && phase != "await_reload")
                return;
            _running = true;
            try
            {
                if (phase == "await_write")
                {
                    Write("after_external_write");
                    string id = Guid.NewGuid().ToString("N");
                    SessionState.SetString(CompileKey, id);
                    SessionState.SetString(PhaseKey, "await_reload");
                    var result = await RefreshUnity.HandleCommand(
                        new JObject
                        {
                            ["mode"] = "force",
                            ["scope"] = "scripts",
                            ["compile"] = "request",
                            ["force_recompile"] = true,
                            ["compile_id"] = id,
                            ["wait_for_ready"] = false,
                        }
                    );
                    var response = JObject.FromObject(result);
                    if ((bool?)response["success"] != true)
                        throw new InvalidOperationException(response.ToString());
                }
                else
                {
                    using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(45));
                    var evidence = await SourceFreshnessService.ObserveAsync(deadline.Token);
                    if ((string)evidence["status"] == "pending")
                    {
                        Write("pending", new JObject { ["reason"] = evidence["reason"], ["value"] = TestNamespace.SourceFreshnessProbeValue.Read() });
                        return;
                    }
                    var after = Sample();
                    after["source_freshness"] = evidence;
                    Write("after", after);
                    SessionState.EraseString(PhaseKey);
                    EditorApplication.Exit((string)evidence["status"] == "observed_current" ? 0 : 2);
                }
            }
            catch (Exception error)
            {
                Write("error", new JObject { ["type"] = error.GetType().Name, ["message"] = error.Message });
                SessionState.EraseString(PhaseKey);
                EditorApplication.Exit(3);
            }
            finally
            {
                _running = false;
            }
        }
    }
}
