using System;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

public sealed class TelescopeProbe : MonoBehaviour, IPlayScenarioStateProvider
{
    private IDisposable registration;

    private void OnEnable() => registration = PlayScenarioStateRegistry.Register("observatory.instrument", this);

    private void OnDisable() => registration?.Dispose();

    public bool TryRead(out PlayScenarioStateValue value)
    {
        value = PlayScenarioStateValue.FromString(gameObject.name);
        return true;
    }
}
