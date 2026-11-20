using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.Rendering;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ShaderVariantPairBuild : IPreprocessShaders
    {
        private const string Folder = "Assets/ShaderVariantPair";
        private const string Scene = Folder + "/Tint.unity";
        private const string MaterialPath = Folder + "/Surface.mat";
        private const string ShaderPath = "Assets/Scripts/ScenarioPlayerFixture/RequiredTint.shader";
        private static string phase;
        private static int offered,
            retained;
        public int callbackOrder => int.MaxValue;

        public void OnProcessShader(Shader shader, ShaderSnippetData snippet, IList<ShaderCompilerData> variants)
        {
            if (phase == null || AssetDatabase.GetAssetPath(shader) != ShaderPath || snippet.passName != "TINT" || snippet.shaderType != ShaderType.Fragment)
                return;
            var keyword = new ShaderKeyword(shader, ShaderVariantPixelProbe.Keyword);
            for (int i = variants.Count - 1; i >= 0; i--)
            {
                if (!variants[i].shaderKeywordSet.IsEnabled(keyword))
                    continue;
                offered++;
                if (phase == "stripped")
                    variants.RemoveAt(i);
            }
            retained += variants.Count(variant => variant.shaderKeywordSet.IsEnabled(keyword));
        }

        private static ShaderVariantPixelProbe CreateFixture()
        {
            if (AssetDatabase.IsValidFolder(Folder) || Directory.Exists(Folder))
                throw new IOException("Shader variant fixture already exists.");
            AssetDatabase.CreateFolder("Assets", "ShaderVariantPair");
            var material = new Material(AssetDatabase.LoadAssetAtPath<Shader>(ShaderPath)) { name = "Surface" };
            material.EnableKeyword(ShaderVariantPixelProbe.Keyword);
            AssetDatabase.CreateAsset(material, MaterialPath);
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var probe = new GameObject("Variant observation").AddComponent<ShaderVariantPixelProbe>();
            var camera = new GameObject("Tint camera").AddComponent<Camera>();
            camera.transform.position = new Vector3(0, 0, -2);
            camera.orthographic = true;
            camera.orthographicSize = 1;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.blue;
            camera.allowHDR = false;
            camera.allowMSAA = false;
            camera.enabled = false;
            var quad = GameObject.CreatePrimitive(PrimitiveType.Quad).GetComponent<Renderer>();
            quad.sharedMaterial = material;
            probe.View = camera;
            probe.Surface = quad;
            probe.MaterialGuid = AssetDatabase.AssetPathToGUID(MaterialPath);
            if (!EditorSceneManager.SaveScene(scene, Scene))
                throw new IOException("Could not save the shader control scene.");
            AssetDatabase.SaveAssets();
            return probe;
        }

        public static void Run()
        {
            string[] args = Environment.GetCommandLineArgs();
            string Arg(string name)
            {
                int index = Array.IndexOf(args, name);
                if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, name) != index)
                    throw new ArgumentException(name);
                return args[index + 1];
            }
            string owner = Arg("--variant-owner");
            if (!Application.isBatchMode || !Guid.TryParseExact(owner, "N", out _) || File.ReadAllText(".shader-variant-owner") != owner)
                throw new InvalidOperationException("Select a prepared disposable shader project.");
            string mode = Arg("--variant-phase");
            string output = Arg("--variant-output");
            if (mode != "stripped" && mode != "retained")
                throw new ArgumentException("Unknown stripping policy.");
            if (mode == "stripped")
            {
                var probe = CreateFixture();
                string reference = Path.Combine(output, "editor-reference");
                Directory.CreateDirectory(reference);
                ShaderUtil.CompilePass(probe.Surface.sharedMaterial, 0, true);
                probe.Capture(reference, "observation.json");
            }
            string build = Path.Combine(output, "build");
            if (Directory.Exists(build))
                throw new IOException("Build directory must be fresh.");
            Directory.CreateDirectory(build);
            var target = BuildTarget.StandaloneWindows64;
            PlayerSettings.SetUseDefaultGraphicsAPIs(target, false);
            PlayerSettings.SetGraphicsAPIs(target, new[] { GraphicsDeviceType.Direct3D11 });
#if UNITY_2022_2_OR_NEWER
            PlayerSettings.strictShaderVariantMatching = false;
#endif
            phase = mode;
            offered = retained = 0;
            try
            {
                var report = BuildPipeline.BuildPlayer(
                    new BuildPlayerOptions
                    {
                        scenes = new[] { Scene },
                        locationPathName = Path.Combine(build, "Tint.exe"),
                        target = target,
                        options = BuildOptions.CleanBuildCache,
                    }
                );
                if (report.summary.result != BuildResult.Succeeded || offered == 0 || (mode == "retained" && retained != offered))
                    throw new InvalidOperationException("Build or shader variant callback did not complete.");
                var facts = new
                {
                    owner,
                    phase,
                    offered,
                    retained,
                    unityVersion = Application.unityVersion,
                    buildGuid = report.summary.guid.ToString(),
                    target = report.summary.platform.ToString(),
                    apis = PlayerSettings.GetGraphicsAPIs(target).Select(api => api.ToString()),
                    scene = Scene,
                    materialGuid = AssetDatabase.AssetPathToGUID(MaterialPath),
                    shaderPath = ShaderPath,
                    keyword = ShaderVariantPixelProbe.Keyword,
                };
                using var stream = new FileStream(Path.Combine(output, "build.json"), FileMode.CreateNew);
                using var writer = new StreamWriter(stream);
                writer.Write(JsonConvert.SerializeObject(facts, Formatting.Indented));
            }
            finally
            {
                phase = null;
            }
        }
    }
}
