using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public static class ConfigurationControlBuild
    {
        private const string Folder = "Assets/ConfigurationControl";
        private const string TexturePath = Folder + "/Sample.png";
        private const string MaterialPath = Folder + "/Surface.mat";
        private const string ScenePath = Folder + "/Sample.unity";

        private static void CreateScene()
        {
            if (File.Exists(TexturePath) || File.Exists(MaterialPath) || File.Exists(ScenePath))
                throw new IOException("Configuration control assets already exist");
            PlayerSettings.colorSpace = ColorSpace.Linear;
            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false, true);
            try
            {
                tex.SetPixels32(Enumerable.Repeat(new Color32(128, 128, 128, 255), 4).ToArray());
                tex.Apply();
                File.WriteAllBytes(TexturePath, tex.EncodeToPNG());
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(tex);
            }
            AssetDatabase.ImportAsset(TexturePath, ImportAssetOptions.ForceSynchronousImport);
            var importer = (TextureImporter)AssetImporter.GetAtPath(TexturePath);
            importer.textureCompression = TextureImporterCompression.Uncompressed;
            importer.sRGBTexture = true;
            importer.SaveAndReimport();
            var material = new Material(Shader.Find("Unlit/Texture")) { mainTexture = AssetDatabase.LoadAssetAtPath<Texture2D>(TexturePath) };
            AssetDatabase.CreateAsset(material, MaterialPath);
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var probe = new GameObject("Material sample").AddComponent<ConfigurationPixelProbe>();
            probe.Surface = material;
            probe.MaterialPath = MaterialPath;
            if (!EditorSceneManager.SaveScene(scene, ScenePath))
                throw new IOException("Could not save the configuration control scene");
            AssetDatabase.SaveAssets();
        }

        public static void Run()
        {
            string[] args = Environment.GetCommandLineArgs();
            string Arg(string key) => args[Array.IndexOf(args, key) + 1];
            if (
                !Application.isBatchMode
                || !Guid.TryParseExact(Arg("--config-owner"), "N", out _)
                || File.ReadAllText(".config-control-owner") != Arg("--config-owner")
            )
                throw new InvalidOperationException("An owned disposable configuration control project is required");
            string phase = Arg("--config-phase");
            string output = Arg("--config-output");
            if (phase == "baseline")
                CreateScene();
            else if (phase == "importer")
            {
                var importer = (TextureImporter)AssetImporter.GetAtPath(TexturePath);
                importer.sRGBTexture = false;
                importer.SaveAndReimport();
            }
            var assembly = VerificationConfiguration.Observe(
                new JObject
                {
                    ["action"] = "observe_assembly",
                    ["path"] = Folder + "/Optional.asmdef",
                    ["source"] = Folder + "/Optional.cs",
                }
            );
            var texture = VerificationConfiguration.Observe(
                new JObject
                {
                    ["action"] = "observe_texture",
                    ["path"] = TexturePath + ".meta",
                    ["search_folder"] = Folder,
                    ["platform"] = "Standalone",
                }
            );
            void Write(string file, JObject contents)
            {
                using var stream = new FileStream(Path.Combine(output, file), FileMode.CreateNew, FileAccess.Write);
                using var writer = new StreamWriter(stream);
                writer.WriteLine(contents.ToString());
            }
            Write("assembly.json", assembly);
            Write("texture.json", texture);
            if (phase != "baseline" && phase != "importer")
                return;
            var definition = PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = "material-sampling",
                    ["steps"] = new JArray(
                        new JObject
                        {
                            ["name"] = "load sample",
                            ["action"] = "load_scene",
                            ["scene"] = ScenePath,
                        },
                        new JObject
                        {
                            ["name"] = "wait for pixel",
                            ["action"] = "wait_state",
                            ["state_id"] = "render.sampled",
                            ["state_equals"] = true,
                            ["timeout_seconds"] = 8,
                        }
                    ),
                }
            );
            new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath)).Save(definition);
            PlayScenarioPlayerBuild.Build(definition.Name, Path.Combine(output, "build"), (string)texture["observation_revision"]);
        }
    }
}
