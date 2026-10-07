using System;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Setup;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class EditorWindowLifecycleTests
    {
        private const BindingFlags InstanceFlags = BindingFlags.NonPublic | BindingFlags.Instance;
        private const BindingFlags StaticFlags = BindingFlags.NonPublic | BindingFlags.Static;

        [Test]
        public void SetupWindowService_DoesNotRegisterAutomaticEditorStartup()
        {
            Assert.IsFalse(Attribute.IsDefined(typeof(SetupWindowService), typeof(InitializeOnLoadAttribute)));
            Assert.IsNull(typeof(SetupWindowService).TypeInitializer);
            Assert.IsNotNull(typeof(SetupWindowService).GetMethod(nameof(SetupWindowService.ShowSetupWindow)));
        }

        [TestCase(TransportMode.Http)]
        [TestCase(TransportMode.Stdio)]
        public void MainWindow_DisablePreservesRunningBridgeAndServer(TransportMode mode)
        {
            var bridgeField = typeof(MCPServiceLocator).GetField("_bridgeService", StaticFlags);
            var serverField = typeof(MCPServiceLocator).GetField("_serverManagementService", StaticFlags);
            var previousBridge = bridgeField.GetValue(null);
            var previousServer = serverField.GetValue(null);
            var bridge = new RunningBridge(mode);
            var server = new RunningServer();
            MCPServiceLocator.Register<IBridgeControlService>(bridge);
            MCPServiceLocator.Register<IServerManagementService>(server);
            MCPForUnityEditorWindow window = null;
            try
            {
                window = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
                Invoke(window, "QueueUpdateCheck");
                Assert.IsTrue((bool)typeof(MCPForUnityEditorWindow).GetField("updateCheckQueued", InstanceFlags).GetValue(window));

                Invoke(window, "OnDisable");
                Assert.IsFalse((bool)typeof(MCPForUnityEditorWindow).GetField("updateCheckQueued", InstanceFlags).GetValue(window));
                UnityEngine.Object.DestroyImmediate(window);
                window = null;

                Assert.AreEqual(0, bridge.StopCalls);
                Assert.AreEqual(0, bridge.DisposeCalls);
                Assert.AreEqual(0, server.StopCalls);
                Assert.AreEqual(0, server.DisposeCalls);
                Assert.IsTrue(bridge.IsRunning);
                Assert.IsTrue(server.IsManagedServerLaunchProcessAlive());
                Assert.AreSame(bridge, bridgeField.GetValue(null));
                Assert.AreSame(server, serverField.GetValue(null));
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                bridgeField.SetValue(null, previousBridge);
                serverField.SetValue(null, previousServer);
            }
        }

        [TestCase("Clients", "clients")]
        [TestCase("Deps", "deps")]
        [TestCase("Advanced", "advanced")]
        [TestCase("Tools", "tools")]
        [TestCase("Resources", "resources")]
        [TestCase("AssetGen", "assetgen")]
        [TestCase("Validation", "deps")]
        [TestCase("999", "clients")]
        [TestCase("invalid", "clients")]
        public void Tabs_AlwaysKeepOneSelectedPanel(string savedPanel, string expectedName)
        {
            bool hadPreference = EditorPrefs.HasKey(EditorPrefKeys.EditorWindowActivePanel);
            string previous = EditorPrefs.GetString(EditorPrefKeys.EditorWindowActivePanel);
            var window = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
            try
            {
                EditorPrefs.SetString(EditorPrefKeys.EditorWindowActivePanel, savedPanel);
                string[] names = { "clients", "deps", "advanced", "tools", "resources", "assetgen" };
                string[] fields = { "clientsPanel", "depsPanel", "advancedPanel", "toolsPanel", "resourcesPanel", "assetGenPanel" };
                for (int i = 0; i < names.Length; i++)
                {
                    var panel = new VisualElement { name = names[i] + "-panel" };
                    panel.AddToClassList("hidden");
                    window.rootVisualElement.Add(panel);
                    window.rootVisualElement.Add(new ToolbarToggle { name = names[i] + "-tab" });
                    typeof(MCPForUnityEditorWindow).GetField(fields[i], InstanceFlags).SetValue(window, panel);
                }

                Invoke(window, "SetupTabs");
                var selected = window.rootVisualElement.Q<ToolbarToggle>(expectedName + "-tab");
                Assert.IsTrue(selected.value);
                selected.value = false;
                Assert.IsTrue(selected.value, "Clicking the active tab must not clear its selection.");
                foreach (string name in names)
                {
                    Assert.AreEqual(name != expectedName, window.rootVisualElement.Q<VisualElement>(name + "-panel").ClassListContains("hidden"));
                }

                var next = window.rootVisualElement.Q<ToolbarToggle>("advanced-tab");
                next.value = true;
                Assert.IsTrue(next.value);
                Assert.IsFalse(window.rootVisualElement.Q<VisualElement>("advanced-panel").ClassListContains("hidden"));
                Assert.AreEqual("Advanced", EditorPrefs.GetString(EditorPrefKeys.EditorWindowActivePanel));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
                if (hadPreference)
                    EditorPrefs.SetString(EditorPrefKeys.EditorWindowActivePanel, previous);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.EditorWindowActivePanel);
            }
        }

        private static void Invoke(MCPForUnityEditorWindow window, string name)
        {
            typeof(MCPForUnityEditorWindow).GetMethod(name, InstanceFlags).Invoke(window, null);
        }

        private sealed class RunningBridge : IBridgeControlService, IDisposable
        {
            public RunningBridge(TransportMode mode)
            {
                ActiveMode = mode;
            }

            public int StopCalls { get; private set; }
            public int DisposeCalls { get; private set; }
            public bool IsRunning => StopCalls == 0 && DisposeCalls == 0;
            public int CurrentPort => 6400;
            public bool IsAutoConnectMode => false;
            public TransportMode? ActiveMode { get; }

            public Task<bool> StartAsync() => Task.FromResult(true);

            public Task StopAsync()
            {
                StopCalls++;
                return Task.CompletedTask;
            }

            public BridgeVerificationResult Verify(int port) => new BridgeVerificationResult { Success = true };

            public Task<BridgeVerificationResult> VerifyAsync() => Task.FromResult(Verify(CurrentPort));

            public void Dispose()
            {
                DisposeCalls++;
            }
        }

        private sealed class RunningServer : IServerManagementService, IDisposable
        {
            public int StopCalls { get; private set; }
            public int DisposeCalls { get; private set; }
            public bool HasManagedServerLaunchHandle => true;

            public bool ClearUvxCache() => true;

            public bool StartLocalHttpServer(bool quiet = false) => true;

            public string GetLocalHttpServerLaunchLogPath() => null;

            public bool IsManagedServerLaunchProcessAlive() => StopCalls == 0 && DisposeCalls == 0;

            public void LogLocalHttpServerLaunchFailure() { }

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

            public bool IsLocalHttpServerRunning() => IsManagedServerLaunchProcessAlive();

            public bool IsLocalHttpServerReachable() => IsManagedServerLaunchProcessAlive();

            public bool TryGetLocalHttpServerCommand(out string command, out string error)
            {
                command = "test-server";
                error = null;
                return true;
            }

            public bool IsLocalUrl() => true;

            public bool CanStartLocalServer() => true;

            public void Dispose()
            {
                DisposeCalls++;
            }
        }
    }
}
