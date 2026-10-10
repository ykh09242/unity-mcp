using System;
using System.IO;
using MCPForUnity.Runtime;
using UnityEngine;
using UnityEngine.UI;

namespace MCPForUnityTests.PlayScenarios.Integration.Runtime
{
    public sealed class PlayScenarioResourceIntegrationProbe : MonoBehaviour
    {
        public static IDisposable BaselineHandle;
        public bool ReplacementOnly;
        public bool Acquired;
        public bool Released;
        public int AcquireCount;
        public int ReleaseCount;
        private PlayScenarioResourceIntegrationClone _clone;
        private IDisposable _subscriptionToken;
        private IDisposable _handleToken;
        private MemoryStream _handle;
        private event Action OwnedEvent;

        private void Awake()
        {
            if (!Application.isPlaying)
                return;
            var canvas = new GameObject("Canvas", typeof(RectTransform), typeof(Canvas), typeof(GraphicRaycaster));
            canvas.transform.SetParent(transform, false);
            canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            CreateButton(canvas.transform, "Acquire", Acquire, -100);
            CreateButton(canvas.transform, "Release", Release, 100);
        }

        private static void CreateButton(Transform parent, string name, Action callback, float x)
        {
            var target = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            target.transform.SetParent(parent, false);
            var rect = target.GetComponent<RectTransform>();
            rect.sizeDelta = new Vector2(120, 60);
            rect.anchoredPosition = new Vector2(x, 0);
            target.GetComponent<Button>().onClick.AddListener(() => callback());
        }

        private void Acquire()
        {
            AcquireCount++;
            if (ReplacementOnly)
            {
                BaselineHandle?.Dispose();
                BaselineHandle = null;
            }
            else
            {
                _clone = ScriptableObject.CreateInstance<PlayScenarioResourceIntegrationClone>();
                PlayScenarioResourceTracker.RegisterScriptableObject(_clone);
                OwnedEvent += OnOwnedEvent;
                _subscriptionToken = PlayScenarioResourceTracker.RegisterSubscription();
            }
            _handle = new MemoryStream(new byte[64]);
            _handleToken = PlayScenarioResourceTracker.RegisterHandle();
            Acquired = true;
            OwnedEvent?.Invoke();
        }

        private void OnOwnedEvent() { }

        private void Release()
        {
            ReleaseCount++;
            if (_clone != null)
                Destroy(_clone);
            OwnedEvent -= OnOwnedEvent;
            _subscriptionToken?.Dispose();
            _subscriptionToken = null;
            _handle?.Dispose();
            _handle = null;
            _handleToken?.Dispose();
            _handleToken = null;
            Released = true;
        }

        private void OnDestroy()
        {
            Release();
            BaselineHandle?.Dispose();
            BaselineHandle = null;
        }
    }

    public sealed class PlayScenarioResourceIntegrationClone : ScriptableObject { }
}
