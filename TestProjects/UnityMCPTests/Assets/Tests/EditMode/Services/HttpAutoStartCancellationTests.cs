using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class HttpAutoStartCancellationTests : TransportPreferenceTestBase
    {
        private const BindingFlags StaticFlags = BindingFlags.NonPublic | BindingFlags.Static;
        private readonly Dictionary<string, object> savedServices = new();
        private bool hadAutoStart;
        private bool savedAutoStart;
        private bool savedPending;
        private bool hadPending;
        private bool savedLatch;
        private bool hadLatch;
        private bool hadScope;
        private string savedScope;
        private bool hadRemoteUrl;
        private string savedRemoteUrl;
        private EditorApplication.CallbackFunction editorReadyCallback;
        private bool hadEditorReadyCallback;
        private bool ownsState;
        private FakeServer server;
        private FakeBridge bridge;
        private readonly List<Task> pendingTasks = new();

        [SetUp]
        public void SetUp()
        {
            // These service-swapping tests require an idle auto-start/reload owner.
            if (SessionState.GetBool(HttpAutoStartHandler.ConnectPendingKey, false) || HttpBridgeReloadHandler.IsResumePending)
                Assert.Ignore("An existing HTTP recovery is pending; do not replace its services.");
            editorReadyCallback = (EditorApplication.CallbackFunction)
                Delegate.CreateDelegate(typeof(EditorApplication.CallbackFunction), typeof(HttpAutoStartHandler).GetMethod("WaitForEditorReady", StaticFlags));
            hadEditorReadyCallback = Array.Exists(
                EditorApplication.update?.GetInvocationList() ?? Array.Empty<Delegate>(),
                entry => entry.Equals(editorReadyCallback)
            );

            foreach (string name in new[] { "_bridgeService", "_serverManagementService", "_transportManager" })
                savedServices[name] = typeof(MCPServiceLocator).GetField(name, StaticFlags).GetValue(null);
            hadAutoStart = EditorPrefs.HasKey(EditorPrefKeys.AutoStartOnLoad);
            savedAutoStart = EditorPrefs.GetBool(EditorPrefKeys.AutoStartOnLoad, false);
            hadScope = EditorPrefs.HasKey(EditorPrefKeys.HttpTransportScope);
            savedScope = EditorPrefs.GetString(EditorPrefKeys.HttpTransportScope);
            hadRemoteUrl = EditorPrefs.HasKey(EditorPrefKeys.HttpRemoteBaseUrl);
            savedRemoteUrl = EditorPrefs.GetString(EditorPrefKeys.HttpRemoteBaseUrl);
            savedPending = SessionState.GetBool(HttpAutoStartHandler.ConnectPendingKey, false);
            hadPending = savedPending == SessionState.GetBool(HttpAutoStartHandler.ConnectPendingKey, true);
            savedLatch = SessionState.GetBool(HttpAutoStartHandler.SessionInitKey, false);
            hadLatch = savedLatch == SessionState.GetBool(HttpAutoStartHandler.SessionInitKey, true);
            ownsState = true;
            HttpAutoStartHandler.CancelPendingReconnect();
            SessionState.SetBool(HttpAutoStartHandler.SessionInitKey, true);
            SessionState.SetBool(HttpAutoStartHandler.ConnectPendingKey, true);
            EditorPrefs.SetBool(EditorPrefKeys.AutoStartOnLoad, true);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
            EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "https://synthetic-reconnect.example");
            EditorConfigurationCache.Instance.Refresh();
            server = new FakeServer();
            bridge = new FakeBridge();
            MCPServiceLocator.Register<IServerManagementService>(server);
            MCPServiceLocator.Register<IBridgeControlService>(bridge);
            MCPServiceLocator.Register(new TransportManager());
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            if (!ownsState)
                yield break;
            HttpAutoStartHandler.CancelPendingReconnect();
            foreach (var gate in bridge.Gates)
                gate.TrySetResult(false);
            foreach (Task task in pendingTasks)
                yield return Complete(task);
            if (hadAutoStart)
                EditorPrefs.SetBool(EditorPrefKeys.AutoStartOnLoad, savedAutoStart);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.AutoStartOnLoad);
            if (hadScope)
                EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, savedScope);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.HttpTransportScope);
            if (hadRemoteUrl)
                EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, savedRemoteUrl);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.HttpRemoteBaseUrl);
            RestoreFlag(HttpAutoStartHandler.ConnectPendingKey, hadPending, savedPending);
            RestoreFlag(HttpAutoStartHandler.SessionInitKey, hadLatch, savedLatch);
            foreach (var entry in savedServices)
                typeof(MCPServiceLocator).GetField(entry.Key, StaticFlags).SetValue(null, entry.Value);
            EditorConfigurationCache.Instance.Refresh();
            EditorApplication.update -= editorReadyCallback;
            if (hadEditorReadyCallback)
                EditorApplication.update += editorReadyCallback;
            savedServices.Clear();
            pendingTasks.Clear();
            ownsState = false;
        }

        [Test]
        public void CancelPendingReconnect_RemovesEditorReadySubscription()
        {
            var callback = (EditorApplication.CallbackFunction)
                Delegate.CreateDelegate(typeof(EditorApplication.CallbackFunction), typeof(HttpAutoStartHandler).GetMethod("WaitForEditorReady", StaticFlags));
            EditorApplication.update -= callback;
            EditorApplication.update += callback;
            HttpAutoStartHandler.CancelPendingReconnect();
            Assert.IsFalse(Array.Exists(EditorApplication.update?.GetInvocationList() ?? Array.Empty<Delegate>(), entry => entry.Equals(callback)));
        }

        [UnityTest]
        public IEnumerator CancelPendingReconnect_StopsLocalPollingAndLateConnect()
        {
            Task task = Reconnect();
            Assert.AreEqual(1, server.Polls);
            HttpAutoStartHandler.CancelPendingReconnect();
            server.Reachable = () => true;
            yield return Complete(task);
            Assert.AreEqual(1, server.Polls, "canceled waiter must not query the server again");
            Assert.AreEqual(0, bridge.StartCalls, "server appearing after cancellation must not reconnect");
        }

        [UnityTest]
        public IEnumerator CancelDuringReachabilityReturn_DoesNotStartBridge()
        {
            server.Reachable = () =>
            {
                HttpAutoStartHandler.CancelPendingReconnect();
                return true;
            };
            yield return Complete(Reconnect());
            Assert.AreEqual(0, bridge.StartCalls);
        }

        [UnityTest]
        public IEnumerator OldConnectCompletion_PreservesNewPendingReconnect()
        {
            EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "remote");
            EditorConfigurationCache.Instance.Refresh();
            var oldGate = new TaskCompletionSource<bool>();
            bridge.Gates.Add(oldGate);
            bridge.Pending = oldGate.Task;
            Task old = Reconnect();
            HttpAutoStartHandler.CancelPendingReconnect();
            SessionState.SetBool(HttpAutoStartHandler.ConnectPendingKey, true);
            var newGate = new TaskCompletionSource<bool>();
            bridge.Gates.Add(newGate);
            bridge.Pending = newGate.Task;
            Task next = Reconnect();
            oldGate.SetResult(true);
            yield return Complete(old);
            Assert.IsTrue(SessionState.GetBool(HttpAutoStartHandler.ConnectPendingKey, false));
            newGate.SetResult(false);
            yield return Complete(next);
            Assert.IsFalse(SessionState.GetBool(HttpAutoStartHandler.ConnectPendingKey, false));
            Assert.AreEqual(2, bridge.StartCalls);
        }

        private Task Reconnect()
        {
            var task = (Task)typeof(HttpAutoStartHandler).GetMethod("ReconnectAsync", StaticFlags).Invoke(null, null);
            pendingTasks.Add(task);
            return task;
        }

        private static IEnumerator Complete(Task task)
        {
            double deadline = EditorApplication.timeSinceStartup + 5;
            while (!task.IsCompleted && EditorApplication.timeSinceStartup < deadline)
                yield return null;
            Assert.IsTrue(task.IsCompleted, "synthetic reconnect did not finish within five seconds");
            Assert.IsFalse(task.IsFaulted, task.Exception?.ToString());
        }

        private static void RestoreFlag(string key, bool existed, bool value)
        {
            if (existed)
                SessionState.SetBool(key, value);
            else
                SessionState.EraseBool(key);
        }

        private sealed class FakeBridge : IBridgeControlService
        {
            public int StartCalls;
            public Task<bool> Pending;
            public readonly List<TaskCompletionSource<bool>> Gates = new();
            public bool IsRunning => false;
            public int CurrentPort => 0;
            public bool IsAutoConnectMode => false;
            public TransportMode? ActiveMode => null;

            public Task<bool> StartAsync()
            {
                StartCalls++;
                return Pending ?? Task.FromResult(true);
            }

            public Task StopAsync() => Task.CompletedTask;

            public BridgeVerificationResult Verify(int port) => new();

            public Task<BridgeVerificationResult> VerifyAsync() => Task.FromResult(new BridgeVerificationResult());
        }

        private sealed class FakeServer : IServerManagementService
        {
            public int Polls;
            public Func<bool> Reachable = () => false;
            public bool HasManagedServerLaunchHandle => false;

            public bool IsLocalHttpServerReachable()
            {
                Polls++;
                return Reachable();
            }

            public bool IsManagedServerLaunchProcessAlive() => true;

            public bool StartLocalHttpServer(bool quiet = false) => throw new InvalidOperationException("Reconnect must never launch a server.");

            public void LogLocalHttpServerLaunchFailure() => throw new InvalidOperationException("Unexpected launch failure.");

            public bool ClearUvxCache() => false;

            public string GetLocalHttpServerLaunchLogPath() => null;

            public bool StopLocalHttpServer() => false;

            public bool StopManagedLocalHttpServer() => false;

            public bool IsLocalHttpServerRunning() => false;

            public bool TryGetLocalHttpServerCommand(out string command, out string error)
            {
                command = error = null;
                return false;
            }

            public bool IsLocalUrl() => true;

            public bool CanStartLocalServer() => false;
        }
    }
}
