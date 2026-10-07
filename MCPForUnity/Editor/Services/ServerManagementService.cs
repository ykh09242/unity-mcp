using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Sockets;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Server;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Service for managing MCP server lifecycle
    /// </summary>
    public class ServerManagementService : IServerManagementService
    {
        private readonly IProcessDetector _processDetector;
        private readonly IPidFileManager _pidFileManager;
        private readonly IServerCommandBuilder _commandBuilder;
        private readonly ITerminalLauncher _terminalLauncher;
        private readonly Func<Uri, string, (bool Accepted, string Error)> _shutdownRequester;

        /// <summary>Actionable explanation of the last refused or failed stop. Never contains credentials.</summary>
        public string LastStopFailure { get; private set; }

        private System.Diagnostics.Process _lastLaunchedProcess;

        /// <summary>
        /// Creates a new ServerManagementService with default dependencies.
        /// </summary>
        public ServerManagementService()
            : this(null, null, null, null, null) { }

        /// <summary>
        /// Creates a new ServerManagementService with injected dependencies (for testing).
        /// </summary>
        /// <param name="processDetector">Process detector implementation (null for default)</param>
        /// <param name="pidFileManager">PID file manager implementation (null for default)</param>
        /// <param name="processTerminator">Retained for constructor compatibility; stop never terminates processes</param>
        /// <param name="commandBuilder">Server command builder implementation (null for default)</param>
        /// <param name="terminalLauncher">Terminal launcher implementation (null for default)</param>
        /// <param name="shutdownRequester">Authenticated atomic shutdown request (null for default; failures leave the server running)</param>
        public ServerManagementService(
            IProcessDetector processDetector,
            IPidFileManager pidFileManager = null,
            IProcessTerminator processTerminator = null,
            IServerCommandBuilder commandBuilder = null,
            ITerminalLauncher terminalLauncher = null,
            Func<Uri, string, (bool Accepted, string Error)> shutdownRequester = null
        )
        {
            _processDetector = processDetector ?? new ProcessDetector();
            _pidFileManager = pidFileManager ?? new PidFileManager();
            _commandBuilder = commandBuilder ?? new ServerCommandBuilder();
            _terminalLauncher = terminalLauncher ?? new TerminalLauncher();
            _shutdownRequester = shutdownRequester ?? RequestAtomicShutdown;
        }

        private string QuoteIfNeeded(string s)
        {
            return _commandBuilder.QuoteIfNeeded(s);
        }

        private string NormalizeForMatch(string s)
        {
            return _processDetector.NormalizeForMatch(s);
        }

        private void ClearLocalServerPidTracking()
        {
            _pidFileManager.ClearTracking();
        }

        private void StoreLocalHttpServerHandshake(string pidFilePath, string instanceToken)
        {
            _pidFileManager.StoreHandshake(pidFilePath, instanceToken);
        }

        private bool TryGetLocalHttpServerHandshake(out string pidFilePath, out string instanceToken)
        {
            return _pidFileManager.TryGetHandshake(out pidFilePath, out instanceToken);
        }

        private string GetLocalHttpServerPidFilePath(int port)
        {
            return _pidFileManager.GetPidFilePath(port);
        }

        private bool TryReadPidFromPidFile(string pidFilePath, out int pid)
        {
            return _pidFileManager.TryReadPid(pidFilePath, out pid);
        }

        private bool TryProcessCommandLineContainsInstanceToken(int pid, string instanceToken, out bool containsToken)
        {
            containsToken = false;
            if (pid <= 1 || string.IsNullOrWhiteSpace(instanceToken))
                return false;
            if (!_processDetector.TryGetProcessCommandLine(pid, out string args))
                return false;
            string expected = NormalizeForMatch("--unity-instance-token " + instanceToken);
            string normalized = NormalizeForMatch(args);
            int index = normalized.IndexOf(expected, StringComparison.Ordinal);
            int end = index + expected.Length;
            containsToken =
                index >= 0
                && (
                    end == normalized.Length
                    || normalized.Substring(end).StartsWith("--", StringComparison.Ordinal)
                    || normalized[end] == '"'
                    || normalized[end] == '\''
                );
            return true;
        }

        private string ComputeShortHash(string input)
        {
            return _pidFileManager.ComputeShortHash(input);
        }

        private bool TryGetStoredLocalServerPid(int expectedPort, out int pid)
        {
            return _pidFileManager.TryGetStoredPid(expectedPort, out pid);
        }

        private string GetStoredArgsHash()
        {
            return _pidFileManager.GetStoredArgsHash();
        }

        /// <summary>
        /// Clear the local uvx cache for the MCP server package
        /// </summary>
        /// <returns>True if successful, false otherwise</returns>
        public bool ClearUvxCache()
        {
            try
            {
                string uvxPath = MCPServiceLocator.Paths.GetUvxPath();
                string uvCommand = BuildUvPathFromUvx(uvxPath);

                // Get the package name
                string packageName = "mcp-for-unity";

                // Run uvx cache clean command
                string args = $"cache clean {packageName}";

                bool success;
                string stdout;
                string stderr;

                success = ExecuteUvCommand(uvCommand, args, out stdout, out stderr);

                if (success)
                {
                    McpLog.Info($"uv cache cleared successfully: {stdout}");
                    return true;
                }
                string combinedOutput = string.Join(
                    Environment.NewLine,
                    new[] { stderr, stdout }.Where(s => !string.IsNullOrWhiteSpace(s)).Select(s => s.Trim())
                );

                string lockHint =
                    (!string.IsNullOrEmpty(combinedOutput) && combinedOutput.IndexOf("currently in-use", StringComparison.OrdinalIgnoreCase) >= 0)
                        ? "Another uv process may be holding the cache lock; wait a moment and try again or clear with '--force' from a terminal."
                        : string.Empty;

                if (string.IsNullOrEmpty(combinedOutput))
                {
                    combinedOutput = "Command failed with no output. Ensure uv is installed, on PATH, or set an override in Advanced Settings.";
                }

                McpLog.Error(
                    $"Failed to clear uv cache using '{uvCommand} {args}'. "
                        + $"Details: {combinedOutput}{(string.IsNullOrEmpty(lockHint) ? string.Empty : " Hint: " + lockHint)}"
                );
                return false;
            }
            catch (Exception ex)
            {
                McpLog.Error($"Error clearing uv cache: {ex.Message}");
                return false;
            }
        }

        private bool ExecuteUvCommand(string uvCommand, string args, out string stdout, out string stderr)
        {
            stdout = null;
            stderr = null;

            string uvxPath = MCPServiceLocator.Paths.GetUvxPath();
            string uvPath = BuildUvPathFromUvx(uvxPath);

            if (!string.Equals(uvCommand, uvPath, StringComparison.OrdinalIgnoreCase))
            {
                return ExecPath.TryRun(uvCommand, args, Application.dataPath, out stdout, out stderr, 30000);
            }

            string command = $"{uvPath} {args}";
            string extraPathPrepend = GetPlatformSpecificPathPrepend();

            if (Application.platform == RuntimePlatform.WindowsEditor)
            {
                return ExecPath.TryRun("cmd.exe", $"/c {command}", Application.dataPath, out stdout, out stderr, 30000, extraPathPrepend);
            }

            string shell = File.Exists("/bin/bash") ? "/bin/bash" : "/bin/sh";

            if (!string.IsNullOrEmpty(shell) && File.Exists(shell))
            {
                string escaped = command.Replace("\"", "\\\"");
                return ExecPath.TryRun(shell, $"-lc \"{escaped}\"", Application.dataPath, out stdout, out stderr, 30000, extraPathPrepend);
            }

            return ExecPath.TryRun(uvPath, args, Application.dataPath, out stdout, out stderr, 30000, extraPathPrepend);
        }

        private string BuildUvPathFromUvx(string uvxPath)
        {
            return _commandBuilder.BuildUvPathFromUvx(uvxPath);
        }

        private string GetPlatformSpecificPathPrepend()
        {
            return _commandBuilder.GetPlatformSpecificPathPrepend();
        }

        /// <summary>
        /// Start the local HTTP server headless (no terminal window), redirecting its
        /// stdout/stderr to Library/MCPForUnity/Logs/server-launch-{port}.log.
        /// Refuses occupied ports and clears stale build artifacts first.
        /// </summary>
        public bool StartLocalHttpServer(bool quiet = false)
        {
            if (!TryPreparePreviousManagedLaunch(out string ownershipError))
            {
                McpLog.Warn("Cannot start local HTTP server: " + ownershipError);
                if (!quiet)
                    EditorUtility.DisplayDialog("Managed Server Launch Still Pending", ownershipError, "OK");
                return false;
            }

            /// Clean stale Python build artifacts when using a local dev server path
            AssetPathUtility.CleanLocalServerBuildArtifacts();

            if (!TryGetLocalHttpServerCommandParts(out _, out _, out var displayCommand, out var error))
            {
                if (!quiet)
                {
                    EditorUtility.DisplayDialog(
                        "Cannot Start HTTP Server",
                        error ?? "The server command could not be constructed with the current settings.",
                        "OK"
                    );
                }
                return false;
            }

            // A listener may serve several projects. Starting this project must never
            // implicitly stop it; the explicit Stop action validates ownership first.

            // If the port is still occupied, don't start and explain why (avoid confusing "refusing to stop" warnings).
            try
            {
                string httpUrl = HttpEndpointUtility.GetLocalBaseUrl();
                if (Uri.TryCreate(httpUrl, UriKind.Absolute, out var uri) && uri.Port > 0)
                {
                    var remaining = GetListeningProcessIdsForPort(uri.Port);
                    if (remaining.Count > 0)
                    {
                        if (!quiet)
                        {
                            EditorUtility.DisplayDialog(
                                "Port In Use",
                                $"Cannot start the local HTTP server because port {uri.Port} is already in use by PID(s): "
                                    + $"{string.Join(", ", remaining)}\n\n"
                                    + $"{ProductInfo.ProductName} will not terminate unrelated processes. Stop the owning process manually or change the HTTP URL.",
                                "OK"
                            );
                        }
                        return false;
                    }
                }
            }
            catch { }

            // Note: Dev mode cache-busting is handled by `uvx --no-cache --refresh` in the generated command.

            // Create a per-launch token + pidfile path so Stop can be deterministic without relying on port/PID heuristics.
            string baseUrlForPid = HttpEndpointUtility.GetLocalBaseUrl();
            Uri.TryCreate(baseUrlForPid, UriKind.Absolute, out var uriForPid);
            int portForPid = uriForPid?.Port ?? 0;
            string instanceToken = Guid.NewGuid().ToString("N");
            string pidFilePath = portForPid > 0 ? GetLocalHttpServerPidFilePath(portForPid) : null;

            string launchCommand = displayCommand;
            if (!string.IsNullOrEmpty(pidFilePath))
            {
                launchCommand = $"{displayCommand} --pidfile {QuoteIfNeeded(pidFilePath)} --unity-instance-token {instanceToken}";
            }

            // First-time-only confirmation. Subsequent launches (and the quiet auto-start path) skip the dialog.
            if (!quiet && !EditorPrefs.GetBool(EditorPrefKeys.HttpServerLaunchConfirmed, false))
            {
                if (
                    !EditorUtility.DisplayDialog(
                        "Start Local HTTP Server",
                        "Start the local MCP server in the background?\n\n"
                            + "It launches headless (no terminal window) and logs progress to the Unity Console. "
                            + "This confirmation is shown only once.",
                        "Start",
                        "Cancel"
                    )
                )
                {
                    return false;
                }
                try
                {
                    EditorPrefs.SetBool(EditorPrefKeys.HttpServerLaunchConfirmed, true);
                }
                catch { }
            }

            string launchLog = portForPid > 0 ? GetLocalHttpServerLaunchLogPath(portForPid) : null;

            try
            {
                // Clear any stale handshake state from prior launches.
                ClearLocalServerPidTracking();
                _lastLaunchedProcess = null;

                // Best-effort: delete stale pidfile if it exists.
                try
                {
                    if (!string.IsNullOrEmpty(pidFilePath) && File.Exists(pidFilePath))
                    {
                        DeletePidFile(pidFilePath);
                    }
                }
                catch { }

                // Truncate the launch log so the tail always reflects the current launch.
                if (!string.IsNullOrEmpty(launchLog))
                {
                    try
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(launchLog));
                        File.WriteAllText(launchLog, string.Empty);
                    }
                    catch { }
                }

                McpLog.Info("Starting local HTTP server… (first run may take a minute while dependencies install)");

                // Launch the server headless (no terminal window); stdout+stderr go to the launch log.
                string effectiveLog = launchLog ?? Path.Combine(Path.GetTempPath(), "mcp-for-unity-server-launch.log");
                var startInfo = CreateHeadlessProcessStartInfo(launchCommand, effectiveLog);

                // The headless shell is not a login shell, so it does not inherit the user's
                // profile PATH (on macOS, GUI-launched Unity has a minimal PATH). Prepend the
                // platform uv/uvx locations so a bare `uvx`/`uv` resolves the same way the old
                // terminal (login shell) launch did.
                string extraPathPrepend = GetPlatformSpecificPathPrepend();
                if (!string.IsNullOrEmpty(extraPathPrepend))
                {
                    string currentPath = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
                    startInfo.EnvironmentVariables["PATH"] = string.IsNullOrEmpty(currentPath)
                        ? extraPathPrepend
                        : (extraPathPrepend + Path.PathSeparator + currentPath);
                }

                _lastLaunchedProcess = System.Diagnostics.Process.Start(startInfo);
                if (_lastLaunchedProcess == null)
                    throw new InvalidOperationException("The server launcher did not return a process handle.");
                if (!string.IsNullOrEmpty(pidFilePath))
                {
                    StoreLocalHttpServerHandshake(pidFilePath, instanceToken);
                    // Both headless launchers run uv in the foreground: cmd /c
                    // on Windows, bash -c on macOS/Linux. Their PID covers the
                    // dependency/bootstrap period before Python writes its PID.
                    if (_pidFileManager is PidFileManager ownedPids)
                        ownedPids.StoreOwnedLaunchProcess(_lastLaunchedProcess.Id, portForPid, instanceToken);
                }
                return true;
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to start server: {ex.Message}");
                if (!quiet)
                {
                    EditorUtility.DisplayDialog("Error", $"Failed to start server: {ex.Message}", "OK");
                }
                return false;
            }
        }

        private bool TryPreparePreviousManagedLaunch(out string error)
        {
            error = null;
            try
            {
                bool launcherHandleExited = false;
                if (_lastLaunchedProcess != null)
                {
                    if (!_lastLaunchedProcess.HasExited)
                    {
                        error = "The previous managed launcher is still running. Wait for startup or shutdown to finish before starting again.";
                        return false;
                    }
                    launcherHandleExited = true;
                }
                if (!TryGetLocalHttpServerHandshake(out string previousPidFile, out string previousToken))
                    return true;
                if (!TryGetPortFromPidFilePath(previousPidFile, out int previousPort))
                {
                    error = "The previous managed launch identity has an invalid port. Its exit cannot be verified; inspect its owning launch before retrying.";
                    return false;
                }

                bool serverPidKnown = TryReadPidFromPidFile(previousPidFile, out int serverPid) && serverPid > 1;
                if (serverPidKnown && _processDetector.ProcessExists(serverPid))
                {
                    error = "The previous managed server process is still running. Wait for startup or shutdown to finish before starting again.";
                    return false;
                }
                int launcherPid = 0;
                bool launcherPidKnown =
                    _pidFileManager is PidFileManager ownedPids && ownedPids.TryGetOwnedLaunchProcess(previousPort, previousToken, out launcherPid);
                if (launcherPidKnown && _processDetector.ProcessExists(launcherPid))
                {
                    error = "The previous managed launcher is still running. Wait for startup or shutdown to finish before starting again.";
                    return false;
                }
                if (!serverPidKnown && !launcherPidKnown && !launcherHandleExited)
                {
                    error =
                        "The previous managed launch has no readable server PID or matching launcher record. Its exit cannot be verified. "
                        + "Let startup finish, or restore its PID file after inspecting the owning process before retrying.";
                    return false;
                }

                // A free port alone does not prove exit. Clear only after all
                // known owned processes are gone, including a pre-bind launcher.
                DeletePidFile(previousPidFile);
                ClearLocalServerPidTracking();
                _lastLaunchedProcess = null;
                return true;
            }
            catch
            {
                error = "The previous managed launch could not be inspected. Its ownership is preserved; check process-query permissions before retrying.";
                return false;
            }
        }

        private bool IsOwnedLauncherExitConfirmed(string pidFilePath, string instanceToken)
        {
            try
            {
                if (_lastLaunchedProcess != null && !_lastLaunchedProcess.HasExited)
                    return false;
                if (
                    TryGetPortFromPidFilePath(pidFilePath, out int port)
                    && _pidFileManager is PidFileManager ownedPids
                    && ownedPids.TryGetOwnedLaunchProcess(port, instanceToken, out int launcherPid)
                )
                    return !_processDetector.ProcessExists(launcherPid);
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>
        /// Stop only this project's managed, idle HTTP server after identity verification.
        /// </summary>
        public bool StopLocalHttpServer()
        {
            return StopLocalHttpServerInternal(quiet: false);
        }

        public bool StopManagedLocalHttpServer()
        {
            if (!TryGetLocalHttpServerHandshake(out var pidFilePath, out _))
            {
                return RefuseStop("This project has no managed local server launch identity.", quiet: true);
            }

            int port = 0;
            if (!TryGetPortFromPidFilePath(pidFilePath, out port) || port <= 0)
            {
                string baseUrl = HttpEndpointUtility.GetLocalBaseUrl();
                if (IsLocalUrl(baseUrl) && Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri) && uri.Port > 0)
                {
                    port = uri.Port;
                }
            }

            if (port <= 0)
            {
                return RefuseStop("The managed local server port could not be determined.", quiet: true);
            }

            return StopLocalHttpServerInternal(quiet: true, portOverride: port, allowNonLocalUrl: true);
        }

        public bool IsLocalHttpServerRunning()
        {
            try
            {
                string httpUrl = HttpEndpointUtility.GetLocalBaseUrl();
                if (!IsLocalUrl(httpUrl))
                {
                    return false;
                }

                if (!Uri.TryCreate(httpUrl, UriKind.Absolute, out var uri) || uri.Port <= 0)
                {
                    return false;
                }

                int port = uri.Port;

                // Handshake path: if we have a pidfile+token and the PID is still the listener, treat as running.
                if (
                    TryGetLocalHttpServerHandshake(out var pidFilePath, out var instanceToken)
                    && TryReadPidFromPidFile(pidFilePath, out var pidFromFile)
                    && pidFromFile > 0
                )
                {
                    var pidsNow = GetListeningProcessIdsForPort(port);
                    if (pidsNow.Contains(pidFromFile))
                    {
                        return true;
                    }
                }

                var pids = GetListeningProcessIdsForPort(port);
                if (pids.Count == 0)
                {
                    return false;
                }

                // Strong signal: stored PID is still the listener.
                if (TryGetStoredLocalServerPid(port, out int storedPid) && storedPid > 0)
                {
                    if (pids.Contains(storedPid))
                    {
                        return true;
                    }
                }

                // Best-effort: if anything listening looks like our server, treat as running.
                foreach (var pid in pids)
                {
                    if (pid <= 0)
                        continue;
                    if (LooksLikeMcpServerProcess(pid))
                    {
                        return true;
                    }
                }

                return false;
            }
            catch
            {
                return false;
            }
        }

        public bool IsLocalHttpServerReachable()
        {
            try
            {
                string httpUrl = HttpEndpointUtility.GetLocalBaseUrl();
                if (!IsLocalUrl(httpUrl))
                {
                    return false;
                }

                if (!Uri.TryCreate(httpUrl, UriKind.Absolute, out var uri) || uri.Port <= 0)
                {
                    return false;
                }

                // 250ms, not 50ms: on a machine busy with test runs or domain reloads a 50ms
                // connect wait produces false "server gone" readings that tore down healthy
                // sessions via the orphaned-session detector (#1207).
                return TryConnectToLocalPort(uri.Host, uri.Port, timeoutMs: 250);
            }
            catch
            {
                return false;
            }
        }

        private static bool TryConnectToLocalPort(string host, int port, int timeoutMs)
        {
            try
            {
                // timeoutMs is an overall budget shared across candidate hosts, so a
                // filtered/dropped first candidate cannot multiply the worst-case wait
                // (this can run on the editor UI tick).
                var elapsed = System.Diagnostics.Stopwatch.StartNew();
                foreach (string target in BuildLocalProbeHosts(host))
                {
                    int remainingMs = timeoutMs - (int)elapsed.ElapsedMilliseconds;
                    if (remainingMs <= 0)
                    {
                        break;
                    }

                    try
                    {
                        using (var client = new TcpClient())
                        {
                            var connectTask = client.ConnectAsync(target, port);
                            if (connectTask.Wait(remainingMs) && client.Connected)
                            {
                                return true;
                            }
                        }
                    }
                    catch
                    {
                        // Ignore per-host failures.
                    }
                }
            }
            catch
            {
                // Ignore probe failures and treat as unreachable.
            }

            return false;
        }

        private static IReadOnlyList<string> BuildLocalProbeHosts(string host)
        {
            if (string.IsNullOrWhiteSpace(host))
            {
                host = "127.0.0.1";
            }
            else
            {
                host = host.Trim();
            }

            var hosts = new List<string>();
            AddHostCandidate(hosts, host);

            if (string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase))
            {
                // Probe both loopback families for localhost to avoid false negatives on systems where
                // localhost resolution prefers an address family different from the server bind.
                AddHostCandidate(hosts, "127.0.0.1");
                AddHostCandidate(hosts, "::1");
            }
            else if (string.Equals(host, "0.0.0.0", StringComparison.OrdinalIgnoreCase))
            {
                AddHostCandidate(hosts, "127.0.0.1");
            }
            else if (
                string.Equals(host, "::", StringComparison.OrdinalIgnoreCase) || string.Equals(host, "0:0:0:0:0:0:0:0", StringComparison.OrdinalIgnoreCase)
            )
            {
                AddHostCandidate(hosts, "::1");
            }

            return hosts;
        }

        private static void AddHostCandidate(List<string> hosts, string candidate)
        {
            if (string.IsNullOrWhiteSpace(candidate))
            {
                return;
            }

            if (hosts.Any(existing => string.Equals(existing, candidate, StringComparison.OrdinalIgnoreCase)))
            {
                return;
            }

            hosts.Add(candidate);
        }

        private bool StopLocalHttpServerInternal(bool quiet, int? portOverride = null, bool allowNonLocalUrl = false)
        {
            LastStopFailure = null;
            try
            {
                string httpUrl = HttpEndpointUtility.GetLocalBaseUrl();
                if (!allowNonLocalUrl && !IsLocalUrl(httpUrl))
                    return RefuseStop("The configured HTTP URL is not local.", quiet);
                if (!Uri.TryCreate(httpUrl, UriKind.Absolute, out var configuredUri))
                    return RefuseStop("The configured HTTP URL is invalid.", quiet);
                int port = portOverride ?? configuredUri.Port;
                if (port <= 0 || port > 65535)
                    return RefuseStop("The local HTTP port is invalid.", quiet);

                // Machine-global legacy preferences cannot prove this project's ownership.
                // Never fall back to killing an arbitrary MCP-looking process on the port.
                if (!TryGetLocalHttpServerHandshake(out string pidFilePath, out string instanceToken))
                    return RefuseStop(
                        "This project has no managed launch identity. The server may belong to another project, "
                            + "a manual launch, or an older package version. Stop it from its owning editor/terminal, then start it here.",
                        quiet
                    );
                if (!TryGetPortFromPidFilePath(pidFilePath, out int ownedPort) || ownedPort != port)
                    return RefuseStop("The managed launch identity belongs to a different port. Restore the launch URL before stopping it.", quiet);
                if (!TryReadPidFromPidFile(pidFilePath, out int pid) || pid <= 1)
                    return RefuseStop("The managed server PID file is unavailable or invalid. Wait for startup to finish and retry.", quiet);
                if (pid == GetCurrentProcessIdSafe())
                    return RefuseStop("The PID file identifies the Unity Editor; refusing to terminate it.", quiet);
                if (!GetListeningProcessIdsForPort(port).Contains(pid))
                {
                    if (!_processDetector.ProcessExists(pid))
                    {
                        if (IsOwnedLauncherExitConfirmed(pidFilePath, instanceToken))
                        {
                            DeletePidFile(pidFilePath);
                            ClearLocalServerPidTracking();
                        }
                        return true;
                    }
                    return RefuseStop("The managed PID is no longer the listener. The port may have been reused; stop its owner manually.", quiet);
                }
                if (!TryProcessCommandLineContainsInstanceToken(pid, instanceToken, out bool tokenMatches))
                    return RefuseStop("The server command line could not be inspected. Check OS process-query permissions and retry.", quiet);
                if (!tokenMatches)
                    return RefuseStop(
                        "The listener does not match this project's launch identity. Refusing to terminate a reused or unrelated process.",
                        quiet
                    );

                // Always use loopback when sending a local launch credential. A bind-all
                // address is a listener setting, never a credential destination.
                string host =
                    configuredUri.Host == "::" || configuredUri.Host == "[::]" ? "::1"
                    : configuredUri.IsLoopback ? configuredUri.Host
                    : "127.0.0.1";
                var endpoint = new UriBuilder(configuredUri.Scheme, host, port).Uri;
                var result = _shutdownRequester(endpoint, instanceToken);
                if (!result.Accepted)
                    return RefuseStop(result.Error ?? "Atomic shutdown was not confirmed. Check server status before retrying.", quiet);

                // Acknowledgement means graceful shutdown was requested, not that the
                // runner has exited. Retain ownership while it still holds the port.
                if (
                    !GetListeningProcessIdsForPort(port).Contains(pid)
                    && !_processDetector.ProcessExists(pid)
                    && IsOwnedLauncherExitConfirmed(pidFilePath, instanceToken)
                )
                {
                    DeletePidFile(pidFilePath);
                    ClearLocalServerPidTracking();
                }
                if (!quiet)
                    McpLog.Info($"Requested graceful local HTTP server shutdown on port {port} (PID: {pid})");
                return true;
            }
            catch (Exception ex)
            {
                // Exception messages from HTTP/auth providers can contain sensitive URLs.
                return RefuseStop($"Shutdown outcome could not be confirmed ({ex.GetType().Name}). Check server status before retrying.", quiet);
            }
        }

        private bool RefuseStop(string reason, bool quiet)
        {
            LastStopFailure = reason;
            if (!quiet)
                McpLog.Warn("Cannot stop local HTTP server: " + reason);
            return false;
        }

        private static (bool Accepted, string Error) RequestAtomicShutdown(Uri endpoint, string instanceToken)
        {
            try
            {
                string token = HttpEndpointUtility.ReadLocalAuthToken(endpoint);
                if (string.IsNullOrWhiteSpace(token))
                    return (false, "Local authentication is unavailable. The server remains running; check the local token file and retry.");
                using var handler = new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false };
                using var client = new HttpClient(handler) { Timeout = TimeSpan.FromMilliseconds(750), MaxResponseContentBufferSize = 64 * 1024 };
                client.DefaultRequestHeaders.Add(AuthConstants.LocalTokenHeader, token);
                using var content = new StringContent(
                    new JObject { ["instance_token"] = instanceToken }.ToString(Newtonsoft.Json.Formatting.None),
                    System.Text.Encoding.UTF8,
                    "application/json"
                );
                using var response = client.PostAsync(new Uri(endpoint, "/api/server/shutdown"), content).ConfigureAwait(false).GetAwaiter().GetResult();
                string body = response.Content.ReadAsStringAsync().ConfigureAwait(false).GetAwaiter().GetResult();
                return ParseShutdownResponse((int)response.StatusCode, body);
            }
            catch
            {
                return (false, "Shutdown outcome is unknown because acknowledgement was lost. Check server status before retrying.");
            }
        }

        internal static (bool Accepted, string Error) ParseShutdownResponse(int statusCode, string body)
        {
            if (statusCode == 404)
                return (
                    false,
                    "This server version does not support atomic shutdown. Stop it from its owning terminal, then restart it with the current package."
                );
            if (statusCode == 409)
                return (false, "The server still has connected or connecting Unity sessions. End those sessions before stopping the shared server.");
            if (statusCode == 401 || statusCode == 403)
                return (
                    false,
                    "The server rejected local authentication or this project's launch identity. It remains running; check its owning launch and retry."
                );
            if (statusCode != 200)
                return (false, "The owned HTTP runner is unavailable for atomic shutdown. It remains running; stop it from its owning terminal if necessary.");
            try
            {
                var json = JObject.Parse(body);
                if (json["success"]?.Type == JTokenType.Boolean && (bool)json["success"] && (string)json["status"] == "shutdown_requested")
                    return (true, null);
            }
            catch { }
            return (false, "The server did not acknowledge atomic shutdown. Its shutdown outcome is unknown; check server status before retrying.");
        }

        private bool TryGetUnixProcessArgs(int pid, out string argsLower)
        {
            return _processDetector.TryGetProcessCommandLine(pid, out argsLower);
        }

        private bool TryGetPortFromPidFilePath(string pidFilePath, out int port)
        {
            return _pidFileManager.TryGetPortFromPidFilePath(pidFilePath, out port);
        }

        private void DeletePidFile(string pidFilePath)
        {
            _pidFileManager.DeletePidFile(pidFilePath);
        }

        private List<int> GetListeningProcessIdsForPort(int port)
        {
            return _processDetector.GetListeningProcessIdsForPort(port);
        }

        private int GetCurrentProcessIdSafe()
        {
            return _processDetector.GetCurrentProcessId();
        }

        private bool LooksLikeMcpServerProcess(int pid)
        {
            return _processDetector.LooksLikeMcpServerProcess(pid);
        }

        /// <summary>
        /// Attempts to build the command used for starting the local HTTP server
        /// </summary>
        public bool TryGetLocalHttpServerCommand(out string command, out string error)
        {
            command = null;
            error = null;
            if (!TryGetLocalHttpServerCommandParts(out var fileName, out var args, out var displayCommand, out error))
            {
                return false;
            }

            // Maintain existing behavior: return a single command string suitable for display/copy.
            command = displayCommand;
            return true;
        }

        private bool TryGetLocalHttpServerCommandParts(out string fileName, out string arguments, out string displayCommand, out string error)
        {
            return _commandBuilder.TryBuildCommand(out fileName, out arguments, out displayCommand, out error);
        }

        /// <summary>
        /// Check if the configured HTTP URL is a local address
        /// </summary>
        public bool IsLocalUrl()
        {
            string httpUrl = HttpEndpointUtility.GetLocalBaseUrl();
            return IsLocalUrl(httpUrl);
        }

        /// <summary>
        /// Check if a URL is local or bind-all (localhost/loopback and 0.0.0.0/::).
        /// This helper is intentionally broader than local-launch policy checks.
        /// </summary>
        private static bool IsLocalUrl(string url)
        {
            if (string.IsNullOrEmpty(url))
                return false;

            try
            {
                var uri = new Uri(url);
                string host = uri.Host;
                return HttpEndpointUtility.IsLoopbackHost(host) || HttpEndpointUtility.IsBindAllInterfacesHost(host);
            }
            catch
            {
                return false;
            }
        }

        /// <summary>
        /// Check if the local HTTP server can be started
        /// </summary>
        public bool CanStartLocalServer()
        {
            bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;
            if (!useHttpTransport)
            {
                return false;
            }

            string httpUrl = HttpEndpointUtility.GetLocalBaseUrl();
            return HttpEndpointUtility.IsHttpLocalUrlAllowedForLaunch(httpUrl, out _);
        }

        private System.Diagnostics.ProcessStartInfo CreateTerminalProcessStartInfo(string command)
        {
            return _terminalLauncher.CreateTerminalProcessStartInfo(command);
        }

        private System.Diagnostics.ProcessStartInfo CreateHeadlessProcessStartInfo(string command, string logFilePath)
        {
            return _terminalLauncher.CreateHeadlessProcessStartInfo(command, logFilePath);
        }

        public string GetLocalHttpServerLaunchLogPath()
        {
            string baseUrl = HttpEndpointUtility.GetLocalBaseUrl();
            if (Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri) && uri.Port > 0)
            {
                return GetLocalHttpServerLaunchLogPath(uri.Port);
            }
            return null;
        }

        private string GetLocalHttpServerLaunchLogPath(int port)
        {
            string dir = Path.Combine(_terminalLauncher.GetProjectRootPath(), "Library", "MCPForUnity", "Logs");
            return Path.Combine(dir, $"server-launch-{port}.log");
        }

        public bool HasManagedServerLaunchHandle => _lastLaunchedProcess != null;

        public bool IsManagedServerLaunchProcessAlive()
        {
            try
            {
                var proc = _lastLaunchedProcess;
                return proc != null && !proc.HasExited;
            }
            catch
            {
                // If we cannot query the process (e.g. it was disposed), assume it is no longer alive
                // so callers stop waiting on a dead handle.
                return false;
            }
        }

        public void LogLocalHttpServerLaunchFailure()
        {
            string logPath = GetLocalHttpServerLaunchLogPath();
            string tail = TailFile(logPath, 40);

            string copyHint;
            if (TryGetLocalHttpServerCommand(out var command, out _) && !string.IsNullOrEmpty(command))
            {
                copyHint = $"To run it yourself, copy this command into a terminal:\n{command}";
            }
            else
            {
                copyHint = "Use the \"Manual Server Launch\" foldout to copy the command and run it yourself.";
            }

            string logRef = string.IsNullOrEmpty(logPath) ? "(launch log unavailable)" : logPath;
            string body = string.IsNullOrEmpty(tail) ? "(no output captured)" : tail;

            McpLog.Error("Local HTTP server did not become reachable. " + $"Launch log: {logRef}\n{body}\n{copyHint}");
        }

        private static string TailFile(string path, int maxLines)
        {
            try
            {
                if (string.IsNullOrEmpty(path) || !File.Exists(path))
                {
                    return string.Empty;
                }

                var lines = File.ReadAllLines(path);
                if (lines.Length <= maxLines)
                {
                    return string.Join(Environment.NewLine, lines).Trim();
                }

                return string.Join(Environment.NewLine, lines.Skip(lines.Length - maxLines)).Trim();
            }
            catch
            {
                return string.Empty;
            }
        }
    }
}
