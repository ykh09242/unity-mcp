using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    // Author/compile only in the audit. No build, profile allocation, scene save,
    // platform switch, PlayerSettings write or scheduled update is permitted here.
    public class BuildPreparationIntegrityTests
    {
        [SetUp]
        public void RequireIdleEditor()
        {
            if (BuildPipeline.isBuildingPlayer || EditorApplication.isPlayingOrWillChangePlaymode)
                Assert.Ignore("Preflight tests require an idle Edit-mode editor.");
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("Do not navigate an unowned prefab stage.");
        }

        [Test]
        public void NullRequestHasNoMutation() => Reject(null, "Parameters cannot be null");

        [TestCase("{}")]
        [TestCase("{action:null}")]
        public void MissingActionHasNoMutation(string json) => Reject(JObject.Parse(json), "action");

        [Test]
        public void UnknownActionHasNoMutation() => Reject(new JObject { ["action"] = "not-an-action" }, "Unknown action");

        [Test]
        public void InvalidOutputPathRejectsBeforeProfilePreparation()
        {
            Reject(
                new JObject
                {
                    ["action"] = "build",
                    ["output_path"] = "Builds/invalid\0.exe",
                    ["profile"] = "Assets/__McpMissingBuildProfile_" + Guid.NewGuid().ToString("N") + ".asset",
                    ["scripting_backend"] = "mono",
                },
                "output_path"
            );
        }

        [TestCase("Assets/__McpMissingScene.unity")]
        [TestCase("Assets/not-a-scene.txt")]
        [TestCase("")]
        public void CreateBuildOptions_RejectsInvalidSceneWithoutScheduling(string scene)
        {
            string before = Snapshot();
            Assert.Throws<ArgumentException>(() =>
                BuildRunner.CreateBuildOptions(BuildTarget.StandaloneWindows64, "Builds/Test.exe", new[] { scene }, BuildOptions.None, 0)
            );
            Assert.AreEqual(before, Snapshot());
        }

        [Test]
        public void CreateBuildOptions_RejectsInvalidOutputPathWithoutScheduling()
        {
            string before = Snapshot();
            Assert.Throws<ArgumentException>(() =>
                BuildRunner.CreateBuildOptions(BuildTarget.StandaloneWindows64, "Builds/invalid\0.exe", Array.Empty<string>(), BuildOptions.None, 0)
            );
            Assert.AreEqual(before, Snapshot());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreateBuildOptions_PreservesValidOutputAndEmptyScenesWithoutScheduling(bool absolute)
        {
            string path = absolute ? Path.Combine(Path.GetTempPath(), "McpBuildValidation", "Test.exe") : "Builds/Test.exe";
            string before = Snapshot();
            var options = BuildRunner.CreateBuildOptions(BuildTarget.StandaloneWindows64, path, Array.Empty<string>(), BuildOptions.None, 0);
            Assert.AreEqual(path, options.locationPathName);
            Assert.IsEmpty(options.scenes);
            Assert.AreEqual(before, Snapshot());
        }

#if UNITY_6000_0_OR_NEWER
        [TestCase("{scenes:3}")]
        [TestCase("{scenes:{path:'Assets/Test.unity'}}")]
        [TestCase("{scenes:['']}")]
        [TestCase("{scenes:[null]}")]
        [TestCase("{scenes:['[\"\"]']}")]
        [TestCase("{scenes:[['']]}")]
        public void MalformedSceneArgumentRejectsBeforeProfileOrSettings(string json)
        {
            JObject request = JObject.Parse(json);
            request["action"] = "build";
            request["profile"] = "Assets/__McpMissingBuildProfile_" + Guid.NewGuid().ToString("N") + ".asset";
            request["scripting_backend"] = "mono";
            Reject(request, "scenes");
        }

        [TestCase("{scenes:[]}")]
        [TestCase("{scenes:null}")]
        public void EmptyOrNullSceneArgumentPreservesProfileLookup(string json)
        {
            JObject request = JObject.Parse(json);
            request["action"] = "build";
            request["profile"] = "Assets/__McpMissingBuildProfile_" + Guid.NewGuid().ToString("N") + ".asset";
            Reject(request, "Build profile not found");
        }
#endif

        [TestCase("build")]
        [TestCase("platform")]
        public void UnknownTargetRejectsEvenWithProfile(string action)
        {
            Reject(
                new JObject
                {
                    ["action"] = action,
                    ["target"] = "not-a-platform",
                    ["profile"] = "Assets/__McpMissingBuildProfile_" + Guid.NewGuid().ToString("N") + ".asset",
                },
                "Unknown"
            );
        }

        [TestCase("bad")]
        [TestCase("0")]
        public void InvalidBackendRejectsBeforeProfileOrSettings(string backend)
        {
            Reject(
                new JObject
                {
                    ["action"] = "build",
                    ["scripting_backend"] = backend,
                    ["profile"] = "Assets/__McpMissingBuildProfile_" + Guid.NewGuid().ToString("N") + ".asset",
                },
                "Unknown scripting_backend"
            );
        }

        [Test]
        public void BatchWithoutSelectorsCreatesNoJobs() => Reject(new JObject { ["action"] = "batch" }, "required");

        [Test]
        public void BatchWithBothSelectorsCreatesNoJobs()
        {
            Reject(
                new JObject
                {
                    ["action"] = "batch",
                    ["targets"] = new JArray("android"),
                    ["profiles"] = new JArray("Assets/Unused.asset"),
                },
                "not both"
            );
        }

        [Test]
        public void UnknownStatusDoesNotCreateJob()
        {
            Reject(new JObject { ["action"] = "status", ["job_id"] = "__McpMissingJob_" + Guid.NewGuid().ToString("N") }, "No job found");
        }

#if UNITY_6000_0_OR_NEWER
        [TestCase("build")]
        [TestCase("batch")]
        public void MissingProfileCreatesNoJobsAndChangesNoSettings(string action)
        {
            string path = "Assets/__McpMissingBuildProfile_" + Guid.NewGuid().ToString("N") + ".asset";
            string fullPath = Path.Combine(Application.dataPath, Path.GetFileName(path));
            Assert.IsFalse(File.Exists(fullPath));
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            var request = new JObject { ["action"] = action };
            if (action == "build")
            {
                request["profile"] = path;
                request["scripting_backend"] = "mono";
            }
            else
                request["profiles"] = new JArray(path);
            Reject(request, action == "build" ? "Build profile not found" : "Profile not found");
            Assert.IsFalse(File.Exists(fullPath));
        }
#endif

        [TestCase("strict_mdoe")]
        [TestCase("invalid")]
        [TestCase("")]
        [TestCase(" ")]
        [TestCase(null)]
        public void UnknownOrEmptyOptionNamesAreRejected(string name)
        {
            Assert.Throws<ArgumentException>(() => BuildRunner.ParseBuildOptions(new[] { "strict_mode", name }, false));
        }

        [TestCase("sevrer")]
        [TestCase("123")]
        [TestCase("")]
        [TestCase(" ")]
        public void UnknownOrEmptySubtargetIsRejected(string name)
        {
            Assert.Throws<ArgumentException>(() => BuildTargetMapping.ResolveSubtarget(name));
        }

        [TestCase(null, StandaloneBuildSubtarget.Player)]
        [TestCase("player", StandaloneBuildSubtarget.Player)]
        [TestCase("PLAYER", StandaloneBuildSubtarget.Player)]
        [TestCase("server", StandaloneBuildSubtarget.Server)]
        [TestCase("SERVER", StandaloneBuildSubtarget.Server)]
        public void SupportedSubtargetDefaultsAndCaseArePreserved(string name, StandaloneBuildSubtarget expected)
        {
            Assert.AreEqual((int)expected, BuildTargetMapping.ResolveSubtarget(name));
        }

        [TestCase("{options:['strict_mode','invalid']}")]
        [TestCase("{options:['strict_mode',123]}")]
        [TestCase("{options:['strict_mode',null]}")]
        [TestCase("{options:['strict_mode','']}")]
        [TestCase("{options:{}}")]
        [TestCase("{options:''}")]
        public void MalformedOptionsRejectBeforeProfileLookup(string json)
        {
            JObject request = JObject.Parse(json);
            request["action"] = "build";
            request["profile"] = "Assets/__McpMissingBuildProfile_" + Guid.NewGuid().ToString("N") + ".asset";
            request["scripting_backend"] = "mono";
            Reject(request, "options");
        }

        [TestCase("sevrer")]
        [TestCase("123")]
        [TestCase("")]
        public void InvalidSubtargetRejectsBeforeAlreadyActivePlatform(string name)
        {
            Reject(
                new JObject
                {
                    ["action"] = "platform",
                    ["target"] = EditorUserBuildSettings.activeBuildTarget.ToString(),
                    ["subtarget"] = name,
                },
                "subtarget"
            );
        }

        private static void Reject(JObject request, string diagnostic)
        {
            string before = Snapshot();
            var response = JObject.FromObject(ManageBuild.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(diagnostic, response.Value<string>("error"));
            Assert.AreEqual(before, Snapshot(), "A rejected request changed editor settings or job inventory.");
        }

        private static string Snapshot()
        {
            var jobs = Store("_buildJobs").Values.Cast<BuildJob>().OrderBy(j => j.JobId).Select(j => JObject.FromObject(j.ToStatusResponse())).ToArray();
            var batches = Store("_batchJobs").Values.Cast<BatchJob>().OrderBy(j => j.JobId).Select(j => JObject.FromObject(j.ToStatusResponse())).ToArray();
            var scenes = EditorBuildSettings
                .scenes.Select(s => new
                {
                    s.path,
                    s.enabled,
                    guid = s.guid.ToString(),
                })
                .ToArray();
            return JObject
                .FromObject(
                    new
                    {
                        target = EditorUserBuildSettings.activeBuildTarget.ToString(),
                        subtarget = EditorUserBuildSettings.standaloneBuildSubtarget.ToString(),
                        PlayerSettings.productName,
                        scenes,
                        jobs,
                        batches,
                        last = BuildJobStore.LastCompletedJob?.JobId,
                    }
                )
                .ToString(Newtonsoft.Json.Formatting.None);
        }

        private static IDictionary Store(string name) =>
            (IDictionary)typeof(BuildJobStore).GetField(name, BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
    }
}
