#if MCP_INPUT_UGUI
using System;
using System.Collections;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.TestTools;
using UnityEngine.UI;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class RaycastPressOnlyProbe : MonoBehaviour, IPointerDownHandler
    {
        public int Presses;

        public void OnPointerDown(PointerEventData data) => Presses++;
    }

    public sealed class UguiScenarioRaycastTests
    {
        private GameObject _canvas;
        private GameObject _events;
        private EventSystem _previousEventSystem;
        private IUguiInputSimulationBackend _previousBackend;
        private Button _button;
        private InputPointerProbe _probe;
        private UguiInputSimulationBackend _backend;
        private bool _ownsPlayMode;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _ownsPlayMode = false;
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                Assert.Ignore("Raycast fixture requires stable Edit Mode before its owned Play transition.");
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene scene = SceneManager.GetSceneAt(index);
                if (scene.isDirty || (string.IsNullOrEmpty(scene.path) && scene.rootCount != 0))
                    Assert.Ignore("Raycast fixture preserves unsaved existing scenes.");
            }
            yield return new EnterPlayMode();
            Assert.That(Application.isPlaying, Is.True, "Real raycast fixtures require native Play Mode canvas rendering.");
            _ownsPlayMode = true;
            _previousEventSystem = EventSystem.current;
            _previousBackend = ManageInput.UguiBackend;
            _backend = new UguiInputSimulationBackend();
            ManageInput.UguiBackend = _backend;
            _canvas = new GameObject("RaycastCanvas" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Canvas), typeof(GraphicRaycaster));
            _canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            var target = new GameObject("Button", typeof(RectTransform), typeof(Image), typeof(Button));
            target.transform.SetParent(_canvas.transform, false);
            ((RectTransform)target.transform).sizeDelta = new Vector2(100, 100);
            _button = target.GetComponent<Button>();
            _probe = target.AddComponent<InputPointerProbe>();
            _events = new GameObject("RaycastEvents", typeof(InputTestEventSystem));
            EventSystem.current = _events.GetComponent<EventSystem>();
            if (Screen.width <= 0 || Screen.height <= 0)
                Assert.Ignore("Raycast fixture requires nonzero screen dimensions.");
            yield return null;
            Canvas.ForceUpdateCanvases();
            Assert.That(
                _button.GetComponent<Image>().depth,
                Is.GreaterThanOrEqualTo(0),
                "The native canvas must render the fixture graphic before raycast tests begin."
            );
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            if (!_ownsPlayMode)
                yield break;
            ManageInput.UguiBackend = _previousBackend;
            UnityEngine.Object.DestroyImmediate(_canvas);
            UnityEngine.Object.DestroyImmediate(_events);
            if (_previousEventSystem != null)
                EventSystem.current = _previousEventSystem;
            _ownsPlayMode = false;
            yield return new ExitPlayMode();
        }

        [UnityTest]
        public IEnumerator CoveringGraphicWaitsWithoutEventsThenUnblockedCenterClicksOnceAndRestoresSelection()
        {
            int clicks = 0;
            _button.onClick.AddListener(() => clicks++);
            EventSystem.current.SetSelectedGameObject(_canvas);
            var blocker = new GameObject("Cover", typeof(RectTransform), typeof(Image));
            blocker.transform.SetParent(_canvas.transform, false);
            ((RectTransform)blocker.transform).sizeDelta = new Vector2(200, 200);
            yield return null;
            Canvas.ForceUpdateCanvases();
            for (int attempt = 0; attempt < 3; attempt++)
            {
                Assert.That(_backend.TryRaycastClick(_button.gameObject, out object result, out string detail), Is.False);
                Assert.That(result, Is.Null);
                Assert.That(detail, Does.Contain("blocked"));
            }
            Assert.That(_probe.Events, Is.Empty);
            Assert.That(clicks, Is.Zero);
            Assert.That(EventSystem.current.currentSelectedGameObject, Is.SameAs(_canvas));
            UnityEngine.Object.DestroyImmediate(blocker);
            Canvas.ForceUpdateCanvases();
            Assert.That(_backend.TryRaycastClick(_button.gameObject, out object clicked, out string clickedDetail), Is.True, clickedDetail);
            Assert.That(clicked, Is.TypeOf<SuccessResponse>());
            Assert.That(clicks, Is.EqualTo(1));
            Assert.That(_probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "click", "exit" }));
            Assert.That(EventSystem.current.currentSelectedGameObject, Is.SameAs(_canvas));
        }

        [UnityTest]
        public IEnumerator DisabledSelectableWaitsEvenWithAnotherActivePointerHandler()
        {
            _button.enabled = false;
            yield return null;
            Assert.That(_backend.TryRaycastClick(_button.gameObject, out _, out string detail), Is.False);
            Assert.That(detail, Does.Contain("enabled"));
            Assert.That(_probe.Events, Is.Empty);
            _button.enabled = true;
            Canvas.ForceUpdateCanvases();
            Assert.That(_backend.TryRaycastClick(_button.gameObject, out _, out detail), Is.True, detail);
            Assert.That(_probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "click", "exit" }));
        }

        [UnityTest]
        public IEnumerator CanvasGroupsWaitForInteractionAndRaycastPermission()
        {
            var group = _button.gameObject.AddComponent<CanvasGroup>();
            group.interactable = false;
            yield return null;
            Assert.That(_backend.TryRaycastClick(_button.gameObject, out _, out _), Is.False);
            group.interactable = true;
            group.blocksRaycasts = false;
            Assert.That(_backend.TryRaycastClick(_button.gameObject, out _, out _), Is.False);
            Assert.That(_probe.Events, Is.Empty);
            group.blocksRaycasts = true;
            Canvas.ForceUpdateCanvases();
            Assert.That(_backend.TryRaycastClick(_button.gameObject, out _, out string detail), Is.True, detail);
        }

        [UnityTest]
        public IEnumerator OffscreenCenterAndDisabledRaycasterNeverDispatch()
        {
            yield return null;
            var rect = (RectTransform)_button.transform;
            rect.anchoredPosition = new Vector2(Screen.width * 2, Screen.height * 2);
            Assert.That(_backend.TryRaycastClick(_button.gameObject, out _, out string detail), Is.False);
            Assert.That(detail, Does.Contain("screen"));
            rect.anchoredPosition = Vector2.zero;
            _canvas.GetComponent<GraphicRaycaster>().enabled = false;
            Assert.That(_backend.TryRaycastClick(_button.gameObject, out _, out detail), Is.False);
            Assert.That(_probe.Events, Is.Empty);
        }

        [UnityTest]
        public IEnumerator StateChangingDuringPointerDownFailsAfterOneSequenceAndRestoresSelection()
        {
            EventSystem.current.SetSelectedGameObject(_canvas);
            _probe.PointerDownAction = () => _button.interactable = false;
            yield return null;
            Assert.Throws<InvalidOperationException>(() => _backend.TryRaycastClick(_button.gameObject, out _, out _));
            Assert.That(_probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "exit" }));
            Assert.That(EventSystem.current.currentSelectedGameObject, Is.SameAs(_canvas));
        }

        [UnityTest]
        public IEnumerator SelectableDestroyedDuringPressFailsEvenWhenAnotherClickHandlerRemains()
        {
            _probe.PointerDownAction = () => UnityEngine.Object.DestroyImmediate(_button);
            GameObject target = _button.gameObject;
            yield return null;
            Assert.Throws<InvalidOperationException>(() => _backend.TryRaycastClick(target, out _, out _));
            Assert.That(_probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "exit" }));
        }

        [UnityTest]
        public IEnumerator DifferentPressHandlerInterceptsTheHitWithoutAnyDispatch()
        {
            var child = new GameObject("PressInterceptor", typeof(RectTransform), typeof(Image), typeof(RaycastPressOnlyProbe));
            child.transform.SetParent(_button.transform, false);
            ((RectTransform)child.transform).sizeDelta = new Vector2(100, 100);
            yield return null;
            Assert.That(_backend.TryRaycastClick(child, out _, out string detail), Is.False);
            Assert.That(detail, Does.Contain("press and click handlers"));
            Assert.That(child.GetComponent<RaycastPressOnlyProbe>().Presses, Is.Zero);
            Assert.That(_probe.Events, Is.Empty);
        }

        [UnityTest]
        public IEnumerator RenderedCoverBecomingBlockingDuringPressFailsWithoutClickAndCleansUp()
        {
            int clicks = 0;
            _button.onClick.AddListener(() => clicks++);
            EventSystem.current.SetSelectedGameObject(_canvas);
            var cover = new GameObject("PressCover", typeof(RectTransform), typeof(Image));
            cover.transform.SetParent(_canvas.transform, false);
            ((RectTransform)cover.transform).sizeDelta = new Vector2(200, 200);
            Image coverGraphic = cover.GetComponent<Image>();
            coverGraphic.raycastTarget = false;
            yield return null;
            Canvas.ForceUpdateCanvases();
            Assert.That(coverGraphic.depth, Is.GreaterThanOrEqualTo(0), "The cover must be rendered before pointer down.");
            Assert.That(FirstCenterRaycastHit(_button.gameObject), Is.SameAs(_button.gameObject));
            GameObject hitDuringPress = null;
            _probe.PointerDownAction = () =>
            {
                coverGraphic.raycastTarget = true;
                Canvas.ForceUpdateCanvases();
                hitDuringPress = FirstCenterRaycastHit(_button.gameObject);
            };
            Assert.Throws<InvalidOperationException>(() => _backend.TryRaycastClick(_button.gameObject, out _, out _));
            Assert.That(hitDuringPress, Is.SameAs(cover), "Enabling the rendered cover must change the real first raycast hit during pointer down.");
            Assert.That(clicks, Is.Zero);
            Assert.That(_probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "exit" }));
            Assert.That(EventSystem.current.currentSelectedGameObject, Is.SameAs(_canvas));
        }

        private static GameObject FirstCenterRaycastHit(GameObject target)
        {
            var rect = (RectTransform)target.transform;
            var pointer = new PointerEventData(EventSystem.current)
            {
                position = RectTransformUtility.WorldToScreenPoint(null, rect.TransformPoint(rect.rect.center)),
            };
            var hits = new System.Collections.Generic.List<RaycastResult>();
            EventSystem.current.RaycastAll(pointer, hits);
            foreach (RaycastResult hit in hits)
            {
                if (hit.gameObject != null)
                    return hit.gameObject;
            }
            return null;
        }

        [UnityTest]
        public IEnumerator ThrowingClickListenerRemainsFailureAndReleaseNeverReplaysEvents()
        {
            const string message = "RAYCAST_LISTENER_FAILURE";
            int clicks = 0;
            _button.onClick.AddListener(() =>
            {
                clicks++;
                throw new InvalidOperationException(message);
            });
            EventSystem.current.SetSelectedGameObject(_canvas);
            yield return null;
            var host = new UnityPlayScenarioHost();
            var step = new PlayScenarioStep
            {
                Name = "raycast",
                Action = "click_ui",
                Target = _canvas.name + "/Button",
                ClickMode = "raycast",
            };
            LogAssert.Expect(LogType.Exception, new Regex("InvalidOperationException: " + message));
            Assert.Throws<InvalidOperationException>(() => host.Evaluate(step, true));
            host.Release();
            host.Release();
            Assert.That(clicks, Is.EqualTo(1));
            Assert.That(_probe.Events, Is.EqualTo(new[] { "enter", "down", "up", "click", "exit" }));
            Assert.That(EventSystem.current.currentSelectedGameObject, Is.SameAs(_canvas));
        }
    }
}
#endif
