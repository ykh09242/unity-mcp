using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools.Blender;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Blender
{
    /// <summary>
    /// Parameter validation that happens before any socket or file I/O, plus the export-script
    /// builder, so these run without Blender.
    /// </summary>
    public class BlenderBridgeToolTests
    {
        private bool? savedEnabled;
        private bool? savedConsent;
        private const string EnabledKey = EditorPrefKeys.ToolEnabledPrefix + "blender_bridge";
        private static string ConsentKey => ToolDiscoveryService.GetConsentPreferenceKey("blender_bridge");

        [SetUp]
        public void GrantConsent()
        {
            savedEnabled = EditorPrefs.HasKey(EnabledKey) ? EditorPrefs.GetBool(EnabledKey) : (bool?)null;
            savedConsent = EditorPrefs.HasKey(ConsentKey) ? EditorPrefs.GetBool(ConsentKey) : (bool?)null;
            MCPServiceLocator.ToolDiscovery.SetToolEnabled("blender_bridge", true);
        }

        [TearDown]
        public void RestoreConsent()
        {
            if (savedEnabled.HasValue) EditorPrefs.SetBool(EnabledKey, savedEnabled.Value);
            else EditorPrefs.DeleteKey(EnabledKey);
            if (savedConsent.HasValue) EditorPrefs.SetBool(ConsentKey, savedConsent.Value);
            else EditorPrefs.DeleteKey(ConsentKey);
        }

        private static JObject Call(JObject p)
            => JObject.Parse(JsonConvert.SerializeObject(BlenderBridgeTool.HandleCommand(p).GetAwaiter().GetResult()));

        [Test]
        public void NullParams_ReturnsError()
        {
            JObject resp = Call(null);
            Assert.AreEqual(false, (bool)resp["success"]);
        }

        [Test]
        public void UnknownAction_ListsValidActions()
        {
            JObject resp = Call(new JObject { ["action"] = "nope" });
            Assert.AreEqual(false, (bool)resp["success"]);
            string error = (string)resp["error"];
            StringAssert.Contains("nope", error);
            StringAssert.Contains("import_model", error);
            StringAssert.Contains("sync_addon", error);
        }

        [Test]
        public void RunPython_WithoutCode_ReturnsError()
        {
            JObject resp = Call(new JObject { ["action"] = "run_python" });
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("'code'", (string)resp["error"]);
        }

        [Test]
        public void ObjectInfo_WithoutName_ReturnsError()
        {
            JObject resp = Call(new JObject { ["action"] = "object_info" });
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("'object_name'", (string)resp["error"]);
        }

        [Test]
        public void ImportModel_RejectsUnsupportedFormat()
        {
            JObject resp = Call(new JObject { ["action"] = "import_model", ["format"] = "obj" });
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("glb or fbx", (string)resp["error"]);
        }

        [Test]
        public void Screenshot_RejectsOutputFolderOutsideAssets()
        {
            JObject resp = Call(new JObject { ["action"] = "screenshot", ["output_folder"] = "Assets/../../outside" });
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("Assets", (string)resp["error"]);
        }

        [Test]
        public void ImportModel_RejectsOutputFolderBeforeContactingBlender()
        {
            JObject resp = Call(new JObject { ["action"] = "import_model", ["format"] = "fbx", ["output_folder"] = "Assets/../../outside" });
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("Assets", (string)resp["error"]);
        }

        [TestCase("origin", "\"origin\"")]
        [TestCase("quote\"remote", "\"quote\\\"remote\"")]
        [TestCase("trailing\\", "\"trailing\\\\\"")]
        public void QuoteGitArgument_PreservesOneLiteralArgument(string value, string expected)
        {
            Assert.AreEqual(expected, BlenderBridgeTool.QuoteGitArgument(value));
        }

        [Test]
        public void StageExportForImport_ProducesAssetsContainedCopy()
        {
            string source = Path.Combine(Path.GetTempPath(), "blender-stage-" + Guid.NewGuid().ToString("N") + ".fbx");
            string staged = null;
            try
            {
                File.WriteAllBytes(source, new byte[] { 1, 2, 3 });
                staged = BlenderBridgeTool.StageExportForImport(source);
                Assert.IsTrue(MCPForUnity.Editor.Helpers.AssetGenPaths.TryGetAssetsRelativePath(staged, out string relative));
                StringAssert.StartsWith("Assets/.BlenderBridge/", relative);
                CollectionAssert.AreEqual(File.ReadAllBytes(source), File.ReadAllBytes(staged));
            }
            finally
            {
                File.Delete(source);
                if (staged != null)
                {
                    File.Delete(staged);
                    Directory.Delete(Path.GetDirectoryName(staged));
                }
            }
        }

        [Test]
        public void StageExportForImport_CopyFailureRemovesNewStagingRoot()
        {
            string root = MCPForUnity.Editor.Helpers.AssetGenPaths.ToAbsolute("Assets/.BlenderBridge");
            if (Directory.Exists(root) || File.Exists(root))
                Assert.Ignore("An existing staging root must be preserved.");
            string missing = Path.Combine(Path.GetTempPath(), "missing-blender-export-" + Guid.NewGuid().ToString("N") + ".fbx");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                Assert.Throws<FileNotFoundException>(() => BlenderBridgeTool.StageExportForImport(missing));
                Assert.IsFalse(Directory.Exists(root));
                Assert.IsFalse(File.Exists(root + ".meta"));
            }
            finally { AssetDatabase.AllowAutoRefresh(); }
        }

        [Test]
        public void StageExportForImport_CopyFailurePreservesExistingStagingRootAndSibling()
        {
            string root = MCPForUnity.Editor.Helpers.AssetGenPaths.ToAbsolute("Assets/.BlenderBridge");
            bool ownsRoot = !Directory.Exists(root);
            string sibling = Path.Combine(root, "keep-" + Guid.NewGuid().ToString("N") + ".txt");
            string missing = Path.Combine(Path.GetTempPath(), "missing-blender-export-" + Guid.NewGuid().ToString("N") + ".fbx");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                Directory.CreateDirectory(root);
                File.WriteAllText(sibling, "Keep existing staging data.");
                string[] before = Directory.GetFileSystemEntries(root);

                Assert.Throws<FileNotFoundException>(() => BlenderBridgeTool.StageExportForImport(missing));

                CollectionAssert.AreEquivalent(before, Directory.GetFileSystemEntries(root));
                Assert.AreEqual("Keep existing staging data.", File.ReadAllText(sibling));
            }
            finally
            {
                File.Delete(sibling);
                if (ownsRoot) Directory.Delete(root);
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SavePrefab_FailedSaveRemovesOnlyNewEmptyPrefabFolder(bool preexisting)
        {
            string root = "Assets/__McpBlenderPrefabFailure_" + Guid.NewGuid().ToString("N");
            Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", Path.GetFileName(root)));
            string folder = root + "/Prefabs";
            string guid = preexisting ? AssetDatabase.CreateFolder(root, "Prefabs") : null;
            var go = new GameObject("Prefab failure fixture");
            bool previousIgnore = LogAssert.ignoreFailingMessages;
            try
            {
                MethodInfo save = typeof(BlenderBridgeTool).GetMethod("SavePrefab", BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(save);
                LogAssert.ignoreFailingMessages = true;
                Assert.Throws<TargetInvocationException>(() => save.Invoke(null,
                    new object[] { go, root + "/Model.fbx", "MissingParent/Result" }));
                LogAssert.ignoreFailingMessages = previousIgnore;

                Assert.AreEqual(preexisting, AssetDatabase.IsValidFolder(folder));
                Assert.AreEqual(preexisting, Directory.Exists(MCPForUnity.Editor.Helpers.AssetGenPaths.ToAbsolute(folder)));
                if (preexisting) Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(folder));
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previousIgnore;
                UnityEngine.Object.DestroyImmediate(go);
                AssetDatabase.DeleteAsset(root);
            }
        }

        [Test]
        public void ActionIsCaseInsensitive()
        {
            JObject resp = Call(new JObject { ["action"] = "RUN_PYTHON" });
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("'code'", (string)resp["error"]);
        }

        [Test]
        public void ExportScript_EmbedsValuesAsOneJsonLiteral()
        {
            string script = BlenderBridgeTool.BuildExportScript(
                "C:/tmp/O'Brien_1.glb", new[] { "O'Brien", "__APPLY__" }, false, true, "glb");

            StringAssert.Contains("cfg = json.loads(\"", script);
            Assert.IsFalse(script.Contains("__CFG__"), "placeholder must be replaced exactly once");
            StringAssert.Contains("O'Brien", script);
            StringAssert.Contains("__APPLY__", script);

            // The literal between json.loads(" and ") must be the JSON-escaped config, so an
            // apostrophe or a placeholder-looking name never changes the program text.
            int start = script.IndexOf("json.loads(", System.StringComparison.Ordinal) + "json.loads(".Length;
            int end = script.IndexOf(")\n", start, System.StringComparison.Ordinal);
            string literal = script.Substring(start, end - start);
            string decoded = JsonConvert.DeserializeObject<string>(literal);
            JObject cfg = JObject.Parse(decoded);
            Assert.AreEqual("C:/tmp/O'Brien_1.glb", (string)cfg["out"]);
            Assert.AreEqual("O'Brien", (string)cfg["names"][0]);
            Assert.AreEqual("__APPLY__", (string)cfg["names"][1]);
            Assert.AreEqual(true, (bool)cfg["apply_modifiers"]);
            Assert.AreEqual(false, (bool)cfg["selection_only"]);
            Assert.AreEqual("glb", (string)cfg["format"]);
        }

        [Test]
        public void RedactRemoteUrl_StripsEmbeddedCredentials()
        {
            Assert.AreEqual("https://github.com/o/r.git",
                BlenderBridgeTool.RedactRemoteUrl("https://user:ghp_secret@github.com/o/r.git"));
            Assert.AreEqual("https://github.com/o/r.git",
                BlenderBridgeTool.RedactRemoteUrl("https://ghp_secret@github.com/o/r.git"));
            Assert.AreEqual("https://github.com/o/r.git",
                BlenderBridgeTool.RedactRemoteUrl("https://github.com/o/r.git"));
            Assert.AreEqual("git@github.com:o/r.git",
                BlenderBridgeTool.RedactRemoteUrl("git@github.com:o/r.git"));
            Assert.AreEqual(string.Empty, BlenderBridgeTool.RedactRemoteUrl(null));
        }

        [Test]
        public void RedactRemoteUrl_StripsQueryAndFragmentCredentials()
        {
            Assert.AreEqual("https://github.com/o/r.git",
                BlenderBridgeTool.RedactRemoteUrl("https://github.com/o/r.git?token=ghp_secret"));
            Assert.AreEqual("https://github.com/o/r.git",
                BlenderBridgeTool.RedactRemoteUrl("https://github.com/o/r.git#access_token=ghp_secret"));
            Assert.AreEqual("https://github.com/o/r.git",
                BlenderBridgeTool.RedactRemoteUrl("https://user:pw@github.com/o/r.git?x=1#y"));
        }

        [Test]
        public void SetupBloom_FailureReportBecomesAnError()
        {
            JObject failed = JObject.Parse("{\"success\": false, \"message\": \"Could not add Bloom: no volume\"}");
            JObject resp = JObject.Parse(JsonConvert.SerializeObject(BlenderBridgeTool.ToBloomResponse(failed)));
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("Could not add Bloom", (string)resp["error"]);

            JObject ok = JObject.Parse("{\"success\": true, \"message\": \"Bloom added to 'Global Volume'.\"}");
            JObject okResp = JObject.Parse(JsonConvert.SerializeObject(BlenderBridgeTool.ToBloomResponse(ok)));
            Assert.AreEqual(true, (bool)okResp["success"]);

            JObject skipped = JObject.Parse("{\"message\": \"Volume system (URP/HDRP) not available; nothing to do.\"}");
            Assert.AreEqual(true, (bool)JObject.Parse(JsonConvert.SerializeObject(BlenderBridgeTool.ToBloomResponse(skipped)))["success"]);
        }

        [Test]
        public void ExportScript_WithoutNames_UsesEmptyList()
        {
            string script = BlenderBridgeTool.BuildExportScript("C:/tmp/x.fbx", null, true, false, "fbx");
            int start = script.IndexOf("json.loads(", System.StringComparison.Ordinal) + "json.loads(".Length;
            int end = script.IndexOf(")\n", start, System.StringComparison.Ordinal);
            JObject cfg = JObject.Parse(JsonConvert.DeserializeObject<string>(script.Substring(start, end - start)));
            Assert.AreEqual(0, ((JArray)cfg["names"]).Count);
            Assert.AreEqual(true, (bool)cfg["selection_only"]);
            Assert.AreEqual(false, (bool)cfg["apply_modifiers"]);
        }
    }

    public class BlenderBridgeAnimationTests
    {
        [TestCase(false)]
        [TestCase(true)]
        public void LegacyClips_UseLoopingAnimationAndPreserveValidDefault(bool existingDefault)
        {
            var go = new GameObject("Model");
            var walk = new AnimationClip { name = "Walk", legacy = true, wrapMode = WrapMode.Once };
            var idle = new AnimationClip { name = "Idle", legacy = true, wrapMode = WrapMode.Once };
            try
            {
                UnityEngine.Animation existing = null;
                if (existingDefault)
                {
                    var rig = new GameObject("Rig");
                    rig.transform.SetParent(go.transform, false);
                    existing = rig.AddComponent<UnityEngine.Animation>();
                    existing.AddClip(idle, idle.name);
                    existing.clip = idle;
                }
                JObject report = BlenderBridgeTool.SetupAnimation(go, "Assets/unused.fbx", "Model", true, new[] { walk, idle });
                var animation = go.GetComponentInChildren<UnityEngine.Animation>(true);
                Assert.NotNull(animation);
                if (existingDefault) Assert.AreSame(existing, animation);
                Assert.IsNull(go.GetComponentInChildren<Animator>(true));
                Assert.AreSame(existingDefault ? idle : walk, animation.clip);
                Assert.AreSame(walk, animation.GetClip("Walk"));
                Assert.AreSame(idle, animation.GetClip("Idle"));
                Assert.AreEqual(WrapMode.Loop, walk.wrapMode);
                Assert.AreEqual(WrapMode.Loop, idle.wrapMode);
                Assert.AreEqual(WrapMode.Loop, animation.wrapMode);
                Assert.IsTrue(animation.playAutomatically);
                Assert.IsTrue(animation.enabled);
                Assert.AreEqual("legacy", (string)report["system"]);
                Assert.AreEqual(animation.clip.name, (string)report["default_clip"]);
                Assert.AreEqual(animation.gameObject.name, (string)report["animation_on"]);
                Assert.IsNull(report["controller_path"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                UnityEngine.Object.DestroyImmediate(walk);
                UnityEngine.Object.DestroyImmediate(idle);
            }
        }

        [Test]
        public void MecanimClips_KeepLoopingControllerWithFirstClipAsDefault()
        {
            string folder = "Assets/BlenderAnimationTest_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            var go = new GameObject("Model");
            var walk = new AnimationClip { name = "Walk" };
            var idle = new AnimationClip { name = "Idle" };
            try
            {
                AssetDatabase.CreateAsset(walk, folder + "/Walk.anim");
                AssetDatabase.CreateAsset(idle, folder + "/Idle.anim");
                JObject report = BlenderBridgeTool.SetupAnimation(go, folder + "/model.fbx", "Model", true, new[] { walk, idle });
                Animator animator = go.GetComponentInChildren<Animator>(true);
                Assert.NotNull(animator);
                Assert.IsNull(go.GetComponentInChildren<UnityEngine.Animation>(true));
                var controller = animator.runtimeAnimatorController as UnityEditor.Animations.AnimatorController;
                Assert.NotNull(controller);
                Assert.AreEqual(2, controller.layers[0].stateMachine.states.Length);
                Assert.AreSame(walk, controller.layers[0].stateMachine.defaultState.motion);
                Assert.IsTrue(AnimationUtility.GetAnimationClipSettings(walk).loopTime);
                Assert.IsTrue(AnimationUtility.GetAnimationClipSettings(idle).loopTime);
                Assert.AreEqual("mecanim", (string)report["system"]);
                StringAssert.StartsWith(folder + "/", (string)report["controller_path"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                AssetDatabase.DeleteAsset(folder);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void AutoAnimateFalse_LeavesEitherClipKindUnconfigured(bool legacy)
        {
            var go = new GameObject("Model");
            var clip = new AnimationClip { name = "Walk", legacy = legacy, wrapMode = WrapMode.Once };
            try
            {
                Assert.IsNull(BlenderBridgeTool.SetupAnimation(go, "invalid asset path", "Model", false, new[] { clip }));
                Assert.IsNull(go.GetComponentInChildren<Animator>(true));
                Assert.IsNull(go.GetComponentInChildren<UnityEngine.Animation>(true));
                Assert.AreEqual(WrapMode.Once, clip.wrapMode);
                Assert.AreEqual(legacy, clip.legacy);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void MixedClipKinds_AreRejectedBeforeAddingPlaybackComponents()
        {
            var go = new GameObject("Model");
            var legacy = new AnimationClip { name = "Legacy", legacy = true };
            var mecanim = new AnimationClip { name = "Mecanim" };
            try
            {
                Assert.Throws<InvalidOperationException>(() => BlenderBridgeTool.SetupAnimation(go, "Assets/unused.fbx", "Model", true, new[] { legacy, mecanim }));
                Assert.IsNull(go.GetComponentInChildren<Animator>(true));
                Assert.IsNull(go.GetComponentInChildren<UnityEngine.Animation>(true));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                UnityEngine.Object.DestroyImmediate(legacy);
                UnityEngine.Object.DestroyImmediate(mecanim);
            }
        }
    }

    public class BlenderBridgeGitTests
    {
        private string root;
        private string checkout;
        private string addon;
        private const string Canary = "REVIEW_FAKE_TOKEN_1375";

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "blender-git-test-" + Guid.NewGuid().ToString("N"));
            checkout = Path.Combine(root, "checkout");
            Directory.CreateDirectory(checkout);
            try { Git("--version"); }
            catch (System.ComponentModel.Win32Exception) { Assert.Ignore("Git is required for update-check integration tests."); }
            Git("init --quiet --initial-branch=main");
            addon = Path.Combine(checkout, "addon.py");
            File.WriteAllText(addon, "# inert test addon\n");
            Git("add addon.py");
            Git("-c user.name=Test -c user.email=test@example.invalid commit --quiet -m initial");
        }

        [TearDown]
        public void TearDown()
        {
            if (root == null || !Directory.Exists(root)) return;
            foreach (string file in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
                File.SetAttributes(file, FileAttributes.Normal);
            Directory.Delete(root, true);
        }

        private void Git(string args)
        {
            var start = new ProcessStartInfo("git", args)
            {
                WorkingDirectory = checkout, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
            };
            start.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0";
            using var process = Process.Start(start);
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(10000))
            {
                process.Kill();
                Assert.Fail("Git fixture command timed out.");
            }
            Assert.AreEqual(0, process.ExitCode, error.Result);
            _ = output.Result;
        }

        [TestCase("?access_token=" + Canary)]
        [TestCase("#access_token=" + Canary)]
        [TestCase("?token=%52EVIEW_FAKE_TOKEN_1375")]
        [TestCase("userinfo")]
        public void FailedFetch_DoesNotExposeCredentialsInCompleteResponse(string suffix)
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            int port = ((IPEndPoint)listener.LocalEndpoint).Port;
            listener.Stop(); // Deliberately unavailable local endpoint; no external request.
            string userInfo = suffix == "userinfo" ? "user:" + Canary + "@" : "";
            string url = $"http://{userInfo}127.0.0.1:{port}/repo.git" + (suffix == "userinfo" ? "" : suffix);
            Git($"remote add origin \"{url}\"");

            string serialized = JsonConvert.SerializeObject(BlenderBridgeTool.CheckUpdatesBlocking(checkout, addon, addon));
            StringAssert.DoesNotContain(Canary, serialized);
            StringAssert.DoesNotContain("%52EVIEW_FAKE_TOKEN_1375", serialized);
            JObject response = JObject.Parse(serialized);
            JObject remote = (JObject)response["data"]["remotes"][0];
            Assert.IsTrue((bool)response["success"], "Keep the partial update report available.");
            Assert.IsFalse((bool)remote["fetched"]);
            Assert.AreEqual($"http://127.0.0.1:{port}/repo.git", (string)remote["url"]);
            StringAssert.Contains("exited with code", (string)remote["fetch_error"]);
            StringAssert.Contains("exited with code", (string)remote["error"]);
        }

        [Test]
        public void SuccessfulFetch_PreservesUpdateAndAddonComparison()
        {
            string remotePath = Path.Combine(root, "remote.git");
            Git($"clone --quiet --bare . \"{remotePath}\"");
            Git($"remote add origin \"{remotePath}\"");
            // Publish one newer commit, then put the checkout back one commit.
            File.WriteAllText(Path.Combine(checkout, "upstream.txt"), "new upstream content");
            Git("add upstream.txt");
            Git("-c user.name=Test -c user.email=test@example.invalid commit --quiet -m upstream-update");
            Git("push --quiet origin main");
            Git("reset --hard HEAD~1");

            JObject response = JObject.FromObject(BlenderBridgeTool.CheckUpdatesBlocking(checkout, addon, addon));
            JObject remote = (JObject)response["data"]["remotes"][0];
            Assert.IsTrue((bool)remote["fetched"]);
            Assert.AreEqual(1, (int)remote["behind"]);
            Assert.AreEqual(0, (int)remote["local_ahead"]);
            StringAssert.Contains("upstream-update", remote["new_commits"].ToString());
            Assert.IsTrue((bool)response["data"]["addon_in_sync"]);
            Assert.IsNull(remote["fetch_error"]);
        }

        [Test]
        public void InvalidWorkingDirectory_DoesNotEchoExceptionDetails()
        {
            string serialized = JsonConvert.SerializeObject(BlenderBridgeTool.CheckUpdatesBlocking(
                Path.Combine(root, Canary), addon, addon));
            StringAssert.DoesNotContain(Canary, serialized);
            Assert.IsFalse((bool)JObject.Parse(serialized)["success"]);
        }
    }
}
