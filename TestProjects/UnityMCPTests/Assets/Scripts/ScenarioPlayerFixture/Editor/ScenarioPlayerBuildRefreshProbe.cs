using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    // Explicit command-line evidence only; ordinary fixture builds do not activate these callbacks.
    public sealed class ScenarioPlayerBuildRefreshProbe : AssetPostprocessor, IPreprocessBuildWithReport
    {
        private static bool active,
            failBuild;
        private static int imports,
            temporaryImports,
            preflightChecks;
        private static HashSet<string> before;
        public int callbackOrder => -10000;

        public static PlayScenarioPlayerBuildResult Build(string name, string output, string revision, string backend, bool disableAutoRefresh)
        {
            if (!Application.isBatchMode || active)
                throw new InvalidOperationException("An isolated batch process is required.");
            before = new HashSet<string>(TemporaryFolders());
            imports = temporaryImports = preflightChecks = 0;
            active = true;
            var timer = Stopwatch.StartNew();
            if (disableAutoRefresh)
                AssetDatabase.DisallowAutoRefresh();
            try
            {
                failBuild = true;
                bool expectedFailure = false;
                try
                {
                    PlayScenarioPlayerBuild.Build(name, output + "-expected-failure", revision, backend: backend);
                }
                catch (Exception error)
                    when (preflightChecks == 1
                        && (error.Message.Contains("Player scenario build failed") || error.Message.Contains("OWNED_REFRESH_PROBE_FAILURE"))
                    )
                {
                    expectedFailure = true;
                }
                if (!expectedFailure)
                    throw new InvalidOperationException("The injected native build failure was not observed.");
                VerifyCleanup();
                double failureSeconds = timer.Elapsed.TotalSeconds;
                int failureImports = imports;
                failBuild = false;
                timer.Restart();
                PlayScenarioPlayerBuildResult result = PlayScenarioPlayerBuild.Build(name, output, revision, backend: backend);
                VerifyCleanup();
                if (preflightChecks != 2)
                    throw new InvalidOperationException("Both builds must see imported bootstrap, linker and resource assets.");
                UnityEngine.Debug.Log(
                    "PLAYER_REFRESH_PROBE:"
                        + new JObject
                        {
                            ["autoRefreshDisabled"] = disableAutoRefresh,
                            ["failureSeconds"] = failureSeconds,
                            ["successSeconds"] = timer.Elapsed.TotalSeconds,
                            ["failureImports"] = failureImports,
                            ["successImports"] = imports - failureImports,
                            ["temporaryImports"] = temporaryImports,
                            ["preflightChecks"] = preflightChecks,
                            ["cleanupVerified"] = true,
                        }.ToString(Newtonsoft.Json.Formatting.None)
                );
                return result;
            }
            finally
            {
                active = failBuild = false;
                if (disableAutoRefresh)
                    AssetDatabase.AllowAutoRefresh();
            }
        }

        public void OnPreprocessBuild(BuildReport report)
        {
            if (!active)
                return;
            string[] added = TemporaryFolders().Where(path => !before.Contains(path)).ToArray();
            if (added.Length != 1)
                throw new InvalidOperationException("Expected exactly one owned temporary build folder.");
            string folder = "Assets/" + Path.GetFileName(added[0]);
            foreach (string relative in new[] { "/Bootstrap.unity", "/link.xml", "/Resources/MCPPlayScenarioBundle.json" })
                if (AssetDatabase.LoadMainAssetAtPath(folder + relative) == null)
                    throw new InvalidOperationException("Scoped import did not expose " + relative);
            preflightChecks++;
            if (failBuild)
                throw new BuildFailedException("OWNED_REFRESH_PROBE_FAILURE");
        }

        private static string[] TemporaryFolders() => Directory.GetDirectories(Application.dataPath, "__MCPScenarioBuild_*");

        private static void VerifyCleanup()
        {
            if (!before.SetEquals(TemporaryFolders()))
                throw new InvalidOperationException("A temporary build folder survived cleanup.");
            foreach (string asset in AssetDatabase.GetAllAssetPaths())
                if (
                    asset.StartsWith("Assets/__MCPScenarioBuild_", StringComparison.Ordinal)
                    && !before.Any(path => asset.StartsWith("Assets/" + Path.GetFileName(path) + "/", StringComparison.Ordinal))
                )
                    throw new InvalidOperationException("A deleted temporary asset remains indexed: " + asset);
        }

        private static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            if (!active)
                return;
            imports += imported.Length;
            temporaryImports += imported.Count(path => path.StartsWith("Assets/__MCPScenarioBuild_", StringComparison.Ordinal));
        }
    }
}
