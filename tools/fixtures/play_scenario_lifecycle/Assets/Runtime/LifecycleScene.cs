using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

namespace UnityMcpLifecycleSample
{
    public sealed class LifecycleScene : MonoBehaviour
    {
        public enum SceneKind
        {
            Menu,
            Game,
        }

        public SceneKind Kind;
        public SessionConfig Source;
        public string MenuScenePath;
        public string GameScenePath;
        public bool Ready;
        public SessionResources Owner { get; private set; }
        public static IReadOnlyList<SessionResources> Retained => RetainedOwners;
        private static readonly List<SessionResources> RetainedOwners = new List<SessionResources>();
        private static bool _retainNextSession;
        private bool _retainThisSession;
        private bool _leaving;
        private Text _status;
        private GameObject _player;

        private void Start()
        {
            var cameraObject = new GameObject("Camera", typeof(Camera));
            cameraObject.transform.SetParent(transform, false);
            cameraObject.transform.position = new Vector3(0, 0, -10);
            var camera = cameraObject.GetComponent<Camera>();
            camera.orthographic = true;
            camera.orthographicSize = 5;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.05f, 0.08f, 0.14f);
            var canvasObject = new GameObject("Canvas", typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            canvasObject.transform.SetParent(transform, false);
            canvasObject.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            var scaler = canvasObject.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(960, 540);
            new GameObject("EventSystem", typeof(EventSystem), typeof(StandaloneInputModule)).transform.SetParent(transform, false);
            Transform canvas = canvasObject.transform;
            Label(canvas, "Title", Kind == SceneKind.Menu ? "RESOURCE LIFECYCLE / MENU" : "RESOURCE LIFECYCLE / GAME", 210, 28);
            _status = Label(canvas, "Status", "", 145, 18);
            if (Kind == SceneKind.Menu)
            {
                Button(canvas, "Start", "Start normal session", () => StartGame(false), 60);
                Button(canvas, "StartRetained", "Start intentional retention", () => StartGame(true), -10);
                Button(
                    canvas,
                    "ReleaseRetained",
                    "Release retained resources",
                    () =>
                    {
                        ReleaseRetained();
                        RefreshStatus();
                    },
                    -80
                );
                Label(canvas, "Hint", "Normal return releases resources. The negative control requires explicit release.", -190, 16);
            }
            else
            {
                _retainThisSession = _retainNextSession;
                _retainNextSession = false;
                Owner = new SessionResources(Source);
                Owner.Clone.Health--;
                _player = GameObject.CreatePrimitive(PrimitiveType.Cube);
                _player.name = "Player";
                _player.transform.position = new Vector3(-3, 0, 0);
                Button(
                    canvas,
                    "Pulse",
                    "Publish event",
                    () =>
                    {
                        SampleSignals.Publish();
                        RefreshStatus();
                    },
                    45
                );
                Button(
                    canvas,
                    "RemovePlayer",
                    "Remove Player (failure control)",
                    () =>
                    {
                        Destroy(_player);
                        RefreshStatus();
                    },
                    -25
                );
                Button(canvas, "Return", "Return to menu", ReturnToMenu, -95);
                Label(canvas, "Hint", "The source asset stays unchanged. A session owns its clone, event and stream.", -190, 16);
            }
            Ready = true;
            RefreshStatus();
        }

        private void StartGame(bool retain)
        {
            if (_leaving)
                return;
            _leaving = true;
            _retainNextSession = retain;
            SceneManager.LoadSceneAsync(GameScenePath, LoadSceneMode.Single);
        }

        private void ReturnToMenu()
        {
            if (_leaving)
                return;
            _leaving = true;
            ReleaseSession();
            SceneManager.LoadSceneAsync(MenuScenePath, LoadSceneMode.Single);
        }

        private void ReleaseSession()
        {
            if (Owner == null)
                return;
            if (_retainThisSession)
                RetainedOwners.Add(Owner);
            else
                Owner.Dispose();
            Owner = null;
        }

        public static void ReleaseRetained()
        {
            foreach (SessionResources owner in RetainedOwners)
                owner.Dispose();
            RetainedOwners.Clear();
        }

        private void OnDestroy() => ReleaseSession();

        private void OnApplicationQuit()
        {
            _retainThisSession = false;
            ReleaseSession();
            ReleaseRetained();
        }

        private void RefreshStatus()
        {
            _status.text =
                Kind == SceneKind.Menu
                    ? "Source health: " + Source.Health + "   |   Retained sessions: " + RetainedOwners.Count
                    : "Clone health: "
                        + Owner.Clone.Health
                        + "   |   Events: "
                        + Owner.EventsReceived
                        + "   |   "
                        + (_retainThisSession ? "INTENTIONAL RETENTION" : "NORMAL CLEANUP");
        }

        private static Text Label(Transform parent, string name, string text, float y, int size)
        {
            var label = new GameObject(name, typeof(RectTransform), typeof(Text));
            label.transform.SetParent(parent, false);
            var rect = label.GetComponent<RectTransform>();
            rect.sizeDelta = new Vector2(900, 60);
            rect.anchoredPosition = new Vector2(0, y);
            var component = label.GetComponent<Text>();
            component.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            component.text = text;
            component.fontSize = size;
            component.alignment = TextAnchor.MiddleCenter;
            component.color = Color.white;
            component.raycastTarget = false;
            return component;
        }

        private static void Button(Transform parent, string name, string text, Action callback, float y)
        {
            var button = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            button.transform.SetParent(parent, false);
            var rect = button.GetComponent<RectTransform>();
            rect.sizeDelta = new Vector2(360, 52);
            rect.anchoredPosition = new Vector2(70, y);
            button.GetComponent<Image>().color = new Color(0.15f, 0.35f, 0.52f);
            button.GetComponent<Button>().onClick.AddListener(() => callback());
            Text label = Label(button.transform, "Label", text, 0, 18);
            label.rectTransform.sizeDelta = rect.sizeDelta;
        }
    }
}
