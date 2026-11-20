using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace MCPForUnityTests.PlayScenarios.Player
{
    /// <summary>Witness for the owned standalone blocked and repaired pointer runs.</summary>
    public sealed class VerifiedPointerPlayerProbe : MonoBehaviour, IPlayScenarioStateProvider
    {
        public const string StateId = "verified-pointer.opened";
        public const string TargetId = "verified-pointer-button";
        private const int WitnessLimit = 16384;
        private const string ControlVariable = "MCP_VERIFIED_POINTER_CONTROL";

        private IDisposable registration;
        private GameObject button;
        private GameObject overlay;
        private CanvasGroup group;
        private string control;
        private string requestPath;
        private int deliveries;
        private Vector2 receivedPosition;
        private GameObject receivedHit;
        private bool opened;
        private JObject initial;

        private void Awake()
        {
            string[] args = Environment.GetCommandLineArgs();
            int requestIndex = Array.IndexOf(args, "--mcp-scenario-request");
            control = Environment.GetEnvironmentVariable(ControlVariable);
            if (requestIndex < 0 || requestIndex + 1 >= args.Length || (control != "blocked" && control != "repaired"))
                throw new ArgumentException("The pointer fixture requires a scenario request and an explicit blocked or repaired control.");
            requestPath = PlayScenarioPlayerFiles.CheckedAbsolute(args[requestIndex + 1]);
            new GameObject("VerifiedPointerEvents", typeof(EventSystem));
            var canvas = new GameObject("VerifiedPointerCanvas", typeof(RectTransform), typeof(Canvas), typeof(GraphicRaycaster));
            canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            button = NewImage("VerifiedPointerButton", canvas.transform);
            button.AddComponent<Button>().onClick.AddListener(() => opened = true);
            button.AddComponent<PlayScenarioTarget>().TargetId = TargetId;
            var trigger = button.AddComponent<EventTrigger>();
            var entry = new EventTrigger.Entry { eventID = EventTriggerType.PointerClick };
            entry.callback.AddListener(value =>
            {
                var pointer = (PointerEventData)value;
                deliveries++;
                receivedPosition = pointer.position;
                receivedHit = pointer.pointerCurrentRaycast.gameObject;
            });
            trigger.triggers.Add(entry);
            overlay = NewImage("VerifiedPointerOverlay", canvas.transform);
            overlay.AddComponent<EventTrigger>();
            group = overlay.AddComponent<CanvasGroup>();
            group.alpha = 0f;
            group.blocksRaycasts = control == "blocked";
            registration = PlayScenarioStateRegistry.Register(StateId, this);
        }

        private static GameObject NewImage(string name, Transform parent)
        {
            var value = new GameObject(name, typeof(RectTransform), typeof(Image));
            value.transform.SetParent(parent, false);
            ((RectTransform)value.transform).sizeDelta = new Vector2(160, 100);
            return value;
        }

        private void Update()
        {
            if (initial == null && button != null && button.GetComponent<Image>().depth >= 0 && overlay.GetComponent<Image>().depth >= 0)
                initial = Observe();
        }

        private JObject Observe()
        {
            var rect = (RectTransform)button.transform;
            Vector2 center = RectTransformUtility.WorldToScreenPoint(null, rect.TransformPoint(rect.rect.center));
            var hits = new List<RaycastResult>();
            EventSystem.current.RaycastAll(new PointerEventData(EventSystem.current) { position = center }, hits);
            return new JObject
            {
                ["viewport"] = new JArray(Screen.width, Screen.height),
                ["screen_position"] = new JArray(center.x, center.y),
                ["hits"] = new JArray(hits.Where(hit => hit.gameObject != null).Select(hit => hit.gameObject.name)),
                ["overlay_alpha"] = group.alpha,
                ["overlay_blocks_raycasts"] = group.blocksRaycasts,
                ["opened"] = opened,
                ["deliveries"] = deliveries,
                ["received_position"] = deliveries == 0 ? null : new JArray(receivedPosition.x, receivedPosition.y),
                ["received_hit"] = receivedHit == null ? null : receivedHit.name,
            };
        }

        public bool TryRead(out PlayScenarioStateValue value)
        {
            value = PlayScenarioStateValue.FromBoolean(opened);
            return initial != null;
        }

        private void OnApplicationQuit()
        {
            if (initial == null || requestPath == null)
                return;
            var request = PlayScenarioPlayerRequest.Parse(PlayScenarioPlayerFiles.Read(requestPath, 4096));
            var witness = new JObject
            {
                ["control"] = control,
                ["unity_version"] = Application.unityVersion,
                ["graphics_device"] = SystemInfo.graphicsDeviceType.ToString(),
                ["job_id"] = request.JobId,
                ["build_id"] = request.BuildId,
                ["build_source_revision"] = request.BuildSourceRevision,
                ["payload_hash"] = request.PayloadHash,
                ["definition_hash"] = request.DefinitionHash,
                ["initial"] = initial,
                ["final"] = Observe(),
            };
            PlayScenarioPlayerFiles.WriteNew(
                Path.Combine(Path.GetDirectoryName(requestPath), "verified-pointer-witness.json"),
                witness.ToString(Formatting.None),
                WitnessLimit
            );
        }

        private void OnDestroy() => registration?.Dispose();
    }
}
