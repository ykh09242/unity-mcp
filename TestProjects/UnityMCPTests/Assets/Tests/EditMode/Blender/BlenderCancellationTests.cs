using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Blender;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Blender;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Blender
{
    // Only owned loopback sockets, temporary files, an additive scene and GUID Assets are used.
    // No consent/endpoint/global preferences or real Blender/addon processes are touched.
    public class BlenderCancellationTests
    {
        private const BindingFlags PrivateStatic = BindingFlags.NonPublic | BindingFlags.Static;
        private static readonly FieldInfo DiscoveryField = typeof(MCPServiceLocator).GetField("_toolDiscoveryService", PrivateStatic);
        private static readonly FieldInfo DispatcherContext = typeof(TransportCommandDispatcher).GetField("_mainThreadContext", PrivateStatic);
        private static readonly FieldInfo DispatcherThread = typeof(TransportCommandDispatcher).GetField("_mainThreadId", PrivateStatic);
        private static readonly FieldInfo HandlersField = typeof(CommandRegistry).GetField("_handlers", PrivateStatic);
        private readonly PumpContext context = new PumpContext();
        private readonly List<LoopbackAddon> peers = new List<LoopbackAddon>();
        private readonly List<CancellationTokenSource> cancellations = new List<CancellationTokenSource>();
        private readonly List<Task> settlements = new List<Task>();
        private readonly Dictionary<string, object> replacedHandlers = new Dictionary<string, object>();
        private Func<bool> previousConsent;
        private Func<BlenderEndpoint> previousEndpoint;
        private object previousDiscovery;
        private object previousContext;
        private object previousThread;
        private UnityEngine.Object previousSelection;
        private Scene previousScene;
        private Scene scene;
        private string unique;
        private string root;
        private string rootGuid;
        private string baselineRoot;
        private string baselineRootGuid;
        private string baselinePath;
        private bool baselineSaved;
        private IDictionary handlers;

        [SetUp]
        public void SetUp()
        {
            previousScene = SceneManager.GetActiveScene();
            previousSelection = Selection.activeObject;
            previousConsent = BlenderBridgeTool.ConsentGrantedOverride;
            previousEndpoint = BlenderBridgeTool.EndpointOverride;
            previousDiscovery = DiscoveryField.GetValue(null);
            previousContext = DispatcherContext.GetValue(null);
            previousThread = DispatcherThread.GetValue(null);
            scene = default;
            root = null;
            rootGuid = null;
            baselineRoot = null;
            baselineRootGuid = null;
            baselinePath = null;
            baselineSaved = false;
            Assert.IsFalse(EditorApplication.isPlayingOrWillChangePlaymode, "The fixture requires stable Edit Mode.");
            Assert.LessOrEqual(SceneManager.sceneCount, 32, "Unexpected initial scene count; preserve the existing setup.");
            var loaded = Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).Where(value => value.isLoaded).ToArray();
            bool hasUntitled = loaded.Any(value => string.IsNullOrEmpty(value.path));
            bool emptyUntitled =
                SceneManager.sceneCount == 1
                && previousScene.IsValid()
                && previousScene.isLoaded
                && string.IsNullOrEmpty(previousScene.path)
                && previousScene.rootCount == 0
                && !previousScene.isDirty;
            string initialScenes = string.Join(
                "; ",
                loaded.Select(value => $"handle={value.handle}, path={value.path}, roots={value.rootCount}, dirty={value.isDirty}")
            );
            Assert.IsTrue(!hasUntitled || emptyUntitled, "Untitled scene content or changes are preserved; fixture setup refused. " + initialScenes);
            unique = "gen08_" + Guid.NewGuid().ToString("N");
            root = "Assets/" + unique;
            string fullRoot = Path.Combine(Application.dataPath, unique);
            Assert.IsFalse(Directory.Exists(fullRoot) || File.Exists(fullRoot) || File.Exists(fullRoot + ".meta"), "The owned folder must be fresh.");
            rootGuid = AssetDatabase.CreateFolder("Assets", unique);
            Assert.IsFalse(string.IsNullOrEmpty(rootGuid));
            Assert.AreEqual(root, AssetDatabase.GUIDToAssetPath(rootGuid));
            if (emptyUntitled)
            {
                // Only the verified sole empty, clean scene may temporarily acquire an owned path.
                baselineRoot = "Assets/" + unique + "_scene";
                string fullBaselineRoot = Path.Combine(Application.dataPath, unique + "_scene");
                Assert.IsFalse(
                    Directory.Exists(fullBaselineRoot) || File.Exists(fullBaselineRoot) || File.Exists(fullBaselineRoot + ".meta"),
                    "The baseline folder must be fresh."
                );
                baselineRootGuid = AssetDatabase.CreateFolder("Assets", unique + "_scene");
                Assert.IsFalse(string.IsNullOrEmpty(baselineRootGuid));
                Assert.AreEqual(baselineRoot, AssetDatabase.GUIDToAssetPath(baselineRootGuid));
                baselinePath = baselineRoot + "/Baseline.unity";
                bool saved = EditorSceneManager.SaveScene(previousScene, baselinePath);
                baselineSaved = previousScene.path == baselinePath;
                Assert.IsTrue(saved && baselineSaved, "Could not name the owned blank baseline.");
            }
            scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(scene.IsValid() && scene.isLoaded, "The owned scene must be valid and loaded.");
            // NewScene can already make the owned scene active; require the state, not a transition.
            if (SceneManager.GetActiveScene().handle != scene.handle)
                SceneManager.SetActiveScene(scene);
            Assert.IsTrue(
                scene.IsValid() && scene.isLoaded && SceneManager.GetActiveScene().handle == scene.handle,
                "The owned scene must be active before the test starts."
            );
            BlenderBridgeTool.ConsentGrantedOverride = () => true;
            MCPServiceLocator.Register<IToolDiscoveryService>(new OwnedDiscovery());
            CommandRegistry.Initialize();
            handlers = (IDictionary)HandlersField.GetValue(null);
            DispatcherContext.SetValue(null, context);
            DispatcherThread.SetValue(null, Thread.CurrentThread.ManagedThreadId);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                foreach (var cancellation in cancellations)
                    cancellation.Cancel();
                foreach (var peer in peers)
                    peer.Dispose();
                var clock = Stopwatch.StartNew();
                while (settlements.Any(task => !task.IsCompleted) && clock.Elapsed < TimeSpan.FromSeconds(5))
                {
                    context.Drain();
                    Thread.Sleep(1);
                }
                context.Drain();
                Assert.IsTrue(settlements.All(task => task.IsCompleted), "No handler may outlive fixture cleanup.");
            }
            finally
            {
                BlenderAssetCreationCancellationProcessor.Cancel = null;
                BlenderAssetCreationCancellationProcessor.OwnedPrefix = null;
                BlenderImportCancellationPostprocessor.Cancel = null;
                BlenderImportCancellationPostprocessor.OwnedPrefix = null;
                foreach (var entry in replacedHandlers)
                {
                    if (entry.Value == null)
                        handlers.Remove(entry.Key);
                    else
                        handlers[entry.Key] = entry.Value;
                }
                BlenderBridgeTool.ConsentGrantedOverride = previousConsent;
                BlenderBridgeTool.EndpointOverride = previousEndpoint;
                DiscoveryField.SetValue(null, previousDiscovery);
                DispatcherContext.SetValue(null, previousContext);
                DispatcherThread.SetValue(null, previousThread);
                Selection.activeObject = previousSelection;
                bool scenesRestored = RestoreOwnedScenes();
                foreach (var peer in peers)
                    peer.DeleteOwnedFiles();
                foreach (var cancellation in cancellations)
                    cancellation.Dispose();
                peers.Clear();
                cancellations.Clear();
                settlements.Clear();
                replacedHandlers.Clear();
                Assert.IsTrue(scenesRestored, "Unexpected scene or asset identity was preserved instead of being discarded during cleanup.");
            }
        }

        private bool RestoreOwnedScenes()
        {
            if (previousScene.IsValid() && previousScene.isLoaded)
                SceneManager.SetActiveScene(previousScene);
            if (scene.IsValid() && scene.isLoaded)
                EditorSceneManager.CloseScene(scene, true);
            if (baselineSaved)
            {
                // Do not discard any unexpected content, changes or additional scene.
                if (
                    !previousScene.IsValid()
                    || !previousScene.isLoaded
                    || previousScene.path != baselinePath
                    || previousScene.rootCount != 0
                    || previousScene.isDirty
                    || SceneManager.sceneCount != 1
                    || SceneManager.GetSceneAt(0).handle != previousScene.handle
                )
                    return false;
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            }
            bool removedOutput = DeleteOwnedFolder(root, rootGuid);
            bool removedBaseline = DeleteOwnedFolder(baselineRoot, baselineRootGuid);
            return removedOutput && removedBaseline;
        }

        private static bool DeleteOwnedFolder(string path, string guid)
        {
            if (string.IsNullOrEmpty(guid))
                return true;
            if (AssetDatabase.AssetPathToGUID(path) != guid)
                return false;
            for (int i = 0; i < SceneManager.sceneCount; i++)
                if (SceneManager.GetSceneAt(i).path.StartsWith(path + "/", StringComparison.Ordinal))
                    return false;
            return AssetDatabase.DeleteAsset(path);
        }

        [UnityTest]
        public IEnumerator Dispatcher_CancelDelayedExport_NoLateAssetsOrPlacement()
        {
            var peer = NewPeer();
            var cancellation = NewCancellation();
            bool dirty = scene.isDirty;
            using var observed = new MutationObserver(unique);
            var operation = Dispatch("blender_bridge", ImportParameters(), cancellation.Token);
            yield return Until(() => peer.Request.Task.IsCompleted);
            Assert.AreEqual("export_scene", (string)peer.Request.Task.Result["type"]);
            cancellation.Cancel();
            yield return Until(() => operation.Response.IsCompleted);
            Assert.IsTrue(operation.Response.IsCanceled);
            peer.Release.Set(); // A remote export may still finish after caller cancellation.
            yield return Until(() => peer.Completion.IsCompleted && operation.Completion.IsCompleted);
            peer.AssertCompleted();
            yield return DrainWatcher();
            observed.AssertNoMutations();
            Assert.AreEqual(0, scene.rootCount);
            Assert.AreEqual(dirty, scene.isDirty);
            Assert.IsEmpty(Directory.GetFiles(Absolute(root), "*", SearchOption.AllDirectories));
        }

        [UnityTest]
        public IEnumerator UncancelledPublicImport_ImportsIndexedFbxAndPlacesInOwnedScene()
        {
            var peer = NewPeer();
            var operation = Dispatch("blender_bridge", ImportParameters(), CancellationToken.None);
            yield return Until(() => peer.Request.Task.IsCompleted);
            peer.Release.Set();
            yield return Until(() => operation.Completion.IsCompleted && peer.Completion.IsCompleted);
            peer.AssertCompleted();
            JObject response = JObject.FromObject(operation.Response.Result.Payload);
            Assert.AreEqual("success", (string)response["status"], response.ToString());
            JToken data = response["result"]?["data"] ?? response["data"];
            Assert.IsNotNull(data, response.ToString());
            string path = (string)data["asset_path"];
            Assert.That(path, Does.StartWith(root + "/"));
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Assert.IsNotNull(model);
            Assert.IsTrue(model.GetComponentsInChildren<MeshFilter>(true).Any(filter => filter.sharedMesh != null && filter.sharedMesh.GetIndexCount(0) > 0));
            Assert.AreEqual(1, scene.rootCount);
            Assert.AreEqual(scene, scene.GetRootGameObjects()[0].scene);
            Assert.IsTrue((bool)data["placed"]);
        }

        [UnityTest]
        public IEnumerator CancelAfterReplyBeforeUnityContinuation_DoesNotStage()
        {
            var peer = NewPeer();
            var cancellation = NewCancellation();
            using var observed = new MutationObserver(unique);
            Task<object> command = Invoke(ImportParameters(), cancellation.Token);
            yield return Until(() => peer.Request.Task.IsCompleted);
            peer.Release.Set();
            yield return Until(() => peer.Completion.IsCompleted && context.Count > 0, pump: false);
            cancellation.Cancel();
            yield return Until(() => command.IsCompleted);
            AssertCanceled(command, cancellation.Token);
            yield return DrainWatcher();
            observed.AssertNoMutations();
            Assert.AreEqual(0, scene.rootCount);
        }

        [UnityTest]
        public IEnumerator CancelDuringSynchronousImport_PreservesAssetButSkipsPlacement()
        {
            var peer = NewPeer();
            var cancellation = NewCancellation();
            BlenderImportCancellationPostprocessor.OwnedPrefix = root + "/";
            BlenderImportCancellationPostprocessor.Cancel = cancellation.Cancel;
            Task<object> command = Invoke(ImportParameters(), cancellation.Token);
            yield return Until(() => peer.Request.Task.IsCompleted);
            peer.Release.Set();
            yield return Until(() => command.IsCompleted && peer.Completion.IsCompleted);
            Assert.IsTrue(cancellation.IsCancellationRequested, "The real import callback must execute.");
            AssertCanceled(command, cancellation.Token);
            string[] models = AssetDatabase.FindAssets("t:GameObject", new[] { root });
            Assert.IsNotEmpty(models, "An admitted synchronous import is retained, not rolled back.");
            Assert.IsTrue(
                models
                    .Select(AssetDatabase.GUIDToAssetPath)
                    .Select(AssetDatabase.LoadAssetAtPath<GameObject>)
                    .Any(model =>
                        model != null
                        && model.GetComponentsInChildren<MeshFilter>(true).Any(filter => filter.sharedMesh != null && filter.sharedMesh.vertexCount > 0)
                    )
            );
            Assert.AreEqual(0, scene.rootCount);
        }

        [UnityTest]
        public IEnumerator Batch_CancelDelayedExport_DoesNotStartFollowingCommand()
        {
            int following = 0;
            ReplaceHandler(
                "gen08_following",
                _ =>
                {
                    following++;
                    return new { success = true };
                }
            );
            var peer = NewPeer();
            var cancellation = NewCancellation();
            using var observed = new MutationObserver(unique);
            var parameters = new JObject
            {
                ["commands"] = new JArray(
                    new JObject { ["tool"] = "blender_bridge", ["params"] = ImportParameters() },
                    new JObject { ["tool"] = "gen08_following", ["params"] = new JObject() }
                ),
            };
            Task<object> command = context.Start(() => BatchExecute.HandleCommand(parameters, cancellation.Token));
            settlements.Add(command);
            yield return Until(() => peer.Request.Task.IsCompleted);
            cancellation.Cancel();
            peer.Release.Set();
            yield return Until(() => command.IsCompleted && peer.Completion.IsCompleted);
            AssertCanceled(command, cancellation.Token);
            Assert.AreEqual(0, following);
            yield return DrainWatcher();
            observed.AssertNoMutations();
        }

        [UnityTest]
        public IEnumerator CancelDelayedScreenshot_DoesNotPublishAsset()
        {
            var peer = NewPeer(screenshot: true);
            var cancellation = NewCancellation();
            using var observed = new MutationObserver(unique);
            Task<object> command = Invoke(
                new JObject
                {
                    ["action"] = "screenshot",
                    ["output_folder"] = root,
                    ["timeout_seconds"] = 10,
                },
                cancellation.Token
            );
            yield return Until(() => peer.Request.Task.IsCompleted);
            cancellation.Cancel();
            peer.Release.Set();
            yield return Until(() => command.IsCompleted && peer.Completion.IsCompleted);
            AssertCanceled(command, cancellation.Token);
            yield return DrainWatcher();
            observed.AssertNoMutations();
        }

        [UnityTest]
        public IEnumerator CancelDelayedComparison_DoesNotInvokeUnityScreenshot()
        {
            int screenshots = 0;
            ReplaceHandler(
                "manage_scene",
                _ =>
                {
                    screenshots++;
                    return new { success = false };
                }
            );
            var target = new GameObject(unique);
            SceneManager.MoveGameObjectToScene(target, scene);
            var peer = NewPeer(screenshot: true);
            var cancellation = NewCancellation();
            Task<object> command = Invoke(
                new JObject
                {
                    ["action"] = "compare_screenshot",
                    ["game_object"] = unique,
                    ["output_folder"] = root,
                },
                cancellation.Token
            );
            yield return Until(() => peer.Request.Task.IsCompleted || command.IsCompleted);
            Assert.IsTrue(peer.Request.Task.IsCompleted, command.IsCompleted ? JObject.FromObject(command.Result).ToString() : "No remote request");
            cancellation.Cancel();
            peer.Release.Set();
            yield return Until(() => command.IsCompleted && peer.Completion.IsCompleted);
            AssertCanceled(command, cancellation.Token);
            Assert.AreEqual(0, screenshots);
        }

        [UnityTest]
        public IEnumerator ExplicitUnknownExport_UsesExactlyOneFallback()
        {
            var peer = NewPeer(unknown: true);
            Task<object> command = Invoke(ImportParameters(), CancellationToken.None);
            yield return Until(() => peer.Request.Task.IsCompleted);
            peer.Release.Set();
            yield return Until(() => command.IsCompleted && peer.Completion.IsCompleted);
            peer.AssertCompleted();
            Assert.IsTrue(JObject.FromObject(command.Result).Value<bool>("success"));
            CollectionAssert.AreEqual(new[] { "export_scene", "execute_code" }, peer.Types.ToArray());
        }

        [UnityTest]
        public IEnumerator CancelBeforeUnknownContinuation_DoesNotIssueFallback()
        {
            var peer = NewPeer(unknown: true);
            var cancellation = NewCancellation();
            Task<object> command = Invoke(ImportParameters(), cancellation.Token);
            yield return Until(() => peer.Request.Task.IsCompleted);
            peer.Release.Set();
            yield return Until(() => context.Count > 0, pump: false);
            cancellation.Cancel();
            yield return Until(() => command.IsCompleted);
            AssertCanceled(command, cancellation.Token);
            Assert.AreEqual(1, peer.Types.Count);
        }

        [UnityTest]
        public IEnumerator LostExportReply_DoesNotReplayThroughFallback()
        {
            var peer = NewPeer(drop: true);
            Task<object> command = Invoke(ImportParameters(), CancellationToken.None);
            yield return Until(() => peer.Request.Task.IsCompleted);
            peer.Release.Set();
            yield return Until(() => command.IsCompleted && peer.Completion.IsCompleted);
            Assert.IsFalse(JObject.FromObject(command.Result).Value<bool>("success"));
            CollectionAssert.AreEqual(new[] { "export_scene" }, peer.Types.ToArray());
        }

        [UnityTest]
        public IEnumerator ConsentDenied_PreservesOneArgumentCompatibility()
        {
            BlenderBridgeTool.ConsentGrantedOverride = () => false;
            BlenderBridgeTool.EndpointOverride = () => throw new AssertionException("Denied calls must not resolve an endpoint.");
            Task<object> command = context.Start(() => BlenderBridgeTool.HandleCommand(new JObject { ["action"] = "scene_info" }));
            settlements.Add(command);
            yield return Until(() => command.IsCompleted);
            JObject result = JObject.FromObject(command.Result);
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("blender_consent_required", result.ToString());
        }

        [UnityTest]
        public IEnumerator Comparison_ForwardsCallerToken_AndStopsAfterNestedCancellation()
        {
            var cancellation = NewCancellation();
            CancellationToken received = default;
            int calls = 0;
            replacedHandlers["manage_scene"] = handlers["manage_scene"];
            handlers["manage_scene"] = HandlerInfo.Cooperative(
                "manage_scene",
                (_, token) =>
                {
                    received = token;
                    calls++;
                    cancellation.Cancel();
                    return Task.FromResult<object>(new { success = true, data = new { fullPath = "not-read-after-cancellation" } });
                }
            );
            var target = new GameObject(unique);
            SceneManager.MoveGameObjectToScene(target, scene);
            var peer = NewPeer(screenshot: true);
            using var observed = new MutationObserver(unique);
            Task<object> command = Invoke(
                new JObject
                {
                    ["action"] = "compare_screenshot",
                    ["game_object"] = unique,
                    ["output_folder"] = root,
                },
                cancellation.Token
            );
            yield return Until(() => peer.Request.Task.IsCompleted);
            peer.Release.Set();
            yield return Until(() => command.IsCompleted && peer.Completion.IsCompleted);
            Assert.AreEqual(1, calls);
            Assert.AreEqual(cancellation.Token, received);
            AssertCanceled(command, cancellation.Token);
            yield return DrainWatcher();
            observed.AssertNoMutations();
        }

        [UnityTest]
        public IEnumerator Bloom_CancelBetweenNestedCommands_DoesNotStartSecondMutation()
        {
            Type volumeType = Type.GetType("UnityEngine.Rendering.Volume, Unity.RenderPipelines.Core.Runtime");
            if (volumeType == null)
                Assert.Ignore("The installed project has no Volume system; no optional package is installed and no real Bloom success is claimed.");
            if (Camera.main != null && Camera.main.gameObject.scene != scene)
                Assert.Ignore("An existing main camera is outside fixture ownership; this test will not modify it.");
            if (
                UnityEngine
                    .Resources.FindObjectsOfTypeAll(volumeType)
                    .OfType<Component>()
                    .Any(component => component.gameObject.scene.IsValid() && component.gameObject.scene != scene)
            )
                Assert.Ignore("An existing Volume is outside fixture ownership; this test will not modify it.");
            var volumeObject = new GameObject(unique + "_volume");
            SceneManager.MoveGameObjectToScene(volumeObject, scene);
            Component volume = volumeObject.AddComponent(volumeType);
            volumeType.GetProperty("isGlobal").SetValue(volume, true);
            var cancellation = NewCancellation();
            int calls = 0;
            CancellationToken received = default;
            replacedHandlers["manage_graphics"] = handlers["manage_graphics"];
            handlers["manage_graphics"] = HandlerInfo.Cooperative(
                "manage_graphics",
                (parameters, token) =>
                {
                    calls++;
                    received = token;
                    Assert.AreEqual("volume_add_effect", (string)parameters["action"]);
                    cancellation.Cancel();
                    return Task.FromResult<object>(new { success = true });
                }
            );
            Task<object> command = Invoke(new JObject { ["action"] = "setup_bloom" }, cancellation.Token);
            yield return Until(() => command.IsCompleted);
            AssertCanceled(command, cancellation.Token);
            Assert.AreEqual(cancellation.Token, received);
            Assert.AreEqual(1, calls, "No second graphics mutation may start after cancellation.");
        }

        [Test]
        public void Animation_CancelDuringControllerCreation_RetainsControllerWithoutLaterBinding()
        {
            var clip = new AnimationClip { name = "OwnedClip" };
            clip.SetCurve("", typeof(Transform), "m_LocalPosition.x", AnimationCurve.Linear(0, 0, 1, 1));
            AssetDatabase.CreateAsset(clip, root + "/OwnedClip.anim");
            var instance = new GameObject(unique + "_animation");
            SceneManager.MoveGameObjectToScene(instance, scene);
            var cancellation = NewCancellation();
            int callbacks = 0;
            BlenderAssetCreationCancellationProcessor.OwnedPrefix = root + "/";
            BlenderAssetCreationCancellationProcessor.Cancel = () =>
            {
                callbacks++;
                cancellation.Cancel();
            };
            MethodInfo method = typeof(BlenderBridgeTool).GetMethod("SetupAnimationWithCancellation", PrivateStatic);
            object[] arguments;
            if (method != null)
                arguments = new object[] { instance, root + "/Model.fbx", unique, true, new[] { clip }, cancellation.Token };
            else
            {
                // The saved baseline has the same real helper without token plumbing. Execute it
                // so RED observes actual late controller binding, not a missing-signature failure.
                method = typeof(BlenderBridgeTool).GetMethod("SetupAnimation", PrivateStatic);
                arguments = new object[] { instance, root + "/Model.fbx", unique, true, new[] { clip } };
            }
            Exception failure = null;
            try
            {
                method.Invoke(null, arguments);
            }
            catch (TargetInvocationException exception)
            {
                failure = exception.InnerException;
            }
            Assert.Greater(callbacks, 0, "The real owned controller creation callback must fire; no cancellation is inferred without this witness.");
            Assert.IsTrue(cancellation.IsCancellationRequested);
            string[] controllers = AssetDatabase.FindAssets("t:AnimatorController", new[] { root });
            Assert.AreEqual(1, controllers.Length, "The already-admitted controller creation is retained.");
            string path = AssetDatabase.GUIDToAssetPath(controllers[0]);
            var controller = AssetDatabase.LoadAssetAtPath<UnityEditor.Animations.AnimatorController>(path);
            Assert.IsNotNull(controller);
            Assert.IsTrue(File.Exists(Absolute(path)));
            Assert.IsNull(instance.GetComponent<Animator>().runtimeAnimatorController, "Cancellation during creation must prevent the later scene binding.");
            Assert.AreEqual(0, controller.layers[0].stateMachine.states.Length, "No animation states may be added after cancellation.");
            Assert.IsInstanceOf<OperationCanceledException>(failure);
            Assert.AreEqual(cancellation.Token, ((OperationCanceledException)failure).CancellationToken);
        }

        private JObject ImportParameters() =>
            new JObject
            {
                ["action"] = "import_model",
                ["format"] = "fbx",
                ["name"] = unique,
                ["output_folder"] = root,
                ["target_size"] = 0,
                ["auto_animate"] = false,
                ["place_in_scene"] = true,
                ["timeout_seconds"] = 10,
            };

        private CancellationTokenSource NewCancellation()
        {
            var cancellation = new CancellationTokenSource();
            cancellations.Add(cancellation);
            return cancellation;
        }

        private LoopbackAddon NewPeer(bool screenshot = false, bool unknown = false, bool drop = false)
        {
            var peer = new LoopbackAddon(unique, screenshot, unknown, drop);
            peers.Add(peer);
            BlenderBridgeTool.EndpointOverride = () => peer.Endpoint;
            return peer;
        }

        private Task<object> Invoke(JObject parameters, CancellationToken token)
        {
            Task<object> command = context.Start(() => CommandRegistry.InvokeCommandAsync("blender_bridge", parameters, token));
            settlements.Add(command);
            return command;
        }

        private TransportCommandOperation Dispatch(string name, JObject parameters, CancellationToken token)
        {
            var operation = context.Start(() => TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name, @params = parameters }, token));
            settlements.Add(operation.Completion);
            return operation;
        }

        private IEnumerator Until(Func<bool> condition, bool pump = true)
        {
            var clock = Stopwatch.StartNew();
            while (!condition())
            {
                if (pump)
                    context.Drain();
                Assert.Less(clock.Elapsed.TotalSeconds, 20, "The owned operation/barrier must settle within its test bound.");
                yield return null;
            }
            if (pump)
                context.Drain();
        }

        private IEnumerator DrainWatcher()
        {
            var clock = Stopwatch.StartNew();
            while (clock.ElapsedMilliseconds < 200)
            {
                context.Drain();
                yield return null;
            }
        }

        private void ReplaceHandler(string name, Func<JObject, object> handler)
        {
            replacedHandlers[name] = handlers[name];
            handlers[name] = new HandlerInfo(name, handler, null);
        }

        private static void AssertCanceled(Task command, CancellationToken token)
        {
            Assert.IsTrue(command.IsCanceled, command.IsFaulted ? command.Exception.ToString() : "Actual handler returned without cancellation.");
            try
            {
                command.GetAwaiter().GetResult();
                Assert.Fail("Expected cancellation.");
            }
            catch (OperationCanceledException exception)
            {
                Assert.AreEqual(token, exception.CancellationToken);
            }
        }

        private static string Absolute(string path) => Path.GetFullPath(path);

        private sealed class PumpContext : SynchronizationContext
        {
            private readonly ConcurrentQueue<Action> pending = new ConcurrentQueue<Action>();
            public int Count => pending.Count;

            public override void Post(SendOrPostCallback callback, object state) => pending.Enqueue(() => callback(state));

            public T Start<T>(Func<T> action)
            {
                SynchronizationContext previous = Current;
                SetSynchronizationContext(this);
                try
                {
                    return action();
                }
                finally
                {
                    SetSynchronizationContext(previous);
                }
            }

            public void Drain()
            {
                int count = 0;
                while (pending.TryDequeue(out Action action))
                {
                    Assert.Less(count++, 1000, "Continuation queue must be bounded.");
                    Start(() =>
                    {
                        action();
                        return true;
                    });
                }
            }
        }

        private sealed class MutationObserver : IDisposable
        {
            private readonly FileSystemWatcher watcher;
            private readonly ConcurrentQueue<string> mutations = new ConcurrentQueue<string>();
            private volatile Exception error;

            public MutationObserver(string unique)
            {
                string stagingRoot = Path.Combine(Application.dataPath, ".BlenderBridge");
                bool Matches(string path) =>
                    path.Contains(unique)
                    || string.Equals(path, stagingRoot, StringComparison.OrdinalIgnoreCase)
                    || path.StartsWith(stagingRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
                watcher = new FileSystemWatcher(Application.dataPath)
                {
                    IncludeSubdirectories = true,
                    NotifyFilter = NotifyFilters.FileName | NotifyFilters.DirectoryName,
                };
                // This isolated fixture has the sole active bridge operation. Observe its staging
                // root as well as named files, so create-then-cancel before File.Copy is visible.
                watcher.Created += (_, entry) =>
                {
                    if (Matches(entry.FullPath))
                        mutations.Enqueue(entry.FullPath);
                };
                watcher.Renamed += (_, entry) =>
                {
                    if (Matches(entry.FullPath))
                        mutations.Enqueue(entry.FullPath);
                };
                watcher.Error += (_, entry) => error = entry.GetException();
                watcher.EnableRaisingEvents = true;
            }

            public void AssertNoMutations()
            {
                Assert.IsNull(error, "Watcher errors/overflow cannot be treated as zero mutations.");
                Assert.IsEmpty(mutations, "Even temporary create-then-delete Assets staging is forbidden before admission.");
            }

            public void Dispose() => watcher.Dispose();
        }

        private sealed class OwnedDiscovery : IToolDiscoveryService
        {
            public List<ToolMetadata> DiscoverAllTools() => new List<ToolMetadata>();

            public ToolMetadata GetToolMetadata(string name) => null;

            public List<ToolMetadata> GetEnabledTools() => new List<ToolMetadata>();

            public bool IsToolEnabled(string name) => true;

            public void SetToolEnabled(string name, bool enabled) => throw new AssertionException("Preference mutation is forbidden.");

            public void InvalidateCache() { }
        }

        private sealed class LoopbackAddon : IDisposable
        {
            private readonly TcpListener listener = new TcpListener(IPAddress.Loopback, 0);
            private readonly string unique;
            private readonly bool screenshot;
            private readonly bool unknown;
            private readonly bool drop;
            private readonly ConcurrentBag<string> files = new ConcurrentBag<string>();
            private TcpClient connected;
            private volatile bool disposed;
            public readonly ManualResetEventSlim Release = new ManualResetEventSlim(false);
            public readonly TaskCompletionSource<JObject> Request = new TaskCompletionSource<JObject>(TaskCreationOptions.RunContinuationsAsynchronously);
            public readonly ConcurrentQueue<string> Types = new ConcurrentQueue<string>();
            public BlenderEndpoint Endpoint { get; }
            public Task Completion { get; }

            public LoopbackAddon(string unique, bool screenshot, bool unknown, bool drop)
            {
                this.unique = unique;
                this.screenshot = screenshot;
                this.unknown = unknown;
                this.drop = drop;
                listener.Start();
                Endpoint = new BlenderEndpoint("127.0.0.1", ((IPEndPoint)listener.LocalEndpoint).Port);
                Completion = Task.Run(Run);
            }

            private void Run()
            {
                try
                {
                    string export = null;
                    for (int index = 0; index < (unknown ? 2 : 1); index++)
                    {
                        using var peer = listener.AcceptTcpClient();
                        connected = peer;
                        peer.ReceiveTimeout = 5000;
                        peer.SendTimeout = 5000;
                        using NetworkStream stream = peer.GetStream();
                        using var bytes = new MemoryStream();
                        var chunk = new byte[4096];
                        JObject request = null;
                        while (request == null)
                        {
                            int read = stream.Read(chunk, 0, chunk.Length);
                            if (read == 0)
                                throw new IOException("Peer closed before a request.");
                            bytes.Write(chunk, 0, read);
                            if (bytes.Length > 65536)
                                throw new InvalidDataException("Owned request exceeded bound.");
                            BlenderSocketClient.TryParseResponse(bytes.GetBuffer(), (int)bytes.Length, out request);
                        }
                        Types.Enqueue((string)request["type"]);
                        if (index == 0)
                        {
                            export = ValidatePath((string)request["params"]?["filepath"]);
                            Request.TrySetResult(request);
                        }
                        if (!Release.Wait(TimeSpan.FromSeconds(15)))
                            throw new TimeoutException("Reply release barrier was not reached.");
                        if (disposed)
                            return;
                        if (drop)
                            return;
                        bool reject = unknown && index == 0;
                        if (!reject)
                        {
                            files.Add(export);
                            if (screenshot)
                                File.WriteAllBytes(
                                    export,
                                    Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==")
                                );
                            else
                                File.WriteAllText(export, AsciiFbx);
                        }
                        var response = reject
                            ? new JObject { ["status"] = "error", ["message"] = "Unknown command type: export_scene" }
                            : new JObject
                            {
                                ["status"] = "success",
                                ["result"] = new JObject { ["result"] = "owned export" },
                            };
                        byte[] payload = Encoding.UTF8.GetBytes(response.ToString());
                        try
                        {
                            stream.Write(payload, 0, payload.Length);
                            stream.Flush();
                        }
                        catch (IOException) { } // Closing the local socket cannot cancel this simulated remote export.
                        catch (SocketException) { }
                    }
                }
                catch (Exception) when (disposed) { }
            }

            private string ValidatePath(string path)
            {
                if (string.IsNullOrEmpty(path))
                    throw new InvalidDataException("Missing owned filepath.");
                string full = Path.GetFullPath(path);
                string allowed = screenshot ? Path.GetFullPath("Library/BlenderBridge") : Path.Combine(Path.GetTempPath(), "BlenderBridge");
                if (!full.StartsWith(allowed + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Export escaped owned location.");
                if (!screenshot && !Path.GetFileName(full).StartsWith(unique + "_", StringComparison.Ordinal))
                    throw new InvalidDataException("Export did not use owned name.");
                if (File.Exists(full))
                    throw new IOException("Owned export already exists.");
                return full;
            }

            public void AssertCompleted() => Completion.GetAwaiter().GetResult();

            public void Dispose()
            {
                disposed = true;
                Release.Set();
                connected?.Dispose();
                listener.Stop();
                Assert.IsTrue(Completion.Wait(TimeSpan.FromSeconds(5)), "Owned peer must settle before cleanup.");
            }

            public void DeleteOwnedFiles()
            {
                foreach (string file in files)
                    if (File.Exists(file))
                        File.Delete(file);
            }
        }

        // Owned ASCII FBX geometry and connections; this is not OBJ data with an FBX suffix.
        private const string AsciiFbx =
            @"; FBX 7.4.0 project file
FBXHeaderExtension: {
 FBXHeaderVersion: 1003
 FBXVersion: 7400
 Creator: ""GEN08 owned fixture""
}
GlobalSettings: {
 Version: 1000
 Properties70: {
  P: ""UpAxis"", ""int"", ""Integer"", """",1
  P: ""UpAxisSign"", ""int"", ""Integer"", """",1
  P: ""FrontAxis"", ""int"", ""Integer"", """",2
  P: ""FrontAxisSign"", ""int"", ""Integer"", """",-1
  P: ""CoordAxis"", ""int"", ""Integer"", """",0
  P: ""CoordAxisSign"", ""int"", ""Integer"", """",1
  P: ""UnitScaleFactor"", ""double"", ""Number"", """",1
 }
}
Documents: {
 Count: 1
 Document: 3000, ""OwnedScene"", ""Scene"" {
  RootNode: 0
 }
}
Definitions: {
 Version: 100
 Count: 2
 ObjectType: ""Model"" {
  Count: 1
 }
 ObjectType: ""Geometry"" {
  Count: 1
 }
}
Objects: {
 Geometry: 1000, ""Geometry::OwnedTriangle"", ""Mesh"" {
  Vertices: *9 {
   a: 0,0,0,300,0,0,0,100,0
  }
  PolygonVertexIndex: *3 {
   a: 0,1,-3
  }
  GeometryVersion: 124
  LayerElementNormal: 0 {
   Version: 101
   Name: """"
   MappingInformationType: ""ByPolygonVertex""
   ReferenceInformationType: ""Direct""
   Normals: *9 {
    a: 0,0,1,0,0,1,0,0,1
   }
  }
  Layer: 0 {
   Version: 100
   LayerElement: {
    Type: ""LayerElementNormal""
    TypedIndex: 0
   }
  }
 }
 Model: 2000, ""Model::OwnedMain"", ""Mesh"" {
  Version: 232
  Properties70: {
   P: ""Lcl Translation"", ""Lcl Translation"", """", ""A"",0,0,0
   P: ""Lcl Rotation"", ""Lcl Rotation"", """", ""A"",0,0,0
   P: ""Lcl Scaling"", ""Lcl Scaling"", """", ""A"",1,1,1
  }
  Shading: T
  Culling: ""CullingOff""
 }
}
Connections: {
 C: ""OO"",1000,2000
 C: ""OO"",2000,0
}
";
    }

    internal sealed class BlenderAssetCreationCancellationProcessor : AssetModificationProcessor
    {
        internal static string OwnedPrefix;
        internal static Action Cancel;

        private static void OnWillCreateAsset(string path)
        {
            if (OwnedPrefix == null || string.IsNullOrEmpty(path))
                return;
            string normalized = path.Replace('\\', '/');
            if (
                normalized.StartsWith(OwnedPrefix, StringComparison.Ordinal)
                && (normalized.EndsWith(".controller", StringComparison.Ordinal) || normalized.EndsWith(".controller.meta", StringComparison.Ordinal))
            )
                Cancel?.Invoke();
        }
    }

    internal sealed class BlenderImportCancellationPostprocessor : AssetPostprocessor
    {
        internal static string OwnedPrefix;
        internal static Action Cancel;

        private void OnPostprocessModel(GameObject model)
        {
            if (OwnedPrefix != null && assetPath.StartsWith(OwnedPrefix, StringComparison.Ordinal))
                Cancel?.Invoke();
        }

        private static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] previous)
        {
            if (OwnedPrefix != null && imported.Any(path => path.StartsWith(OwnedPrefix, StringComparison.Ordinal)))
                Cancel?.Invoke();
        }
    }
}
