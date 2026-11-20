using System;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

public sealed class BerthProbe : MonoBehaviour, IPlayScenarioStateProvider
{
    private IDisposable registration;

    private void OnEnable() => registration = PlayScenarioStateRegistry.Register("harbor.berth", this);

    private void OnDisable() => registration?.Dispose();

    public bool TryRead(out PlayScenarioStateValue value)
    {
        value = PlayScenarioStateValue.FromInteger(Mathf.RoundToInt(transform.position.y));
        return true;
    }
}
