using System;
using System.Collections;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    /// <summary>Explicit scalar probe and bounded failure modes for the dedicated native test Player.</summary>
    public sealed class ScenarioPlayerFixtureProbe : MonoBehaviour, IPlayScenarioStateProvider
    {
        private IDisposable registration;
        private bool ready;
        private int reads;

        private void Awake() => registration = PlayScenarioStateRegistry.Register("fixture-ready", this);

        public bool TryRead(out PlayScenarioStateValue value)
        {
            reads++;
            value = PlayScenarioStateValue.FromBoolean(ready);
            return true;
        }

        private IEnumerator Start()
        {
            string mode = ScenarioPlayerFixture.Mode;
            if (mode == "crash" || mode == "hang")
            {
                yield return new WaitForSecondsRealtime(2);
                Debug.Log("PLAYER_FIXTURE_AFTER_LIVE_UPDATES:" + mode);
                if (mode == "crash")
                {
                    using (var process = System.Diagnostics.Process.GetCurrentProcess())
                        process.Kill();
                }
                else
                {
                    // A deliberately bounded main-thread stall; the harness owns the shorter termination deadline.
                    System.Threading.Thread.Sleep(8000);
                }
                yield break;
            }
            yield return new WaitForSecondsRealtime(0.4f);
            if (mode == "state-destroyed")
            {
                Debug.Log("PLAYER_FIXTURE_PROVIDER_DESTROYED:reads=" + reads);
                Destroy(gameObject);
                yield break;
            }
            if (mode == "state-wrong" || mode == "timeout" || mode == "cancel")
                yield break;
            ready = true;
            Debug.Log("PLAYER_FIXTURE_PROVIDER_READY:reads=" + reads);
        }

        private void OnDestroy() => registration?.Dispose();
    }
}
