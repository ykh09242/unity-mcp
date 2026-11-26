using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

// Only compiled into an explicitly provisioned disposable Editor. The production assembly
// performs all discovery, consent and command admission against its normal services.
public static class IsolatedToolPreferences
{
    public static void Start()
    {
        string project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        string lane = Path.GetDirectoryName(project);
        string control = Path.Combine(lane, "control");
        if (
            !Application.isBatchMode
            || !string.Equals(project, Environment.GetEnvironmentVariable("LIVE_LANE_PROJECT"), StringComparison.OrdinalIgnoreCase)
            || !string.Equals(Path.Combine(lane, "status"), Environment.GetEnvironmentVariable("UNITY_MCP_STATUS_DIR"), StringComparison.OrdinalIgnoreCase)
            || !File.Exists(Path.Combine(lane, "owned-lane.json"))
        )
            throw new InvalidOperationException("Isolated preference fixture requires its own batch Editor.");
        try
        {
            var input = JObject.Parse(File.ReadAllText(Path.Combine(control, "tool-preferences.json")));
            var snapshot = input["values"].ToObject<Dictionary<string, bool>>();
            var values = new Dictionary<string, bool>(snapshot);
            const string prefix = "MCPForUnity.ToolEnabled.";
            if (values.Keys.Any(name => !name.StartsWith(prefix, StringComparison.Ordinal)))
                throw new InvalidDataException("Expected a snapshot of tool enablement preferences.");
            VerifySharedPreferences(snapshot);

            var assembly = AppDomain.CurrentDomain.GetAssemblies().Single(a => a.GetName().Name == "MCPForUnity.Editor");
            var type = assembly.GetType("MCPForUnity.Editor.Services.ToolDiscoveryService", true);
            var contract = assembly.GetType("MCPForUnity.Editor.Services.IToolDiscoveryService", true);
            var locator = assembly.GetType("MCPForUnity.Editor.Services.MCPServiceLocator", true);
            var service = Activator.CreateInstance(
                type,
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new object[]
                {
                    new Func<string, bool>(values.ContainsKey),
                    new Func<string, bool, bool>((name, fallback) => values.TryGetValue(name, out var value) ? value : fallback),
                    new Action<string, bool>((name, value) => values[name] = value),
                },
                null
            );
            locator.GetMethod("Register").MakeGenericMethod(contract).Invoke(null, new[] { service });
            if (!ReferenceEquals(locator.GetProperty("ToolDiscovery").GetValue(null), service))
                throw new InvalidOperationException("The selected Editor discovery service did not register.");
            int discovered = ((IEnumerable)type.GetMethod("DiscoverAllTools").Invoke(service, null)).Cast<object>().Count();
            var expected = new Dictionary<string, bool>(values);
            var selected = input["enableTools"].ToObject<string[]>();
            foreach (string name in selected)
            {
                var metadata = type.GetMethod("GetToolMetadata").Invoke(service, new object[] { name });
                if (metadata == null || (bool)metadata.GetType().GetProperty("RequiresExplicitConsent").GetValue(metadata))
                    throw new InvalidOperationException("An isolated selection must be an ordinary discovered tool.");
                type.GetMethod("SetToolEnabled").Invoke(service, new object[] { name, true });
                if (!(bool)type.GetMethod("IsToolEnabled").Invoke(service, new object[] { name }))
                    throw new InvalidOperationException("The isolated tool selection did not take effect.");
                expected[prefix + name] = true;
            }
            if (values.Count != expected.Count || expected.Any(pair => values[pair.Key] != pair.Value))
                throw new InvalidOperationException("Tool selection changed an unrelated private preference.");
            VerifySharedPreferences(snapshot);
            File.WriteAllText(
                Path.Combine(control, "tool-preference-evidence.json"),
                JObject
                    .FromObject(
                        new
                        {
                            discovered,
                            inherited = snapshot.Count,
                            initialized = values.Count,
                            selected,
                        }
                    )
                    .ToString()
            );
            LaneHost.Start();
        }
        catch (Exception error)
        {
            File.WriteAllText(Path.Combine(control, "startup-error.txt"), error.GetBaseException().ToString());
            EditorApplication.Exit(2);
        }
    }

    private static void VerifySharedPreferences(Dictionary<string, bool> snapshot)
    {
        foreach (var saved in snapshot)
            if (!EditorPrefs.HasKey(saved.Key) || EditorPrefs.GetBool(saved.Key) != saved.Value)
                throw new InvalidDataException("The frozen tool preferences do not match this Editor.");
    }
}
