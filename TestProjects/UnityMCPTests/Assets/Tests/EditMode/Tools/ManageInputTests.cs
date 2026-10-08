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
            Assert.That(data.Value<int>("max_frames"), Is.EqualTo(600));
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
        [TestCase("601")]
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
