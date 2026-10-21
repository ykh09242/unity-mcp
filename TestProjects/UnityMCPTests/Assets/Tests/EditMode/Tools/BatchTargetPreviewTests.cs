using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;
using Stopwatch = System.Diagnostics.Stopwatch;

namespace MCPForUnityTests.Editor.Tools
{
    public class BatchTargetPreviewTests
    {
        private readonly PrefabTestSceneFixture fixture = new PrefabTestSceneFixture();
        private readonly Dictionary<string, bool> enabled = new Dictionary<string, bool>();
        private MCPForUnityTests.Editor.Services.ScopedConsentTestState consent;
        private GameObject root;
        private string folder;

        [OneTimeSetUp]
        public void Bootstrap() => fixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => fixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            fixture.Create("McpBatchPreview_", Guid.NewGuid().ToString("N"));
            root = new GameObject("BatchPreviewRoot");
            folder = "Assets/__McpBatchPreview_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            consent = new MCPForUnityTests.Editor.Services.ScopedConsentTestState("batch_execute");
            foreach (string tool in new[] { "gameobject_edit", "asset_edit", "prefab_contents" })
            {
                enabled.Add(tool, MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool));
                MCPServiceLocator.ToolDiscovery.SetToolEnabled(tool, true);
            }
            CommandRegistry.Initialize();
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var item in enabled)
                MCPServiceLocator.ToolDiscovery.SetToolEnabled(item.Key, item.Value);
            enabled.Clear();
            BatchPreviewCallbackProbe.OnDisabled = null;
            consent?.Dispose();
            Undo.ClearAll();
            fixture.Close();
            if (AssetDatabase.IsValidFolder(folder))
                AssetDatabase.DeleteAsset(folder);
        }

        private JObject Edit(GameObject target = null) =>
            new JObject
            {
                ["tool"] = "gameobject_edit",
                ["params"] = new JObject
                {
                    ["action"] = "modify",
                    ["target"] = (target ?? root).GetInstanceIDCompat(),
                    ["search_method"] = "by_id",
                    ["position"] = new JArray(1, 2, 3),
                },
            };

        private JObject Move(string source, string destination) =>
            new JObject
            {
                ["tool"] = "asset_edit",
                ["params"] = new JObject
                {
                    ["action"] = "move",
                    ["path"] = source,
                    ["destination"] = destination,
                },
            };

        private static JObject Preview(JArray commands) =>
            JObject.FromObject(BatchExecute.ValidateCommand(new JObject { ["commands"] = commands, ["previewScope"] = "targets" }));

        private static async Task<JObject> Apply(JArray commands, JToken preview) =>
            JObject.FromObject(await BatchExecute.HandleCommand(new JObject { ["commands"] = commands, ["preview"] = preview.DeepClone() }));

        private static JToken Guard(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("targets", (string)response.SelectToken("data.validation.scope"));
            Assert.IsNotNull(response.SelectToken("data.preview"));
            return response["data"]["preview"];
        }

        private string Asset(string name)
        {
            string path = folder + "/" + name + ".txt";
            File.WriteAllText(path, "original");
            AssetDatabase.ImportAsset(path);
            return path;
        }

        private string LargeAsset(int mebibytes = 8)
        {
            string path = Asset("LargeSource");
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.None))
                stream.SetLength((long)mebibytes * 1024 * 1024);
            return path;
        }

        [Test]
        public async Task AssetPreviewYieldsAndHonorsCancellationBeforeIssuingGuard()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Cooperative file hashing requires the Windows read-sharing guarantee; other Editors retain synchronous hashing.");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                string path = LargeAsset();
                var commands = new JArray(Move(path, folder + "/Moved.txt"));
                using (var cancellation = new CancellationTokenSource())
                {
                    Task<object> pending = CommandRegistry.InvokeCommandAsync(
                        "batch_validate",
                        new JObject { ["commands"] = commands, ["previewScope"] = "targets" },
                        cancellation.Token
                    );
                    Assert.IsFalse(pending.IsCompleted, "Large-file hashing must yield the Editor thread.");
                    cancellation.Cancel();
                    try
                    {
                        await pending;
                        Assert.Fail("Cancellation must not issue a usable guard.");
                    }
                    catch (OperationCanceledException) { }
                }
                Assert.IsTrue(File.Exists(path));
                Assert.IsFalse(File.Exists(folder + "/Moved.txt"));
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [Test]
        public async Task AsyncPreviewRechecksPermissionsAfterFileWork()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Cooperative file hashing requires the Windows read-sharing guarantee; other Editors retain synchronous hashing.");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                string path = LargeAsset();
                Task<object> pending = CommandRegistry.InvokeCommandAsync(
                    "batch_validate",
                    new JObject { ["commands"] = new JArray(Move(path, folder + "/Moved.txt")), ["previewScope"] = "targets" }
                );
                MCPServiceLocator.ToolDiscovery.SetToolEnabled("asset_edit", false);
                JObject result = JObject.FromObject(await pending);
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.IsTrue(File.Exists(path));
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [Test]
        public async Task GuardedApplyCancellationSettlesFileWorkWithoutMutatingEarlierTarget()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Cooperative file hashing requires the Windows read-sharing guarantee; other Editors retain synchronous hashing.");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                string path = LargeAsset();
                var commands = new JArray(Edit(), Move(path, folder + "/Moved.txt"));
                JToken guard = Guard(Preview(commands));
                using (var cancellation = new CancellationTokenSource())
                {
                    Task<object> pending = BatchExecute.HandleCommand(new JObject { ["commands"] = commands, ["preview"] = guard }, cancellation.Token);
                    Assert.IsFalse(pending.IsCompleted);
                    cancellation.Cancel();
                    try
                    {
                        await pending;
                        Assert.Fail("Canceled verification must not dispatch children.");
                    }
                    catch (OperationCanceledException) { }
                }
                Assert.AreEqual(Vector3.zero, root.transform.localPosition);
                using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                Assert.IsFalse(File.Exists(folder + "/Moved.txt"));
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [Test]
        public async Task EarlierObjectChangeDuringLaterFilePreviewInvalidatesWholeGuard()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Cooperative file hashing requires the Windows read-sharing guarantee; other Editors retain synchronous hashing.");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                string path = LargeAsset();
                var commands = new JArray(Edit(), Move(path, folder + "/Moved.txt"));
                Task<object> pending = CommandRegistry.InvokeCommandAsync(
                    "batch_validate",
                    new JObject { ["commands"] = commands, ["previewScope"] = "targets" }
                );
                root.name = "ChangedWhileHashing";
                JObject result = JObject.FromObject(await pending);
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual("target_changed_during_preparation", (string)result.SelectToken("data.validation.items[0].reason"));
                Assert.AreEqual(JTokenType.Null, result["data"]["preview"].Type);
                Assert.AreEqual(Vector3.zero, root.transform.localPosition);
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [Test]
        public async Task EarlierObjectChangeDuringLaterFileVerificationRefusesAllMutations()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Cooperative file hashing requires the Windows read-sharing guarantee; other Editors retain synchronous hashing.");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                string path = LargeAsset();
                var commands = new JArray(Edit(), Move(path, folder + "/Moved.txt"));
                JToken guard = Guard(Preview(commands));
                Task<JObject> pending = Apply(commands, guard);
                root.name = "ChangedWhileVerifying";
                JObject result = await pending;
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(0, result["data"].Value<int>("attemptedCount"));
                Assert.AreEqual(Vector3.zero, root.transform.localPosition);
                Assert.IsTrue(File.Exists(path));
                Assert.IsFalse(File.Exists(folder + "/Moved.txt"));
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [Test]
        public async Task EarlierFileCannotChangeUnnoticedWhileLaterFileIsVerified()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Cooperative file hashing requires the Windows read-sharing guarantee; other Editors retain synchronous hashing.");
            AssetDatabase.DisallowAutoRefresh();
            EditorApplication.CallbackFunction callback = null;
            try
            {
                string earlier = Asset("Earlier"),
                    later = LargeAsset();
                var commands = new JArray(Edit(), Move(earlier, folder + "/EarlierMoved.txt"), Move(later, folder + "/LaterMoved.txt"));
                JToken guard = Guard(Preview(commands));
                bool attemptedWrite = false,
                    changed = false;
                callback = () =>
                {
                    if (attemptedWrite)
                        return;
                    // Wait for the actual later-file reader, without a scheduler-duration guess.
                    try
                    {
                        using (File.Open(later, FileMode.Open, FileAccess.Write, FileShare.None)) { }
                        return;
                    }
                    catch (IOException) { }
                    attemptedWrite = true;
                    try
                    {
                        File.AppendAllText(earlier, "changed during later hash");
                        changed = true;
                    }
                    catch (IOException) { /* A held read lease may prevent the write. */ }
                };
                EditorApplication.update += callback;
                var verify = typeof(BatchExecute)
                    .Assembly.GetType("MCPForUnity.Editor.Tools.BatchTargetPreview")
                    .GetMethod("VerifyAsync", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
                Task pending = (Task)verify.Invoke(null, new object[] { commands, guard, CancellationToken.None });
                await pending;
                object observation = pending.GetType().GetProperty("Result").GetValue(pending);
                bool current = observation != null;
                (observation as IDisposable)?.Dispose();
                Assert.IsTrue(attemptedWrite, "The fixture must exercise the actual later-file read interval.");
                Assert.AreEqual(!changed, current, "A byte change during another file read cannot retain a valid whole-batch observation.");
                using (File.Open(earlier, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                using (File.Open(later, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                Assert.AreEqual(Vector3.zero, root.transform.localPosition);
            }
            finally
            {
                if (callback != null)
                    EditorApplication.update -= callback;
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [Test, Explicit("Controller-owned large-file A/B measurement; ordinary regressions use smaller files.")]
        public async Task LargeAssetPreviewMeasurementPreservesStrongRevision()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Cooperative file hashing requires the Windows read-sharing guarantee; other Editors retain synchronous hashing.");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                string path = LargeAsset(128);
                var commands = new JArray(Move(path, folder + "/Moved.txt"));
                JToken expected = Guard(Preview(commands));
                // Counterbalanced warm A/B pairs use the same bytes, metadata and target identities.
                for (int round = 0; round < 3; round++)
                for (int position = 0; position < 2; position++)
                {
                    bool asynchronous = (round + position) % 2 == 0;
                    int updates = 0;
                    double maximumGap = 0;
                    var timer = Stopwatch.StartNew();
                    double previous = timer.Elapsed.TotalMilliseconds;
                    void Observe()
                    {
                        double now = timer.Elapsed.TotalMilliseconds;
                        maximumGap = Math.Max(maximumGap, now - previous);
                        previous = now;
                        updates++;
                    }
                    EditorApplication.update += Observe;
                    try
                    {
                        Task<object> pending = asynchronous
                            ? CommandRegistry.InvokeCommandAsync("batch_validate", new JObject { ["commands"] = commands, ["previewScope"] = "targets" })
                            : Task.FromResult<object>(BatchExecute.ValidateCommand(new JObject { ["commands"] = commands, ["previewScope"] = "targets" }));
                        double dispatchMs = timer.Elapsed.TotalMilliseconds;
                        JObject response = JObject.FromObject(await pending);
                        double totalMs = timer.Elapsed.TotalMilliseconds;
                        maximumGap = Math.Max(maximumGap, totalMs - previous);
                        Assert.IsTrue(JToken.DeepEquals(expected, Guard(response)), response.ToString());
                        TestContext.WriteLine(
                            $"BETA03 mode={(asynchronous ? "async" : "sync")} round={round} bytes={new FileInfo(path).Length} dispatch_ms={dispatchMs:F3} total_ms={totalMs:F3} updates={updates} max_gap_ms={maximumGap:F3}"
                        );
                    }
                    finally
                    {
                        EditorApplication.update -= Observe;
                    }
                }
                Assert.IsTrue(File.Exists(path));
                Assert.IsFalse(File.Exists(folder + "/Moved.txt"));
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [Test]
        public async Task PreviewDoesNotMutateAndCurrentObjectGuardApplies()
        {
            int dirty = EditorUtility.GetDirtyCount(root);
            var commands = new JArray(Edit());
            JToken guard = Guard(Preview(commands));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(root));
            JObject result = await Apply(commands, guard);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(new Vector3(1, 2, 3), root.transform.localPosition);
            Assert.IsFalse(result["data"].Value<bool>("rollbackPerformed"));
        }

        [Test]
        public async Task StaleLaterObjectRefusesBeforeEarlierMutation()
        {
            var other = new GameObject("Other");
            var commands = new JArray(Edit(), Edit(other));
            JToken guard = Guard(Preview(commands));
            other.name = "Changed";
            JObject result = await Apply(commands, guard);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0, result["data"].Value<int>("attemptedCount"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
        }

        [Test]
        public async Task TamperedGuardAndChangedCommandsRefuse()
        {
            var commands = new JArray(Edit());
            var guard = (JObject)Guard(Preview(commands)).DeepClone();
            guard["signature"] = "tampered";
            Assert.IsFalse((await Apply(commands, guard)).Value<bool>("success"));
            guard = (JObject)Guard(Preview(commands)).DeepClone();
            commands[0]["params"]["position"] = new JArray(8, 9, 10);
            Assert.IsFalse((await Apply(commands, guard)).Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
        }

        [Test]
        public async Task AssetMoveDetectsDestinationCollisionAndLateSourceBytes()
        {
            string source = Asset("Source"),
                destination = folder + "/Destination.txt";
            var commands = new JArray(Move(source, destination));
            JToken guard = Guard(Preview(commands));
            File.AppendAllText(source, "changed without import");
            Assert.IsFalse((await Apply(commands, guard)).Value<bool>("success"));
            using (File.Open(source, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            using (File.Open(source + ".meta", FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            Assert.IsTrue(File.Exists(source));
            Assert.IsFalse(File.Exists(destination));
            Asset("Destination");
            JObject collision = Preview(commands);
            Assert.IsFalse(collision.Value<bool>("success"));
            Assert.AreEqual("rejected", (string)collision.SelectToken("data.validation.items[0].status"));
        }

        [Test]
        public async Task CurrentAssetMoveGuardAppliesWithoutPreviewRefresh()
        {
            string source = Asset("Source"),
                destination = folder + "/Destination.txt";
            string guid = AssetDatabase.AssetPathToGUID(source);
            var commands = new JArray(Move(source, destination));
            JToken guard = Guard(Preview(commands));
            Assert.IsTrue(File.Exists(source));
            Assert.IsFalse(File.Exists(destination));
            JObject result = await Apply(commands, guard);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(destination));
        }

        [Test]
        public void UnsupportedFutureAndRepeatedTargetsRemainIncomplete()
        {
            var future = Edit();
            future["params"]["target"] = "$future";
            JObject unresolved = Preview(new JArray(future));
            Assert.IsFalse(unresolved.Value<bool>("success"));
            Assert.IsFalse(unresolved.SelectToken("data.validation.complete").Value<bool>());
            Assert.AreEqual("incomplete", (string)unresolved.SelectToken("data.validation.items[0].status"));
            JObject repeated = Preview(new JArray(Edit(), Edit()));
            Assert.IsFalse(repeated.Value<bool>("success"));
            Assert.IsFalse(repeated.SelectToken("data.validation.complete").Value<bool>());
        }

        [Test]
        public async Task PreparedPrefabPlanIsObservedWithoutConsumptionAndRechecked()
        {
            string path = folder + "/Root.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            JObject prepared = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "preview_modify_contents",
                        ["prefabPath"] = path,
                        ["position"] = new JArray(4, 5, 6),
                    }
                )
            );
            Assert.IsTrue(prepared.Value<bool>("success"), prepared.ToString());
            var commands = new JArray(
                new JObject
                {
                    ["tool"] = "prefab_contents",
                    ["params"] = new JObject { ["action"] = "apply_preview", ["preview_id"] = prepared["data"]["preview_id"] },
                }
            );
            byte[] before = File.ReadAllBytes(path);
            JToken guard = Guard(Preview(commands));
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            JObject result = await Apply(commands, guard);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(new Vector3(4, 5, 6), AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.localPosition);
            Assert.IsFalse((await Apply(commands, guard)).Value<bool>("success"), "Consumed prefab plans cannot be replayed.");
        }

        [Test]
        public async Task EarlierCallbackInvalidatesLaterTargetWithoutClaimingRollback()
        {
            var other = new GameObject("LaterTarget");
            root.AddComponent<BatchPreviewCallbackProbe>();
            var first = Edit();
            first["params"]["set_active"] = false;
            var commands = new JArray(first, Edit(other));
            JToken guard = Guard(Preview(commands));
            BatchPreviewCallbackProbe.OnDisabled = () => other.name = "ChangedByCallback";
            JObject result = JObject.FromObject(await BatchExecute.HandleCommand(new JObject { ["commands"] = commands, ["preview"] = guard.DeepClone() }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(1, result["data"].Value<int>("attemptedCount"));
            Assert.AreEqual("stale_preview", (string)result.SelectToken("data.outcomes[1].reason"));
            Assert.IsFalse(result["data"].Value<bool>("rollbackPerformed"));
            Assert.AreEqual(new Vector3(1, 2, 3), root.transform.localPosition);
            Assert.AreEqual(Vector3.zero, other.transform.localPosition);
        }

        [Test]
        public async Task ReplacedHandlerInvalidatesGuardWithoutDispatch()
        {
            var commands = new JArray(Edit());
            JToken guard = Guard(Preview(commands));
            var field = typeof(CommandRegistry).GetField("_handlers", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic);
            var handlers = (System.Collections.IDictionary)field.GetValue(null);
            object previous = handlers["gameobject_edit"];
            try
            {
                Type handlerType = previous.GetType();
                var callback = new Func<JObject, object>(_ => throw new InvalidOperationException("Replacement must not execute"));
                handlers["gameobject_edit"] = Activator.CreateInstance(handlerType, new object[] { "gameobject_edit", callback, null });
                Assert.IsFalse((await Apply(commands, guard)).Value<bool>("success"));
                Assert.AreEqual(Vector3.zero, root.transform.localPosition);
            }
            finally
            {
                handlers["gameobject_edit"] = previous;
            }
        }

        [Test]
        public void DedicatedApplyRequiresGuardAndRejectsNestedAlias()
        {
            var commands = new JArray(Edit());
            JObject result = JObject.FromObject(
                CommandRegistry.InvokeCommandAsync("batch_apply_preview", new JObject { ["commands"] = commands }).GetAwaiter().GetResult()
            );
            Assert.IsFalse(result.Value<bool>("success"));
            result = Preview(
                new JArray(
                    new JObject
                    {
                        ["tool"] = "batch_apply_preview",
                        ["params"] = new JObject { ["commands"] = commands },
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
        }

        [Test]
        public void InvalidVectorRejectsWithoutChangingTarget()
        {
            var edit = Edit();
            edit["params"]["position"] = new JArray(1, 2);
            JObject result = Preview(new JArray(edit));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual("rejected", (string)result.SelectToken("data.validation.items[0].status"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
        }

        [Test]
        public void UnsupportedNullFieldsCannotProduceGuard()
        {
            var parent = new GameObject("Parent");
            root.transform.SetParent(parent.transform);
            root.tag = "Player";
            foreach (string field in new[] { "parent", "tag" })
            {
                var edit = Edit();
                edit["params"][field] = JValue.CreateNull();
                JObject result = Preview(new JArray(edit));
                Assert.IsFalse(result.Value<bool>("success"), field + result);
                Assert.AreEqual("incomplete", (string)result.SelectToken("data.validation.items[0].status"));
                Assert.AreEqual(JTokenType.Null, result["data"]["preview"].Type);
                Assert.AreEqual(parent.transform, root.transform.parent);
                Assert.AreEqual("Player", root.tag);
            }
        }

        [Test]
        public void InactiveTargetRequiresExplicitActivation()
        {
            root.SetActive(false);
            JObject result = Preview(new JArray(Edit()));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("rejected", (string)result.SelectToken("data.validation.items[0].status"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
        }

        [Test]
        public async Task ExplicitActivationOfInactiveTargetRemainsSupported()
        {
            root.SetActive(false);
            var edit = Edit();
            edit["params"]["set_active"] = true;
            var commands = new JArray(edit);
            JObject result = await Apply(commands, Guard(Preview(commands)));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(root.activeSelf);
            Assert.AreEqual(new Vector3(1, 2, 3), root.transform.localPosition);
        }

        [Test]
        public void NonActiveAdditiveSceneTargetCannotProduceGuard()
        {
            Scene active = SceneManager.GetActiveScene();
            Assert.IsTrue(EditorSceneManager.SaveScene(active, folder + "/" + active.name + ".unity"));
            Scene additive = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                var other = new GameObject("OtherScene");
                SceneManager.MoveGameObjectToScene(other, additive);
                SceneManager.SetActiveScene(active);
                JObject result = Preview(new JArray(Edit(), Edit(other)));
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual("rejected", (string)result.SelectToken("data.validation.items[1].status"));
                Assert.AreEqual(Vector3.zero, root.transform.localPosition);
                Assert.AreEqual(Vector3.zero, other.transform.localPosition);
            }
            finally
            {
                SceneManager.SetActiveScene(active);
                EditorSceneManager.CloseScene(additive, true);
            }
        }

        [Test]
        public async Task ActiveSceneChangeRefusesBeforeFirstMutation()
        {
            var other = new GameObject("LaterTarget");
            var commands = new JArray(Edit(), Edit(other));
            Scene active = SceneManager.GetActiveScene();
            Assert.IsTrue(EditorSceneManager.SaveScene(active, folder + "/" + active.name + ".unity"));
            JToken guard = Guard(Preview(commands));
            Scene additive = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                SceneManager.SetActiveScene(additive);
                JObject result = JObject.FromObject(await BatchExecute.HandleCommand(new JObject { ["commands"] = commands, ["preview"] = guard }));
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(0, result["data"].Value<int>("attemptedCount"));
                Assert.AreEqual(Vector3.zero, root.transform.localPosition);
                Assert.AreEqual(Vector3.zero, other.transform.localPosition);
            }
            finally
            {
                SceneManager.SetActiveScene(active);
                EditorSceneManager.CloseScene(additive, true);
            }
        }

        [Test]
        public async Task AncestorDeactivationRefusesBeforeFirstMutation()
        {
            var parent = new GameObject("Parent");
            var other = new GameObject("LaterTarget");
            other.transform.SetParent(parent.transform);
            var commands = new JArray(Edit(), Edit(other));
            JToken guard = Guard(Preview(commands));
            parent.SetActive(false);
            JObject result = JObject.FromObject(await BatchExecute.HandleCommand(new JObject { ["commands"] = commands, ["preview"] = guard }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0, result["data"].Value<int>("attemptedCount"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
            Assert.AreEqual(Vector3.zero, other.transform.localPosition);
        }

        [Test]
        public async Task DisabledChildAfterPreviewRefusesWholeGuardedBatch()
        {
            var commands = new JArray(Edit());
            JToken guard = Guard(Preview(commands));
            MCPServiceLocator.ToolDiscovery.SetToolEnabled("gameobject_edit", false);
            Assert.IsFalse((await Apply(commands, guard)).Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
        }
    }

    [ExecuteAlways]
    public sealed class BatchPreviewCallbackProbe : MonoBehaviour
    {
        internal static Action OnDisabled;

        private void OnDisable() => OnDisabled?.Invoke();
    }
}
