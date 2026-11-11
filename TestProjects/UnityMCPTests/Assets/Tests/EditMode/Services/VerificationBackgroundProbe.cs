using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.ScriptQuality;
using MCPForUnity.Editor.Services.SourceFreshness;
using MCPForUnity.Editor.Setup;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Services
{
    // A file bridge for this owned GUI acceptance fixture, never a product transport.
    [InitializeOnLoad]
    public static class VerificationBackgroundProbe
    {
        private const string Key = "MCPForUnity.VerificationQA.";
        private static readonly string Domain = Guid.NewGuid().ToString("N");
        private static bool busy;
        private static string Root => Path.GetFullPath(Application.dataPath + "/..");
        private static string DirectoryPath =>
            Path.Combine(Root, "Library", "MCPVerificationQA", Environment.GetEnvironmentVariable("UNITY_MCP_VERIFICATION_QA_ID"));
        private static bool Owned =>
            string.Equals(Environment.GetEnvironmentVariable("UNITY_MCP_VERIFICATION_QA_PROJECT"), Root, StringComparison.Ordinal)
            && Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_VERIFICATION_QA_ID"), "N", out _);

        static VerificationBackgroundProbe()
        {
            if (Owned)
                EditorApplication.update += Tick;
        }

        public static void Begin()
        {
            if (!Owned || Application.isBatchMode)
                throw new InvalidOperationException("An explicitly owned GUI fixture is required.");
            Directory.CreateDirectory(DirectoryPath);
            Write("ready.json", new JObject { ["pid"] = Process.GetCurrentProcess().Id, ["domain"] = Domain });
        }

        private static void Write(string file, JObject result)
        {
            string path = Path.Combine(DirectoryPath, file);
            if (File.Exists(path))
                return; // Immutable per-request receipt; a read race cannot replace a prior reply.
            string temporary = path + ".tmp";
            File.WriteAllText(temporary, result.ToString());
            File.Move(temporary, path);
        }

        private static void Reply(string id, object result)
        {
            Write(id + ".json", JObject.FromObject(result));
            SessionState.SetString(Key + "completed", id);
            SessionState.EraseString(Key + "phase");
        }

        private static async void Tick()
        {
            if (!Owned || busy || EditorApplication.isCompiling || EditorApplication.isUpdating || !Directory.Exists(DirectoryPath))
                return;
            string id = SessionState.GetString(Key + "started", "");
            string phase = SessionState.GetString(Key + "phase", "");
            busy = true;
            try
            {
                if (phase == "bootstrap" && SessionState.GetString(Key + "domain", "") != Domain)
                {
                    var observed = await SourceFreshnessService.ObserveAsync(CancellationToken.None);
                    if ((string)observed["status"] == "pending")
                        return;
                    Reply(
                        id,
                        new
                        {
                            success = true,
                            source_freshness = observed,
                            pid = Process.GetCurrentProcess().Id,
                        }
                    );
                    return;
                }
                if (phase == "host_reload" && SessionState.GetString(Key + "domain", "") != Domain)
                {
                    bool exited = true;
                    int pid = SessionState.GetInt(Key + "host_pid", 0);
                    long ticks = long.Parse(SessionState.GetString(Key + "host_start", "0"));
                    try
                    {
                        using var process = Process.GetProcessById(pid);
                        exited = process.HasExited || process.StartTime.ToUniversalTime().Ticks != ticks;
                    }
                    catch (ArgumentException) { }
                    Reply(
                        id,
                        new
                        {
                            success = true,
                            old_process_exited = exited,
                            occupied = ScriptAnalysisHost.Owner.Occupied,
                            domain_changed = true,
                            host_pid = pid,
                            host_start_ticks = ticks,
                            capabilities = ScriptAnalysisHost.Capabilities(),
                        }
                    );
                    return;
                }
                if (phase.Length != 0)
                    return;
                string requestPath = Path.Combine(DirectoryPath, "request.json");
                if (!File.Exists(requestPath) || new FileInfo(requestPath).Length > 256 * 1024)
                    return;
                var request = JObject.Parse(File.ReadAllText(requestPath));
                string next = (string)request["id"];
                if (!Guid.TryParseExact(next, "N", out _) || next == SessionState.GetString(Key + "started", ""))
                    return;
                id = next;
                SessionState.SetString(Key + "started", id); // A lost response never replays dispatch.
                string command = (string)request["command"];
                var parameters = request["parameters"] as JObject ?? new JObject();
                if (command == "bootstrap")
                {
                    await ScriptAnalysisHostInstaller.InstallAsync(CancellationToken.None);
                    var grant = MCPServiceLocator.ToolConsent.Grant(ScriptAnalysisHost.ConsentName, ToolConsentScope.EditorSession);
                    if (!grant.Succeeded)
                        throw new InvalidOperationException(grant.ErrorCode);
                    SessionState.SetString(Key + "phase", "bootstrap");
                    SessionState.SetString(Key + "domain", Domain);
                    await RefreshUnity.HandleCommand(
                        new JObject
                        {
                            ["mode"] = "force",
                            ["scope"] = "scripts",
                            ["compile"] = "request",
                            ["force_recompile"] = true,
                            ["compile_id"] = Guid.NewGuid().ToString("N"),
                            ["wait_for_ready"] = false,
                        }
                    );
                    return;
                }
                if (command == "host_reload")
                {
                    string host = ScriptAnalysisHostInstaller.ResolveVerifiedHost(
                        ScriptAnalysisHost.CacheRoot,
                        ScriptAnalysisHostInstaller.ExpectedSourceSignature()
                    );
                    _ = ScriptAnalysisHost.Owner.RunAsync(
                        "dotnet",
                        new[] { host },
                        Path.GetDirectoryName(host),
                        new WaitingInput(),
                        true,
                        CancellationToken.None
                    );
                    double until = EditorApplication.timeSinceStartup + 10;
                    while (ScriptAnalysisHost.Owner.ActiveProcessId == 0 && EditorApplication.timeSinceStartup < until)
                        await Task.Delay(25);
                    int pid = ScriptAnalysisHost.Owner.ActiveProcessId;
                    using var process = Process.GetProcessById(pid);
                    SessionState.SetInt(Key + "host_pid", pid);
                    SessionState.SetString(Key + "host_start", process.StartTime.ToUniversalTime().Ticks.ToString());
                    SessionState.SetString(Key + "domain", Domain);
                    SessionState.SetString(Key + "phase", "host_reload");
                    EditorUtility.RequestScriptReload();
                    return;
                }
                if (command == "quit")
                {
                    Reply(id, new { success = true });
                    EditorApplication.Exit(0);
                    return;
                }
                object result = command switch
                {
                    "get_compile_job" => await GetCompileJob.HandleCommand(parameters),
                    "refresh_unity" => await RefreshUnity.HandleCommand(parameters),
                    "manage_script_quality" => ManageScriptQuality.HandleCommand(parameters),
                    "manage_verification" => ManageVerification.HandleCommand(parameters),
                    "run_tests" => await RunTests.HandleCommand(parameters),
                    "get_test_job" => GetTestJob.HandleCommand(parameters),
                    "cancel_test_job" => CancelTestJob.HandleCommand(parameters),
                    "tail_console" => TailConsole.HandleCommand(parameters),
                    _ => throw new InvalidOperationException("Unknown owned fixture command."),
                };
                Reply(id, result);
            }
            catch (Exception error)
            {
                if (Guid.TryParseExact(id, "N", out _))
                    Reply(
                        id,
                        new
                        {
                            success = false,
                            error = error.GetType().Name,
                            message = error.Message,
                        }
                    );
            }
            finally
            {
                busy = false;
            }
        }

        private sealed class WaitingInput : Stream
        {
            public override bool CanRead => true;
            public override bool CanSeek => false;
            public override bool CanWrite => false;
            public override long Length => throw new NotSupportedException();
            public override long Position
            {
                get => throw new NotSupportedException();
                set => throw new NotSupportedException();
            }

            public override void Flush() { }

            public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();

            public override async Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
            {
                await Task.Delay(Timeout.Infinite, cancellationToken).ConfigureAwait(false);
                return 0;
            }

            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        }
    }
}
