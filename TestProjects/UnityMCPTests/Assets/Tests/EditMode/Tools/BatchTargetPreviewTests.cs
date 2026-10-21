using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

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

        private static JObject Apply(JArray commands, JToken preview) =>
            JObject.FromObject(BatchExecute.HandleCommand(new JObject { ["commands"] = commands, ["preview"] = preview.DeepClone() }).GetAwaiter().GetResult());

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

        [Test]
        public void PreviewDoesNotMutateAndCurrentObjectGuardApplies()
        {
            int dirty = EditorUtility.GetDirtyCount(root);
            var commands = new JArray(Edit());
            JToken guard = Guard(Preview(commands));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(root));
            JObject result = Apply(commands, guard);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(new Vector3(1, 2, 3), root.transform.localPosition);
            Assert.IsFalse(result["data"].Value<bool>("rollbackPerformed"));
        }

        [Test]
        public void StaleLaterObjectRefusesBeforeEarlierMutation()
        {
            var other = new GameObject("Other");
            var commands = new JArray(Edit(), Edit(other));
            JToken guard = Guard(Preview(commands));
            other.name = "Changed";
            JObject result = Apply(commands, guard);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0, result["data"].Value<int>("attemptedCount"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
        }

        [Test]
        public void TamperedGuardAndChangedCommandsRefuse()
        {
            var commands = new JArray(Edit());
            var guard = (JObject)Guard(Preview(commands)).DeepClone();
            guard["signature"] = "tampered";
            Assert.IsFalse(Apply(commands, guard).Value<bool>("success"));
            guard = (JObject)Guard(Preview(commands)).DeepClone();
            commands[0]["params"]["position"] = new JArray(8, 9, 10);
            Assert.IsFalse(Apply(commands, guard).Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
        }

        [Test]
        public void AssetMoveDetectsDestinationCollisionAndLateSourceBytes()
        {
            string source = Asset("Source"),
                destination = folder + "/Destination.txt";
            var commands = new JArray(Move(source, destination));
            JToken guard = Guard(Preview(commands));
            File.AppendAllText(source, "changed without import");
            Assert.IsFalse(Apply(commands, guard).Value<bool>("success"));
            Assert.IsTrue(File.Exists(source));
            Assert.IsFalse(File.Exists(destination));
            Asset("Destination");
            JObject collision = Preview(commands);
            Assert.IsFalse(collision.Value<bool>("success"));
            Assert.AreEqual("rejected", (string)collision.SelectToken("data.validation.items[0].status"));
        }

        [Test]
        public void CurrentAssetMoveGuardAppliesWithoutPreviewRefresh()
        {
            string source = Asset("Source"),
                destination = folder + "/Destination.txt";
            string guid = AssetDatabase.AssetPathToGUID(source);
            var commands = new JArray(Move(source, destination));
            JToken guard = Guard(Preview(commands));
            Assert.IsTrue(File.Exists(source));
            Assert.IsFalse(File.Exists(destination));
            JObject result = Apply(commands, guard);
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
        public void PreparedPrefabPlanIsObservedWithoutConsumptionAndRechecked()
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
            JObject result = Apply(commands, guard);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(new Vector3(4, 5, 6), AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.localPosition);
            Assert.IsFalse(Apply(commands, guard).Value<bool>("success"), "Consumed prefab plans cannot be replayed.");
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
        public void ReplacedHandlerInvalidatesGuardWithoutDispatch()
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
                Assert.IsFalse(Apply(commands, guard).Value<bool>("success"));
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
        public void DisabledChildAfterPreviewRefusesWholeGuardedBatch()
        {
            var commands = new JArray(Edit());
            JToken guard = Guard(Preview(commands));
            MCPServiceLocator.ToolDiscovery.SetToolEnabled("gameobject_edit", false);
            Assert.IsFalse(Apply(commands, guard).Value<bool>("success"));
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
