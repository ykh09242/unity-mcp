using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.UI16CaretRuntime
{
    // Engine-only, test-project-only. This never constructs or repairs a tool graph.
    public sealed class UguiCompoundCaretSceneWitness : MonoBehaviour
    {
        public string nonce;
        public string ownedScenePath;
        public bool observed;
        public int matchingEvents;
        public string observedNonce;
        public string observedScenePath;
        private bool subscribed;

        private void OnEnable()
        {
            if (!Application.isPlaying || subscribed)
                return;
            if (string.IsNullOrEmpty(nonce) || nonce.Length != 32 || string.IsNullOrEmpty(ownedScenePath) || ownedScenePath.Length > 512)
                return;
            SceneManager.sceneLoaded += ObserveOwnedScene;
            subscribed = true;
        }

        private void ObserveOwnedScene(Scene scene, LoadSceneMode mode)
        {
            if (!Application.isPlaying || scene.path != ownedScenePath || !scene.Equals(gameObject.scene))
                return;
            matchingEvents++;
            observed = true;
            observedNonce = nonce;
            observedScenePath = scene.path;
            Unsubscribe();
        }

        private void OnDisable() => Unsubscribe();

        private void OnDestroy() => Unsubscribe();

        private void Unsubscribe()
        {
            if (!subscribed)
                return;
            SceneManager.sceneLoaded -= ObserveOwnedScene;
            subscribed = false;
        }
    }
}
