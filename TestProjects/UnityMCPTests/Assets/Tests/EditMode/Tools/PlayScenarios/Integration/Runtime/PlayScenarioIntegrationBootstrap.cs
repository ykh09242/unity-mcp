using System.Collections;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

namespace MCPForUnityTests.PlayScenarios.Integration.Runtime
{
    public sealed class PlayScenarioIntegrationBootstrap : MonoBehaviour
    {
        public enum SceneRole
        {
            Menu,
            Game,
            MissingPlayer,
            Hardening,
        }

        public SceneRole Role;
        public string GameScenePath;
        public bool DisableStartButton;
        public bool ThrowOnStartClick;
        public bool DelayedError;
        public bool CleanupError;
        public bool Initialized;
        public int StartClickCount;
        public int CleanupClickCount;

        private IEnumerator EmitDelayedError()
        {
            yield return new WaitForSecondsRealtime(0.15f);
            Debug.LogError("PLAY_SCENARIO_QA_DELAYED_ERROR");
        }

        private void PrepareHardening()
        {
            new GameObject("Camera", typeof(Camera)).transform.SetParent(transform, false);
            var canvas = new GameObject("Canvas", typeof(Canvas), typeof(GraphicRaycaster));
            canvas.transform.SetParent(transform, false);
            canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            CreateHardeningButton(
                canvas.transform,
                "StartButton",
                () =>
                {
                    StartClickCount++;
                    Debug.Log("PLAY_SCENARIO_QA_HARDENING_START");
                    if (DelayedError)
                        StartCoroutine(EmitDelayedError());
                }
            );
            CreateHardeningButton(
                canvas.transform,
                "CleanupButton",
                () =>
                {
                    CleanupClickCount++;
                    Initialized = false;
                    Debug.Log("PLAY_SCENARIO_QA_HARDENING_CLEANUP");
                    if (CleanupError)
                        Debug.LogError("PLAY_SCENARIO_QA_CLEANUP_ERROR");
                }
            );
            for (int i = 0; i < 2; i++)
            {
                var duplicate = new GameObject("InactiveDuplicate");
                duplicate.transform.SetParent(transform, false);
                duplicate.SetActive(false);
            }
            new GameObject("EventSystem", typeof(EventSystem)).transform.SetParent(transform, false);
        }

        private static void CreateHardeningButton(Transform parent, string name, UnityEngine.Events.UnityAction action)
        {
            var button = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            button.transform.SetParent(parent, false);
            button.GetComponent<Button>().onClick.AddListener(action);
        }

        private void Start()
        {
            if (Role == SceneRole.Menu)
            {
                var canvas = new GameObject("Canvas", typeof(Canvas), typeof(GraphicRaycaster));
                canvas.transform.SetParent(transform, false);
                canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
                var button = new GameObject("StartButton", typeof(RectTransform), typeof(Image), typeof(Button));
                button.transform.SetParent(canvas.transform, false);
                var startButton = button.GetComponent<Button>();
                startButton.enabled = !DisableStartButton;
                startButton.onClick.AddListener(() =>
                {
                    Debug.Log("PLAY_SCENARIO_QA_CLICK");
                    if (ThrowOnStartClick)
                        throw new System.InvalidOperationException("PLAY_SCENARIO_QA_LISTENER_FAILURE");
                    SceneManager.LoadSceneAsync(GameScenePath, LoadSceneMode.Single);
                });
                var events = new GameObject("EventSystem", typeof(EventSystem));
                events.transform.SetParent(transform, false);
                Debug.Log("PLAY_SCENARIO_QA_MENU");
            }
            else if (Role == SceneRole.Game)
            {
                var player = new GameObject("Player");
                SceneManager.MoveGameObjectToScene(player, gameObject.scene);
                Debug.Log("PLAY_SCENARIO_QA_PLAYER");
            }
            else if (Role == SceneRole.Hardening)
                PrepareHardening();
            else
                Debug.LogWarning("PLAY_SCENARIO_QA_MISSING_PLAYER");
        }
    }
}
