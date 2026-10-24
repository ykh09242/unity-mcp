using System;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ManageEditorTransitionTests
    {
        private bool _playing;
        private bool _willPlay;
        private bool _changing;
        private int _requests;
        private Action<bool> _request;

        private static void Override(string name, object value) =>
            typeof(ManageEditor).GetField(name, BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, value);

        private static JObject Invoke(string action) => JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = action }));

        [SetUp]
        public void SetUp()
        {
            _playing = _willPlay = _changing = false;
            _requests = 0;
            _request = target =>
            {
                _requests++;
                _willPlay = target;
                _changing = true;
            };
            Override("_isPlayingOverride", (Func<bool>)(() => _playing));
            Override("_willPlayOverride", (Func<bool>)(() => _willPlay));
            Override("_isChangingOverride", (Func<bool>)(() => _changing));
            Override("_requestPlayOverride", (Action<bool>)(target => _request(target)));
        }

        [TearDown]
        public void TearDown()
        {
            foreach (string name in new[] { "_isPlayingOverride", "_willPlayOverride", "_isChangingOverride", "_requestPlayOverride" })
                Override(name, null);
        }

        [TestCase(false, "play")]
        [TestCase(true, "stop")]
        public void DelayedTransition_ReportsPendingObservedState(bool playing, string action)
        {
            // Given a setter whose transition has not completed.
            _playing = _willPlay = playing;
            // When requesting the opposite mode.
            JObject result = Invoke(action);
            // Then successful submission does not assert completed entry/exit.
            Assert.IsTrue(result.Value<bool>("success"));
            Assert.AreEqual("pending", result["data"].Value<string>("status"));
            Assert.AreEqual(playing, result["data"].Value<bool>("is_playing"));
            Assert.IsTrue(result["data"].Value<bool>("request_submitted"));
            Assert.AreEqual("mcpforunity://editor/state", result["data"].Value<string>("follow_up"));
            Assert.AreEqual(1, _requests);
        }

        [TestCase(false, true, "play")]
        [TestCase(true, false, "stop")]
        [TestCase(false, false, "stop")]
        [TestCase(true, true, "play")]
        public void PendingTransition_DoesNotResubmitEvenAcrossReloadEdge(bool playing, bool willPlay, string action)
        {
            // Given either differing Unity flags or the persisted transition edge.
            _playing = playing;
            _willPlay = willPlay;
            _changing = true;
            // When retrying after an acknowledgement was lost.
            JObject result = Invoke(action);
            // Then the existing transition is still pending without invoking the setter.
            Assert.AreEqual("pending", result["data"].Value<string>("status"));
            Assert.IsFalse(result["data"].Value<bool>("request_submitted"));
            Assert.AreEqual(0, _requests);
        }

        [TestCase(false, "stop")]
        [TestCase(true, "play")]
        public void StableTarget_ReportsObservedCompletion(bool playing, string action)
        {
            // Given a stable target state observed before the request.
            _playing = _willPlay = playing;
            // When requesting that same state.
            JObject result = Invoke(action);
            // Then completion is an observation with no mutation.
            Assert.AreEqual("completed", result["data"].Value<string>("status"));
            Assert.IsFalse(result["data"].Value<bool>("is_changing"));
            Assert.AreEqual(0, _requests);
        }

        [Test]
        public void SetterTargetFlag_DoesNotConfirmEntryReadiness()
        {
            // Given a setter that immediately changes its flags without an observed lifecycle callback.
            _request = target =>
            {
                _requests++;
                _playing = _willPlay = target;
            };
            // When requesting entry.
            JObject result = Invoke("play");
            // Then even target flags do not turn a submitted request into observed completion.
            Assert.AreEqual("pending", result["data"].Value<string>("status"));
            Assert.IsTrue(result["data"].Value<bool>("request_submitted"));
            Assert.AreEqual(1, _requests);
        }

        [Test]
        public void LostAcknowledgement_RetryDoesNotSubmitAgain()
        {
            // Given an earlier accepted request whose acknowledgement was lost.
            Invoke("play");
            // When the same caller retries while the native transition is delayed.
            JObject result = Invoke("play");
            // Then the original setter is not replayed.
            Assert.AreEqual("pending", result["data"].Value<string>("status"));
            Assert.IsFalse(result["data"].Value<bool>("request_submitted"));
            Assert.AreEqual(1, _requests);
        }

        [Test]
        public void StopDuringEntry_RequestsCancellationWithoutClaimingExit()
        {
            // Given entry requested but no Play Mode entry observation yet.
            _willPlay = _changing = true;
            // When explicitly stopping.
            JObject result = Invoke("stop");
            // Then the cancellation request is pending despite is_playing already being false.
            Assert.AreEqual("pending", result["data"].Value<string>("status"));
            Assert.IsTrue(result["data"].Value<bool>("request_submitted"));
            Assert.IsFalse(_willPlay);
            Assert.AreEqual(1, _requests);
        }

        [Test]
        public void CancelledEntry_AllowsNewPlayWithoutClaimingEntry()
        {
            // Given an entry cancelled back to stable Edit Mode.
            _playing = _willPlay = _changing = false;
            // When the caller explicitly requests entry again.
            JObject result = Invoke("play");
            // Then it is a fresh pending request, not observed completion.
            Assert.AreEqual("pending", result["data"].Value<string>("status"));
            Assert.IsFalse(result["data"].Value<bool>("is_playing"));
            Assert.AreEqual(1, _requests);
        }

        [TestCase("play")]
        [TestCase("stop")]
        public void RejectedSetter_PreservesError(string action)
        {
            // Given a native setter failure.
            _playing = _willPlay = action == "stop";
            _request = target => throw new InvalidOperationException("controlled transition rejection");
            // When requesting the opposite mode.
            JObject result = Invoke(action);
            // Then rejection remains failure rather than accepted or completed.
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("controlled transition rejection", result.Value<string>("error"));
        }

        [Test]
        public void PersistedTransitionObserver_SurvivesDomainReloadState()
        {
            // Given the edge persisted by ExitingEditMode/ExitingPlayMode callbacks.
            const string key = "MCPForUnity.EditorState.PlayTransition";
            bool before = SessionState.GetBool(key, false);
            try
            {
                SessionState.SetBool(key, true);
                Type cache = typeof(ManageEditor).Assembly.GetType("MCPForUnity.Editor.Services.EditorStateCache", true);
                // When sampling the observer in a newly loaded domain.
                bool changing = (bool)cache.GetProperty("IsPlayModeTransition", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                // Then a settled-looking flag pair cannot suppress the persisted edge.
                Assert.IsTrue(changing);
            }
            finally
            {
                SessionState.SetBool(key, before);
            }
        }
    }
}
