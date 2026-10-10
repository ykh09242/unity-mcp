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
        }

        public SceneRole Role;
        public string GameScenePath;

        private void Start()
        {
            if (Role == SceneRole.Menu)
            {
                var canvas = new GameObject("Canvas", typeof(Canvas), typeof(GraphicRaycaster));
                canvas.transform.SetParent(transform, false);
                canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
                var button = new GameObject("StartButton", typeof(RectTransform), typeof(Image), typeof(Button));
                button.transform.SetParent(canvas.transform, false);
                button
                    .GetComponent<Button>()
                    .onClick.AddListener(() =>
                    {
                        Debug.Log("PLAY_SCENARIO_QA_CLICK");
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
            else
                Debug.LogWarning("PLAY_SCENARIO_QA_MISSING_PLAYER");
        }
    }
}
