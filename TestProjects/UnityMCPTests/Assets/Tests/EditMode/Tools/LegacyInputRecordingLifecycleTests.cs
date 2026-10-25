#if ENABLE_LEGACY_INPUT_MANAGER && !ENABLE_INPUT_SYSTEM
using System.Collections;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class LegacyInputRecordingLifecycleTests
    {
        [UnityTearDown]
        public IEnumerator LeavePlayModeAfterFailure()
        {
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }

        [UnityTest]
        public IEnumerator OldModeObserverAdvancesOnGameFramesAndReleasesOnlyItsPump()
        {
            yield return new EnterPlayMode();
            int original = Resources.FindObjectsOfTypeAll<LegacyInputRecordingPump>().Length;
            var backend = new LegacyInputScenarioRecordingBackend();
            var session = backend.Begin((_, __) => { }, _ => { });
            try
            {
                Assert.That(Resources.FindObjectsOfTypeAll<LegacyInputRecordingPump>().Length, Is.EqualTo(original + 1));
                yield return null;
                yield return null;
                Assert.That(session.UpdateCount, Is.GreaterThan(0));
                session.Dispose();
                long releasedAt = session.UpdateCount;
                Assert.That(Resources.FindObjectsOfTypeAll<LegacyInputRecordingPump>().Length, Is.EqualTo(original));
                yield return null;
                yield return null;
                Assert.That(session.UpdateCount, Is.EqualTo(releasedAt));
                session.Dispose();
            }
            finally
            {
                session.Dispose();
            }
            yield return new ExitPlayMode();
        }
    }
}
#endif
