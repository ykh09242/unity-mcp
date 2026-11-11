using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Setup;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.ScriptQuality
{
    [InitializeOnLoad]
    internal static class ScriptAnalysisHost
    {
        internal const string ConsentName = "script_analysis_host";
        private const string UnsettledKey = "MCPForUnity.ScriptAnalysisHost.Unsettled";
        internal static readonly AnalysisHostProcess Owner = new AnalysisHostProcess();
        internal static string ProjectRoot => Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        internal static string CacheRoot => Path.Combine(ProjectRoot, "Library", "MCPForUnity", "ScriptDiagnosticsHost");

        static ScriptAnalysisHost()
        {
            AssemblyReloadEvents.beforeAssemblyReload += Shutdown;
            EditorApplication.quitting += Shutdown;
        }

        private static void Shutdown()
        {
            int pid = Owner.ActiveProcessId;
            string identity = "";
            if (pid != 0)
            {
                try
                {
                    using var process = Process.GetProcessById(pid);
                    identity = pid + ":" + process.StartTime.ToUniversalTime().Ticks;
                }
                catch (Exception)
                {
                    identity = "unknown";
                }
            }
            SessionState.SetString(UnsettledKey, identity);
            if (Owner.Stop())
                SessionState.EraseString(UnsettledKey);
        }

        private static bool HasUnsettledProcess()
        {
            string identity = SessionState.GetString(UnsettledKey, "");
            if (identity.Length == 0)
                return false;
            string[] parts = identity.Split(':');
            if (parts.Length != 2 || !int.TryParse(parts[0], out int pid) || !long.TryParse(parts[1], out long ticks))
                return true;
            try
            {
                using var process = Process.GetProcessById(pid);
                if (!process.HasExited && process.StartTime.ToUniversalTime().Ticks == ticks)
                    return true;
            }
            catch (ArgumentException) { }
            catch (Exception)
            {
                return true;
            }
            SessionState.EraseString(UnsettledKey);
            return false;
        }

        internal static JObject Capabilities()
        {
            var consent = MCPServiceLocator.ToolConsent.GetStatus(ConsentName);
            string error = HasUnsettledProcess() ? "analysis_process_unsettled" : null;
            JObject installation = ScriptAnalysisHostInstaller.Inspect(CacheRoot, out string unavailable);
            return new JObject
            {
                ["protocol"] = 1,
                ["available"] = error == null && installation != null,
                ["reason"] = error ?? unavailable,
                ["consent"] = consent.Enabled,
                ["consent_scope"] = consent.Scope.ToString(),
                ["installation"] =
                    installation == null
                        ? null
                        : new JObject
                        {
                            ["schema"] = installation["schema"],
                            ["sdk"] = installation["sdk"],
                            ["runtime"] = installation["runtime"],
                            ["providers"] = installation["providers"],
                            ["source_signature"] = installation["source_signature"],
                            ["verified_at_unix_ms"] = installation["verified_at_unix_ms"],
                            ["runtime_evidence"] = "verified_at_explicit_setup",
                        },
                ["setup_required"] = installation == null,
                ["occupied"] = Owner.Occupied,
            };
        }

        internal static async Task<JObject> AnalyzeAsync(JObject request, CancellationToken cancellation)
        {
            string cache = CacheRoot;
            var mainThread = SynchronizationContext.Current ?? throw new InvalidOperationException("main_thread_required");
            void CheckConsent()
            {
                if (HasUnsettledProcess())
                    throw new InvalidOperationException("analysis_process_unsettled");
                if (!MCPServiceLocator.ToolConsent.GetStatus(ConsentName).Enabled)
                    throw new InvalidOperationException("analysis_consent_required");
            }
            CheckConsent();
            string sourceSignature = ScriptAnalysisHostInstaller.ExpectedSourceSignature();
            string host = await Task.Run(() => ScriptAnalysisHostInstaller.ResolveVerifiedHost(cache, sourceSignature), cancellation);
            using var payload = new LimitedMemoryStream(272L * 1024 * 1024 + 4);
            payload.Write(new byte[4], 0, 4);
            using (var writer = new StreamWriter(payload, new UTF8Encoding(false), 65536, true))
            using (var json = new JsonTextWriter(writer))
            {
                request.WriteTo(json);
                json.Flush();
                writer.Flush();
            }
            int length = checked((int)payload.Length - 4);
            payload.Position = 0;
            payload.Write(new[] { (byte)length, (byte)(length >> 8), (byte)(length >> 16), (byte)(length >> 24) }, 0, 4);
            payload.Position = 0;
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
            deadline.CancelAfter(TimeSpan.FromMinutes(5));
            byte[] response = await Owner.RunAsync("dotnet", new[] { host }, Path.GetDirectoryName(host), payload, true, deadline.Token, () =>
            {
                var admission = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                mainThread.Post(
                    _ =>
                    {
                        try
                        {
                            CheckConsent();
                            admission.SetResult(true);
                        }
                        catch (Exception e)
                        {
                            admission.SetException(e);
                        }
                    },
                    null
                );
                return admission.Task;
            });
            var result = JObject.Parse(Encoding.UTF8.GetString(response));
            if ((int?)result["protocol"] != 1 || (string)result["source_token"] != (string)request["source_token"])
                throw new InvalidDataException("analysis_response_identity_mismatch");
            result["host_source_signature"] = sourceSignature;
            result["environment_signature"] = SourceFreshness.SourceSnapshot.Hash((string)result["environment_signature"] + "\n" + sourceSignature);
            return result;
        }

        private sealed class LimitedMemoryStream : MemoryStream
        {
            private readonly long _maximum;

            internal LimitedMemoryStream(long maximum) => _maximum = maximum;

            public override void Write(byte[] buffer, int offset, int count)
            {
                if (Position + count > _maximum)
                    throw new InvalidDataException("analysis_context_limit");
                base.Write(buffer, offset, count);
            }
        }
    }
}
