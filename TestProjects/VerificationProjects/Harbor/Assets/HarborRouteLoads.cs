using System;
using MCPForUnity.Runtime;
using UnityEngine;

public static class HarborRouteLoads
{
    private const string Owner = "Assets/HarborRouteLoads.cs";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void LoadRoute()
    {
        var guide = Resources.Load<GameObject>("Harbor/Guide");
        ReferenceGraphRuntime.Load("resources", "Harbor/Guide", guide, Owner);
        string route = Environment.GetEnvironmentVariable("MCP_HARBOR_ROUTE");
        if (string.IsNullOrEmpty(route))
            return;
        if (route == "maintenance")
        {
            var maintenance = Resources.Load<GameObject>("Harbor/Maintenance");
            ReferenceGraphRuntime.Load("resources", "Harbor/Maintenance", maintenance, Owner);
            return;
        }
        var vessel = Resources.Load<GameObject>(route);
        ReferenceGraphRuntime.Load("resources", route, vessel, Owner);
    }
}
