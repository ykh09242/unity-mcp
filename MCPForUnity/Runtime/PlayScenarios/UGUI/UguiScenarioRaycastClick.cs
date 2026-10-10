#if MCP_INPUT_UGUI
using System;
using System.Collections.Generic;
using MCPForUnity.Runtime.PlayScenarios;
using MCPForUnity.Runtime.Helpers;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace MCPForUnity.Editor.Tools.Input
{
    public sealed partial class UguiScenarioDispatch
    {
        public bool TryRaycastClick(GameObject target, out object result, out string detail)
        {
            result = null;
            if (!TryPrepare(target, true, out var context, out detail))
                return false;
            if (!target.activeInHierarchy || (context.Selectable != null && !context.Selectable.isActiveAndEnabled))
                return NotReady("Waiting for an active target and enabled Selectable.", true, out detail);
            if (!AllowsCanvasGroups(target.transform))
                return NotReady("Waiting for CanvasGroup interaction and raycasts.", true, out detail);
            var rect = target.transform as RectTransform;
            if (rect == null)
                throw new ArgumentException("Raycast clicks require a RectTransform target.");
            Canvas.ForceUpdateCanvases();
            Camera camera =
                context.Canvas.renderMode == RenderMode.ScreenSpaceOverlay
                    ? null
                    : (context.Canvas.GetComponent<GraphicRaycaster>()?.eventCamera ?? context.Canvas.worldCamera);
            Vector3 center = rect.TransformPoint(rect.rect.center);
            if (camera != null && camera.WorldToScreenPoint(center).z <= 0)
                return NotReady("Waiting for the target center to be in front of its canvas camera.", true, out detail);
            Vector2 position = RectTransformUtility.WorldToScreenPoint(camera, center);
            if (!IsFinite(position.x) || !IsFinite(position.y) || position.x < 0 || position.y < 0 || position.x >= Screen.width || position.y >= Screen.height)
                return NotReady("Waiting for the target center to be within the screen.", true, out detail);
            var data = new PointerEventData(context.EventSystem)
            {
                button = PointerEventData.InputButton.Left,
                position = position,
                pressPosition = position,
                pointerId = -1,
                clickCount = 1,
                clickTime = Time.unscaledTime,
                eligibleForClick = true,
            };
            var hits = new List<RaycastResult>();
            context.EventSystem.RaycastAll(data, hits);
            RaycastResult first = default;
            foreach (RaycastResult hit in hits)
            {
                if (hit.gameObject == null)
                    continue;
                first = hit;
                break;
            }
            if (first.gameObject == null)
                return NotReady("Waiting for a uGUI raycast hit at the target center.", true, out detail);
            if (!(first.module is GraphicRaycaster) || ExecuteEvents.GetEventHandler<IPointerClickHandler>(first.gameObject) != context.Handler)
                return NotReady("Waiting for the target's click handler to be the first uGUI raycast hit; its center is blocked.", true, out detail);
            GameObject pressHandler = ExecuteEvents.GetEventHandler<IPointerDownHandler>(first.gameObject);
            if (pressHandler != null && pressHandler != context.Handler)
                return NotReady("Waiting for the pointer press and click handlers at the target center to match.", true, out detail);
            data.pointerCurrentRaycast = first;
            data.pointerPressRaycast = first;
            data.pointerPress = context.Handler;
            data.rawPointerPress = first.gameObject;
            data.pointerEnter = context.Handler;
            result = DispatchRaycast(context, target, first.gameObject, data);
            detail = "Dispatched one raycast-verified uGUI pointer click.";
            return true;
        }

        private static bool IsFinite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        private static bool AllowsCanvasGroups(Transform target)
        {
            for (Transform current = target; current != null; current = current.parent)
            {
                bool stop = false;
                foreach (CanvasGroup group in current.GetComponents<CanvasGroup>())
                {
                    if (!group.isActiveAndEnabled)
                        continue;
                    if (!group.interactable || !group.blocksRaycasts)
                        return false;
                    stop |= group.ignoreParentGroups;
                }
                if (stop)
                    break;
            }
            return true;
        }

        private static object DispatchRaycast(ClickContext context, GameObject target, GameObject hit, PointerEventData data)
        {
            EventSystem eventSystem = context.EventSystem;
            GameObject handler = context.Handler;
            GameObject previousSelection = eventSystem.currentSelectedGameObject;
            GameObject downHandler = ExecuteEvents.GetEventHandler<IPointerDownHandler>(hit);
            int handlerId = handler.GetInstanceIDCompat();
            bool hadSelectable = context.Selectable != null;
            bool entered = false;
            bool pressed = false;
            bool released = false;
            try
            {
                entered = true;
                ExecuteEvents.Execute(handler, data, ExecuteEvents.pointerEnterHandler);
                if (downHandler != null)
                {
                    pressed = true;
                    if (!ExecuteEvents.Execute(downHandler, data, ExecuteEvents.pointerDownHandler))
                        throw new InvalidOperationException("The pointer press handler became unavailable during raycast dispatch.");
                    released = true;
                    ExecuteEvents.Execute(downHandler, data, ExecuteEvents.pointerUpHandler);
                }
                if (
                    eventSystem == null
                    || !eventSystem.isActiveAndEnabled
                    || context.Canvas == null
                    || !context.Canvas.isActiveAndEnabled
                    || target == null
                    || !target.activeInHierarchy
                    || !AllowsCanvasGroups(target.transform)
                    || handler == null
                    || !handler.activeInHierarchy
                    || (hadSelectable && (context.Selectable == null || !context.Selectable.isActiveAndEnabled || !context.Selectable.IsInteractable()))
                )
                    throw new InvalidOperationException("The click handler became unavailable during raycast pointer dispatch.");
                var releaseHits = new List<RaycastResult>();
                eventSystem.RaycastAll(data, releaseHits);
                RaycastResult releaseHit = default;
                foreach (RaycastResult candidate in releaseHits)
                {
                    if (candidate.gameObject == null)
                        continue;
                    releaseHit = candidate;
                    break;
                }
                if (
                    releaseHit.gameObject == null
                    || !(releaseHit.module is GraphicRaycaster)
                    || ExecuteEvents.GetEventHandler<IPointerClickHandler>(releaseHit.gameObject) != handler
                )
                    throw new InvalidOperationException("The target became blocked during raycast pointer dispatch; the click was not sent.");
                data.pointerCurrentRaycast = releaseHit;
                if (!ExecuteEvents.Execute(handler, data, ExecuteEvents.pointerClickHandler))
                    throw new InvalidOperationException("No active click handler accepted the raycast pointer event.");
                return new PlayScenarioClickResult(
                    "Dispatched raycast-verified uGUI pointer click.",
                    new
                    {
                        handler_instance_id = handlerId,
                        click_mode = "raycast",
                        raycast_verified = true,
                        direct_event_dispatch = true,
                        note = "EventSystem raycast and synchronous pointer dispatch; no OS input injection.",
                    }
                );
            }
            finally
            {
                try
                {
                    if (pressed && !released && downHandler != null)
                        ExecuteEvents.Execute(downHandler, data, ExecuteEvents.pointerUpHandler);
                }
                finally
                {
                    try
                    {
                        if (entered && handler != null)
                            ExecuteEvents.Execute(handler, data, ExecuteEvents.pointerExitHandler);
                    }
                    finally
                    {
                        data.eligibleForClick = false;
                        data.pointerPress = null;
                        data.rawPointerPress = null;
                        data.pointerEnter = null;
                        data.pointerCurrentRaycast = default;
                        data.pointerPressRaycast = default;
                        if (eventSystem != null && eventSystem.currentSelectedGameObject != previousSelection)
                            eventSystem.SetSelectedGameObject(previousSelection == null ? null : previousSelection);
                    }
                }
            }
        }
    }
}
#endif
