using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.TestTools;
using UnityMcpLifecycleSample;

namespace UnityMcpLifecycleTransport.Tests
{
    public sealed class TransportScenarioTests
    {
        [UnityTearDown]
        public IEnumerator Stop()
        {
            StdioBridgeHost.Stop();
            LifecycleScene.ReleaseRetained();
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }

        [UnityTest]
        public IEnumerator McpCallsReachTheOwnedEditor()
        {
            Assert.That(Application.isBatchMode, Is.True);
            Assert.That(Environment.GetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH"), Is.Null.Or.Empty);
            string output = Environment.GetEnvironmentVariable("UNITY_MCP_SAMPLE_TRANSPORT_OUTPUT");
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_SAMPLE_TRANSPORT_NONCE");
            Assert.That(Directory.Exists(output), Is.True);
            Assert.That(Guid.TryParseExact(nonce, "N", out _), Is.True);
            string project = Path.GetDirectoryName(Application.dataPath);
            JObject marker = JObject.Parse(File.ReadAllText(Path.Combine(project, "ProjectSettings/LifecycleSample.json")));
            Assert.That((string)marker["sample"], Is.EqualTo("play-scenario-lifecycle"));
            Assert.That(StdioBridgeHost.IsRunning, Is.False);
            EditorSceneManager.OpenScene("Assets/Generated/LifecycleSample/Menu.unity");
            yield return new EnterPlayMode();
            // Play Mode reload resets iterator locals; reacquire this owned execution's metadata.
            output = Environment.GetEnvironmentVariable("UNITY_MCP_SAMPLE_TRANSPORT_OUTPUT");
            nonce = Environment.GetEnvironmentVariable("UNITY_MCP_SAMPLE_TRANSPORT_NONCE");
            project = Path.GetDirectoryName(Application.dataPath);
            PlayScenarioRegisteredResources baseline = PlayScenarioResourceTracker.Capture();
            // Existing owned QA seam keeps authentication while avoiding credential stores and preferences.
            typeof(StdioBridgeHost)
                .GetMethod("StartOwned", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { 0, "owned-editor-qa-synthetic-token-only" });
            Assert.That(StdioBridgeHost.IsRunning, Is.True);
            string hash;
            using (var sha = SHA1.Create())
                hash = string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(Application.dataPath)).Select(value => value.ToString("x2"))).Substring(0, 8);
            var ready = new JObject
            {
                ["host"] = "127.0.0.1",
                ["port"] = StdioBridgeHost.GetCurrentPort(),
                ["pid"] = System.Diagnostics.Process.GetCurrentProcess().Id,
                ["project"] = project,
                ["instance"] = "project@" + hash,
                ["nonce"] = nonce,
            };
            string readyPath = Path.Combine(output, "ready.json");
            File.WriteAllText(readyPath + ".tmp", ready.ToString());
            File.Move(readyPath + ".tmp", readyPath);
            double deadline = Time.realtimeSinceStartupAsDouble + 150;
            string completedPath = Path.Combine(output, "complete.json");
            while (!File.Exists(completedPath) && Time.realtimeSinceStartupAsDouble < deadline)
                yield return new WaitForSecondsRealtime(0.1f);
            Assert.That(File.Exists(completedPath), Is.True, "External MCP client must complete.");
            JObject completed = JObject.Parse(File.ReadAllText(completedPath));
            Assert.That((string)completed["nonce"], Is.EqualTo(nonce));
            Assert.That((bool)completed["success"], Is.True, "See the external MCP client evidence.");
            Assert.That(completed["reports"].Count(), Is.EqualTo(5));
            Assert.That(SampleSignals.ListenerCount, Is.Zero);
            Assert.That(LifecycleScene.Retained.Count, Is.Zero);
            Assert.That(UnityEngine.SceneManagement.SceneManager.GetActiveScene().path, Is.EqualTo("Assets/Generated/LifecycleSample/Menu.unity"));
            PlayScenarioRegisteredResources current = PlayScenarioResourceTracker.Capture();
            Assert.That(current.Error, Is.Null);
            Assert.That(current.ScriptableObjectIds, Is.EquivalentTo(baseline.ScriptableObjectIds));
            Assert.That(current.SubscriptionIds, Is.EquivalentTo(baseline.SubscriptionIds));
            Assert.That(current.HandleIds, Is.EquivalentTo(baseline.HandleIds));
            Assert.That(current.RegistrationFailureCount, Is.EqualTo(baseline.RegistrationFailureCount));
        }
    }
}
