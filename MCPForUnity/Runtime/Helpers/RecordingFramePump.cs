using System;
using System.Collections;
using UnityEngine;

namespace MCPForUnity.Runtime.Helpers
{
    /// <summary>Invokes the Editor recorder only after cameras and overlay UI have finished rendering.</summary>
    internal sealed class RecordingFramePump : MonoBehaviour
    {
        private Action _onFrame;
        private Action _onDestroyed;

        internal static RecordingFramePump Begin(Action onFrame, Action onDestroyed)
        {
            if (!Application.isPlaying)
                throw new InvalidOperationException("Game View recording requires Play Mode.");
            var go = new GameObject("__MCP_RecordingFramePump__");
            try
            {
                go.hideFlags = HideFlags.HideAndDontSave;
                DontDestroyOnLoad(go);
                var pump = go.AddComponent<RecordingFramePump>();
                pump._onFrame = onFrame;
                pump._onDestroyed = onDestroyed;
                return pump;
            }
            catch
            {
                DestroyImmediate(go);
                throw;
            }
        }

        private IEnumerator Start()
        {
            var endOfFrame = new WaitForEndOfFrame();
            while (_onFrame != null)
            {
                yield return endOfFrame;
                _onFrame?.Invoke();
            }
        }

        internal void Cancel()
        {
            _onFrame = null;
            _onDestroyed = null;
            StopAllCoroutines();
            if (this != null)
                DestroyImmediate(gameObject);
        }

        private void OnDestroy()
        {
            _onFrame = null;
            var callback = _onDestroyed;
            _onDestroyed = null;
            callback?.Invoke();
        }
    }
}
