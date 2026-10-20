using System;
using NUnit.Framework;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace MCPForUnityTests.PlayMode
{
    [Category("playmode_evidence")]
    public class PlayModeExecutionEvidenceTests
    {
#if UNITY_EDITOR
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
