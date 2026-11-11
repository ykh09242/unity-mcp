using System;
using System.Collections;
using System.IO;
using System.Threading;
using MCPForUnity.Editor.Services.SourceFreshness;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class SourceFreshnessReloadTests
    {
        private static readonly string Domain = Guid.NewGuid().ToString("N");

        [UnityTest, Explicit("Owned disposable Editor only; performs an actual domain reload.")]
        public IEnumerator RealDomainReloadPreservesPendingIdentityWithoutInventingCompilation()
        {
            Assert.IsTrue(Application.isBatchMode);
            Assert.AreEqual(Path.GetFullPath(Application.dataPath + "/.."), Environment.GetEnvironmentVariable("UNITY_MCP_SOURCE_RELOAD_PROJECT"));
            const string key = "MCPForUnity.Tests.SourceReload.";
            SessionState.SetString(key + "domain", Domain);
            SessionState.SetString(key + "id", Guid.NewGuid().ToString("N"));
            try
            {
                var prepare = SourceFreshnessService.PrepareAsync(SessionState.GetString(key + "id", ""), CancellationToken.None);
                while (!prepare.IsCompleted)
                    yield return null;
                Assert.IsFalse(prepare.IsFaulted, prepare.Exception?.ToString());
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                Assert.AreNotEqual(SessionState.GetString(key + "domain", ""), Domain);
                Assert.AreEqual("reload_requires_observation", (string)SourceFreshnessService.Cached()["reason"]);
                var observe = SourceFreshnessService.ObserveAsync(CancellationToken.None);
                while (!observe.IsCompleted)
                    yield return null;
                Assert.IsFalse(observe.IsFaulted, observe.Exception?.ToString());
                Assert.AreNotEqual("observed_current", (string)observe.Result["status"], "Reload alone cannot prove compilation.");
                Assert.AreEqual(SessionState.GetString(key + "id", ""), (string)observe.Result["compile_id"]);
            }
            finally
            {
                SessionState.EraseString(key + "domain");
                SessionState.EraseString(key + "id");
            }
        }

        [Test]
        public void PendingObservationSurvivesReloadButNotAProcessOrProjectChange()
        {
            var ledger = new SourceFreshnessLedger("project", "session");
            ledger.BeginCompilation(SourceFreshnessTests.Compilation().CompileId, SourceFreshnessTests.Snapshot(), "old-domain");
            string checkpoint = ledger.Encode();
            var restored = new SourceFreshnessLedger("project", "session", checkpoint);
            Assert.AreEqual("old-domain", restored.RequestDomain);
            Assert.AreEqual("observed_current", restored.ObserveCompilation(SourceFreshnessTests.Snapshot(), SourceFreshnessTests.Compilation()).Status);
            Assert.AreEqual("incomplete", new SourceFreshnessLedger("project", "another-session", checkpoint).Observation.Status);
            Assert.AreEqual("incomplete", new SourceFreshnessLedger("another-project", "session", checkpoint).Observation.Status);
        }

        [TestCase("{}")]
        [TestCase("not-json")]
        public void CorruptCheckpointCannotRestoreAnExecutionVerdict(string checkpoint)
        {
            Assert.AreEqual("incomplete", new SourceFreshnessLedger("project", "session", checkpoint).Observation.Status);
        }
    }
}
