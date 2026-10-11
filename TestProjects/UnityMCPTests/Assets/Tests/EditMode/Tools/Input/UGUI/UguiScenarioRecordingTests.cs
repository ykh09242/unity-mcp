using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class UguiScenarioRecordingTests
    {
        public sealed class ScrollReceiver : MonoBehaviour, IScrollHandler
        {
            public int Calls;

            public void OnScroll(PointerEventData data) => Calls++;
        }

        private readonly List<GameObject> objects = new List<GameObject>();
        private IPlayScenarioRecordingSession session;

        [TearDown]
        public void TearDown()
        {
            session?.Dispose();
            session = null;
            foreach (GameObject value in objects)
                if (value != null)
                    UnityEngine.Object.DestroyImmediate(value);
            objects.Clear();
        }

        private GameObject Object(string name, params Type[] components)
        {
            var value = new GameObject(name, components);
            objects.Add(value);
            return value;
        }

        [Test]
        public void PointerObservationPreservesButtonCallbacksAndScrollBubbling()
        {
            GameObject root = Object("RecorderCanvas-" + Guid.NewGuid().ToString("N"));
            ScrollReceiver scroll = root.AddComponent<ScrollReceiver>();
            GameObject target = Object("Start", typeof(RectTransform), typeof(Button));
            target.transform.SetParent(root.transform);
            EventSystem events = Object("RecorderEventSystem", typeof(EventSystem)).GetComponent<EventSystem>();
            var observed = new List<PlayScenarioRecordedTarget>();
            int clicks = 0;
            target.GetComponent<Button>().onClick.AddListener(() => clicks++);
            session = new UguiScenarioRecordingBackend().Begin(observed.Add, _ => { });
            var data = new PointerEventData(events) { button = PointerEventData.InputButton.Left, pointerId = -1 };
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerDownHandler);
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerClickHandler);
            Assert.That(clicks, Is.EqualTo(1));
            Assert.That(observed.Count, Is.EqualTo(1));
            Assert.That(observed[0].Target, Is.EqualTo(root.name + "/Start"));
            ExecuteEvents.ExecuteHierarchy(target, data, ExecuteEvents.scrollHandler);
            Assert.That(scroll.Calls, Is.EqualTo(1));
            UguiScenarioRecordingObserver observer = target.GetComponent<UguiScenarioRecordingObserver>();
            Assert.That((object)observer is IDragHandler, Is.False);
            Assert.That((object)observer is IScrollHandler, Is.False);
            Assert.That((object)observer is ISubmitHandler, Is.False);
            session.Dispose();
            session.Dispose();
            Assert.That(target.GetComponent<UguiScenarioRecordingObserver>(), Is.Null);
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerClickHandler);
            Assert.That(clicks, Is.EqualTo(2));
            Assert.That(observed.Count, Is.EqualTo(1));
        }

        [Test]
        public void ObservationPrecedesDestructiveCallbackAndOriginalOrderingIsPreserved()
        {
            GameObject target = Object("DestructiveButton-" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Button));
            EventSystem events = Object("RecorderEventSystem", typeof(EventSystem)).GetComponent<EventSystem>();
            Button button = target.GetComponent<Button>();
            var observed = new List<PlayScenarioRecordedTarget>();
            Component[] original = target.GetComponents<Component>();
            session = new UguiScenarioRecordingBackend().Begin(observed.Add, _ => { });
            var remaining = new List<Component>();
            foreach (Component component in target.GetComponents<Component>())
                if (!(component is UguiScenarioRecordingObserver))
                    remaining.Add(component);
            Assert.That(remaining, Is.EqualTo(original));
            button.onClick.AddListener(() =>
            {
                Assert.That(observed.Count, Is.EqualTo(1), "The source click must exist before a synchronous scene destruction.");
                UnityEngine.Object.DestroyImmediate(target);
            });
            var data = new PointerEventData(events) { button = PointerEventData.InputButton.Left, pointerId = -1 };
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerDownHandler);
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerClickHandler);
            Assert.That(observed.Count, Is.EqualTo(1));
            Assert.That(observed[0].Error, Is.Null);
        }

        [Test]
        public void ProgrammaticCallbacksRightClicksAndNonInteractableButtonsAreNotRecorded()
        {
            GameObject target = Object("FilteredButton-" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Button));
            EventSystem events = Object("RecorderEventSystem", typeof(EventSystem)).GetComponent<EventSystem>();
            var observed = new List<PlayScenarioRecordedTarget>();
            session = new UguiScenarioRecordingBackend().Begin(observed.Add, _ => { });
            Button button = target.GetComponent<Button>();
            button.onClick.Invoke();
            var data = new PointerEventData(events) { button = PointerEventData.InputButton.Right, pointerId = -1 };
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerDownHandler);
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerClickHandler);
            button.interactable = false;
            data.button = PointerEventData.InputButton.Left;
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerDownHandler);
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerClickHandler);
            button.interactable = true;
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerDownHandler);
            button.interactable = false;
            ExecuteEvents.Execute(target, data, ExecuteEvents.pointerClickHandler);
            Assert.That(observed, Is.Empty);
        }
    }
}
