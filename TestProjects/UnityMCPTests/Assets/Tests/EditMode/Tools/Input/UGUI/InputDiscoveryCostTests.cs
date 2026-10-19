using System;
using System.Collections.Generic;
using System.Diagnostics;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class InputDiscoveryCostTests
    {
        private Scene scene;
        private Scene preview;
        private IPlayScenarioRecordingSession session;
        private object sceneFixture;

        private object Fixture(string method, params object[] args) => sceneFixture.GetType().GetMethod(method).Invoke(sceneFixture, args);

        [OneTimeSetUp]
        public void Prepare()
        {
            Type type = Type.GetType("MCPForUnityTests.Editor.Helpers.PrefabTestSceneFixture, MCPForUnityTests.EditMode", true);
            sceneFixture = Activator.CreateInstance(type, true);
            Fixture("PrepareRunnerBootstrap");
        }

        [OneTimeTearDown]
        public void Restore() => Fixture("RestoreRunnerBootstrap");

        [SetUp]
        public void SetUp() => scene = (Scene)Fixture("Create", "McpInputDiscovery_", Guid.NewGuid().ToString("N"));

        [TearDown]
        public void TearDown()
        {
            session?.Dispose();
            session = null;
            if (preview.IsValid())
                EditorSceneManager.ClosePreviewScene(preview);
            Fixture("Close");
        }

        private GameObject Root(string name, params Type[] components)
        {
            var value = new GameObject(name, components);
            SceneManager.MoveGameObjectToScene(value, scene);
            return value;
        }

        [Test]
        public void RecorderDoesNotAttachToPreviewScenes()
        {
            preview = EditorSceneManager.NewPreviewScene();
            var value = new GameObject("PreviewButton", typeof(RectTransform), typeof(Button));
            SceneManager.MoveGameObjectToScene(value, preview);
            session = new UguiScenarioRecordingBackend().Begin(_ => { }, _ => { });
            Assert.IsNull(value.GetComponent<UguiScenarioRecordingObserver>());
        }

        [Test]
        public void WarmRecorderReconciliationReusesInventoryStorage()
        {
            var root = Root("RecorderCost-" + Guid.NewGuid().ToString("N"));
            for (int i = 0; i < 256; i++)
            {
                var child = new GameObject("Button" + i, typeof(RectTransform), typeof(Button));
                child.transform.SetParent(root.transform);
            }
            session = new UguiScenarioRecordingBackend().Begin(_ => { }, _ => { });
            for (int i = 0; i < 3; i++)
                session.Refresh();
            long before = GC.GetAllocatedBytesForCurrentThread();
            var watch = Stopwatch.StartNew();
            for (int i = 0; i < 20; i++)
                session.Refresh();
            watch.Stop();
            long allocated = GC.GetAllocatedBytesForCurrentThread() - before;
            TestContext.WriteLine($"recorder_256_buttons_20_scans bytes={allocated} ms={watch.Elapsed.TotalMilliseconds:F3}");
            TestContext.WriteLine("allocation_counter_available=" + (allocated != 0));
            Assert.AreEqual(256, root.GetComponentsInChildren<UguiScenarioRecordingObserver>().Length);
            if (allocated != 0)
                Assert.Less(allocated, 8192, "Warm reconciliation must reuse lists instead of allocating a global Button array each time.");
        }

        [Test]
        public void ExactPathsPreserveAmbiguityInactiveAndPreviewRules()
        {
            string name = "Target-" + Guid.NewGuid().ToString("N");
            var root = Root(name);
            var target = new GameObject("Child");
            target.transform.SetParent(root.transform);
            Assert.AreSame(target, InputTargetResolver.Resolve(new JValue(name + "/Child")));
            target.SetActive(false);
            Assert.Throws<ArgumentException>(() => InputTargetResolver.Resolve(new JValue(name + "/Child")));
            target.SetActive(true);
            var duplicate = new GameObject("Child");
            duplicate.transform.SetParent(root.transform);
            Assert.Throws<ArgumentException>(() => InputTargetResolver.Resolve(new JValue(name + "/Child")));
            Object.DestroyImmediate(duplicate);
            preview = EditorSceneManager.NewPreviewScene();
            var previewRoot = new GameObject(name);
            SceneManager.MoveGameObjectToScene(previewRoot, preview);
            Assert.AreSame(root, InputTargetResolver.Resolve(new JValue(name)));
        }

        [Test]
        public void RepeatedPathLookupDoesNotAllocateTheLoadedObjectInventory()
        {
            string name = "LookupCost-" + Guid.NewGuid().ToString("N");
            var root = Root(name);
            var branch = new GameObject("Noise");
            branch.transform.SetParent(root.transform);
            for (int i = 0; i < 3000; i++)
            {
                var child = new GameObject("Noise" + i);
                child.transform.SetParent(branch.transform);
            }
            var target = new GameObject("Target");
            target.transform.SetParent(root.transform);
            var path = new JValue(name + "/Target");
            InputTargetResolver.Resolve(path);
            long before = GC.GetAllocatedBytesForCurrentThread();
            var watch = Stopwatch.StartNew();
            for (int i = 0; i < 50; i++)
                Assert.AreSame(target, InputTargetResolver.Resolve(path));
            watch.Stop();
            long allocated = GC.GetAllocatedBytesForCurrentThread() - before;
            TestContext.WriteLine($"path_3000_objects_50_lookups bytes={allocated} ms={watch.Elapsed.TotalMilliseconds:F3}");
            TestContext.WriteLine("allocation_counter_available=" + (allocated != 0));
            if (allocated != 0)
                Assert.Less(allocated, 200000, "Path lookup must enumerate scene roots, not allocate all loaded GameObjects.");
        }
    }
}
