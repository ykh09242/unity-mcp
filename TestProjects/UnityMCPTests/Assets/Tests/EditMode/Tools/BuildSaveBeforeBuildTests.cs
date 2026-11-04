using System;
using System.IO;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Build;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// SaveBeforeBuild originally called EditorSceneManager.SaveOpenScenes() unconditionally.
    /// A scene that has never been saved carries an empty path, and handing one to Unity's save
    /// API opens the modal "Save Scene" file panel — the exact block manage_build exists to
    /// avoid (issue #1341). These tests pin the guard so the modal cannot come back.
    /// </summary>
    [TestFixture]
    public class BuildSaveBeforeBuildTests
    {
        private readonly PrefabTestSceneFixture fixture = new PrefabTestSceneFixture();
        private Scene ownedScene;

        [OneTimeSetUp]
        public void OneTimeSetUp() => fixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => fixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp() => ownedScene = fixture.Create("McpBuildSave_", Guid.NewGuid().ToString("N"));

        [TearDown]
        public void TearDown() => fixture.Close();

        [TestCase(false)]
        [TestCase(true)]
        public void SceneSaveFailure_PreventsNativeInvocationAndRecordsFailedJob(bool throws)
        {
            Assert.IsFalse(BuildRunner.HasNativeOwner || BuildRunner.NativeRecoveryRequired, "The fixture requires an idle native build boundary.");
            string folder = "Assets/__McpBuildSave_" + Guid.NewGuid().ToString("N");
            string guid = AssetDatabase.CreateFolder("Assets", folder.Substring("Assets/".Length));
            Assert.IsNotEmpty(guid);
            var originalSave = BuildRunner.SaveSceneForBuild;
            var lastCompleted = typeof(BuildJobStore).GetField("_lastCompletedJob", BindingFlags.Static | BindingFlags.NonPublic);
            var originalLastCompleted = lastCompleted.GetValue(null);
            var storageRoot = typeof(BuildReportStore).GetField("StorageRootOverride", BindingFlags.Static | BindingFlags.NonPublic);
            var originalStorage = storageRoot.GetValue(null);
            string reportRoot = "Library/MCPForUnityTests/BuildSave_" + Guid.NewGuid().ToString("N");
            var job = new BuildJob("save-failure-" + Guid.NewGuid().ToString("N"), BuildTarget.StandaloneWindows64, "unused-output");
            int nativeCalls = 0;
            int saveCalls = 0;
            try
            {
                storageRoot.SetValue(null, reportRoot);
                string path = folder + "/" + ownedScene.name + ".unity";
                Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, path));
                StringAssert.StartsWith("McpBuildSave_", ownedScene.name, "Saving the owned scene must preserve the cleanup ownership prefix.");
                EditorSceneManager.MarkSceneDirty(ownedScene);
                BuildRunner.SaveSceneForBuild = scene =>
                {
                    Assert.AreEqual(ownedScene, scene, "Only the owned dirty scene may reach the injected save boundary.");
                    saveCalls++;
                    if (throws)
                        throw new IOException("injected scene-save failure");
                    return false;
                };
                LogAssert.Expect(LogType.Error, new Regex(@"\[MCP Build\].*Build failed: StandaloneWindows64"));
                var run = typeof(BuildRunner).GetMethod("RunBuildCore", BindingFlags.Static | BindingFlags.NonPublic);
                run.Invoke(
                    null,
                    new object[]
                    {
                        job,
                        new Func<BuildReport>(() =>
                        {
                            nativeCalls++;
                            throw new InvalidOperationException("native invocation must not be reached");
                        }),
                        null,
                    }
                );
                Assert.AreEqual(1, saveCalls);
                Assert.AreEqual(0, nativeCalls, "An unsaved build input must stop before the native boundary.");
                Assert.AreEqual(BuildJobState.Failed, job.State);
                var status = JObject.FromObject(job.ToStatusResponse());
                Assert.AreEqual("failed", status.Value<string>("result"));
                StringAssert.Contains(path, status.Value<string>("error"));
                Assert.IsNotNull(status["completed_at"]);
                Assert.IsFalse(
                    SessionState.GetBool("MCPForUnity.Build.NativeOwnerV1", false),
                    "Preparation failure must not persist a native invocation fence."
                );
                Assert.IsTrue(ownedScene.isDirty);
            }
            finally
            {
                BuildRunner.SaveSceneForBuild = originalSave;
                BuildRunner.ReleaseNative(job.JobId);
                lastCompleted.SetValue(null, originalLastCompleted);
                storageRoot.SetValue(null, originalStorage);
                fixture.Close();
                Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(folder));
                AssetDatabase.DeleteAsset(folder);
                string fullReportRoot = Path.Combine(Path.GetDirectoryName(Application.dataPath), reportRoot);
                if (Directory.Exists(fullReportRoot))
                    Directory.Delete(fullReportRoot, true);
            }
        }

        [Test]
        public void SaveBeforeBuild_LeavesUntitledSceneUnsaved()
        {
            // In batchmode the active scene is already untitled; in the interactive Test Runner
            // it usually is not, so fall back to an additive scratch scene there.
            Scene target = SceneManager.GetActiveScene();
            bool createdAdditive = false;
            if (!string.IsNullOrEmpty(target.path))
            {
                target = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                createdAdditive = true;
            }

            try
            {
                Assert.IsTrue(string.IsNullOrEmpty(target.path), "sanity: the target scene must have never been saved");

                EditorSceneManager.MarkSceneDirty(target);
                Assert.IsTrue(target.isDirty, "sanity: scene must be dirty to reach the save path");

                // Before the guard this reached SaveOpenScenes(), which opens a modal file panel
                // for a pathless scene.
                Assert.DoesNotThrow(() => BuildRunner.SaveBeforeBuild());

                // The sharp assertion: the scene was skipped, not saved. A saved scene would have
                // been given a path and would no longer be dirty.
                Assert.IsTrue(string.IsNullOrEmpty(target.path), "an untitled scene must not be written anywhere");
                Assert.IsTrue(target.isDirty, "an untitled scene must be left dirty — saving it is what opens the modal");
            }
            finally
            {
                if (createdAdditive)
                {
                    EditorSceneManager.CloseScene(target, removeScene: true);
                }
            }
        }

        [Test]
        public void SaveBeforeBuild_WithNoDirtyScenes_IsANoOp()
        {
            Assert.DoesNotThrow(() => BuildRunner.SaveBeforeBuild());
        }
    }
}
