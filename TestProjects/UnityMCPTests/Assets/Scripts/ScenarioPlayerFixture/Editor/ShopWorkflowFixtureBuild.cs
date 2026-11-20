using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public static class ShopWorkflowFixtureBuild
    {
        private const string Folder = "Assets/VerifiedWorkflowShop";
        private const string ResourceRoot = Folder + "/Resources/VerifiedShop";
        private const string Original = ResourceRoot + "/UI/ShopBanner.prefab";
        private const string Moved = ResourceRoot + "/Store/ShopBanner.prefab";
        internal const string ScenePath = Folder + "/Shop.unity";
        private const string LoaderPath = "Assets/Scripts/ScenarioPlayerFixture/ShopWorkflowProbe.cs";

        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string project = Path.GetDirectoryName(Application.dataPath);
            string session = Argument(args, "--shop-session");
            if (
                !Application.isBatchMode
                || !Guid.TryParseExact(session, "N", out _)
                || File.ReadAllText(Path.Combine(project, ".shop-workflow-owner")) != session
            )
                throw new InvalidOperationException("Use the owned disposable shop workflow project.");
            string phase = Argument(args, "--shop-phase");
            string output = Path.GetFullPath(Argument(args, "--mcp-scenario-output"));
            string assetPath;
            switch (phase)
            {
                case "original":
                    CreateOriginal(project);
                    assetPath = Original;
                    break;
                case "moved":
                    string error = AssetDatabase.MoveAsset(Original, Moved);
                    if (!string.IsNullOrEmpty(error))
                        throw new IOException(error);
                    assetPath = Moved;
                    break;
                case "repaired":
                    assetPath = Moved;
                    break;
                default:
                    throw new ArgumentException("Unknown shop phase: " + phase);
            }

            Scene scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            var shop = scene.GetRootGameObjects().Single().GetComponent<ShopWorkflowProbe>();
            GameObject loaded = ShopWorkflowProbe.LoadBanner();
            var inputs = new JObject();
            var manifest = new StringBuilder();
            using (SHA256 sha = SHA256.Create())
            {
                foreach (
                    string path in new[] { ScenePath, ScenePath + ".meta", assetPath, assetPath + ".meta", LoaderPath, LoaderPath + ".meta" }.OrderBy(
                        p => p,
                        StringComparer.Ordinal
                    )
                )
                {
                    using (FileStream file = File.OpenRead(Path.Combine(project, path)))
                    {
                        string digest = Hex(sha.ComputeHash(file));
                        inputs[path] = digest;
                        manifest.Append(path).Append('\n').Append(digest).Append('\n');
                    }
                }
            }
            string revision;
            using (SHA256 sha = SHA256.Create())
                revision = Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(manifest.ToString())));
            var observed = new JObject
            {
                ["session"] = session,
                ["phase"] = phase,
                ["project"] = project,
                ["unity_version"] = Application.unityVersion,
                ["package_version"] = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(PlayScenarioPlayerBuild).Assembly)?.version,
                ["source_revision"] = revision,
                ["inputs"] = inputs,
                ["scene"] = ScenePath,
                ["asset_path"] = assetPath,
                ["asset_guid"] = AssetDatabase.AssetPathToGUID(assetPath),
                ["serialized_guid"] = shop.Banner == null ? "" : AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(shop.Banner)),
                ["dependency_retained"] = AssetDatabase.GetDependencies(ScenePath, false).Contains(assetPath),
                ["key"] = ShopWorkflowProbe.BannerKey,
                ["loader_guid"] = loaded == null ? "" : AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(loaded)),
            };
            string evidencePath = Path.Combine(Path.GetDirectoryName(output), "editor.json");
            using (var stream = new FileStream(evidencePath, FileMode.CreateNew, FileAccess.Write))
            using (var writer = new StreamWriter(stream))
                writer.WriteLine(observed.ToString());

            var definition = new JObject
            {
                ["name"] = "verified-shop",
                ["steps"] = new JArray(
                    new JObject
                    {
                        ["name"] = "open shop",
                        ["action"] = "load_scene",
                        ["scene"] = ScenePath,
                    },
                    new JObject
                    {
                        ["name"] = "confirm shop scene",
                        ["action"] = "wait_scene",
                        ["scene"] = ScenePath,
                    },
                    new JObject
                    {
                        ["name"] = "show shop banner",
                        ["action"] = "wait_state",
                        ["state_id"] = "shop.ready",
                        ["state_equals"] = true,
                        ["timeout_seconds"] = 3,
                    }
                ),
            };
            var scenario = PlayScenarioDefinition.Parse(definition);
            var store = new PlayScenarioStore(project);
            store.Save(scenario);
            PlayScenarioPlayerBuild.Build(scenario.Name, output, revision);
            if (Array.IndexOf(args, "--shop-reduced-output") >= 0)
            {
                ((JArray)definition["steps"]).RemoveAt(1);
                var reduced = PlayScenarioDefinition.Parse(definition);
                try
                {
                    store.Save(reduced);
                    PlayScenarioPlayerBuild.Build(reduced.Name, Argument(args, "--shop-reduced-output"), revision);
                }
                finally
                {
                    store.Save(scenario);
                }
            }
        }

        private static string Hex(byte[] bytes) => BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();

        internal static void CreateOriginal(string project)
        {
            if (Directory.Exists(Path.Combine(project, Folder)))
                throw new IOException("The shop fixture already exists.");
            Directory.CreateDirectory(Path.Combine(project, ResourceRoot, "UI"));
            Directory.CreateDirectory(Path.Combine(project, ResourceRoot, "Store"));
            AssetDatabase.Refresh();
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var template = new GameObject("ShopBanner", typeof(RectTransform), typeof(Canvas));
            try
            {
                template.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
                var panel = new GameObject("Banner surface", typeof(RectTransform), typeof(Image));
                panel.transform.SetParent(template.transform, false);
                var surface = panel.GetComponent<Image>();
                surface.rectTransform.sizeDelta = new Vector2(320, 100);
                surface.color = new Color(0.12f, 0.52f, 0.32f);
                GameObject prefab = PrefabUtility.SaveAsPrefabAsset(template, Original);
                if (prefab == null)
                    throw new IOException("Could not create the shop banner prefab.");
                new GameObject("Shop").AddComponent<ShopWorkflowProbe>().Banner = prefab;
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(template);
            }
            if (!EditorSceneManager.SaveScene(scene, ScenePath))
                throw new IOException("Could not save the shop scene.");
        }

        private static string Argument(string[] args, string name)
        {
            int index = Array.IndexOf(args, name);
            if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, name) != index)
                throw new ArgumentException("Expected one value for " + name);
            return args[index + 1];
        }
    }
}
