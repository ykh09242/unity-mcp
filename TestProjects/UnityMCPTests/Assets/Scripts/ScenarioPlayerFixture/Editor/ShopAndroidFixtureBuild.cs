using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public static class ShopAndroidFixtureBuild
    {
        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string project = Path.GetDirectoryName(Application.dataPath);
            string buildId = Argument(args, "--android-fixture-build-id");
            if (
                !Application.isBatchMode
                || !Guid.TryParseExact(buildId, "N", out _)
                || File.ReadAllText(Path.Combine(project, ".android-fixture-owner")) != buildId
                || EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android
            )
                throw new InvalidOperationException("Use the prepared disposable Android fixture project with -buildTarget Android.");
            string sourceRevision = Argument(args, "--android-fixture-source");
            if (!Regex.IsMatch(sourceRevision, "^[0-9a-f]{64}$"))
                throw new ArgumentException("Expected the staged source SHA-256.");
            string output = Path.GetFullPath(Argument(args, "--android-fixture-output"));
            if (!Directory.Exists(output) || Directory.EnumerateFileSystemEntries(output).Any())
                throw new IOException("Android fixture output must exist and be empty.");

            ShopWorkflowFixtureBuild.CreateOriginal(project);
            string path = ShopWorkflowFixtureBuild.ScenePath;
            Scene scene = EditorSceneManager.OpenScene(path, OpenSceneMode.Single);
            var probe = scene.GetRootGameObjects().Single().AddComponent<ShopAndroidProbe>();
            probe.BuildId = buildId;
            probe.SourceRevision = sourceRevision;
            if (!EditorSceneManager.SaveScene(scene))
                throw new IOException("Could not save the Android shop fixture scene.");

            PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android, ShopAndroidProbe.ApplicationId);
            PlayerSettings.SetScriptingBackend(NamedBuildTarget.Android, ScriptingImplementation.IL2CPP);
            PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
            PlayerSettings.Android.buildApkPerCpuArchitecture = false;
            PlayerSettings.Android.useCustomKeystore = false;
            EditorUserBuildSettings.buildAppBundle = false;
            EditorUserBuildSettings.exportAsGoogleAndroidProject = false;
            string apk = Path.Combine(output, "fixture.apk");
            BuildReport report = BuildPipeline.BuildPlayer(
                new BuildPlayerOptions
                {
                    scenes = new[] { path },
                    locationPathName = apk,
                    target = BuildTarget.Android,
                    options = BuildOptions.Development | BuildOptions.StrictMode,
                }
            );
            if (report.summary.result != BuildResult.Succeeded || !File.Exists(apk))
                throw new InvalidOperationException("Android fixture build failed: " + report.summary.result);

            using var sha = SHA256.Create();
            using var package = File.OpenRead(apk);
            string digest = BitConverter.ToString(sha.ComputeHash(package)).Replace("-", "").ToLowerInvariant();
            var manifest = new JObject
            {
                ["schema_version"] = 1,
                ["app_id"] = ShopAndroidProbe.ApplicationId,
                ["scenario"] = ShopAndroidProbe.Scenario,
                ["source_revision"] = sourceRevision,
                ["build_id"] = buildId,
                ["unity_version"] = Application.unityVersion,
                ["backend"] = PlayerSettings.GetScriptingBackend(NamedBuildTarget.Android).ToString(),
                ["abi"] = "arm64-v8a",
                ["apk_sha256"] = digest,
                ["apk_file"] = Path.GetFileName(apk),
            };
            using var stream = new FileStream(Path.Combine(output, "build.json"), FileMode.CreateNew, FileAccess.Write);
            using var writer = new StreamWriter(stream);
            writer.WriteLine(manifest.ToString(Formatting.Indented));
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
