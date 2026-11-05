using System;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine.SceneManagement;
#endif

namespace MCPForUnityTests.PlayMode
{
    [Category("playmode_evidence")]
    public class PlayModeExecutionEvidenceTests
    {
#if UNITY_EDITOR
        private static bool RunAnimationCommand(string json)
        {
            var jsonType = Type.GetType("Newtonsoft.Json.Linq.JObject, Newtonsoft.Json", true);
            var parse = jsonType.GetMethod("Parse", new[] { typeof(string) });
            var tool = Type.GetType("MCPForUnity.Editor.Tools.Animation.ManageAnimation, MCPForUnity.Editor", true);
            var result = tool.GetMethod("HandleCommand").Invoke(null, new[] { parse.Invoke(null, new object[] { json }) });
            bool success = (bool)result.GetType().GetProperty("success").GetValue(result);
            if (!success)
                Assert.That((string)result.GetType().GetProperty("message").GetValue(result), Does.Contain("Play mode"));
            return success;
        }

        private static string AnimationCommand(string action, string target, string arguments)
        {
            return "{\"action\":\"" + action + "\",\"target\":" + target + "," + arguments + "}";
        }

        [Test]
        public void RuntimeAnimatorActionsRejectAssetsAndPreviewObjectsWhileAllowingPlayingInstances()
        {
            Assert.That(Application.isPlaying, Is.True);
            string folderName = "AnimatorRuntimeGuard_" + Guid.NewGuid().ToString("N");
            string folder = "Assets/" + folderName;
            Assert.That(AssetDatabase.CreateFolder("Assets", folderName), Is.Not.Empty);
            GameObject source = null;
            GameObject live = null;
            GameObject preview = null;
            var previewScene = default(Scene);
            try
            {
                var controller = AnimatorController.CreateAnimatorControllerAtPath(folder + "/Controller.controller");
                controller.AddParameter("Speed", AnimatorControllerParameterType.Float);
                controller.layers[0].stateMachine.AddState("Idle");
                source = new GameObject("RuntimeGuardSource");
                source.AddComponent<Animator>().runtimeAnimatorController = controller;
                string prefabPath = folder + "/Source.prefab";
                var prefab = PrefabUtility.SaveAsPrefabAsset(source, prefabPath);
                Assert.That(prefab, Is.Not.Null);
                var prefabAnimator = prefab.GetComponent<Animator>();
                Assert.That(EditorUtility.IsPersistent(prefabAnimator), Is.True);
                live = UnityEngine.Object.Instantiate(prefab);
                var liveAnimator = live.GetComponent<Animator>();
                liveAnimator.Rebind();
                liveAnimator.Update(0f);
                previewScene = EditorSceneManager.NewPreviewScene();
                preview = new GameObject("RuntimeGuardPreview");
                SceneManager.MoveGameObjectToScene(preview, previewScene);
                preview.AddComponent<Animator>().runtimeAnimatorController = controller;
                Assert.That(Application.IsPlaying(preview), Is.False);

                string assetTarget = "{\"find\":\"" + prefabPath + "\"}";
                string previewTarget = preview.GetInstanceIDCompat().ToString();
                string liveTarget = live.GetInstanceIDCompat().ToString();
                foreach (string target in new[] { assetTarget, previewTarget })
                {
                    Assert.That(
                        RunAnimationCommand(AnimationCommand("animator_set_enabled_runtime", target, "\"enabled\":false,\"runtime_only\":false")),
                        Is.False
                    );
                    Assert.That(
                        RunAnimationCommand(
                            AnimationCommand("animator_set_parameter_runtime", target, "\"parameterName\":\"Speed\",\"value\":4,\"runtime_only\":false")
                        ),
                        Is.False
                    );
                    Assert.That(RunAnimationCommand(AnimationCommand("animator_set_enabled", target, "\"enabled\":false,\"runtime_only\":true")), Is.False);
                    Assert.That(
                        RunAnimationCommand(
                            AnimationCommand("animator_set_parameter", target, "\"parameterName\":\"Speed\",\"value\":4,\"runtime_only\":true")
                        ),
                        Is.False
                    );
                }
                Assert.That(prefabAnimator.enabled, Is.True);
                Assert.That(preview.GetComponent<Animator>().enabled, Is.True);
                Assert.That(controller.parameters[0].defaultFloat, Is.EqualTo(0f));
                Assert.That(
                    RunAnimationCommand(AnimationCommand("animator_set_parameter_runtime", liveTarget, "\"parameterName\":\"Speed\",\"value\":4")),
                    Is.True
                );
                Assert.That(liveAnimator.GetFloat("Speed"), Is.EqualTo(4f));
                Assert.That(RunAnimationCommand(AnimationCommand("animator_set_enabled_runtime", liveTarget, "\"enabled\":false")), Is.True);
                Assert.That(liveAnimator.enabled, Is.False);
            }
            finally
            {
                if (live != null)
                    UnityEngine.Object.DestroyImmediate(live);
                if (source != null)
                    UnityEngine.Object.DestroyImmediate(source);
                if (previewScene.IsValid())
                    EditorSceneManager.ClosePreviewScene(previewScene);
                AssetDatabase.DeleteAsset(folder);
            }
        }

        [Serializable]
        private class Readiness
        {
            public EditorReadiness editor;
            public ActivityReadiness activity;
        }

        [Serializable]
        private class EditorReadiness
        {
            public PlayReadiness play_mode;
        }

        [Serializable]
        private class PlayReadiness
        {
            public bool is_playing;
            public bool is_changing;
        }

        [Serializable]
        private class ActivityReadiness
        {
            public string phase;
        }

        [Test]
        public void StablePlayAndPauseAreNotTransitions()
        {
            Assert.That(EditorApplication.isPlaying, Is.True);
            var cache = Type.GetType("MCPForUnity.Editor.Services.EditorStateCache, MCPForUnity.Editor", true);
            var build = cache.GetMethod("BuildSnapshot", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
            bool paused = EditorApplication.isPaused;
            try
            {
                foreach (bool pause in new[] { false, true })
                {
                    EditorApplication.isPaused = pause;
                    var snapshot = JsonUtility.FromJson<Readiness>(build.Invoke(null, new object[] { "play-readiness-evidence" }).ToString());
                    Assert.That(snapshot.editor.play_mode.is_playing, Is.True);
                    Assert.That(snapshot.editor.play_mode.is_changing, Is.False);
                    Assert.That(snapshot.activity.phase, Is.Not.EqualTo("playmode_transition"));
                }
            }
            finally
            {
                EditorApplication.isPaused = paused;
            }
        }
#endif

        [Test]
        public void RequiredBodyRunsWithRequestedReloadConfiguration()
        {
            string[] args = Environment.GetCommandLineArgs();
            int reload = Array.IndexOf(args, "--mcp-playmode-reload");
            int session = Array.IndexOf(args, "--mcp-playmode-session");
            if (reload < 0 && session < 0)
                Assert.Ignore("Explicit PlayMode matrix execution was not requested.");
            Assert.That(reload, Is.GreaterThanOrEqualTo(0));
            Assert.That(session, Is.GreaterThanOrEqualTo(0));
            Assert.That(reload + 1, Is.LessThan(args.Length));
            Assert.That(session + 1, Is.LessThan(args.Length));
            Assert.That(Array.LastIndexOf(args, "--mcp-playmode-reload"), Is.EqualTo(reload));
            Assert.That(Array.LastIndexOf(args, "--mcp-playmode-session"), Is.EqualTo(session));
            string mode = args[reload + 1];
            string nonce = args[session + 1];
            Assert.That(mode, Is.EqualTo("on").Or.EqualTo("off"));
            Assert.That(nonce, Does.Match("^[0-9a-f]{32}$"));
            Assert.That(Application.isPlaying, Is.True, "The required body must execute in Play Mode.");
#if UNITY_EDITOR
            bool disabled = EditorSettings.enterPlayModeOptionsEnabled && (EditorSettings.enterPlayModeOptions & EnterPlayModeOptions.DisableDomainReload) != 0;
            Assert.That(disabled, Is.EqualTo(mode == "off"));
            Assert.That((EditorSettings.enterPlayModeOptions & EnterPlayModeOptions.DisableSceneReload), Is.EqualTo(EnterPlayModeOptions.None));
            string before = SessionState.GetString("MCP.PlayModeEvidence.BeforeDomain", "");
            string after = SessionState.GetString("MCP.PlayModeEvidence.AfterDomain", "");
            Assert.That(before, Does.Match("^[0-9a-f]{32}$"), "The Editor must record the domain before entering Play Mode.");
            Assert.That(after, Does.Match("^[0-9a-f]{32}$"), "The Editor must record the domain after entering Play Mode.");
            Assert.That(before == after, Is.EqualTo(mode == "off"), "Observed domain identity must match the requested reload behavior.");
#else
            Assert.Fail("This evidence contract requires an Editor PlayMode test run.");
#endif
            TestContext.WriteLine("MCP_PLAYMODE_BODY session=" + nonce + " reload=" + mode + " unity=" + Application.unityVersion);
        }
    }
}
