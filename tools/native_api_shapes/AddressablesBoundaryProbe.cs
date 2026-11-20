using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools.Addressables;
using Newtonsoft.Json.Linq;
using UnityEditor.PackageManager;
using UnityEngine;

public static class AddressablesBoundaryProbe
{
    public static void Observe()
    {
        string project = Path.GetDirectoryName(Application.dataPath);
        string[] args = Environment.GetCommandLineArgs();
        string session = Argument(args, "--boundary-session");
        if (!Application.isBatchMode || !Guid.TryParseExact(session, "N", out _) || File.ReadAllText(Path.Combine(project, ".boundary-owner")) != session)
            throw new InvalidOperationException("Use an owned disposable Addressables boundary project.");
        Type settings = Type.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings, Unity.Addressables.Editor");
        var assembly = typeof(ManageAddressables).Assembly;
        JObject read = JObject.FromObject(
            ManageAddressables.HandleCommand(new JObject { ["action"] = "inspect", ["settings_path"] = "Assets/MissingAddressableSettings.asset" })
        );
        var result = new JObject
        {
            ["session"] = session,
            ["unity_version"] = Application.unityVersion,
            ["adapter_assembly"] = assembly.GetName().Name,
            ["adapter_references"] = new JArray(assembly.GetReferencedAssemblies().Select(value => value.Name).OrderBy(value => value, StringComparer.Ordinal)),
            ["provider_assembly"] = settings?.Assembly.GetName().Name,
            ["provider_version"] = settings == null ? null : PackageInfo.FindForAssembly(settings.Assembly)?.version,
            ["inspection_result"] = read["code"],
        };
        using var stream = new FileStream(Argument(args, "--boundary-output"), FileMode.CreateNew, FileAccess.Write);
        using var writer = new StreamWriter(stream);
        writer.WriteLine(result.ToString());
    }

    private static string Argument(string[] args, string name)
    {
        int index = Array.IndexOf(args, name);
        if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, name) != index)
            throw new ArgumentException("Expected one value for " + name);
        return args[index + 1];
    }
}
