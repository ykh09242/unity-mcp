using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Windows.Components.Connection;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class McpConnectionSectionInteractionTests
    {
        private const BindingFlags InstanceFlags = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags StaticFlags = BindingFlags.Static | BindingFlags.NonPublic;
        private readonly Dictionary<string, object> services = new();
        private readonly Dictionary<string, (bool exists, string value)> stringPrefs = new();
        private readonly Dictionary<string, (bool exists, bool value)> sessionPrefs = new();
        private bool hadPort;
        private int previousPort;
        private bool previousHttp;
        private bool hadHttp;
        private bool wasPinned;
        private bool hadResume;
        private bool previousResume;
        private int previousGeneration;
        private FakeBridge bridge;
        private FakeServer server;

        [SetUp]
        public void SetUp()
        {
            foreach (string name in new[] { "_bridgeService", "_serverManagementService", "_transportManager" })
                services[name] = typeof(MCPServiceLocator).GetField(name, StaticFlags).GetValue(null);
            foreach (
                string key in new[] { EditorPrefKeys.HttpBaseUrl, EditorPrefKeys.HttpRemoteBaseUrl, EditorPrefKeys.HttpTransportScope, EditorPrefKeys.ApiKey }
            )
                stringPrefs[key] = (EditorPrefs.HasKey(key), EditorPrefs.GetString(key));
            hadHttp = EditorPrefs.HasKey(EditorPrefKeys.UseHttpTransport);
            previousHttp = EditorPrefs.GetBool(EditorPrefKeys.UseHttpTransport, true);
            wasPinned = SessionState.GetBool("MCPForUnity.ForceStdioForSession", false);
            hadPort = EditorPrefs.HasKey(EditorPrefKeys.UnitySocketPort);
            previousPort = EditorPrefs.GetInt(EditorPrefKeys.UnitySocketPort);
            hadResume = EditorPrefs.HasKey(EditorPrefKeys.ResumeStdioAfterReload);
            previousResume = EditorPrefs.GetBool(EditorPrefKeys.ResumeStdioAfterReload);
            previousGeneration = (int)typeof(McpConnectionSection).GetField("autoStartGeneration", StaticFlags).GetValue(null);
            typeof(McpConnectionSection).GetField("autoStartGeneration", StaticFlags).SetValue(null, 0);
            foreach (
                string key in new[] { "MCPForUnity.ResumeHttpAfterReload", "HttpAutoStartHandler.ConnectPending", "MCPForUnity.ResumeHttpAfterReload.Migrated" }
            )
            {
                bool value = SessionState.GetBool(key, false);
                sessionPrefs[key] = (value == SessionState.GetBool(key, true), value);
            }
            bridge = new FakeBridge();
            server = new FakeServer();
            MCPServiceLocator.Register<IBridgeControlService>(bridge);
            MCPServiceLocator.Register<IServerManagementService>(server);
            MCPServiceLocator.Register(new TransportManager());
            EditorConfigurationCache.Instance.UnpinStdioForSession();
            EditorConfigurationCache.Instance.SetUseHttpTransport(false);
            EditorConfigurationCache.Instance.SetHttpTransportScope("local");
            EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:8080");
            EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "https://server-a.example");
            EditorPrefs.SetString(EditorPrefKeys.ApiKey, "memory-key");
            EditorPrefs.SetInt(EditorPrefKeys.UnitySocketPort, 1234);
            EditorPrefs.DeleteKey(EditorPrefKeys.ResumeStdioAfterReload);
        }

        [TearDown]
        public void TearDown()
        {
            EditorConfigurationCache.Instance.SetUseHttpTransport(previousHttp);
            if (!hadHttp)
                EditorPrefs.DeleteKey(EditorPrefKeys.UseHttpTransport);
            foreach (var pref in stringPrefs)
            {
                if (pref.Value.exists)
                    EditorPrefs.SetString(pref.Key, pref.Value.value);
                else
                    EditorPrefs.DeleteKey(pref.Key);
            }
            if (hadPort)
                EditorPrefs.SetInt(EditorPrefKeys.UnitySocketPort, previousPort);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.UnitySocketPort);
            if (hadResume)
                EditorPrefs.SetBool(EditorPrefKeys.ResumeStdioAfterReload, previousResume);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.ResumeStdioAfterReload);
            if (wasPinned)
                EditorConfigurationCache.Instance.PinStdioForSession();
            EditorConfigurationCache.Instance.Refresh();
            typeof(McpConnectionSection).GetField("autoStartGeneration", StaticFlags).SetValue(null, previousGeneration);
            foreach (var pref in sessionPrefs)
            {
                if (pref.Value.exists)
                    SessionState.SetBool(pref.Key, pref.Value.value);
                else
                    SessionState.EraseBool(pref.Key);
            }
            foreach (var service in services)
                typeof(MCPServiceLocator).GetField(service.Key, StaticFlags).SetValue(null, service.Value);
            services.Clear();
            stringPrefs.Clear();
            sessionPrefs.Clear();
        }

        [Test]
        public void StatusPoll_PreservesTypedPortUntilCommit()
        {
            var section = CreateSection();
            var field = Get<TextField>(section, "unityPortField");
            ChangePort(field, "9876");
            Assert.IsTrue(Get<bool>(section, "unityPortDirty"));
            section.UpdateConnectionStatus();
            section.UpdateConnectionStatus();
            Assert.AreEqual("9876", field.value);
            Assert.AreEqual(1234, EditorPrefs.GetInt(EditorPrefKeys.UnitySocketPort));
        }

        [TestCase("")]
        [TestCase("invalid")]
        [TestCase("0")]
        [TestCase("-1")]
        public void InvalidPortCommit_RevertsAndClearsDirtyState(string draft)
        {
            var section = CreateSection();
            var field = Get<TextField>(section, "unityPortField");
            ChangePort(field, draft);
            Invoke(section, "PersistUnityPortFromField");
            Assert.AreEqual(bridge.CurrentPort.ToString(), field.value);
            Assert.IsFalse(Get<bool>(section, "unityPortDirty"));
            Assert.AreEqual(1234, EditorPrefs.GetInt(EditorPrefKeys.UnitySocketPort));
        }

        [Test]
        public void RunningBridgePort_OverridesDraftWithoutMarkingItDirty()
        {
            var section = CreateSection();
            var field = Get<TextField>(section, "unityPortField");
            ChangePort(field, "9876");
            bridge.Running = true;
            section.UpdateConnectionStatus();
            Assert.AreEqual(bridge.CurrentPort.ToString(), field.value);
            Assert.IsFalse(field.enabledSelf);
            Assert.IsFalse(Get<bool>(section, "unityPortDirty"));
        }

        [TestCase(true)]
        [TestCase(false)]
        public void StatusPoll_KeepsConnectionActionDisabledWhileBusy(bool running)
        {
            var section = CreateSection();
            bridge.Running = running;
            Set(section, "connectionToggleInProgress", true);
            section.UpdateConnectionStatus();
            Assert.IsFalse(Get<Button>(section, "connectionToggleButton").enabledSelf);
            Set(section, "connectionToggleInProgress", false);
            section.UpdateConnectionStatus();
            Assert.IsTrue(Get<Button>(section, "connectionToggleButton").enabledSelf);
        }

        [UnityTest]
        public IEnumerator PendingLocalLaunch_DoesNotConnectAfterContextChanges()
        {
            foreach (string change in new[] { "stdio", "remote", "url", "generation" })
            {
                var section = CreateSection();
                UseHttp("local");
                HttpEndpointUtility.SaveLocalBaseUrl("http://127.0.0.1:8080");
                server.Reachable = false;
                Task pending = (Task)Invoke(section, "TryAutoStartSessionAsync");
                ChangeContext(section, change);
                server.Reachable = true;
                yield return Complete(pending);
                Assert.AreEqual(0, bridge.StartCalls, change);
                Assert.AreEqual(0, bridge.StopCalls);
                Assert.AreEqual(0, server.StopCalls);
            }
        }

        [Test]
        public void LocalLaunch_UnchangedContextConnectsWithoutRequiringAttachedWindow()
        {
            var section = CreateSection();
            UseHttp("local");
            server.Reachable = true;
            int updates = 0;
            section.SetHealthStatusUpdateCallback((healthy, status) => updates++);
            AssertCompleted((Task)Invoke(section, "TryAutoStartSessionAsync"));
            Assert.AreEqual(1, bridge.StartCalls);
            Assert.AreEqual(1, bridge.VerifyCalls);
            Assert.AreEqual(1, updates);
            Assert.AreEqual(0, bridge.StopCalls);
            Assert.AreEqual(0, server.StopCalls);
        }

        [UnityTest]
        public IEnumerator LocalLaunch_OldControllerDoesNotReviveNewControllersExplicitDisconnect()
        {
            var oldSection = CreateSection();
            UseHttp("local");
            Task pending = (Task)Invoke(oldSection, "TryAutoStartSessionAsync");
            var newSection = CreateSection();
            Invoke(newSection, "OnConnectionToggleClicked");
            Invoke(newSection, "OnConnectionToggleClicked");
            Assert.AreEqual(1, bridge.StartCalls);
            Assert.AreEqual(1, bridge.StopCalls);
            server.Reachable = true;
            yield return Complete(pending);
            Assert.AreEqual(1, bridge.StartCalls, "The detached controller must not reconnect after the new controller disconnects.");
            Assert.AreEqual(1, bridge.StopCalls);
            Assert.IsFalse(bridge.IsRunning);
        }

        [UnityTest]
        public IEnumerator LocalLaunch_FallbackStillConnectsForUnchangedContext()
        {
            var section = CreateSection();
            UseHttp("local");
            server.Alive = false;
            yield return Complete((Task)Invoke(section, "TryAutoStartSessionAsync"));
            Assert.AreEqual(1, bridge.StartCalls);
            Assert.AreEqual(1, bridge.VerifyCalls);
        }

        [Test]
        public void LocalLaunch_StartCompletionForOldScopeDoesNotVerify()
        {
            var section = CreateSection();
            UseHttp("local");
            server.Reachable = true;
            bridge.StartPending = new TaskCompletionSource<bool>();
            Task pending = (Task)Invoke(section, "TryAutoStartSessionAsync");
            UseHttp("remote");
            bridge.StartPending.SetResult(true);
            AssertCompleted(pending);
            Assert.AreEqual(1, bridge.StartCalls);
            Assert.AreEqual(0, bridge.VerifyCalls);
            Assert.AreEqual(0, bridge.StopCalls);
        }

        [TestCase("stdio")]
        [TestCase("remote")]
        [TestCase("url")]
        [TestCase("generation")]
        public void PendingVerification_DoesNotPublishOldContextHealth(string change)
        {
            var section = CreateSection();
            UseHttp("local");
            bridge.Running = true;
            bridge.VerifyPending = new TaskCompletionSource<BridgeVerificationResult>();
            int updates = 0;
            section.SetHealthStatusUpdateCallback((healthy, status) => updates++);
            Task pending = section.VerifyBridgeConnectionAsync();
            ChangeContext(section, change);
            bridge.VerifyPending.SetResult(FakeBridge.Healthy());
            AssertCompleted(pending);
            Assert.AreEqual(0, updates);
            Assert.IsNull(Get<string>(section, "lastHealthStatus"));
        }

        [TestCase("url")]
        [TestCase("stdio")]
        [TestCase("local")]
        public void LoginResponse_ForOldEndpointOrScopeIsNotReturnedOrCached(string change)
        {
            var section = CreateSection();
            UseHttp("remote");
            var response = new TaskCompletionSource<string>();
            Set(section, "loginUrlFetcher", new Func<string, Task<string>>(_ => response.Task));
            Task<string> pending = (Task<string>)Invoke(section, "GetLoginUrlAsync");
            if (change == "url")
                HttpEndpointUtility.SaveRemoteBaseUrl("https://server-b.example");
            else if (change == "stdio")
                EditorConfigurationCache.Instance.SetUseHttpTransport(false);
            else
                UseHttp("local");
            response.SetResult("memory-login-a");
            AssertCompleted(pending);
            Assert.IsNull(pending.Result);
            Assert.IsNull(Get<string>(section, "cachedLoginUrl"));
        }

        [Test]
        public void LoginCache_IsReusedOnlyForTheSameEndpoint()
        {
            var section = CreateSection();
            UseHttp("remote");
            int fetches = 0;
            Set(
                section,
                "loginUrlFetcher",
                new Func<string, Task<string>>(url =>
                {
                    fetches++;
                    return Task.FromResult(url + "/memory-login");
                })
            );
            var firstFetch = (Task<string>)Invoke(section, "GetLoginUrlAsync");
            AssertCompleted(firstFetch);
            string first = firstFetch.Result;
            Assert.AreEqual(first, ((Task<string>)Invoke(section, "GetLoginUrlAsync")).Result);
            Assert.AreEqual(1, fetches);
            HttpEndpointUtility.SaveRemoteBaseUrl("https://server-b.example");
            var secondFetch = (Task<string>)Invoke(section, "GetLoginUrlAsync");
            AssertCompleted(secondFetch);
            string second = secondFetch.Result;
            Assert.AreNotEqual(first, second);
            Assert.AreEqual(2, fetches);
        }

        [Test]
        public void LoginFailure_ForOldEndpointDoesNotFaultOrPopulateCache()
        {
            var section = CreateSection();
            UseHttp("remote");
            var response = new TaskCompletionSource<string>();
            Set(section, "loginUrlFetcher", new Func<string, Task<string>>(_ => response.Task));
            Task<string> pending = (Task<string>)Invoke(section, "GetLoginUrlAsync");
            HttpEndpointUtility.SaveRemoteBaseUrl("https://server-b.example");
            response.SetException(new InvalidOperationException("synthetic failure"));
            AssertCompleted(pending);
            Assert.IsNull(pending.Result);
            Assert.IsNull(Get<string>(section, "cachedLoginUrl"));
        }

        [Test]
        public void PersistedUrl_OnlyInvalidatesLaunchGenerationWhenChanged()
        {
            var section = CreateSection();
            UseHttp("local");
            var field = Get<TextField>(section, "httpUrlField");
            field.SetValueWithoutNotify(HttpEndpointUtility.GetBaseUrl());
            Invoke(section, "PersistHttpUrlFromField");
            Assert.AreEqual(0, Get<int>(section, "autoStartGeneration"));
            field.SetValueWithoutNotify("http://127.0.0.1:9090");
            Invoke(section, "PersistHttpUrlFromField");
            Assert.AreEqual(1, Get<int>(section, "autoStartGeneration"));
        }

        private static void UseHttp(string scope)
        {
            EditorConfigurationCache.Instance.SetUseHttpTransport(true);
            EditorConfigurationCache.Instance.SetHttpTransportScope(scope);
        }

        private static void ChangeContext(McpConnectionSection section, string change)
        {
            if (change == "stdio")
                EditorConfigurationCache.Instance.SetUseHttpTransport(false);
            else if (change == "remote")
                UseHttp("remote");
            else if (change == "url")
                HttpEndpointUtility.SaveLocalBaseUrl("http://127.0.0.1:9090");
            else
                Set(section, "autoStartGeneration", Get<int>(section, "autoStartGeneration") + 1);
        }

        private static IEnumerator Complete(Task pending)
        {
            double deadline = EditorApplication.timeSinceStartup + 3;
            while (!pending.IsCompleted && EditorApplication.timeSinceStartup < deadline)
                yield return null;
            AssertCompleted(pending);
        }

        private static void AssertCompleted(Task pending)
        {
            Assert.IsTrue(pending.IsCompleted, "Controlled operation did not finish.");
            pending.GetAwaiter().GetResult();
        }

        private static void ChangePort(TextField field, string value)
        {
            string previous = field.value;
            field.SetValueWithoutNotify(value);
            using (var evt = ChangeEvent<string>.GetPooled(previous, value))
            {
                evt.target = field;
                field.SendEvent(evt);
            }
        }

        private static McpConnectionSection CreateSection()
        {
            // Bypass initialization and provide controls/services without creating a real window.
            var section = (McpConnectionSection)FormatterServices.GetUninitializedObject(typeof(McpConnectionSection));
            var protocol = typeof(McpConnectionSection).GetNestedType("TransportProtocol", BindingFlags.NonPublic);
            var dropdown = new EnumField();
            dropdown.Init((Enum)Enum.Parse(protocol, "Stdio"));
            Set(section, "transportDropdown", dropdown);
            foreach (string name in new[] { "httpUrlRow", "httpServerControlRow", "unitySocketPortRow", "statusIndicator" })
                Set(section, name, new VisualElement());
            Set(section, "connectionStatusLabel", new Label());
            foreach (string name in new[] { "unityPortField", "httpUrlField" })
                Set(section, name, new TextField());
            Set(section, "connectionToggleButton", new Button());
            Set(section, "getApiKeyButton", new Button());
            Invoke(section, "RegisterCallbacks");
            return section;
        }

        private static T Get<T>(McpConnectionSection section, string name)
        {
            var field = typeof(McpConnectionSection).GetField(name, InstanceFlags | StaticFlags);
            return (T)field.GetValue(field.IsStatic ? null : section);
        }

        private static void Set(McpConnectionSection section, string name, object value)
        {
            var field = typeof(McpConnectionSection).GetField(name, InstanceFlags | StaticFlags);
            field.SetValue(field.IsStatic ? null : section, value);
        }

        private static object Invoke(McpConnectionSection section, string name) =>
            typeof(McpConnectionSection).GetMethod(name, InstanceFlags).Invoke(section, null);

        private sealed class FakeBridge : IBridgeControlService
        {
            public bool Running;
            public int StartCalls,
                StopCalls,
                VerifyCalls;
            public TaskCompletionSource<bool> StartPending;
            public TaskCompletionSource<BridgeVerificationResult> VerifyPending;
            public bool IsRunning => Running;
            public int CurrentPort => 1234;
            public bool IsAutoConnectMode => false;
            public TransportMode? ActiveMode => TransportMode.Http;

            public Task<bool> StartAsync()
            {
                StartCalls++;
                Running = true;
                return StartPending?.Task ?? Task.FromResult(true);
            }

            public Task StopAsync()
            {
                StopCalls++;
                Running = false;
                return Task.CompletedTask;
            }

            public BridgeVerificationResult Verify(int port) => Healthy();

            public Task<BridgeVerificationResult> VerifyAsync()
            {
                VerifyCalls++;
                return VerifyPending?.Task ?? Task.FromResult(Healthy());
            }

            public static BridgeVerificationResult Healthy() =>
                new()
                {
                    Success = true,
                    PingSucceeded = true,
                    HandshakeValid = true,
                    Message = "memory-only",
                };
        }

        private sealed class FakeServer : IServerManagementService
        {
            public bool Reachable;
            public bool Alive = true;
            public int StopCalls;
            public bool HasManagedServerLaunchHandle => true;

            public bool ClearUvxCache() => false;

            public bool StartLocalHttpServer(bool quiet = false) => false;

            public string GetLocalHttpServerLaunchLogPath() => null;

            public bool IsManagedServerLaunchProcessAlive() => Alive;

            public void LogLocalHttpServerLaunchFailure()
            {
                throw new InvalidOperationException("Unexpected launch failure path.");
            }

            public bool StopLocalHttpServer()
            {
                StopCalls++;
                return true;
            }

            public bool StopManagedLocalHttpServer()
            {
                StopCalls++;
                return true;
            }

            public bool IsLocalHttpServerRunning() => Reachable;

            public bool IsLocalHttpServerReachable() => Reachable;

            public bool TryGetLocalHttpServerCommand(out string command, out string error)
            {
                command = null;
                error = "memory-only";
                return false;
            }

            public bool IsLocalUrl() => true;

            public bool CanStartLocalServer() => true;
        }
    }
}
