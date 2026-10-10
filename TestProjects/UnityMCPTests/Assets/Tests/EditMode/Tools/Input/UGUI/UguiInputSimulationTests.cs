#if MCP_INPUT_UGUI
using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Input;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    // EventSystem normally registers itself only during Play Mode. Exercise its real
    // lifecycle in these isolated Edit Mode tests, without manually mutating its registry.
    [ExecuteAlways]
    public sealed class InputTestEventSystem : EventSystem { }

    public sealed class InputPointerProbe
        : MonoBehaviour,
            IPointerEnterHandler,
            IPointerDownHandler,
            IPointerUpHandler,
            IPointerClickHandler,
            IPointerExitHandler
    {
        public readonly List<string> Events = new List<string>();
        public Action PointerDownAction;

        public void OnPointerEnter(PointerEventData data) => Events.Add("enter");

        public void OnPointerDown(PointerEventData data)
        {
            Events.Add("down");
            PointerDownAction?.Invoke();
        }

        public void OnPointerUp(PointerEventData data) => Events.Add("up");

        public void OnPointerClick(PointerEventData data) => Events.Add("click");

        public void OnPointerExit(PointerEventData data) => Events.Add("exit");
    }

    public class UguiInputSimulationTests
    {
        private GameObject _canvas;
        private GameObject _events;
        private EventSystem _previousEventSystem;
        private Button _button;

        [SetUp]
        public void SetUp()
        {
            _previousEventSystem = EventSystem.current;
            _canvas = new GameObject("InputCanvas" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Canvas));
            _canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            var buttonObject = new GameObject("Button", typeof(RectTransform), typeof(Button));
            buttonObject.transform.SetParent(_canvas.transform);
            _button = buttonObject.GetComponent<Button>();
            _events = new GameObject("InputEvents", typeof(InputTestEventSystem));
            EventSystem.current = _events.GetComponent<EventSystem>();
        }

        [TearDown]
        public void TearDown()
        {
            UnityEngine.Object.DestroyImmediate(_canvas);
            UnityEngine.Object.DestroyImmediate(_events);
            if (_previousEventSystem != null)
                EventSystem.current = _previousEventSystem;
        }

        [Test]
        public void DirectClickInvokesButtonListenerOnceWithoutRawInputModule()
        {
            // Given: a button and EventSystem with no raw input module.
            int count = 0;
            _button.onClick.AddListener(() => count++);
            // When: the adapter dispatches a click.
            var result = new UguiInputSimulationBackend().Click(_button.gameObject) as SuccessResponse;
            // Then: the real uGUI listener receives exactly one click.
            Assert.That(result, Is.Not.Null);
            Assert.That(count, Is.EqualTo(1));
        }

        [Test]
        public void PointerSequenceReleasesAndExitsAfterClick()
        {
            // Given: a real pointer handler records event order.
            InputPointerProbe probe = _button.gameObject.AddComponent<InputPointerProbe>();
            // When: a complete targeted click is dispatched.
            new UguiInputSimulationBackend().Click(_button.gameObject);
            // Then: press and hover state both have a matching cleanup event.
            Assert.That(probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "click", "exit" }));
        }

        [Test]
        public void ChildTargetResolvesItsNearestButtonHandler()
        {
            // Given: a text/image child under the button.
            var child = new GameObject("Label", typeof(RectTransform));
            child.transform.SetParent(_button.transform);
            int count = 0;
            _button.onClick.AddListener(() => count++);
            // When: that exact child is clicked.
            new UguiInputSimulationBackend().Click(child);
            // Then: its containing button receives the event.
            Assert.That(count, Is.EqualTo(1));
        }

        [Test]
        public void DisabledButtonCannotReceiveDirectClick()
        {
            // Given: a disabled interaction state on the button.
            _button.interactable = false;
            int count = 0;
            _button.onClick.AddListener(() => count++);
            // When/Then: direct dispatch respects uGUI interactability.
            Assert.Throws<ArgumentException>(() => new UguiInputSimulationBackend().Click(_button.gameObject));
            Assert.That(count, Is.Zero);
        }

        [Test]
        public void ScenarioClickWaitsBeforeEventsThenDispatchesOnce()
        {
            InputPointerProbe probe = _button.gameObject.AddComponent<InputPointerProbe>();
            int clicks = 0;
            _button.onClick.AddListener(() => clicks++);
            _button.interactable = false;
            var backend = new UguiInputSimulationBackend();
            Assert.That(backend.TryClick(_button.gameObject, out object waiting, out string detail), Is.False);
            Assert.That(waiting, Is.Null);
            Assert.That(detail, Does.Contain("interactable"));
            Assert.That(probe.Events, Is.Empty);
            Assert.That(clicks, Is.Zero);
            _button.interactable = true;
            Assert.That(backend.TryClick(_button.gameObject, out object result, out _), Is.True);
            Assert.That(result, Is.TypeOf<SuccessResponse>());
            Assert.That(probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "click", "exit" }));
            Assert.That(clicks, Is.EqualTo(1));
        }

        [Test]
        public void ScenarioInactiveCanvasWaitsWithoutPointerEvents()
        {
            InputPointerProbe probe = _button.gameObject.AddComponent<InputPointerProbe>();
            _canvas.GetComponent<Canvas>().enabled = false;
            Assert.That(new UguiInputSimulationBackend().TryClick(_button.gameObject, out _, out _), Is.False);
            Assert.That(probe.Events, Is.Empty);
        }

        [Test]
        public void ScenarioInvalidTargetFailsRatherThanWaitingForever()
        {
            var backend = new UguiInputSimulationBackend();
            Assert.Throws<ArgumentException>(() => backend.TryClick(null, out _, out _));
            Assert.Throws<ArgumentException>(() => backend.TryClick(_canvas, out _, out _));
        }

        [Test]
        public void ScenarioPostDispatchFailureThrowsAndReleasesPointerState()
        {
            InputPointerProbe probe = _button.gameObject.AddComponent<InputPointerProbe>();
            probe.PointerDownAction = () => _button.interactable = false;
            Assert.Throws<InvalidOperationException>(() => new UguiInputSimulationBackend().TryClick(_button.gameObject, out _, out _));
            Assert.That(probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "exit" }));
        }
    }
}
#endif
