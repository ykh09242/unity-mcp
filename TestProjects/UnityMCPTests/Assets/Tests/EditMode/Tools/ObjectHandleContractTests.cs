using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public abstract class ObjectHandleFixture
    {
        private static readonly FieldInfo ResourceDiscoveryField = typeof(MCPServiceLocator).GetField(
            "_resourceDiscoveryService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        private object _previousResources;
        private object _previousTools;
        private static readonly FieldInfo ToolDiscoveryField = typeof(MCPServiceLocator).GetField(
            "_toolDiscoveryService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        protected readonly HandleResourceDiscovery Resources = new HandleResourceDiscovery();
        protected string AssetRoot;
        private string _rootGuid;
        protected Scene OwnedScene;
        private Scene _previousScene;
        private Object[] _previousSelection;
        protected GameObject Target;
        protected BoxCollider First;
        protected BoxCollider Second;

        [SetUp]
        public void SetUpHandles()
        {
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage(), "Fixture requires an isolated Editor, never a user prefab stage.");
            Assert.IsNotNull(ResourceDiscoveryField);
            _previousResources = ResourceDiscoveryField.GetValue(null);
            _previousTools = ToolDiscoveryField.GetValue(null);
            MCPServiceLocator.Register<IToolDiscoveryService>(new HandleToolDiscovery());
            Resources.Enabled = Resources.AliasEnabled = true;
            MCPServiceLocator.Register<IResourceDiscoveryService>(Resources);
            _previousScene = SceneManager.GetActiveScene();
            _previousSelection = Selection.objects;
            AssetRoot = "Assets/__McpHandles_" + Guid.NewGuid().ToString("N");
            _rootGuid = AssetDatabase.CreateFolder("Assets", AssetRoot.Substring(7));
            Assert.IsNotEmpty(_rootGuid);
            OwnedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(EditorSceneManager.SaveScene(OwnedScene, AssetRoot + "/Scene.unity"));
            SceneManager.SetActiveScene(OwnedScene);
            Target = new GameObject("IdentityTarget");
            SceneManager.MoveGameObjectToScene(Target, OwnedScene);
            First = Target.AddComponent<BoxCollider>();
            Second = Target.AddComponent<BoxCollider>();
            First.size = Vector3.one;
            Second.size = Vector3.one * 2;
            Assert.IsTrue(EditorSceneManager.SaveScene(OwnedScene));
        }

        [TearDown]
        public void TearDownHandles()
        {
            try
            {
                if (OwnedScene.IsValid() && OwnedScene.isLoaded)
                    EditorSceneManager.CloseScene(OwnedScene, true);
            }
            finally
            {
                if (_previousScene.IsValid() && _previousScene.isLoaded)
                    SceneManager.SetActiveScene(_previousScene);
                Selection.objects = _previousSelection;
                ResourceDiscoveryField.SetValue(null, _previousResources);
                ToolDiscoveryField.SetValue(null, _previousTools);
                if (!string.IsNullOrEmpty(_rootGuid) && AssetDatabase.AssetPathToGUID(AssetRoot) == _rootGuid)
                    Assert.IsTrue(AssetDatabase.DeleteAsset(AssetRoot));
            }
        }

        protected sealed class HandleResourceDiscovery : IResourceDiscoveryService
        {
            internal bool Enabled = true;
            internal bool AliasEnabled = true;

            public bool IsResourceEnabled(string name) => name == "get_gameobject" ? Enabled : name == "get_gameobject_by_handle_v2" && AliasEnabled;

            public ResourceMetadata GetResourceMetadata(string name) =>
                name == "get_gameobject" || name == "get_gameobject_by_handle_v2" ? new ResourceMetadata { Name = name } : null;

            public List<ResourceMetadata> DiscoverAllResources() => new List<ResourceMetadata>();

            public List<ResourceMetadata> GetEnabledResources() => new List<ResourceMetadata>();

            public void SetResourceEnabled(string name, bool enabled) => throw new InvalidOperationException("No preference writes in fixtures.");

            public void InvalidateCache() { }
        }

        private sealed class HandleToolDiscovery : IToolDiscoveryService
        {
            public bool IsToolEnabled(string name) => name == "manage_object_handles_v2";

            public ToolMetadata GetToolMetadata(string name) => name == "manage_object_handles_v2" ? new ToolMetadata { Name = name } : null;

            public List<ToolMetadata> DiscoverAllTools() => new List<ToolMetadata>();

            public List<ToolMetadata> GetEnabledTools() => new List<ToolMetadata>();

            public void SetToolEnabled(string name, bool enabled) => throw new InvalidOperationException("No preference writes in fixtures.");

            public void InvalidateCache() { }
        }

        protected static IDisposable Observe(Action<string> observer) => new HandleSeam("OperationForTests", observer);

        protected static IDisposable Clock(Func<double> clock) => new HandleSeam("ClockForTests", clock);

        private sealed class HandleSeam : IDisposable
        {
            private readonly FieldInfo _field;
            private readonly object _previous;

            internal HandleSeam(string name, object value)
            {
                Type helper = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Helpers.ObjectHandles");
                Assert.IsNotNull(helper);
                _field = helper.GetField(name, BindingFlags.NonPublic | BindingFlags.Static);
                Assert.IsNotNull(_field);
                _previous = _field.GetValue(null);
                _field.SetValue(null, value);
            }

            public void Dispose() => _field.SetValue(null, _previous);
        }

        public static JObject Call(string typeName, JObject request)
        {
            Type type = typeof(CommandRegistry).Assembly.GetType(typeName);
            Assert.IsNotNull(type, "The versioned public object-handle handler must exist; legacy target lookup is not a fallback.");
            MethodInfo method = type.GetMethod("HandleCommand", new[] { typeof(JObject) });
            Assert.IsNotNull(method);
            return JObject.FromObject(method.Invoke(null, new object[] { request }));
        }

        public static JObject Command(JObject request) => Call("MCPForUnity.Editor.Tools.ManageObjectHandles", request);

        public static JObject Capture(Object target) => Command(new JObject { ["action"] = "capture", ["instance_id"] = target.GetInstanceIDCompat() });

        public static JObject Resolve(string handle) => Command(new JObject { ["action"] = "resolve", ["handle"] = handle });

        public static JObject Success(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        public static void Error(JObject response, string code)
        {
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(code, response.Value<string>("code"), response.ToString());
            Assert.AreEqual(code, response.SelectToken("data.reason")?.Value<string>(), response.ToString());
        }

        protected static string Token(JObject response) => Success(response).Value<string>("handle");

        protected GameObject SavePrefab(Material dependency = null)
        {
            var source = new GameObject("AssetRoot");
            SceneManager.MoveGameObjectToScene(source, OwnedScene);
            if (dependency != null)
                source.AddComponent<MeshRenderer>().sharedMaterial = dependency;
            var child = new GameObject("AssetChild");
            child.transform.SetParent(source.transform);
            child.AddComponent<BoxCollider>();
            try
            {
                return PrefabUtility.SaveAsPrefabAsset(source, AssetRoot + "/Identity.prefab");
            }
            finally
            {
                Object.DestroyImmediate(source);
            }
        }
    }
}

// Tests-first source draft. Integrate into the existing ObjectHandleContractTests.cs.
// Uses that file's owned fixture; does not add a Unity asset or .meta file.

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ObjectHandleSessionAcceptance : ObjectHandleFixture
    {
        private static JObject CaptureSession(Object target) => Command(new JObject { ["action"] = "capture", ["instance_id"] = target.GetInstanceIDCompat() });

        private static JObject ResolveSession(string handle) => Command(new JObject { ["action"] = "resolve", ["handle"] = handle });

        [Test]
        public void CleanSavedSceneIsSessionOnlyAndSaveRetainsOriginalReference()
        {
            JObject data = Success(CaptureSession(Target));
            Assert.AreEqual(2, data.Value<int>("contractVersion"));
            Assert.AreEqual("session", data.Value<string>("persistence"));
            StringAssert.StartsWith("mcp-oh-v2.", data.Value<string>("handle"));
            Assert.AreEqual(600, data.SelectToken("validUntil.ttlSeconds").Value<int>());
            Target.name = "RenamedWithinSession";
            Assert.IsTrue(EditorSceneManager.SaveScene(OwnedScene));
            JObject resolved = Success(ResolveSession(data.Value<string>("handle")));
            Assert.AreEqual(Target.GetInstanceIDCompat(), resolved.Value<int>("instanceID"));
            Assert.AreEqual("RenamedWithinSession", resolved.Value<string>("name"));
            Assert.AreEqual(data.SelectToken("validUntil.editorTimeSeconds"), resolved.SelectToken("validUntil.editorTimeSeconds"));
        }

        [Test]
        public void SceneReopenExpiresOldHandleAndFreshCaptureSucceeds()
        {
            string handle = Token(CaptureSession(Target));
            string path = OwnedScene.path;
            Assert.IsTrue(EditorSceneManager.CloseScene(OwnedScene, true));
            OwnedScene = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
            Error(ResolveSession(handle), "stale_handle");
            GameObject restored = OwnedScene.GetRootGameObjects()[0];
            JObject fresh = Success(CaptureSession(restored));
            Assert.AreNotEqual(handle, fresh.Value<string>("handle"));
            Assert.AreEqual(restored.GetInstanceIDCompat(), Success(ResolveSession(fresh.Value<string>("handle"))).Value<int>("instanceID"));
        }

        [Test]
        public void ComponentReplacementNeverAliasesAndSiblingRemainsExact()
        {
            string first = Token(CaptureSession(First));
            string second = Token(CaptureSession(Second));
            Assert.AreNotEqual(first, second);
            Object.DestroyImmediate(First);
            BoxCollider replacement = Target.AddComponent<BoxCollider>();
            Error(ResolveSession(first), "stale_handle");
            Assert.AreEqual(Second.GetInstanceIDCompat(), Success(ResolveSession(second)).Value<int>("instanceID"));
            Assert.AreNotEqual(first, Token(CaptureSession(replacement)));
            Error(ResolveSession(first), "stale_handle");
        }

        [Test]
        public void DestroyedGameObjectNeverResolvesSameNameReplacement()
        {
            string handle = Token(CaptureSession(Target));
            Object.DestroyImmediate(Target);
            Target = new GameObject("IdentityTarget");
            SceneManager.MoveGameObjectToScene(Target, OwnedScene);
            Error(ResolveSession(handle), "stale_handle");
            Assert.AreNotEqual(handle, Token(CaptureSession(Target)));
        }

        [Test]
        public void CrossSceneMoveInvalidationDoesNotReviveWhenMovedBack()
        {
            string handle = Token(CaptureSession(Target));
            Scene other = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                // Capture after creation: the conservative scene event invalidates previous tokens.
                handle = Token(CaptureSession(Target));
                SceneManager.MoveGameObjectToScene(Target, other);
                Error(ResolveSession(handle), "stale_handle");
                SceneManager.MoveGameObjectToScene(Target, OwnedScene);
                Error(ResolveSession(handle), "stale_handle");
            }
            finally
            {
                Assert.IsTrue(EditorSceneManager.CloseScene(other, true));
            }
        }

        [Test]
        public void UnobservedSceneMoveRoundtripCanStillResolveOnlyOriginalReference()
        {
            Scene other = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                string handle = Token(CaptureSession(Target));
                int originalId = Target.GetInstanceIDCompat();
                SceneManager.MoveGameObjectToScene(Target, other);
                SceneManager.MoveGameObjectToScene(Target, OwnedScene);
                Assert.AreEqual(originalId, Success(ResolveSession(handle)).Value<int>("instanceID"));
            }
            finally
            {
                Assert.IsTrue(EditorSceneManager.CloseScene(other, true));
            }
        }

        [Test]
        public void UndoDestructionAndRestorationNeverReviveOldSessionHandle()
        {
            string handle = Token(CaptureSession(First));
            string sibling = Token(CaptureSession(Second));
            BoxCollider restored = null;
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            try
            {
                Undo.SetCurrentGroupName("Session handle owned component removal");
                Undo.DestroyObjectImmediate(First);
                Undo.CollapseUndoOperations(group);
                // No intermediate resolve: Undo expiry must work without prior tombstoning.
                Undo.PerformUndo();
                Error(ResolveSession(handle), "stale_handle");
                Error(ResolveSession(sibling), "stale_handle");
                restored = Target.GetComponents<BoxCollider>().Single(component => component.size == Vector3.one);
                Assert.IsNotNull(restored);
                string fresh = Token(CaptureSession(restored));
                Assert.AreNotEqual(handle, fresh);
                Assert.AreEqual(restored.GetInstanceIDCompat(), Success(ResolveSession(fresh)).Value<int>("instanceID"));
            }
            finally
            {
                if (restored != null)
                    Undo.ClearUndo(restored);
                Undo.IncrementCurrentGroup();
            }
        }

        [Test]
        public void PersistentPrefabAssetCaptureIsUnsupportedWithoutIdentityOrLoadOperations()
        {
            GameObject asset = SavePrefab();
            var operations = new System.Collections.Generic.List<string>();
            using (Observe(operations.Add))
                Error(CaptureSession(asset), "unsupported_context");
            CollectionAssert.DoesNotContain(operations, "before:global_id");
            CollectionAssert.DoesNotContain(operations, "before:global_resolve");
            CollectionAssert.DoesNotContain(operations, "before:asset_load");
            CollectionAssert.DoesNotContain(operations, "before:dependency_hash");
        }

        [TestCase("require_persistent", false)]
        [TestCase("require_persistent", true)]
        [TestCase("allow_asset_load", false)]
        [TestCase("allow_asset_load", true)]
        public void ObsoleteFlagsAreRejectedByPresence(string flag, bool value)
        {
            Error(
                Command(
                    new JObject
                    {
                        ["action"] = "capture",
                        ["instance_id"] = Target.GetInstanceIDCompat(),
                        [flag] = value,
                    }
                ),
                "invalid_parameters"
            );
            Error(
                Command(
                    new JObject
                    {
                        ["action"] = "resolve",
                        ["handle"] = Token(CaptureSession(Target)),
                        [flag] = value,
                    }
                ),
                "invalid_parameters"
            );
        }

        [Test]
        public void DeadlineIsFixedAndExpiresAtExactBoundary()
        {
            double now = 100;
            using (Clock(() => now))
            {
                string handle = Token(CaptureSession(Target));
                now = 699.999;
                Assert.AreEqual(700, Success(ResolveSession(handle)).SelectToken("validUntil.editorTimeSeconds").Value<double>());
                now = 700;
                Error(ResolveSession(handle), "stale_handle");
            }
        }

        [Test]
        public void BoundedRegistryEvictsOldestButResolveRefreshesRecencyOnly()
        {
            using (Clock(() => 100))
            {
                var handles = new string[512];
                for (int i = 0; i < handles.Length; i++)
                    handles[i] = Token(CaptureSession(Target));
                Success(ResolveSession(handles[0]));
                string latest = Token(CaptureSession(Target));
                Error(ResolveSession(handles[1]), "stale_handle");
                Assert.AreEqual(700, Success(ResolveSession(handles[0])).SelectToken("validUntil.editorTimeSeconds").Value<double>());
                Success(ResolveSession(latest));
            }
        }

        [Test]
        public void OldSerializedTokenIsUnsupportedAndNeverResolved()
        {
            Error(ResolveSession("mcp-oh-v1.eyJ2IjoxLCJrIjoic2NlbmUifQ"), "unsupported_handle_version");
        }
    }
}

namespace MCPForUnityTests.Editor.Tools
{
    [Explicit("Requires the reviewed isolated reload-safe EDIT03 launcher and exact project/nonce guard.")]
    [Parallelizable(ParallelScope.None)]
    public class ObjectHandlePlayModeContractTests
    {
        [UnityTest]
        public IEnumerator RuntimeObjectHandlesAreSessionBound()
        {
            AssertOwnedRuntimeProject();
            yield return new EnterPlayMode();
            AssertOwnedRuntimeProject();
            Assert.IsTrue(Application.isPlaying);
            var target = new GameObject("OwnedRuntimeHandle");
            try
            {
                JObject captured = ObjectHandleFixture.Success(ObjectHandleFixture.Capture(target));
                Assert.AreEqual("session", captured.Value<string>("persistence"));
                Assert.AreEqual(2, captured.Value<int>("contractVersion"));
                string token = captured.Value<string>("handle");
                Assert.AreEqual(target.GetInstanceIDCompat(), ObjectHandleFixture.Success(ObjectHandleFixture.Resolve(token)).Value<int>("instanceID"));
                Object.DestroyImmediate(target);
                ObjectHandleFixture.Error(ObjectHandleFixture.Resolve(token), "stale_handle");
            }
            finally
            {
                if (target != null)
                    Object.DestroyImmediate(target);
            }
        }

        private static void AssertOwnedRuntimeProject()
        {
            string expected = Environment.GetEnvironmentVariable("UNITY_MCP_EDIT03_RUNTIME_PROJECT");
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_EDIT03_RUNTIME_NONCE");
            Assert.IsFalse(string.IsNullOrEmpty(expected), "The isolated runtime project must be explicitly supplied.");
            Assert.IsTrue(nonce != null && nonce.Length == 32 && nonce.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')));
            string actual = Path.GetFullPath(Path.GetDirectoryName(Application.dataPath)).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string supplied = Path.GetFullPath(expected).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            StringComparison comparison = Application.platform == RuntimePlatform.WindowsEditor ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            Assert.IsTrue(string.Equals(actual, supplied, comparison), "The runtime fixture may only transition its exact owned project.");
            string marker = Path.Combine(actual, ".mcp-edit03-runtime-owner");
            Assert.IsTrue(File.Exists(marker), "The isolated runtime ownership marker is required.");
            Assert.AreEqual(nonce, File.ReadAllText(marker).Trim(), "The runtime ownership marker must match this launch nonce.");
        }

        [UnityTearDown]
        public IEnumerator ExitOwnedPlayMode()
        {
            if (Application.isPlaying)
            {
                AssertOwnedRuntimeProject();
                yield return new ExitPlayMode();
            }
        }
    }
}

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ObjectHandleContractTests : ObjectHandleFixture
    {
        private static void ExpireEpoch(PlayModeStateChange state = PlayModeStateChange.ExitingEditMode)
        {
            Type helper = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Helpers.ObjectHandles");
            MethodInfo callback = helper.GetMethod("OnPlayModeStateChanged", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(callback);
            callback.Invoke(null, new object[] { state });
        }

        [TestCase(PlayModeStateChange.ExitingEditMode)]
        [TestCase(PlayModeStateChange.EnteredPlayMode)]
        [TestCase(PlayModeStateChange.ExitingPlayMode)]
        [TestCase(PlayModeStateChange.EnteredEditMode)]
        public void EveryPlayCallbackExpiresLiveReference(PlayModeStateChange state)
        {
            string handle = Token(Capture(Target));
            ExpireEpoch(state);
            Assert.IsNotNull(Target);
            Error(Resolve(handle), "stale_handle");
            Assert.AreEqual(Target.GetInstanceIDCompat(), Success(Resolve(Token(Capture(Target)))).Value<int>("instanceID"));
        }

        [TestCase("before:instance_lookup")]
        [TestCase("after:instance_lookup")]
        [TestCase("before:descriptor")]
        [TestCase("after:descriptor")]
        public void CaptureRefusesAnEpochChangeAcrossItsOperations(string boundary)
        {
            bool observed = false;
            using (
                Observe(operation =>
                {
                    if (operation == boundary)
                    {
                        observed = true;
                        ExpireEpoch();
                    }
                })
            )
                Error(Capture(Target), "stale_handle");
            Assert.IsTrue(observed);
            Success(Capture(Target));
        }

        [TestCase("before:descriptor")]
        [TestCase("after:descriptor")]
        public void ResolveRefusesEpochChangeDuringDescriptorRead(string boundary)
        {
            string handle = Token(Capture(Target));
            using (
                Observe(operation =>
                {
                    if (operation == boundary)
                        ExpireEpoch();
                })
            )
                Error(Resolve(handle), "stale_handle");
            Error(Resolve(handle), "stale_handle");
        }

        [TestCase("before:descriptor")]
        [TestCase("after:descriptor")]
        public void ActualUndoDuringCaptureExpiresItsInFlightEpoch(string boundary)
        {
            Undo.IncrementCurrentGroup();
            Undo.RegisterCompleteObjectUndo(Second, "Session handle owned collider edit");
            Second.size = Vector3.one * 3;
            Undo.FlushUndoRecordObjects();
            bool observed = false;
            try
            {
                using (
                    Observe(operation =>
                    {
                        if (!observed && operation == boundary)
                        {
                            observed = true;
                            Undo.PerformUndo();
                        }
                    })
                )
                    Error(Capture(Target), "stale_handle");
                Assert.IsTrue(observed);
                Assert.AreEqual(Vector3.one * 2, Second.size);
                Success(Capture(Target));
            }
            finally
            {
                Undo.ClearUndo(Second);
                Undo.IncrementCurrentGroup();
            }
        }

        [TestCase("capture", "before:instance_lookup")]
        [TestCase("capture", "after:instance_lookup")]
        [TestCase("capture", "before:descriptor")]
        [TestCase("capture", "after:descriptor")]
        [TestCase("resolve", "before:descriptor")]
        [TestCase("resolve", "after:descriptor")]
        public void CancellationAtOperationBoundaryPropagates(string action, string boundary)
        {
            string handle = Token(Capture(Target));
            Type handler = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Tools.ManageObjectHandles");
            MethodInfo method = handler.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            using var cancellation = new CancellationTokenSource();
            JObject request =
                action == "capture"
                    ? new JObject { ["action"] = action, ["instance_id"] = Target.GetInstanceIDCompat() }
                    : new JObject { ["action"] = action, ["handle"] = handle };
            bool observed = false;
            using (
                Observe(operation =>
                {
                    if (operation == boundary)
                    {
                        observed = true;
                        cancellation.Cancel();
                    }
                })
            )
            {
                var exception = Assert.Throws<TargetInvocationException>(() => method.Invoke(null, new object[] { request, cancellation.Token }));
                Assert.IsInstanceOf<OperationCanceledException>(exception.InnerException);
            }
            Assert.IsTrue(observed);
            Success(Resolve(handle));
        }

        [Test]
        public void CancellationBeforeResolvePerformsNoOperation()
        {
            string handle = Token(Capture(Target));
            Type handler = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Tools.ManageObjectHandles");
            MethodInfo method = handler.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            int operations = 0;
            using (Observe(_ => operations++))
            {
                var exception = Assert.Throws<TargetInvocationException>(() =>
                    method.Invoke(
                        null,
                        new object[]
                        {
                            new JObject { ["action"] = "resolve", ["handle"] = handle },
                            cancellation.Token,
                        }
                    )
                );
                Assert.IsInstanceOf<OperationCanceledException>(exception.InnerException);
            }
            Assert.AreEqual(0, operations);
        }

        [TestCase("before:descriptor")]
        [TestCase("after:descriptor")]
        public void DestroyedReferenceDuringResolveNeverReturnsSuccess(string boundary)
        {
            string handle = Token(Capture(Target));
            using (
                Observe(operation =>
                {
                    if (operation == boundary)
                        Object.DestroyImmediate(Target);
                })
            )
                Error(Resolve(handle), "stale_handle");
        }

        [TestCase("{}")]
        [TestCase("{\"action\":\"CAPTURE\",\"instance_id\":1}")]
        [TestCase("{\"action\":\"capture\",\"instance_id\":true}")]
        [TestCase("{\"action\":\"capture\",\"instance_id\":0}")]
        [TestCase("{\"action\":\"capture\",\"instance_id\":2147483648}")]
        [TestCase("{\"action\":\"capture\",\"instance_id\":-2147483649}")]
        [TestCase("{\"action\":\"resolve\",\"handle\":null}")]
        [TestCase("{\"action\":\"resolve\",\"handle\":false}")]
        public void InvalidParametersRefuseBeforeOperations(string json)
        {
            int operations = 0;
            using (Observe(_ => operations++))
                Error(Command(JObject.Parse(json)), "invalid_parameters");
            Assert.AreEqual(0, operations);
        }

        [TestCase("", "invalid_handle")]
        [TestCase("mcp-oh-v2.a_b", "invalid_handle")]
        [TestCase("mcp-oh-v1.eyJ2IjoxfQ", "unsupported_handle_version")]
        public void InvalidTokensRefuseWithoutRediscovery(string handle, string code)
        {
            var operations = new List<string>();
            using (Observe(operations.Add))
                Error(Resolve(handle), code);
            Assert.AreEqual(0, operations.Count);
        }

        [Test]
        public void MutatingNonceDoesNotChangeWhichReferenceCanResolve()
        {
            string handle = Token(Capture(Target));
            string mutation = handle.Substring(0, handle.Length - 1) + (handle.EndsWith("a", StringComparison.Ordinal) ? "b" : "a");
            Error(Resolve(mutation), "stale_handle");
            Assert.AreEqual(Target.GetInstanceIDCompat(), Success(Resolve(handle)).Value<int>("instanceID"));
        }

        [Test]
        public void UnsavedSceneAndDirectReparentSaveRetainSessionReference()
        {
            Scene unsaved = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                var go = new GameObject("Unsaved");
                var parent = new GameObject("OwnedParent");
                SceneManager.MoveGameObjectToScene(go, unsaved);
                SceneManager.MoveGameObjectToScene(parent, unsaved);
                string token = Token(Capture(go));
                go.transform.SetParent(parent.transform);
                go.SetActive(false);
                Assert.AreEqual(go.GetInstanceIDCompat(), Success(Resolve(token)).Value<int>("instanceID"));
                Assert.IsTrue(EditorSceneManager.SaveScene(unsaved, AssetRoot + "/PreviouslyUnsaved.unity"));
                Assert.AreEqual("session", Success(Resolve(token)).Value<string>("persistence"));
            }
            finally
            {
                Assert.IsTrue(EditorSceneManager.CloseScene(unsaved, true));
            }
        }

        [Test]
        public void PrefabStageCloseAndReopenNeverResolveOldReferenceOrSourceAsset()
        {
            SavePrefab();
            Assert.IsTrue(EditorSceneManager.SaveScene(OwnedScene));
            string handle;
            try
            {
                var stage = PrefabStageUtility.OpenPrefab(AssetRoot + "/Identity.prefab");
                handle = Token(Capture(stage.prefabContentsRoot));
                Assert.AreEqual(stage.prefabContentsRoot.GetInstanceIDCompat(), Success(Resolve(handle)).Value<int>("instanceID"));
            }
            finally
            {
                StageUtility.GoToMainStage();
            }
            Error(Resolve(handle), "stale_handle");
            try
            {
                var stage = PrefabStageUtility.OpenPrefab(AssetRoot + "/Identity.prefab");
                Error(Resolve(handle), "stale_handle");
                Assert.AreNotEqual(handle, Token(Capture(stage.prefabContentsRoot)));
            }
            finally
            {
                StageUtility.GoToMainStage();
            }
        }

        [Test]
        public void ResolveUsesOnlyRegisteredReferenceWithoutNativeIdentityLookup()
        {
            string handle = Token(Capture(Second));
            var calls = new List<string>();
            using (Observe(calls.Add))
                Assert.AreEqual(Second.GetInstanceIDCompat(), Success(Resolve(handle)).Value<int>("instanceID"));
            CollectionAssert.AreEqual(new[] { "before:descriptor", "after:descriptor" }, calls);
        }

        [Test]
        public void DistinctPrefabInstancesAndChildComponentsNeverAlias()
        {
            GameObject asset = SavePrefab();
            var first = (GameObject)PrefabUtility.InstantiatePrefab(asset, OwnedScene);
            var second = (GameObject)PrefabUtility.InstantiatePrefab(asset, OwnedScene);
            Object[] targets =
            {
                first,
                second,
                first.transform.GetChild(0).GetComponent<BoxCollider>(),
                second.transform.GetChild(0).GetComponent<BoxCollider>(),
            };
            string[] handles = targets.Select(target => Token(Capture(target))).ToArray();
            Assert.AreEqual(4, handles.Distinct().Count());
            for (int i = 0; i < targets.Length; i++)
                Assert.AreEqual(targets[i].GetInstanceIDCompat(), Success(Resolve(handles[i])).Value<int>("instanceID"));
            Object.DestroyImmediate(first);
            PrefabUtility.InstantiatePrefab(asset, OwnedScene);
            Assert.IsTrue(EditorSceneManager.SaveScene(OwnedScene));
            Error(Resolve(handles[0]), "stale_handle");
            Error(Resolve(handles[2]), "stale_handle");
            Assert.AreEqual(second.GetInstanceIDCompat(), Success(Resolve(handles[1])).Value<int>("instanceID"));
        }

        [Test]
        public async System.Threading.Tasks.Task ActualDispatcherAcceptsOnlyV2Command()
        {
            CommandRegistry.Initialize();
            var request = new JObject
            {
                ["type"] = "manage_object_handles_v2",
                ["params"] = new JObject { ["action"] = "capture", ["instance_id"] = Target.GetInstanceIDCompat() },
            };
            JObject response = JObject.Parse(
                await MCPForUnity.Editor.Services.Transport.TransportCommandDispatcher.ExecuteCommandJsonAsync(request.ToString(), CancellationToken.None)
            );
            Assert.AreEqual(2, Success((JObject)response["result"]).Value<int>("contractVersion"));
            request["type"] = "manage_object_handles_v1";
            response = JObject.Parse(
                await MCPForUnity.Editor.Services.Transport.TransportCommandDispatcher.ExecuteCommandJsonAsync(request.ToString(), CancellationToken.None)
            );
            Assert.AreEqual("error", response.Value<string>("status"));
        }
    }
}
