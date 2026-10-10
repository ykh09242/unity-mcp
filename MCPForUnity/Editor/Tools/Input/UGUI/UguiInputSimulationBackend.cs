#if MCP_INPUT_UGUI
using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using UnityEditor;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Direct uGUI event dispatch, independent of the project's raw input backend.</summary>
    [InitializeOnLoad]
    public sealed class UguiInputSimulationBackend : IUguiInputSimulationBackend, IUguiScenarioClickBackend
    {
        static UguiInputSimulationBackend() => ManageInput.UguiBackend = new UguiInputSimulationBackend();

        private struct ClickContext
        {
            public EventSystem EventSystem;
            public Canvas Canvas;
            public GameObject Handler;
            public Selectable Selectable;
        }

        public object Click(GameObject target)
        {
            TryPrepare(target, false, out var context, out _);
            return Dispatch(target, context);
        }

        public bool TryClick(GameObject target, out object result, out string detail)
        {
            result = null;
            if (!TryPrepare(target, true, out var context, out detail))
                return false;
            result = Dispatch(target, context);
            detail = "Dispatched uGUI pointer click.";
            return true;
        }

        private static bool TryPrepare(GameObject target, bool waitForReady, out ClickContext context, out string detail)
        {
            context = default;
            detail = null;
            if (target == null)
                throw new ArgumentException("ui_click requires a non-null target.");
            EventSystem eventSystem = EventSystem.current;
            if (eventSystem == null || !eventSystem.isActiveAndEnabled)
                return NotReady("ui_click requires an active EventSystem in the scene.", waitForReady, out detail);
            Canvas canvas = target.GetComponentInParent<Canvas>();
            if (canvas == null)
                throw new ArgumentException("ui_click target must be beneath an active Canvas.");
            if (!canvas.isActiveAndEnabled)
                return NotReady("ui_click target must be beneath an active Canvas.", waitForReady, out detail);
            GameObject handler = ExecuteEvents.GetEventHandler<IPointerClickHandler>(target);
            if (handler == null)
                throw new ArgumentException("target has no active uGUI pointer click handler in its parent chain.");
            Selectable selectable = handler.GetComponent<Selectable>();
            if (selectable != null && !selectable.IsInteractable())
                return NotReady("target's Selectable is not interactable.", waitForReady, out detail);
            context = new ClickContext
            {
                EventSystem = eventSystem,
                Canvas = canvas,
                Handler = handler,
                Selectable = selectable,
            };
            return true;
        }

        private static bool NotReady(string reason, bool waitForReady, out string detail)
        {
            detail = reason;
            if (!waitForReady)
                throw new ArgumentException(reason);
            return false;
        }

        private static object Dispatch(GameObject target, ClickContext context)
        {
            EventSystem eventSystem = context.EventSystem;
            Canvas canvas = context.Canvas;
            GameObject handler = context.Handler;
            Selectable selectable = context.Selectable;
            var rect = target.transform as RectTransform;
            Camera camera = canvas.renderMode == RenderMode.ScreenSpaceOverlay ? null : canvas.worldCamera;
            Vector2 position = RectTransformUtility.WorldToScreenPoint(
                camera,
                rect == null ? target.transform.position : rect.TransformPoint(rect.rect.center)
            );
            var data = new PointerEventData(eventSystem)
            {
                button = PointerEventData.InputButton.Left,
                position = position,
                pressPosition = position,
                pointerId = -1,
                clickCount = 1,
                clickTime = Time.unscaledTime,
                eligibleForClick = true,
                pointerPress = handler,
                rawPointerPress = target,
                pointerEnter = handler,
                pointerCurrentRaycast = new RaycastResult { gameObject = target, screenPosition = position },
                pointerPressRaycast = new RaycastResult { gameObject = target, screenPosition = position },
            };
            int handlerId = handler.GetInstanceIDCompat();
            GameObject downHandler = ExecuteEvents.GetEventHandler<IPointerDownHandler>(target);
            bool released = false;
            try
            {
                ExecuteEvents.Execute(handler, data, ExecuteEvents.pointerEnterHandler);
                if (downHandler != null)
                    ExecuteEvents.Execute(downHandler, data, ExecuteEvents.pointerDownHandler);
                if (downHandler != null)
                    ExecuteEvents.Execute(downHandler, data, ExecuteEvents.pointerUpHandler);
                released = true;
                if (handler == null || !handler.activeInHierarchy || (selectable != null && !selectable.IsInteractable()))
                    throw new InvalidOperationException("The click handler became inactive during pointer dispatch.");
                bool dispatched = ExecuteEvents.Execute(handler, data, ExecuteEvents.pointerClickHandler);
                if (!dispatched)
                    throw new InvalidOperationException("No active click handler accepted the pointer event.");
                return new SuccessResponse(
                    "Dispatched uGUI pointer click.",
                    new
                    {
                        handler_instance_id = handlerId,
                        direct_event_dispatch = true,
                        note = "Direct dispatch targets the resolved handler; it does not test screen occlusion or raycast hit order.",
                    }
                );
            }
            finally
            {
                if (!released && downHandler != null)
                    ExecuteEvents.Execute(downHandler, data, ExecuteEvents.pointerUpHandler);
                if (handler != null)
                    ExecuteEvents.Execute(handler, data, ExecuteEvents.pointerExitHandler);
            }
        }
    }
}
#endif
