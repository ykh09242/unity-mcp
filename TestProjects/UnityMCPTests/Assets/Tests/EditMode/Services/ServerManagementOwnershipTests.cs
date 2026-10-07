using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Server;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class ServerManagementOwnershipTests : TransportPreferenceTestBase
    {
        private const int Port = 59981;
        private const int ServerPid = 45678;
        private const int LauncherPid = 45679;
        private const string InstanceToken = "0123456789abcdef0123456789abcdef";
        private PidFileManager _pids;
        private Detector _detector;
        private Terminator _terminator;
        private ServerManagementService _service;
        private string _pidPath;
        private string _root;
        private string _savedUrl;
        private int _shutdownStatus;
        private string _shutdownBody;
        private bool _exitsAfterRequest;
        private int _requests;
        private bool _loseAcknowledgement;

        [SetUp]
        public void SetUp()
        {
            _savedUrl = EditorPrefs.GetString(EditorPrefKeys.HttpBaseUrl, string.Empty);
            EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:" + Port);
            _root = Path.Combine(Path.GetTempPath(), "mcp-ownership-" + Guid.NewGuid().ToString("N"));
            _pids = new PidFileManager(_root);
            _pidPath = _pids.GetPidFilePath(Port);
            File.WriteAllText(_pidPath, ServerPid.ToString());
            _pids.StoreHandshake(_pidPath, InstanceToken);
            _detector = new Detector();
            _terminator = new Terminator();
            _shutdownStatus = 200;
            _shutdownBody = "{\"success\":true,\"status\":\"shutdown_requested\"}";
            _exitsAfterRequest = false;
            _requests = 0;
            _loseAcknowledgement = false;
            _service = new ServerManagementService(
                _detector,
                _pids,
                _terminator,
                shutdownRequester: (endpoint, token) =>
                {
                    Assert.AreEqual("127.0.0.1", endpoint.Host);
                    Assert.AreEqual(InstanceToken, token);
                    _requests++;
                    if (_loseAcknowledgement)
                        throw new TimeoutException("simulated lost response");
                    if (_exitsAfterRequest)
                        _detector.Listening = false;
                    return ServerManagementService.ParseShutdownResponse(_shutdownStatus, _shutdownBody);
                }
            );
        }

        [TearDown]
        public void TearDown()
        {
            _pids.ClearTracking();
            if (File.Exists(_pidPath))
                File.Delete(_pidPath);
            // All paths are rooted in this fixture's newly allocated temporary directory.
            if (Directory.Exists(_root))
                Directory.Delete(_root, true);
            if (string.IsNullOrEmpty(_savedUrl))
                EditorPrefs.DeleteKey(EditorPrefKeys.HttpBaseUrl);
            else
                EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, _savedUrl);
        }

        [Test]
        public void Stop_AnotherProjectHasNoLaunchIdentity_RefusesWithoutTermination()
        {
            // Given this editor did not launch the listener.
            _pids.ClearTracking();
            // When explicit stop is requested.
            bool stopped = _service.StopLocalHttpServer();
            // Then the unrelated listener survives with actionable diagnostics.
            Assert.IsFalse(stopped);
            Assert.AreEqual(0, _terminator.Calls);
            StringAssert.Contains("no managed launch identity", _service.LastStopFailure);
        }

        [Test]
        public void Stop_SharedSessionsRemain_RefusesWithoutTermination()
        {
            // Given a valid owned server used by another editor.
            _shutdownStatus = 409;
            // When stop is requested.
            bool stopped = _service.StopLocalHttpServer();
            // Then no process is terminated and its ownership is preserved.
            Assert.IsFalse(stopped);
            Assert.AreEqual(0, _terminator.Calls);
            Assert.IsTrue(_pids.TryGetHandshake(out _, out _));
            StringAssert.Contains("connected or connecting Unity sessions", _service.LastStopFailure);
        }

        [Test]
        public void Stop_AtomicEndpointUnavailable_FailsClosed()
        {
            // Given the server cannot reserve an owned runner shutdown.
            _shutdownStatus = 503;
            // When stop is requested.
            bool stopped = _service.StopLocalHttpServer();
            // Then the server survives with no OS termination fallback.
            Assert.IsFalse(stopped);
            Assert.AreEqual(0, _terminator.Calls);
            StringAssert.Contains("runner is unavailable", _service.LastStopFailure);
        }

        [Test]
        public void Stop_LaunchTokenMismatch_DoesNotFallBackToPortHeuristics()
        {
            // Given the PID was reused by an MCP-looking process with another identity.
            _detector.CommandLine = "python mcp-for-unity --unity-instance-token another-token";
            // When stop is requested.
            bool stopped = _service.StopLocalHttpServer();
            // Then its process is never terminated.
            Assert.IsFalse(stopped);
            Assert.AreEqual(0, _terminator.Calls);
            StringAssert.Contains("does not match", _service.LastStopFailure);
        }

        [Test]
        public void Stop_CommandLineUnavailable_DoesNotFallBackToProcessName()
        {
            // Given Windows CIM/process inspection is unavailable.
            _detector.CanInspect = false;
            // When stop is requested.
            bool stopped = _service.StopLocalHttpServer();
            // Then a generic MCP-looking process name cannot authorize termination.
            Assert.IsFalse(stopped);
            Assert.AreEqual(0, _terminator.Calls);
            StringAssert.Contains("process-query permissions", _service.LastStopFailure);
        }

        [Test]
        public void Stop_LaunchTokenIsOnlyAPrefix_RefusesTermination()
        {
            // Given a different token beginning with the recorded launch token.
            _detector.CommandLine += "suffix";
            // When stop is requested.
            bool stopped = _service.StopLocalHttpServer();
            // Then a substring match cannot authorize termination.
            Assert.IsFalse(stopped);
            Assert.AreEqual(0, _terminator.Calls);
        }

        [Test]
        public void Stop_OldServerRefusesWithoutOsFallbackAndPreservesIdentity()
        {
            // Given an older server has no atomic shutdown endpoint.
            _shutdownStatus = 404;
            // When stop is requested.
            bool stopped = _service.StopLocalHttpServer();
            // Then the next retry retains its identity and the reason is observable.
            Assert.IsFalse(stopped);
            Assert.IsTrue(_pids.TryGetHandshake(out _, out _));
            StringAssert.Contains("does not support atomic shutdown", _service.LastStopFailure);
            Assert.AreEqual(0, _terminator.Calls);
        }

        [Test]
        public void Stop_ConfirmedExitClearsOwnershipWithoutOsTermination()
        {
            // Given the owned runner releases its listener after acknowledgement.
            _exitsAfterRequest = true;
            // When explicit stop is requested.
            bool stopped = _service.StopLocalHttpServer();
            // Then confirmed exit clears tracking without invoking OS termination.
            Assert.IsTrue(stopped);
            Assert.AreEqual(1, _requests);
            Assert.AreEqual(0, _terminator.Calls);
            Assert.IsFalse(_pids.TryGetHandshake(out _, out _));
            Assert.IsFalse(File.Exists(_pidPath));
            Assert.IsNull(_service.LastStopFailure);
        }

        [Test]
        public void Stop_AcknowledgementRetainsOwnershipUntilExit()
        {
            Assert.IsTrue(_service.StopLocalHttpServer());
            Assert.IsTrue(_detector.Listening);
            Assert.IsTrue(_pids.TryGetHandshake(out _, out _));
            Assert.IsTrue(File.Exists(_pidPath));
            Assert.AreEqual(0, _terminator.Calls);
            // Repeated acknowledgement is safe and retains the same launch identity.
            Assert.IsTrue(_service.StopLocalHttpServer());
            Assert.AreEqual(2, _requests);
        }

        [Test]
        public void Stop_RetryAfterConfirmedExitClearsTrackingWithoutNewRequest()
        {
            Assert.IsTrue(_service.StopLocalHttpServer());
            _detector.Listening = false;
            Assert.IsTrue(_service.StopLocalHttpServer());
            Assert.AreEqual(1, _requests);
            Assert.IsFalse(_pids.TryGetHandshake(out _, out _));
            Assert.IsFalse(File.Exists(_pidPath));
            Assert.AreEqual(0, _terminator.Calls);
        }

        [TestCase(401)]
        [TestCase(403)]
        public void Stop_AuthenticationOrIdentityRefusalPreservesOwnership(int status)
        {
            _shutdownStatus = status;
            Assert.IsFalse(_service.StopLocalHttpServer());
            Assert.IsTrue(_pids.TryGetHandshake(out _, out _));
            Assert.AreEqual(0, _terminator.Calls);
            StringAssert.Contains("authentication or this project's launch identity", _service.LastStopFailure);
        }

        [Test]
        public void Stop_LostAcknowledgementPreservesIdentityAndReportsUnknownOutcome()
        {
            _loseAcknowledgement = true;
            Assert.IsFalse(_service.StopLocalHttpServer());
            Assert.IsTrue(_pids.TryGetHandshake(out _, out _));
            Assert.IsTrue(File.Exists(_pidPath));
            Assert.AreEqual(0, _terminator.Calls);
            StringAssert.Contains("Check server status before retrying", _service.LastStopFailure);
            StringAssert.DoesNotContain("remains running", _service.LastStopFailure);
        }

        [TestCase("{}")]
        [TestCase("{\"success\":true}")]
        [TestCase("{\"success\":true,\"status\":\"stopped\"}")]
        [TestCase("invalid")]
        public void Stop_InvalidAcknowledgementNeverFallsBackToOsTermination(string body)
        {
            _shutdownBody = body;
            Assert.IsFalse(_service.StopLocalHttpServer());
            Assert.IsTrue(_pids.TryGetHandshake(out _, out _));
            Assert.AreEqual(0, _terminator.Calls);
            StringAssert.Contains("outcome is unknown", _service.LastStopFailure);
        }

        [Test]
        public void Start_PreviousOwnedPidAliveBeforeBinding_PreservesIdentityWithoutLaunch()
        {
            _detector.Listening = false;
            _detector.ExistsOverride = true;
            var launcher = BuildStartService();

            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(0, launcher.Calls);
            AssertOriginalOwnership();
        }

        [Test]
        public void Start_ShutdownAcknowledgedButProcessAlive_PreservesIdentityWithoutLaunch()
        {
            Assert.IsTrue(_service.StopLocalHttpServer());
            _detector.Listening = false;
            _detector.ExistsOverride = true;
            var launcher = BuildStartService();

            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(0, launcher.Calls);
            AssertOriginalOwnership();
        }

        [Test]
        public void Start_MissingPidFileCannotProvePendingLaunchExited_PreservesIdentity()
        {
            File.Delete(_pidPath);
            _detector.Listening = false;
            var launcher = BuildStartService();

            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(0, launcher.Calls);
            Assert.IsTrue(_pids.TryGetHandshake(out var path, out var token));
            Assert.AreEqual(_pidPath, path);
            Assert.AreEqual(InstanceToken, token);
        }

        [Test]
        public void Start_PreviousOwnedPidExited_AllowsLaunchAttemptAndClearsStaleIdentity()
        {
            _detector.Listening = false;
            _detector.ExistsOverride = false;
            var launcher = BuildStartService();

            // The fixture aborts before Process.Start, so false means the fake
            // launcher deliberately prevented a real process from being spawned.
            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(1, launcher.Calls);
            Assert.IsFalse(_pids.TryGetHandshake(out _, out _));
            Assert.IsFalse(File.Exists(_pidPath));
        }

        [Test]
        public void Start_PersistedLauncherSurvivesReloadAndHeuristicExpiry_PreservesPendingIdentity()
        {
            File.Delete(_pidPath);
            _detector.Listening = false;
            _detector.ExistsByPid[LauncherPid] = true;
            _pids.StoreOwnedLaunchProcess(LauncherPid, Port, InstanceToken);
            EditorPrefs.SetString(_pids.PreferenceKey(EditorPrefKeys.LastLocalHttpServerStartedUtc), DateTime.UtcNow.AddDays(-2).ToString("O"));
            _pids = new PidFileManager(_root);
            Assert.IsFalse(_pids.TryGetStoredPid(Port, out _), "The heuristic PID record should have expired.");
            var launcher = BuildStartService();

            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(0, launcher.Calls);
            Assert.IsTrue(_pids.TryGetHandshake(out _, out var token));
            Assert.AreEqual(InstanceToken, token);
            Assert.IsTrue(_pids.TryGetOwnedLaunchProcess(Port, token, out int pid));
            Assert.AreEqual(LauncherPid, pid);
        }

        [Test]
        public void Start_FailedStartupLauncherExitedWithoutServerPid_AllowsRetryAfterReload()
        {
            File.Delete(_pidPath);
            _detector.Listening = false;
            _detector.ExistsByPid[LauncherPid] = false;
            _pids.StoreOwnedLaunchProcess(LauncherPid, Port, InstanceToken);
            EditorPrefs.SetString(_pids.PreferenceKey(EditorPrefKeys.LastLocalHttpServerStartedUtc), DateTime.UtcNow.AddDays(-2).ToString("O"));
            _pids = new PidFileManager(_root);
            var launcher = BuildStartService();

            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(1, launcher.Calls, "A proven exited failed startup must not leave a permanent stale-token block.");
            Assert.IsFalse(_pids.TryGetHandshake(out _, out _));
            Assert.IsFalse(_pids.TryGetOwnedLaunchProcess(Port, InstanceToken, out _));
        }

        [Test]
        public void Start_ServerExitedButOwnedLauncherStillAlive_PreservesBothRecords()
        {
            _detector.Listening = false;
            _detector.ExistsByPid[ServerPid] = false;
            _detector.ExistsByPid[LauncherPid] = true;
            _pids.StoreOwnedLaunchProcess(LauncherPid, Port, InstanceToken);
            var launcher = BuildStartService();

            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(0, launcher.Calls);
            AssertOriginalOwnership();
            Assert.IsTrue(_pids.TryGetOwnedLaunchProcess(Port, InstanceToken, out _));
        }

        [Test]
        public void Start_LauncherRecordBelongsToAnotherToken_DoesNotProvePendingLaunchExited()
        {
            File.Delete(_pidPath);
            _detector.Listening = false;
            _pids.StoreOwnedLaunchProcess(LauncherPid, Port, "another-launch");
            var launcher = BuildStartService();

            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(0, launcher.Calls);
            Assert.IsTrue(_pids.TryGetHandshake(out _, out var token));
            Assert.AreEqual(InstanceToken, token);
        }

        [Test]
        public void Start_ProcessInspectionThrows_PreservesIdentityWithoutLaunch()
        {
            _detector.Listening = false;
            _detector.ThrowOnExists = true;
            var launcher = BuildStartService();

            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));

            Assert.AreEqual(0, launcher.Calls);
            AssertOriginalOwnership();
        }

        [Test]
        public void Stop_ServerExitedButLauncherAlive_RetainsOwnershipForStartGuard()
        {
            _pids.StoreOwnedLaunchProcess(LauncherPid, Port, InstanceToken);
            _detector.ExistsByPid[ServerPid] = false;
            _detector.ExistsByPid[LauncherPid] = true;
            _exitsAfterRequest = true;

            Assert.IsTrue(_service.StopLocalHttpServer());

            AssertOriginalOwnership();
            var launcher = BuildStartService();
            Assert.IsFalse(_service.StartLocalHttpServer(quiet: true));
            Assert.AreEqual(0, launcher.Calls);
        }

        private BlockingLauncher BuildStartService()
        {
            var launcher = new BlockingLauncher(_root);
            _service = new ServerManagementService(_detector, _pids, _terminator, new CommandBuilder(), launcher);
            return launcher;
        }

        private void AssertOriginalOwnership()
        {
            Assert.IsTrue(_pids.TryGetHandshake(out var path, out var token));
            Assert.AreEqual(_pidPath, path);
            Assert.AreEqual(InstanceToken, token);
            Assert.AreEqual(ServerPid.ToString(), File.ReadAllText(_pidPath));
        }

        private sealed class CommandBuilder : IServerCommandBuilder
        {
            public bool TryBuildCommand(out string fileName, out string arguments, out string displayCommand, out string error)
            {
                fileName = "fake-server";
                arguments = string.Empty;
                displayCommand = "fake-server";
                error = null;
                return true;
            }

            public string QuoteIfNeeded(string value) => value;

            public string BuildUvPathFromUvx(string value) => value;

            public string GetPlatformSpecificPathPrepend() => string.Empty;
        }

        private sealed class BlockingLauncher : ITerminalLauncher
        {
            private readonly string _root;
            internal int Calls;

            internal BlockingLauncher(string root) => _root = root;

            public string GetProjectRootPath() => _root;

            public System.Diagnostics.ProcessStartInfo CreateTerminalProcessStartInfo(string command) =>
                throw new InvalidOperationException("Unexpected terminal launch");

            public System.Diagnostics.ProcessStartInfo CreateHeadlessProcessStartInfo(string command, string logFilePath)
            {
                Calls++;
                LogAssert.Expect(LogType.Error, new Regex("Failed to start server: Fixture prevented process creation$"));
                throw new InvalidOperationException("Fixture prevented process creation");
            }
        }

        private sealed class Detector : IProcessDetector
        {
            internal bool Listening = true;
            internal bool CanInspect = true;
            internal bool? ExistsOverride;
            internal bool ThrowOnExists;
            internal readonly Dictionary<int, bool> ExistsByPid = new Dictionary<int, bool>();
            internal string CommandLine = "python mcp-for-unity --unity-instance-token " + InstanceToken;

            public bool LooksLikeMcpServerProcess(int pid) => true;

            public bool TryGetProcessCommandLine(int pid, out string argsLower)
            {
                argsLower = CommandLine;
                return CanInspect;
            }

            public List<int> GetListeningProcessIdsForPort(int port) => Listening ? new List<int> { ServerPid } : new List<int>();

            public int GetCurrentProcessId() => 98765;

            public bool ProcessExists(int pid)
            {
                if (ThrowOnExists)
                    throw new InvalidOperationException("Fixture process inspection unavailable");
                return ExistsByPid.TryGetValue(pid, out bool exists) ? exists : ExistsOverride ?? Listening;
            }

            public string NormalizeForMatch(string input) => input.Replace(" ", string.Empty).ToLowerInvariant();
        }

        private sealed class Terminator : IProcessTerminator
        {
            internal int Calls;

            public bool Terminate(int pid)
            {
                Calls++;
                return false;
            }
        }
    }
}
