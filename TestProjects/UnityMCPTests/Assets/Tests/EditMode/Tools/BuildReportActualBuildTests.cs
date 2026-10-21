using System;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    /// <summary>Two explicit phases: exactly one native build, then read retained artifacts after a real Editor domain/process reload.</summary>
    public sealed class BuildReportActualBuildTests
    {
        private static Type Store => typeof(BuildJobStore).Assembly.GetType("MCPForUnity.Editor.Tools.Build.BuildReportStore");
        private static FieldInfo Storage => Store.GetField("StorageRootOverride", BindingFlags.NonPublic | BindingFlags.Static);
        private static string Project => Path.GetDirectoryName(Application.dataPath);
        private static string Nonce
        {
            get
            {
                string expected = Environment.GetEnvironmentVariable("MCP_DEV06_PROJECT_ROOT");
                Assert.AreEqual(
                    Path.GetFullPath(expected ?? "missing").TrimEnd('\\', '/'),
                    Path.GetFullPath(Project).TrimEnd('\\', '/'),
                    "Actual builds require the controller's exact disposable project identity."
                );
                string nonce = Environment.GetEnvironmentVariable("MCP_DEV06_NONCE");
                Assert.IsTrue(Guid.TryParseExact(nonce, "N", out _), "A unique owned artifact nonce is required.");
                return nonce;
            }
        }

        private static string Reports(string nonce) => Path.Combine(Project, ".work", "Dev06Reports_" + nonce);

        private static JToken Read(string id, string section = "summary")
        {
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "report",
                        ["report_id"] = id,
                        ["report_section"] = section,
                        ["page_size"] = 100,
                    }
                )
            );
            Assert.IsTrue((bool)response["success"], response.ToString());
            return response["data"];
        }

        [Test, Explicit("Controller only: one supported-target native build in the existing disposable cached project.")]
        public void Phase1BuildOneOwnedSceneAndPersistNativeReport()
        {
            string nonce = Nonce;
            Assert.IsNotNull(Store);
            Assert.IsNull(BuildJobStore.ActiveJobId, "An unowned active build must remain untouched.");
            Assert.AreEqual(
                BuildTarget.StandaloneWindows64,
                EditorUserBuildSettings.activeBuildTarget,
                "Do not switch the controller's target or change settings for this fixture."
            );
            Assert.IsTrue(BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.Standalone, BuildTarget.StandaloneWindows64));
            string reports = Reports(nonce);
            Assert.IsFalse(Directory.Exists(reports), "The artifact nonce must be fresh; never overwrite a prior run.");
            object previousStorage = Storage.GetValue(null);
            Storage.SetValue(null, reports);
            var scenes = new PrefabTestSceneFixture();
            string assetRoot = "Assets/__McpBuildReport_" + nonce;
            string output = Path.Combine(Project, ".work", "Dev06Output_" + nonce);
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsFalse(Directory.Exists(output));
            BuildJob previousLast = BuildJobStore.LastCompletedJob;
            BuildJob job = null;
            try
            {
                scenes.PrepareRunnerBootstrap();
                Scene scene = scenes.Create("McpBuildReport_", nonce);
                Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", "__McpBuildReport_" + nonce));
                var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                cube.name = "OwnedReportCube";
                var shader = Shader.Find("Standard") ?? Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Hidden/InternalErrorShader");
                Assert.IsNotNull(shader);
                var material = new Material(shader);
                AssetDatabase.CreateAsset(material, assetRoot + "/Included.mat");
                cube.GetComponent<MeshRenderer>().sharedMaterial = material;
                bool largeInventory = Environment.GetEnvironmentVariable("MCP_BUILD_REPORT_LARGE_FIXTURE") == "1";
                if (largeInventory)
                {
                    // Explicit controller cohort: exercise an inventory beyond the old 2048-row capture cap.
                    string resources = assetRoot + "/Resources";
                    Directory.CreateDirectory(resources);
                    AssetDatabase.StartAssetEditing();
                    try
                    {
                        for (int i = 0; i < 2560; i++)
                            File.WriteAllText(resources + "/Evidence" + i.ToString("D4") + ".txt", "Owned report evidence " + i);
                    }
                    finally
                    {
                        AssetDatabase.StopAssetEditing();
                    }
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                }
                string scenePath = assetRoot + "/" + scene.name + ".unity";
                Assert.IsTrue(EditorSceneManager.SaveScene(scene, scenePath));
                var options = BuildRunner.CreateBuildOptions(
                    BuildTarget.StandaloneWindows64,
                    Path.Combine(output, "Owned.exe"),
                    new[] { scenePath },
                    BuildOptions.DetailedBuildReport,
                    (int)StandaloneBuildSubtarget.Player
                );
                job = new BuildJob(BuildJobStore.CreateJobId(), options.target, options.locationPathName);
                BuildJobStore.AddBuildJob(job);
                BuildReport capturedNative = null;
                // Existing real runner, with the genuine native BuildReport returned by BuildPlayer.
                typeof(BuildRunner)
                    .GetMethod("RunBuildCore", BindingFlags.NonPublic | BindingFlags.Static)
                    .Invoke(null, new object[] { job, new Func<BuildReport>(() => capturedNative = BuildPipeline.BuildPlayer(options)), null });
                Assert.AreEqual(BuildJobState.Succeeded, job.State, job.ErrorMessage);
                string id = (string)JObject.FromObject(job.ToStatusResponse())["report_id"];
                Assert.IsNotEmpty(id, JObject.FromObject(job.ToStatusResponse()).ToString());
                var summary = Read(id);
                Assert.IsTrue((bool)summary["summary"]["native_report_available"]);
                Assert.Greater((ulong)summary["summary"]["total_size_bytes"], 0);
                Assert.Greater(Read(id, "steps")["entries"].Count(), 0, "The actual native report must provide build steps.");
                Assert.Greater(Read(id, "packed_assets").Value<int>("total_entries"), 0, "DetailedBuildReport must expose a retained packed-asset inventory.");
                if (largeInventory)
                {
                    var bounded = new JObject();
                    BuildReportCapture.AddDetails(bounded, capturedNative);
                    int retained = Read(id, "packed_assets").Value<int>("total_entries");
                    Assert.AreEqual(BuildReportStore.MaxPackedAssets, ((JArray)bounded["packed_assets"]).Count);
                    Assert.IsFalse(bounded.Value<bool>("packed_assets_complete"));
                    Assert.GreaterOrEqual(retained, 2560);
                    Assert.IsTrue(Read(id, "packed_assets").Value<bool>("details_complete"));
                    TestContext.WriteLine($"NEXT13_NATIVE_LARGE old_bounded_rows={((JArray)bounded["packed_assets"]).Count} indexed_rows={retained}");
                }
                capturedNative = null;
                var state = new JObject
                {
                    ["report_id"] = id,
                    ["report_revision"] = summary["report_revision"],
                    ["session"] = (string)Store.GetField("sessionId", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null),
                };
                File.WriteAllText(Path.Combine(reports, "qa-state.json"), state.ToString());
            }
            finally
            {
                scenes.Close();
                scenes.RestoreRunnerBootstrap();
                if (AssetDatabase.IsValidFolder(assetRoot))
                    AssetDatabase.DeleteAsset(assetRoot);
                if (Directory.Exists(output))
                {
                    StringAssert.StartsWith(Path.Combine(Project, ".work", "Dev06Output_"), output);
                    Directory.Delete(output, true);
                }
                if (job != null)
                    (
                        (System.Collections.Generic.Dictionary<string, BuildJob>)
                            typeof(BuildJobStore).GetField("_buildJobs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null)
                    ).Remove(job.JobId);
                typeof(BuildJobStore).GetField("_lastCompletedJob", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, previousLast);
                Storage.SetValue(null, previousStorage);
            }
        }

        [Test, Explicit("Controller only: run after Phase1 and an actual Editor domain/process reload; no second build.")]
        public void Phase2ReadReportAfterActualDomainReload()
        {
            string root = Reports(Nonce);
            var state = JObject.Parse(File.ReadAllText(Path.Combine(root, "qa-state.json")));
            Assert.AreNotEqual(
                (string)state["session"],
                (string)Store.GetField("sessionId", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null),
                "A real domain/process reload is required between the two phases."
            );
            object previousStorage = Storage.GetValue(null);
            Storage.SetValue(null, root);
            try
            {
                var report = Read((string)state["report_id"]);
                Assert.AreEqual(state["report_revision"], report["report_revision"]);
                Assert.IsTrue((bool)report["summary"]["native_report_available"]);
                Assert.AreEqual("succeeded", (string)report["summary"]["native_result"]);
                Assert.Greater(Read((string)state["report_id"], "steps")["entries"].Count(), 0);
                Assert.Greater(Read((string)state["report_id"], "packed_assets").Value<int>("total_entries"), 0);
            }
            finally
            {
                Storage.SetValue(null, previousStorage);
                StringAssert.StartsWith(Path.Combine(Project, ".work", "Dev06Reports_"), root);
                Assert.IsFalse((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0);
                Directory.Delete(root, true);
            }
        }
    }
}
