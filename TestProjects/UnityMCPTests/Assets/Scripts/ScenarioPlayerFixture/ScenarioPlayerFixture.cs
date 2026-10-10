using System;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

namespace MCPForUnityTests.PlayScenarios.Player
{
    /// <summary>Dedicated synthetic native Player menu, gameplay and repeat-reset witness.</summary>
    public sealed class ScenarioPlayerFixture : MonoBehaviour
    {
        public bool IsMenu;
        public string GameScene;
        private static ScenarioPlayerFixtureState state;
        internal static int Iteration => state == null ? 0 : state.ResetCount;
        internal static string Mode
        {
            get
            {
                string[] args = Environment.GetCommandLineArgs();
                int index = Array.IndexOf(args, "--mcp-fixture-mode");
                return index >= 0 && index + 1 < args.Length ? args[index + 1] : Environment.GetEnvironmentVariable("MCP_SCENARIO_FIXTURE_MODE") ?? "success";
            }
        }

        private void Awake()
        {
            if (IsMenu)
            {
                if (state == null)
                {
                    var owner = new GameObject("Fixture State");
                    DontDestroyOnLoad(owner);
                    state = owner.AddComponent<ScenarioPlayerFixtureState>();
                }
                var events = new GameObject("EventSystem", typeof(EventSystem));
                var canvas = new GameObject("MainMenu", typeof(RectTransform), typeof(Canvas), typeof(GraphicRaycaster));
                canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
                var buttonObject = new GameObject("StartButton", typeof(RectTransform), typeof(Image), typeof(Button), typeof(PlayScenarioTarget));
                buttonObject.transform.SetParent(canvas.transform, false);
                var rect = buttonObject.GetComponent<RectTransform>();
                rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
                rect.sizeDelta = new Vector2(240, 100);
                buttonObject.GetComponent<PlayScenarioTarget>().TargetId = "fixture-start";
                buttonObject
                    .GetComponent<Button>()
                    .onClick.AddListener(() =>
                    {
                        state.ClicksInIteration++;
                        state.TotalClicks++;
                        if (state.ClicksInIteration != 1 || state.ResetCount != state.TotalClicks)
                            throw new InvalidOperationException("Fixture reset did not isolate the iteration.");
                        Debug.Log("Player fixture click:" + state.TotalClicks);
                        if (Mode == "listener-error")
                            Debug.LogError("Player fixture listener unexpected error.");
                        SceneManager.LoadScene(GameScene);
                    });
                if (Mode == "blocked")
                {
                    var cover = new GameObject("Blocker", typeof(RectTransform), typeof(Image));
                    cover.transform.SetParent(canvas.transform, false);
                    var coverRect = cover.GetComponent<RectTransform>();
                    coverRect.anchorMin = Vector2.zero;
                    coverRect.anchorMax = Vector2.one;
                    coverRect.offsetMin = coverRect.offsetMax = Vector2.zero;
                    cover.GetComponent<Image>().color = Color.black;
                }
            }
            else
            {
                if (Mode == "error")
                    Debug.LogError("Player fixture deliberate unexpected error.");
                if (Mode != "state-missing")
                    new GameObject("Fixture Ready Probe").AddComponent<ScenarioPlayerFixtureProbe>();
                if (Mode == "timeout" || Mode == "cancel" || Mode == "crash" || Mode == "hang")
                    return;
                var player = new GameObject("Player", typeof(PlayScenarioTarget));
                player.GetComponent<PlayScenarioTarget>().TargetId = "fixture-player";
                Debug.Log("Player fixture gameplay ready:" + state.TotalClicks);
            }
        }
    }
}
