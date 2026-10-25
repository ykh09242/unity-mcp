using System;
using UnityEngine;

namespace MCPForUnity.Runtime.Helpers
{
    /// <summary>Samples legacy input in game Update, before uGUI dispatch, only while an Editor recording owns it.</summary>
    [DefaultExecutionOrder(-32000)]
    public sealed class LegacyInputRecordingPump : MonoBehaviour
    {
        private Action sample;
        private Action destroyed;

        public static LegacyInputRecordingPump Begin(Action sample, Action destroyed)
        {
            if (!Application.isPlaying)
                throw new InvalidOperationException("Legacy input recording requires Play Mode.");
            var owner = new GameObject("__MCP_LegacyInputRecording__");
            try
            {
                owner.hideFlags = HideFlags.HideAndDontSave;
                DontDestroyOnLoad(owner);
                var pump = owner.AddComponent<LegacyInputRecordingPump>();
                pump.sample = sample;
                pump.destroyed = destroyed;
                return pump;
            }
            catch
            {
                DestroyImmediate(owner);
                throw;
            }
        }

        private void Update() => sample?.Invoke();

        public void Cancel()
        {
            sample = null;
            destroyed = null;
            if (this != null)
                DestroyImmediate(gameObject);
        }

        private void OnDestroy()
        {
            sample = null;
            var callback = destroyed;
            destroyed = null;
            callback?.Invoke();
        }
    }
}
