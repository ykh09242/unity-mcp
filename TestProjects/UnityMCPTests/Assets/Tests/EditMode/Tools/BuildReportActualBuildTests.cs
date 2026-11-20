using System;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using MCPForUnityTests.Editor;
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
            BuildJob candidateJob = null;
            BuildJob repairedJob = null;
            try
            {
                scenes.PrepareRunnerBootstrap();
                Scene scene = scenes.Create("McpBuildReport_", nonce);
                Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", "__McpBuildReport_" + nonce));
                var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                cube.name = "OwnedReportCube";
                var shader = TestUtilities.FindFirstShader("Standard", "Universal Render Pipeline/Lit", "Hidden/InternalErrorShader");
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
                bool conditionPair = Environment.GetEnvironmentVariable("MCP_BUILD_CONDITION_PAIR") == "1";
                if (conditionPair)
                    Assert.AreEqual(1, cube.AddComponent<TestNamespace.BuildConditionRewardProbe>().CollectReward(0), "Editor reward transition");
                Assert.IsTrue(EditorSceneManager.SaveScene(scene, scenePath));
                var options = BuildRunner.CreateBuildOptions(
                    BuildTarget.StandaloneWindows64,
                    Path.Combine(output, "Owned.exe"),
                    new[] { scenePath },
                    BuildOptions.DetailedBuildReport,
                    (int)StandaloneBuildSubtarget.Player
                );
                if (conditionPair)
                    options.extraScriptingDefines = new[] { "MCP_FIXTURE_REWARD_DISABLED" };
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
                if (conditionPair)
                {
                    var broken = ExerciseRewardPlayer(options.locationPathName, summary, reports, true);
                    var fixedOptions = options;
                    fixedOptions.extraScriptingDefines = Array.Empty<string>();
                    fixedOptions.locationPathName = Path.Combine(output, "Repaired", "Owned.exe");
                    Directory.CreateDirectory(Path.GetDirectoryName(fixedOptions.locationPathName));
                    repairedJob = new BuildJob(BuildJobStore.CreateJobId(), fixedOptions.target, fixedOptions.locationPathName);
                    BuildJobStore.AddBuildJob(repairedJob);
                    typeof(BuildRunner)
                        .GetMethod("RunBuildCore", BindingFlags.NonPublic | BindingFlags.Static)
                        .Invoke(null, new object[] { repairedJob, new Func<BuildReport>(() => BuildPipeline.BuildPlayer(fixedOptions)), null });
                    Assert.AreEqual(BuildJobState.Succeeded, repairedJob.State, repairedJob.ErrorMessage);
                    var repaired = ExerciseRewardPlayer(fixedOptions.locationPathName, Read(repairedJob.ReportId), reports, false);
                    Assert.AreNotEqual(broken["report"]["report_id"], repaired["report"]["report_id"]);
                    File.WriteAllText(
                        Path.Combine(reports, "build-condition-pair.json"),
                        new JObject
                        {
                            ["editor_reward"] = 1,
                            ["broken"] = broken,
                            ["repaired"] = repaired,
                        }.ToString()
                    );
                }
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
                if (Environment.GetEnvironmentVariable("MCP_BUILD_REPORT_SIZE_PAIR") == "1")
                {
                    const int sourceBytes = 4 * 1024 * 1024;
                    string resourceDirectory = assetRoot + "/Resources";
                    Directory.CreateDirectory(resourceDirectory);
                    string assetPath = resourceDirectory + "/SizeWitness.bytes";
                    byte[] contents = new byte[sourceBytes];
                    new System.Random(30130).NextBytes(contents);
                    File.WriteAllBytes(assetPath, contents);
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    candidateJob = new BuildJob(BuildJobStore.CreateJobId(), options.target, options.locationPathName);
                    BuildJobStore.AddBuildJob(candidateJob);
                    typeof(BuildRunner)
                        .GetMethod("RunBuildCore", BindingFlags.NonPublic | BindingFlags.Static)
                        .Invoke(null, new object[] { candidateJob, new Func<BuildReport>(() => BuildPipeline.BuildPlayer(options)), null });
                    Assert.AreEqual(BuildJobState.Succeeded, candidateJob.State, candidateJob.ErrorMessage);
                    var comparison = JObject.FromObject(
                        ManageBuild.HandleCommand(
                            new JObject
                            {
                                ["action"] = "compare_reports",
                                ["baseline_report_id"] = id,
                                ["candidate_report_id"] = candidateJob.ReportId,
                                ["report_section"] = "packed_assets",
                                ["page_size"] = 100,
                            }
                        )
                    );
                    Assert.IsTrue(comparison.Value<bool>("success"), comparison.ToString());
                    var delta = comparison["data"];
                    Assert.AreEqual("matched_report_settings", (string)delta["size_comparability"]["status"]);
                    Assert.IsTrue(delta.Value<bool>("details_complete"));
                    var addition = delta["entries"].Single(row => (string)row["key"] == assetPath);
                    Assert.AreEqual("added", (string)addition["change"]);
                    Assert.GreaterOrEqual(addition["candidate"].Value<long>("packed_size_bytes"), sourceBytes);
                    Assert.AreEqual(
                        delta.Value<long>("total_size_bytes_delta"),
                        delta.Value<long>("named_packed_delta_bytes") + delta.Value<long>("unattributed_delta_bytes")
                    );
                    var pair = new JObject
                    {
                        ["unity"] = Application.unityVersion,
                        ["source_bytes"] = sourceBytes,
                        ["baseline"] = summary["summary"],
                        ["candidate"] = Read(candidateJob.ReportId)["summary"],
                        ["comparison"] = delta,
                    };
                    File.WriteAllText(Path.Combine(reports, "size-pair.json"), pair.ToString());
                    TestContext.WriteLine(
                        $"BUILD_SIZE_PAIR source_bytes={sourceBytes} named_asset_bytes={addition["candidate"]["packed_size_bytes"]} total_delta={delta["total_size_bytes_delta"]} named_delta={delta["named_packed_delta_bytes"]} remainder={delta["unattributed_delta_bytes"]}"
                    );
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
                foreach (var created in new[] { job, candidateJob, repairedJob }.Where(created => created != null))
                {
                    BuildRunner.ReleaseNative(created.JobId);
                    (
                        (System.Collections.Generic.Dictionary<string, BuildJob>)
                            typeof(BuildJobStore).GetField("_buildJobs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null)
                    ).Remove(created.JobId);
                }
                typeof(BuildJobStore).GetField("_lastCompletedJob", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, previousLast);
                Storage.SetValue(null, previousStorage);
            }
        }

        private static JObject ExerciseRewardPlayer(string executable, JToken report, string reports, bool broken)
        {
            var conditions = report["build_conditions"];
            Assert.AreEqual("StandaloneWindows64", (string)conditions["player_target"]);
            CollectionAssert.Contains(conditions["editor_compilation_only_assemblies"]["names"].Values<string>().ToArray(), "MCPForUnity.Editor");
            CollectionAssert.Contains(conditions["player_filter_assemblies"]["names"].Values<string>().ToArray(), "TestAsmdef");
            Assert.AreEqual(broken, conditions["player_extra_defines"]["names"].Values<string>().Contains("MCP_FIXTURE_REWARD_DISABLED"));
            Assert.IsTrue(conditions["player_extra_defines"].Value<bool>("complete"));
            string log = Path.Combine(reports, broken ? "broken-player.log" : "repaired-player.log");
            Assert.IsFalse(File.Exists(log));
            using (
                var process = System.Diagnostics.Process.Start(
                    new System.Diagnostics.ProcessStartInfo
                    {
                        FileName = executable,
                        Arguments = "-batchmode -nographics -logFile \"" + log + "\"",
                        UseShellExecute = false,
                        CreateNoWindow = true,
                        WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden,
                    }
                )
            )
            {
                if (!process.WaitForExit(60000))
                {
                    process.Kill();
                    process.WaitForExit(5000);
                    Assert.Fail("Owned fixture Player did not exit after the reward transition.");
                }
                Assert.AreEqual(broken ? 42 : 0, process.ExitCode);
                string marker = File.ReadLines(log).Single(line => line.StartsWith("MCP_BUILD_CONDITION_REWARD ", StringComparison.Ordinal));
                string[] fields = marker.Split(' ');
                Assert.AreEqual(Guid.Parse((string)report["summary"]["native_build_guid"]), Guid.Parse(fields[1]));
                Assert.AreEqual(broken ? "0" : "1", fields[2]);
                return new JObject
                {
                    ["report"] = report,
                    ["process_exit"] = process.ExitCode,
                    ["player_marker"] = marker,
                };
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
