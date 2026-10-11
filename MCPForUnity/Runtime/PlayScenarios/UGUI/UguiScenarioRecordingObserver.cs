using System;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Uses only Button's existing pointer-click interface; recording ownership remains in the Editor.</summary>
    [AddComponentMenu("")]
    [DefaultExecutionOrder(-10000)]
    public sealed class UguiScenarioRecordingObserver : MonoBehaviour, IPointerClickHandler
    {
        private Button button;
        private Action<GameObject> click;
        public bool HasButton => button != null;

        public void Bind(Button observed, Action<GameObject> capture)
        {
            button = observed;
            click = capture;
        }

        public void Release()
        {
            click = null;
            button = null;
            enabled = false;
        }

        private void Update()
        {
            if (button == null)
                Release();
        }

        public void OnPointerClick(PointerEventData data)
        {
            if (data.button != PointerEventData.InputButton.Left || click == null || button == null || !button.IsActive() || !button.IsInteractable())
                return;
            // The Editor inserts this transient component before Button's callback, so scene destruction
            // cannot erase target identity before the synchronous owner callback snapshots it.
            click(gameObject);
        }
    }
}
