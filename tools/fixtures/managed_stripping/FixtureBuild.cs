using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace ManagedStrippingFixture
{
    public static class FixtureBuild
    {
        [Serializable]
        private sealed class BuildReceipt
        {
            public string nonce;
            public string unityVersion;
            public string activeTarget;
            public string target;
            public string backend;
            public string strippingLevel;
            public bool engineStripping;
            public string scriptingDefines;
            public string scene;
            public string options;
            public string buildGuid;
            public string result;
            public int totalErrors;
            public string[] nativeSteps;
        }

        public static void Run()
        {
            const BuildTarget target = BuildTarget.StandaloneWindows64;
            const string scenePath = "Assets/StrippingProbe.unity";
            string root = ReflectionProbe.Argument("--strip-build-output");
            if (Application.unityVersion != ReflectionProbe.Argument("--strip-version") || EditorUserBuildSettings.activeBuildTarget != target)
                throw new InvalidOperationException("The fixture requires its pinned Windows x64 Unity Editor.");

            ReflectionProbe.Log(ReflectionProbe.Observe());
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            if (!EditorSceneManager.SaveScene(scene, scenePath))
                throw new IOException("The fixture could not save its build scene.");

            var namedTarget = NamedBuildTarget.Standalone;
            PlayerSettings.SetScriptingBackend(namedTarget, ScriptingImplementation.IL2CPP);
            PlayerSettings.SetManagedStrippingLevel(namedTarget, ManagedStrippingLevel.High);
            PlayerSettings.companyName = "MCP Fixture";
            PlayerSettings.productName = "StrippingProbe";
            var receipt = new BuildReceipt
            {
                nonce = ReflectionProbe.Argument("--strip-nonce"),
                unityVersion = Application.unityVersion,
                activeTarget = EditorUserBuildSettings.activeBuildTarget.ToString(),
                backend = PlayerSettings.GetScriptingBackend(namedTarget).ToString(),
                strippingLevel = PlayerSettings.GetManagedStrippingLevel(namedTarget).ToString(),
                engineStripping = PlayerSettings.stripEngineCode,
                scriptingDefines = PlayerSettings.GetScriptingDefineSymbols(namedTarget),
                scene = scenePath,
            };
            var report = BuildPipeline.BuildPlayer(
                new BuildPlayerOptions
                {
                    target = target,
                    subtarget = (int)StandaloneBuildSubtarget.Player,
                    scenes = new[] { scenePath },
                    options = BuildOptions.DetailedBuildReport,
                    locationPathName = Path.Combine(root, "StrippingProbe.exe"),
                }
            );
            receipt.target = report.summary.platform.ToString();
            receipt.options = report.summary.options.ToString();
            receipt.buildGuid = report.summary.guid.ToString();
            receipt.result = report.summary.result.ToString();
            receipt.totalErrors = report.summary.totalErrors;
            receipt.nativeSteps = report.steps.Select(step => step.name).ToArray();
            File.WriteAllText(ReflectionProbe.Argument("--strip-build-report"), JsonUtility.ToJson(receipt, prettyPrint: true));
            if (report.summary.result != BuildResult.Succeeded)
                throw new InvalidOperationException("The native IL2CPP build failed. See its report and Editor log.");
        }
    }
}
