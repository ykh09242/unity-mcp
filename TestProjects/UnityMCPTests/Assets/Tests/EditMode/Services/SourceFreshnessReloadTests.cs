using MCPForUnity.Editor.Services.SourceFreshness;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class SourceFreshnessReloadTests
    {
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
