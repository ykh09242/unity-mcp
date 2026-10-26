using System;
using System.Globalization;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ManageInputTests
    {
        private GameObject _root;

        private class CapabilityBackend : IInputSimulationBackend
        {
            public bool Available { get; set; }
            public string UnavailableReason => null;
            public object Status => null;

            public object Apply(InputSimulationRequest request) => throw new InvalidOperationException("A status query must not dispatch input.");

            public void ReleaseAll() => throw new InvalidOperationException("A status query must not release input.");
        }

        private sealed class RoutingCapabilityBackend : CapabilityBackend, IPlayerInputSimulationBackend
        {
            public object DiagnosePlayer(GameObject target) => throw new InvalidOperationException("A status query must not inspect a player.");

            public object PairPlayer(GameObject target, string device) => throw new InvalidOperationException("A status query must not pair a device.");

            public object ReleasePlayer(GameObject target) => throw new InvalidOperationException("A status query must not release a player.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PairingCapabilityRequiresAvailableBackendWhileDiagnosticsRemainDiscoverable(bool available)
        {
            IInputSimulationBackend previous = ManageInput.InputBackend;
            ManageInput.InputBackend = new RoutingCapabilityBackend { Available = available };
            try
            {
                var result = (SuccessResponse)ManageInput.HandleCommand(new JObject { ["action"] = "status" });
                var data = JObject.FromObject(result.Data);
                Assert.That(data.Value<bool>("player_input_diagnostics"), Is.True);
                Assert.That(data.Value<bool>("reversible_gamepad_pairing"), Is.EqualTo(available));
                Assert.That(data.Value<bool>("input_system"), Is.EqualTo(available));
                Assert.That(data.Value<bool>("legacy_raw_input"), Is.False);
                Assert.That(data.Value<bool>("ui_dispatch_changes_raw_input"), Is.False);
            }
            finally
            {
                ManageInput.InputBackend = previous;
            }
        }

        [TearDown]
        public void TearDown()
        {
            if (_root != null)
                UnityEngine.Object.DestroyImmediate(_root);
        }

        [Test]
        public void StatusIsAvailableWithoutPlayMode()
        {
            // Given: Edit Mode, with no simulated input requested.
            // When: capabilities are queried.
            var result = ManageInput.HandleCommand(new JObject { ["action"] = "status" }) as SuccessResponse;
            // Then: the read-only capability query remains usable.
            Assert.That(result, Is.Not.Null);
            var data = JObject.FromObject(result.Data);
            Assert.That(data.Value<bool>("legacy_raw_input"), Is.False);
            Assert.That(data.Value<bool>("ui_dispatch_changes_raw_input"), Is.False);
            Assert.That(data.Value<int>("max_frames"), Is.EqualTo(InputSimulationRequest.MaxFrames));
            Assert.That(data.Value<string>("active_input_handling"), Is.EqualTo(ManageInput.ActiveInputHandling));
        }

        [Test]
        public void LegacyObservationCapabilityIsReachableOnlyInOldMode()
        {
            var result = (SuccessResponse)ManageInput.HandleCommand(new JObject { ["action"] = "status" });
            var data = JObject.FromObject(result.Data);
#if ENABLE_LEGACY_INPUT_MANAGER && !ENABLE_INPUT_SYSTEM
            Assert.That(data.Value<bool>("legacy_input_observation"), Is.True);
            Assert.That(data["legacy_input_observation_unavailable_reason"].Type, Is.EqualTo(JTokenType.Null));
#else
            Assert.That(data.Value<bool>("legacy_input_observation"), Is.False);
            Assert.That(data.Value<string>("legacy_input_observation_unavailable_reason"), Does.Contain("Old Input Handling"));
            var error = Assert.Throws<InvalidOperationException>(() => new LegacyInputScenarioRecordingBackend().Begin((_, __) => { }, _ => { }));
            Assert.That(error.Message, Does.Contain("New and Both retain the Input System observer"));
#endif
#if ENABLE_LEGACY_INPUT_MANAGER && ENABLE_INPUT_SYSTEM
            Assert.That(data.Value<string>("active_input_handling"), Is.EqualTo("Both"));
#endif
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LegacyReplayOptionsRemainDiscoverableWithoutChangingBackendCapabilities(bool backendAvailable)
        {
            IInputSimulationBackend previous = ManageInput.InputBackend;
            ManageInput.InputBackend = new CapabilityBackend { Available = backendAvailable };
            try
            {
                var result = (SuccessResponse)ManageInput.HandleCommand(new JObject { ["action"] = "status" });
                var data = JObject.FromObject(result.Data);
                Assert.That(data.Value<bool>("legacy_raw_input"), Is.False);
                Assert.That(data.Value<bool>("input_system"), Is.EqualTo(backendAvailable));
                foreach (string capability in new[] { "keyboard", "mouse", "touch", "gamepad", "wheel" })
                    Assert.That(data.Value<bool>(capability), Is.EqualTo(backendAvailable), capability);
                string reason = data.Value<string>("legacy_raw_input_unavailable_reason");
                Assert.That(reason, Does.Contain("UnityEngine.Input"));
                Assert.That(reason, Does.Contain("ui_click or scenario click_ui"));
                Assert.That(reason, Does.Contain("Input System Package/Both handling and an Input System consumer"));
                Assert.That(reason, Does.Contain("changing handling alone does not redirect"));
                Assert.That(reason, Does.Contain("explicit project-code integration"));
                Assert.That(reason, Does.Contain("MCP provides no legacy raw adapter"));
                Assert.That(reason, Does.Contain("does not change input settings or send OS input"));
                Assert.That(data["raw_input_unavailable_reason"].Type == JTokenType.Null, Is.EqualTo(backendAvailable));
            }
            finally
            {
                ManageInput.InputBackend = previous;
            }
        }

        [TestCase("ui_click")]
        [TestCase("key")]
        [TestCase("mouse")]
        [TestCase("touch")]
        [TestCase("release_all")]
        public void MutationsRequirePlayMode(string action)
        {
            // Given: a valid mutation request while the editor is not playing.
            var parameters = new JObject
            {
                ["action"] = action,
                ["key"] = "Space",
                ["position"] = new JArray(1, 2),
            };
            // When: input is requested.
            var result = ManageInput.HandleCommand(parameters) as ErrorResponse;
            // Then: no backend or UI callback is invoked.
            Assert.That(result, Is.Not.Null);
            Assert.That(result.Error, Does.Contain("requires Play Mode"));
        }

        [TestCase("0")]
        [TestCase("36001")]
        [TestCase("true")]
        [TestCase("1.5")]
        [TestCase("\"2\"")]
        public void FrameDurationRejectsCoercionAndUnboundedValues(string frames)
        {
            // Given: a malformed duration.
            JObject parameters = JObject.Parse("{\"action\":\"key\",\"key\":\"A\",\"frames\":" + frames + "}");
            // When/Then: parsing fails before input state can be changed.
            Assert.Throws<ArgumentException>(() => InputSimulationRequest.Parse(parameters));
        }

        [Test]
        public void NamedKeyHoldPreservesBoundedFrameCount()
        {
            // Given: a valid bounded key command.
            var parameters = new JObject
            {
                ["action"] = "key",
                ["key"] = "LeftShift",
                ["frames"] = 20,
            };
            // When: it is parsed for the optional backend.
            InputSimulationRequest request = InputSimulationRequest.Parse(parameters);
            // Then: the semantic hold and key identity survive parsing.
            Assert.That(request.Frames, Is.EqualTo(20));
            Assert.That(request.Key, Is.EqualTo("LeftShift"));
            Assert.That(request.State, Is.EqualTo("press"));
        }

        [TestCase("en-US")]
        [TestCase("de-DE")]
        [TestCase("fr-FR")]
        [TestCase("ar-SA")]
        public void FractionalCoordinatesDoNotDependOnEditorCulture(string cultureName)
        {
            CultureInfo previousCulture = CultureInfo.CurrentCulture;
            try
            {
                CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo(cultureName);
                var parameters = JObject.Parse("{\"action\":\"mouse\",\"state\":\"move\",\"position\":[1.5,2.25]}");

                InputSimulationRequest request = InputSimulationRequest.Parse(parameters);

                Assert.That(request.Position, Is.EqualTo(new Vector2(1.5f, 2.25f)));
            }
            finally
            {
                CultureInfo.CurrentCulture = previousCulture;
            }
        }

        [Test]
        public void DuplicateSiblingPathRequiresInstanceId()
        {
            // Given: two active children share an identical exact path.
            _root = new GameObject("InputTest" + Guid.NewGuid().ToString("N"));
            var first = new GameObject("Button");
            var second = new GameObject("Button");
            first.transform.SetParent(_root.transform);
            second.transform.SetParent(_root.transform);
            // When/Then: path targeting refuses to guess.
            Assert.Throws<ArgumentException>(() => InputTargetResolver.Resolve(new JValue(_root.name + "/Button")));
        }

        [Test]
        public void IntegerTargetResolvesExactObject()
        {
            // Given: an active scene object with a known integer ID.
            _root = new GameObject("InputTest" + Guid.NewGuid().ToString("N"));
            // When: the target is resolved.
            GameObject target = InputTargetResolver.Resolve(new JValue(_root.GetInstanceIDCompat()));
            // Then: the exact object is returned.
            Assert.That(target, Is.SameAs(_root));
        }

        [Test]
        public void FullPathResolvesExactChild()
        {
            // Given: an active child below a uniquely named root.
            _root = new GameObject("InputTest" + Guid.NewGuid().ToString("N"));
            var child = new GameObject("Button");
            child.transform.SetParent(_root.transform);
            // When: its full path is resolved.
            GameObject target = InputTargetResolver.Resolve(new JValue("/" + _root.name + "/Button"));
            // Then: it resolves the child without a fuzzy name fallback.
            Assert.That(target, Is.SameAs(child));
        }

        [Test]
        public void InactiveTargetCannotReceiveClick()
        {
            // Given: an inactive scene object.
            _root = new GameObject("InputTest" + Guid.NewGuid().ToString("N"));
            _root.SetActive(false);
            // When/Then: resolution rejects it before event dispatch.
            Assert.Throws<ArgumentException>(() => InputTargetResolver.Resolve(new JValue(_root.GetInstanceIDCompat())));
        }

        [Test]
        public void OutOfRangeIntegerCannotFallBackToNumericObjectName()
        {
            // Given: a numeric hierarchy name that is outside signed32-bit ID range.
            _root = new GameObject("2147483648");
            // When/Then: a JSON integer remains an ID and fails rather than finding a name.
            Assert.Throws<ArgumentException>(() => InputTargetResolver.Resolve(new JValue(2147483648L)));
        }
    }
}
