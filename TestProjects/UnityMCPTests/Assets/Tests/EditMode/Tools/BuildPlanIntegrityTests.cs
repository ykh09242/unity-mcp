using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Build;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    // Read-only command calls only. No build/apply, save, switch, setter or UPM call.
    public class BuildPlanIntegrityTests
    {
        [TestCase("{action:'plan',scenes:[]}", "build_plan_empty_scenes")]
        [TestCase("{action:'plan',scenes:['Assets/__McpMissingPlanScene.unity']}", "build_plan_invalid")]
        [TestCase("{action:'plan',profile:'Assets/Profile.asset'}", "build_plan_scope_unsupported")]
        [TestCase("{action:'plan',targets:['windows64']}", "build_plan_scope_unsupported")]
        [TestCase("{action:'plan',value:''}", "build_plan_scope_unsupported")]
        [TestCase("{action:'plan',options:['invalid']}", "build_plan_invalid")]
        [TestCase("{action:'build_from_plan',expected_plan_sha256:'bad'}", "build_plan_hash_invalid")]
        public void InvalidPlanDoesNotMutateOrSchedule(string json, string code)
        {
            Assert.IsFalse(BuildPipeline.isBuildingPlayer, "An idle owned Editor is required; do not skip.");
            string before = Snapshot();
            JObject request = JObject.Parse(json);
            JObject result = JObject.FromObject(ManageBuild.HandleCommand(request));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(code, result.Value<string>("code"), result.ToString());
            Assert.IsNull(result.SelectToken("data.plan_sha256"));
            Assert.AreEqual(before, Snapshot(), "Plan/refusal changed observed settings or jobs.");
        }

        [Test, Explicit("Requires exact disposable project and preauthored saved scene; sole QA only.")]
        public void ActualPlanReadOnlyInOwnedProject()
        {
            string root = Environment.GetEnvironmentVariable("MCP_DEV14_PROJECT_ROOT");
            string nonce = Environment.GetEnvironmentVariable("MCP_DEV14_NONCE");
            string scene = Environment.GetEnvironmentVariable("MCP_DEV14_SCENE");
            Assert.IsNotEmpty(root);
            Assert.IsTrue(Guid.TryParseExact(nonce, "N", out _));
            Assert.AreEqual(
                System.IO.Path.GetFullPath(root).TrimEnd('\\', '/'),
                System.IO.Directory.GetParent(Application.dataPath).FullName.TrimEnd('\\', '/')
            );
            Assert.IsTrue(scene?.StartsWith("Assets/", StringComparison.Ordinal) == true);
            string sceneFile = Path.GetFullPath(Path.Combine(root, scene));
            Assert.IsTrue(
                sceneFile.StartsWith(Path.GetFullPath(Path.Combine(root, "Assets")) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            );
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<SceneAsset>(scene));
            Assert.IsFalse(BuildPipeline.isBuildingPlayer);
            string before = Snapshot();
            string filesBefore = OwnedFileSnapshot(root, sceneFile);
            var request = new JObject
            {
                ["action"] = "plan",
                ["scenes"] = new JArray(scene),
                ["scripting_backend"] = "il2cpp",
                ["options"] = new JArray("clean_build", "auto_run"),
            };
            JObject first = JObject.FromObject(ManageBuild.HandleCommand(request));
            JObject second = JObject.FromObject(ManageBuild.HandleCommand((JObject)request.DeepClone()));
            Assert.IsTrue(first.Value<bool>("success"), first.ToString());
            Assert.AreEqual("unity-mcp/build-plan@1", first.SelectToken("data.schema_version")?.Value<string>());
            StringAssert.IsMatch("^[0-9a-f]{64}$", first.SelectToken("data.plan_sha256")?.Value<string>());
            Assert.AreEqual(first.SelectToken("data.plan_sha256"), second.SelectToken("data.plan_sha256"));
            Assert.IsNull(first.SelectToken("data.job_id"));
            Assert.AreEqual(before, Snapshot());
            Assert.AreEqual(filesBefore, OwnedFileSnapshot(root, sceneFile), "Plan changed owned ProjectSettings or the saved scene bytes.");
            var invalidOutput = (JObject)request.DeepClone();
            invalidOutput["output_path"] = "Builds/invalid\0.exe";
            JObject rejected = JObject.FromObject(ManageBuild.HandleCommand(invalidOutput));
            Assert.IsFalse(rejected.Value<bool>("success"), rejected.ToString());
            Assert.AreEqual("build_plan_invalid", rejected.Value<string>("code"), rejected.ToString());
            Assert.IsNull(rejected.SelectToken("data.plan_sha256"));
            Assert.AreEqual(before, Snapshot(), "Invalid output preview changed settings, callbacks or jobs.");
            Assert.AreEqual(filesBefore, OwnedFileSnapshot(root, sceneFile), "Invalid output preview changed owned input bytes.");
            Debug.Log("DEV14_READONLY_PLAN " + first.ToString(Newtonsoft.Json.Formatting.None));
        }

        private static string Snapshot()
        {
            var target = BuildTargetMapping.GetNamedBuildTarget(EditorUserBuildSettings.activeBuildTarget, EditorUserBuildSettings.standaloneBuildSubtarget);
            return JObject
                .FromObject(
                    new
                    {
                        PlayerSettings.productName,
                        PlayerSettings.companyName,
                        PlayerSettings.bundleVersion,
                        backend = (int)PlayerSettings.GetScriptingBackend(target),
                        defines = PlayerSettings.GetScriptingDefineSymbols(target),
                        architecture = PlayerSettings.GetArchitecture(target),
                        identifier = PlayerSettings.GetApplicationIdentifier(target),
                        active = (int)EditorUserBuildSettings.activeBuildTarget,
                        subtarget = (int)EditorUserBuildSettings.standaloneBuildSubtarget,
                        EditorUserBuildSettings.buildAppBundle,
                        scenes = EditorBuildSettings
                            .scenes.Select(s => new
                            {
                                s.path,
                                s.enabled,
                                guid = s.guid.ToString(),
                            })
                            .ToArray(),
                        callbacks = UpdateCallbacks(),
                        jobs = Store("_buildJobs").Count,
                        batches = Store("_batchJobs").Count,
                        last = BuildJobStore.LastCompletedJob?.JobId,
                    }
                )
                .ToString(Newtonsoft.Json.Formatting.None);
        }

        private static string[] UpdateCallbacks()
        {
            FieldInfo field = typeof(EditorApplication).GetField("update", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            Assert.IsNotNull(field, "Editor update callback inventory is unavailable; do not claim invariance.");
            var callback = field.GetValue(null) as Delegate;
            return callback
                    ?.GetInvocationList()
                    .Select(d => d.Method.DeclaringType?.FullName + "." + d.Method.Name + ":" + (d.Target == null ? 0 : RuntimeHelpers.GetHashCode(d.Target)))
                    .ToArray()
                ?? Array.Empty<string>();
        }

        private static string OwnedFileSnapshot(string root, string sceneFile)
        {
            string settings = Path.Combine(root, "ProjectSettings");
            string[] paths = Directory
                .GetFiles(settings, "*", SearchOption.AllDirectories)
                .Concat(new[] { sceneFile, sceneFile + ".meta" })
                .OrderBy(p => p, StringComparer.Ordinal)
                .ToArray();
            Assert.LessOrEqual(paths.Length, 128, "Owned input inventory exceeded the fixture bound.");
            var rows = new JArray();
            using (SHA256 sha = SHA256.Create())
                foreach (string path in paths)
                {
                    Assert.IsFalse((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0);
                    var file = new FileInfo(path);
                    Assert.LessOrEqual(file.Length, 4 * 1024 * 1024, "Owned input exceeds the fixture read bound.");
                    using (FileStream stream = File.OpenRead(path))
                        rows.Add(
                            new JObject
                            {
                                ["path"] = path,
                                ["bytes"] = file.Length,
                                ["sha256"] = BitConverter.ToString(sha.ComputeHash(stream)),
                            }
                        );
                }
            return rows.ToString(Newtonsoft.Json.Formatting.None);
        }

        private static IDictionary Store(string name) =>
            (IDictionary)typeof(BuildJobStore).GetField(name, BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
    }
}
