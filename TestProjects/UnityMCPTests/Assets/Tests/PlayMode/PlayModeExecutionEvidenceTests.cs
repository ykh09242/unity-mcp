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
