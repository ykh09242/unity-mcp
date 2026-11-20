using System;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class SceneIndexProbe : MonoBehaviour
    {
        [Serializable]
        private sealed class Observation
        {
            public string trial;
            public string build_guid;
            public int pid;
            public int index;
            public string scene;
        }

        private string trial;

        private void Start()
        {
            string[] args = Environment.GetCommandLineArgs();
            int flag = Array.IndexOf(args, "--scene-trial");
            if (Application.isEditor || flag < 0 || flag + 1 >= args.Length || !Guid.TryParseExact(args[flag + 1], "N", out _))
                return;
            trial = args[flag + 1];
            DontDestroyOnLoad(gameObject);
            SceneManager.sceneLoaded += Loaded;
            SceneManager.LoadScene(1);
        }

        private void Loaded(Scene scene, LoadSceneMode mode)
        {
            SceneManager.sceneLoaded -= Loaded;
            using var process = System.Diagnostics.Process.GetCurrentProcess();
            var observation = new Observation
            {
                trial = trial,
                build_guid = Application.buildGUID,
                pid = process.Id,
                scene = scene.path,
                index = scene.buildIndex,
            };
            Debug.Log("MCP_SCENE_INDEX " + JsonUtility.ToJson(observation));
            Application.Quit(0);
        }
    }
}
