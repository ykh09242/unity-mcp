using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>Opt-in native peer. Every preference and journal belongs to the controller's fresh directory.</summary>
    [InitializeOnLoad]
    public static class NativePreferenceLeasePeer
    {
        private static string root;
        private static string peerDirectory;
        private static string nonce;
        private static EditorPreferenceLease lease;
        private static int pid;
        private static long started;
        private static int lastCommand;
        private static int refreshes;
        private static double deadline;
        private static WebSocketTransportClient transport;
        private static Task<bool> connectTask;
        private static Task disconnectTask;
        private static string clientScenePath;
        private static bool readOnlyDiscovery;

        static NativePreferenceLeasePeer()
        {
            if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_LEASE_ROOT")))
                return;
            InitializeOwnedPreferences();
        }

        private static void InitializeOwnedPreferences()
        {
            root = Path.GetFullPath(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_LEASE_ROOT"));
            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), root);
            nonce = Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_LEASE_NONCE");
            string peer = Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_LEASE_PEER");
            var marker = JObject.Parse(File.ReadAllText(Path.Combine(root, "owner.json")));
            string project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            if (
                marker.Value<string>("schema") != "unity-mcp-native-preference-lease-v1"
                || string.IsNullOrEmpty(nonce)
                || marker.Value<string>("nonce") != nonce
                || (peer != "a" && peer != "b")
                || !string.Equals(Path.GetFullPath(marker.Value<string>("project_" + peer)), project, StringComparison.OrdinalIgnoreCase)
            )
                throw new IOException("Native lease peer ownership differs.");
            peerDirectory = SafePathUtility.ResolveWithinRoot(root, peer);
            Directory.CreateDirectory(peerDirectory);
            // This executes before delayed startup recovery or test callbacks. No EditorPrefs call is used.
            TestRunnerNoThrottle.LeaseRootForTests = Path.Combine(root, "background");
            TestRunnerNoThrottle.ReadPreference = ReadPreference;
            TestRunnerNoThrottle.WritePreference = WritePreference;
            TestRunnerNoThrottle.ApplyInteractionPreferences = () => { };
            using var process = Process.GetCurrentProcess();
            pid = process.Id;
            started = process.StartTime.ToUniversalTime().Ticks;
            var alive =
                (Func<int, long, bool>)
                    Delegate.CreateDelegate(
                        typeof(Func<int, long, bool>),
                        typeof(TestRunnerNoThrottle).GetMethod("OwnerIsAlive", BindingFlags.NonPublic | BindingFlags.Static)
                    );
            lease = new EditorPreferenceLease(Path.Combine(root, "lease"), pid, started, alive, ReadPreference, WritePreference, () => refreshes++);
        }

        public static void Run()
        {
            if (lease == null)
                throw new IOException("Native peer requires explicit owned environment.");
            lease.Recover();
            WriteJson(Path.Combine(peerDirectory, "ready.json"), Snapshot());
            deadline = EditorApplication.timeSinceStartup + 900;
            EditorApplication.update += Tick;
            EditorApplication.quitting += lease.Release;
        }

        private static void Tick()
        {
            if (EditorApplication.timeSinceStartup >= deadline)
            {
                lease.Release();
                EditorApplication.Exit(2);
                return;
            }
            string path = Path.Combine(peerDirectory, "command.json");
            if (!File.Exists(path))
                return;
            if (!TryReadCommand(path, out var command))
                return;
            int id = command.Value<int>("id");
            if (id <= lastCommand || command.Value<string>("nonce") != nonce)
                return;
            lastCommand = id;
            Exception failure = null;
            string operation = command.Value<string>("operation");
            try
            {
                switch (operation)
                {
                    case "acquire":
                        lease.Acquire();
                        break;
                    case "release":
                    case "quit":
                        if (operation == "quit" && transport != null && (transport.IsConnected || disconnectTask?.IsCompleted == false))
                            throw new IOException("Disconnect the owned client transport before graceful exit.");
                        lease.Release();
                        break;
                    case "connect":
                        if (transport != null)
                            throw new IOException("Owned transport already initialized.");
                        string authPath = SafePathUtility.ResolveWithinRoot(root, command.Value<string>("auth_token_file"));
                        var endpoint = new Uri(command.Value<string>("ws_endpoint"));
                        var options = new WebSocketTransportClient.OwnedConnectionOptions(
                            endpoint,
                            File.ReadAllText(authPath).Trim(),
                            ProjectIdentityUtility.GetProjectName(),
                            ProjectIdentityUtility.GetProjectHash(),
                            Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
                            Application.unityVersion
                        );
                        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                        clientScenePath =
                            "Assets/McpNativeLeaseClient_" + nonce + "_" + Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_LEASE_PEER") + ".unity";
                        if (!EditorSceneManager.SaveScene(scene, clientScenePath))
                            throw new IOException("Owned synthetic client scene could not be saved.");
                        var discovery = CreateReadOnlyDiscovery();
                        MCPServiceLocator.Register<IToolDiscoveryService>(discovery);
                        readOnlyDiscovery = true;
                        transport = new WebSocketTransportClient(discovery, options);
                        connectTask = transport.StartAsync();
                        break;
                    case "disconnect":
                        disconnectTask = transport?.StopAsync() ?? Task.CompletedTask;
                        break;
                    case "status":
                        break;
                    case "recover":
                        lease.Recover();
                        break;
                    case "manual":
                        WritePreference("ApplicationIdleTime", command.Value<int>("idle"));
                        WritePreference("InteractionMode", command.Value<int>("mode"));
                        break;
                    case "stale-start":
                        string statePath = Path.Combine(root, "lease", "state.json");
                        var state = JObject.Parse(File.ReadAllText(statePath));
                        var owners = (JArray)state["Owners"];
                        if (owners.Count != 1 || owners[0].Value<int>("Pid") != pid || owners[0].Value<long>("Started") != started)
                            throw new IOException("Identity test requires only this live owner.");
                        owners[0]["Started"] = started - 1;
                        WriteJson(statePath, state);
                        lease.Recover();
                        break;
                    default:
                        throw new IOException("Unknown native peer operation.");
                }
            }
            catch (Exception exception)
            {
                // The controller treats every error as failure and retains the observation.
                failure = exception;
            }
            var result = Snapshot();
            result["id"] = id;
            result["operation"] = operation;
            result["error"] = failure?.ToString();
            WriteJson(Path.Combine(peerDirectory, "reply-" + id + ".json"), result);
            if (operation == "quit")
                EditorApplication.Exit(failure == null ? 0 : 1);
        }

        private static JObject Snapshot()
        {
            string statePath = Path.Combine(root, "lease", "state.json");
            var state = File.Exists(statePath) ? JObject.Parse(File.ReadAllText(statePath)) : null;
            return new JObject
            {
                ["pid"] = pid,
                ["started_ticks"] = started,
                ["unity_version"] = Application.unityVersion,
                ["package_path"] = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(EditorPreferenceLease).Assembly)?.resolvedPath,
                ["nonce"] = nonce,
                ["preferences"] = JObject.Parse(File.ReadAllText(Path.Combine(root, "preferences.json"))),
                ["owners"] = state?["Owners"] ?? new JArray(),
                ["phase"] = state?["Phase"],
                ["refreshes"] = refreshes,
                ["project_id"] = ProjectIdentityUtility.GetProjectName() + "@" + ProjectIdentityUtility.GetProjectHash(),
                ["active_scene"] = SceneManager.GetActiveScene().name,
                ["client_scene_path"] = clientScenePath,
                ["transport_connected"] = transport?.IsConnected ?? false,
                ["transport_connect_completed"] = connectTask?.IsCompleted ?? false,
                ["transport_disconnect_completed"] = disconnectTask?.IsCompleted ?? false,
                ["transport_failed"] = connectTask?.IsFaulted == true || disconnectTask?.IsFaulted == true,
                ["discovery_preference_initialization_bypassed"] = readOnlyDiscovery,
                ["error"] = null,
            };
        }

        private static int ReadPreference(string key, int fallback) =>
            JObject.Parse(File.ReadAllText(Path.Combine(root, "preferences.json"))).Value<int?>(key) ?? fallback;

        internal static bool TryReadCommand(string path, out JObject command)
        {
            command = null;
            try
            {
                using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                using var text = new StreamReader(input);
                command = JObject.Parse(text.ReadToEnd());
                return true;
            }
            catch (FileNotFoundException)
            {
                return false;
            }
            catch (IOException exception) when ((exception.HResult & 0xFFFF) == 32 || (exception.HResult & 0xFFFF) == 33)
            {
                // A competing atomic publisher owns this path briefly; the controller's deadline bounds retry.
                return false;
            }
        }

        private static ToolDiscoveryService CreateReadOnlyDiscovery()
        {
            // Use actual indexed types and the production classifier/handler validation. Skip only
            // EnsurePreferenceInitialized, which would otherwise create missing global defaults.
            var discovery = new ToolDiscoveryService();
            const BindingFlags hidden = BindingFlags.Instance | BindingFlags.NonPublic;
            typeof(ToolDiscoveryService).GetField("_cachedTools", hidden).SetValue(discovery, new Dictionary<string, ToolMetadata>());
            foreach (var type in ToolDiscoveryService.InRegistrationOrder(TypeCache.GetTypesWithAttribute<McpForUnityToolAttribute>()))
            {
                var attribute = type.GetCustomAttribute<McpForUnityToolAttribute>();
                if (attribute != null)
                    discovery.ApplyDiagnosticCandidate(type, attribute);
            }
            ((ToolDiscoveryDiagnostics)typeof(ToolDiscoveryService).GetField("_diagnostics", hidden).GetValue(discovery)).Complete();
            return discovery;
        }

        private static void WritePreference(string key, int value)
        {
            string path = Path.Combine(root, "preferences.json");
            var preferences = JObject.Parse(File.ReadAllText(path));
            preferences[key] = value;
            WriteJson(path, preferences);
        }

        private static void WriteJson(string path, JObject value)
        {
            string stage = path + ".stage";
            File.WriteAllText(stage, value.ToString(Formatting.Indented));
            if (File.Exists(path))
                File.Replace(stage, path, null);
            else
                File.Move(stage, path);
        }
    }

    public class NativePreferenceLeasePeerTests
    {
        [Test]
        public void ExclusiveCommandPublisher_IsPendingThenReadsAfterRelease()
        {
            string directory = SafePathUtility.ResolveWithinRoot(Application.temporaryCachePath, "McpNativeCommandRead/" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "command.json");
            try
            {
                File.WriteAllText(path, "{\"id\":42}");
                using (var publisher = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                    Assert.IsFalse(
                        NativePreferenceLeasePeer.TryReadCommand(path, out _),
                        "An in-flight atomic publication must be retried without an Editor exception."
                    );
                Assert.IsTrue(NativePreferenceLeasePeer.TryReadCommand(path, out var command));
                Assert.AreEqual(42, command.Value<int>("id"));
                File.WriteAllText(path, "{invalid-json");
                Assert.Throws<JsonReaderException>(
                    () => NativePreferenceLeasePeer.TryReadCommand(path, out _),
                    "Malformed commands must not become pending successes."
                );
            }
            finally
            {
                SafePathUtility.ResolveWithinRoot(Application.temporaryCachePath, directory);
                File.Delete(path);
                Directory.Delete(directory);
            }
        }
    }
}
