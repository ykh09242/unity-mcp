using System.Collections;
using MCPForUnity.Runtime;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace UnityMcpLifecycleSample.Tests
{
    public sealed class ResourceOwnershipTests
    {
        [UnityTest]
        public IEnumerator DisposalReleasesActualCloneEventAndStream()
        {
            PlayScenarioRegisteredResources baseline = PlayScenarioResourceTracker.Capture();
            var source = ScriptableObject.CreateInstance<SessionConfig>();
            var owner = new SessionResources(source);
            try
            {
                SessionConfig clone = owner.Clone;
                Assert.That(clone != null, Is.True, "Acquisition must create a real native clone.");
                Assert.That(clone, Is.Not.SameAs(source));
                clone.Health = 7;
                Assert.That(source.Health, Is.EqualTo(100));
                var stream = owner.Stream;
                Assert.That(stream.CanRead, Is.True);
                SampleSignals.Publish();
                Assert.That(owner.EventsReceived, Is.EqualTo(1));
                Assert.That(SampleSignals.ListenerCount, Is.EqualTo(1));
                PlayScenarioRegisteredResources acquired = PlayScenarioResourceTracker.Capture();
                Assert.That(acquired.ScriptableObjectCount, Is.EqualTo(baseline.ScriptableObjectCount + 1));
                Assert.That(acquired.SubscriptionCount, Is.EqualTo(baseline.SubscriptionCount + 1));
                Assert.That(acquired.HandleCount, Is.EqualTo(baseline.HandleCount + 1));
                owner.Dispose();
                owner.Dispose();
                yield return null;
                yield return null;
                Assert.That(clone == null, Is.True, "The native object must be destroyed.");
                Assert.That(stream.CanRead, Is.False, "The actual stream must be closed.");
                SampleSignals.Publish();
                Assert.That(owner.EventsReceived, Is.EqualTo(1), "Disposed owners must no longer receive events.");
                Assert.That(SampleSignals.ListenerCount, Is.Zero);
                PlayScenarioRegisteredResources after = PlayScenarioResourceTracker.Capture();
                Assert.That(after.ScriptableObjectIds, Is.EquivalentTo(baseline.ScriptableObjectIds));
                Assert.That(after.SubscriptionIds, Is.EquivalentTo(baseline.SubscriptionIds));
                Assert.That(after.HandleIds, Is.EquivalentTo(baseline.HandleIds));
            }
            finally
            {
                owner.Dispose();
                Object.DestroyImmediate(source);
            }
        }
    }
}
