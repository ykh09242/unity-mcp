using System;
using System.IO;
using UnityEditor;
using UnityEditor.AddressableAssets;
using UnityEditor.AddressableAssets.Build;
using UnityEditor.AddressableAssets.Settings;
using UnityEditor.AddressableAssets.Settings.GroupSchemas;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class CatalogMarkerBuild
{
    public static void Build()
    {
        string project = Path.GetDirectoryName(Application.dataPath);
        string session = Environment.GetEnvironmentVariable("MCP_CATALOG_SESSION");
        if (!Application.isBatchMode || !Guid.TryParseExact(session, "N", out _) || File.ReadAllText(Path.Combine(project, ".catalog-owner")) != session)
            throw new InvalidOperationException("Use the owned disposable catalog fixture project.");
        string origin = Environment.GetEnvironmentVariable("MCP_CATALOG_ORIGIN");
        var uri = new Uri(origin);
        if (uri.Scheme != "http" || uri.Host != "127.0.0.1" || uri.AbsoluteUri != origin + "/")
            throw new InvalidOperationException("Expected the loopback fixture origin.");
        string output = Path.Combine(project, "CatalogFixture");
        if (Directory.Exists(output))
            throw new IOException("Fixture output already exists.");
        Directory.CreateDirectory(output);
        PlayerSettings.companyName = "MCPForUnityFixtures";
        PlayerSettings.productName = "Catalog-" + session;
        PlayerSettings.insecureHttpOption = InsecureHttpOption.DevelopmentOnly;
        var settings = AddressableAssetSettingsDefaultObject.GetSettings(true);
        settings.BuildRemoteCatalog = true;
        settings.DisableCatalogUpdateOnStartup = true;
        settings.BuildAddressablesWithPlayerBuild = AddressableAssetSettings.PlayerBuildOption.DoNotBuildWithPlayer;
        settings.OverridePlayerVersion = session;
        settings.UniqueBundleIds = true;
        settings.profileSettings.SetValue(settings.activeProfileId, AddressableAssetSettings.kRemoteBuildPath, "CatalogFixture/remote");
        settings.profileSettings.SetValue(settings.activeProfileId, AddressableAssetSettings.kRemoteLoadPath, origin);
        var group = settings.CreateGroup("Marker-" + session, false, false, false, null, typeof(BundledAssetGroupSchema), typeof(ContentUpdateGroupSchema));
        var schema = group.GetSchema<BundledAssetGroupSchema>();
        schema.BuildPath.SetVariableByName(settings, AddressableAssetSettings.kRemoteBuildPath);
        schema.LoadPath.SetVariableByName(settings, AddressableAssetSettings.kRemoteLoadPath);
        schema.UseAssetBundleCache = true;
        schema.BundleMode = BundledAssetGroupSchema.BundlePackingMode.PackSeparately;
        group.GetSchema<ContentUpdateGroupSchema>().StaticContent = false;
        const string markerPath = "Assets/CatalogMarker.txt";
        File.WriteAllText(markerPath, "old-" + session);
        AssetDatabase.ImportAsset(markerPath, ImportAssetOptions.ForceSynchronousImport);
        settings.AddLabel("catalog-marker");
        var marker = settings.CreateOrMoveEntry(AssetDatabase.AssetPathToGUID(markerPath), group);
        marker.SetAddress("marker-text", false);
        marker.SetLabel("catalog-marker", true);
        const string extraPath = "Assets/CatalogUnrelated.asset";
        AssetDatabase.CreateAsset(new Texture2D(2, 2) { name = "Unrelated-" + session }, extraPath);
        var extra = settings.CreateOrMoveEntry(AssetDatabase.AssetPathToGUID(extraPath), group);
        extra.SetAddress("marker-unrelated", false);
        extra.SetLabel("catalog-marker", true);
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene);
        new GameObject("Catalog marker probe").AddComponent<CatalogMarkerPlayer>();
        const string scenePath = "Assets/CatalogProbe.unity";
        if (!EditorSceneManager.SaveScene(scene, scenePath))
            throw new IOException("Cannot save fixture scene.");
        // The full project and all its settings were created solely for this invocation.
        AssetDatabase.SaveAssets();
        AddressableAssetSettings.BuildPlayerContent(out AddressablesPlayerBuildResult original);
        RequireBuild(original);
        string state = Path.Combine(output, "original-state.bin");
        File.Copy(ContentUpdateScript.GetContentStateDataPath(false), state);
        Snapshot(output, "old");
        var player = BuildPipeline.BuildPlayer(
            new BuildPlayerOptions
            {
                target = BuildTarget.StandaloneWindows64,
                scenes = new[] { scenePath },
                locationPathName = Path.Combine(output, "Player", "Catalog.exe"),
                options = BuildOptions.Development,
            }
        );
        if (player.summary.result != BuildResult.Succeeded)
            throw new InvalidOperationException("Original Player build failed.");
        File.WriteAllText(markerPath, "new-" + session);
        AssetDatabase.ImportAsset(markerPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
        RequireBuild(ContentUpdateScript.BuildContentUpdate(settings, state));
        Snapshot(output, "new");
    }

    private static void RequireBuild(AddressablesPlayerBuildResult result)
    {
        if (result == null || !string.IsNullOrEmpty(result.Error))
            throw new InvalidOperationException("Native Addressables content build failed: " + result?.Error);
    }

    private static void Snapshot(string output, string name)
    {
        string destination = Directory.CreateDirectory(Path.Combine(output, name)).FullName;
        foreach (string path in Directory.GetFiles(Path.Combine(output, "remote")))
            if (Path.GetExtension(path) == ".bundle" || Path.GetExtension(path) == ".json" || Path.GetExtension(path) == ".hash")
                File.Copy(path, Path.Combine(destination, Path.GetFileName(path)));
    }
}
