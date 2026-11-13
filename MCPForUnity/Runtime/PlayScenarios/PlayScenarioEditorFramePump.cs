#if UNITY_EDITOR
using System;
using UnityEngine;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Executes measured Editor scenario work inside the Player frame reported by Unity's counters.</summary>
    internal sealed class PlayScenarioEditorFramePump : MonoBehaviour
    {
        private Action tick;

        internal static PlayScenarioEditorFramePump Begin(Action tick)
        {
            var owner = new GameObject("__MCP_ScenarioEditorFramePump__");
            try
            {
                owner.hideFlags = HideFlags.HideAndDontSave;
                DontDestroyOnLoad(owner);
                var pump = owner.AddComponent<PlayScenarioEditorFramePump>();
                pump.tick = tick;
                return pump;
            }
            catch
            {
                DestroyImmediate(owner);
                throw;
            }
        }

        private void Update() => tick?.Invoke();

        internal void Stop()
        {
            tick = null;
            DestroyImmediate(gameObject);
        }
    }
}
#endif
