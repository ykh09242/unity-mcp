using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools.Build;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioPlayerBuildResult
    {
        public string BundlePath;
        public string ExecutablePath;
        public string DefinitionHash;
    }

    /// <summary>Explicit standalone scenario builds; restores the selected scripting backend after building.</summary>
    public static class PlayScenarioPlayerBuild
    {
        internal static void ValidateNativeAdmission(string owner = null)
        {
            if (NativeOperationBarriers.TryGetBuildContinuationBlocker(owner, null, out NativeAdmissionBlocker blocker))
                throw new InvalidOperationException("Scenario Player build blocked: " + blocker.Reason + ". " + blocker.Recovery?.Message);
        }

        private sealed class Reservation : IDisposable
        {
            private readonly string id = "scenario-player-" + Guid.NewGuid().ToString("N");

            internal Reservation()
            {
                ValidateNativeAdmission();
                BuildRunner.ReserveNative(id);
            }

            internal BuildReport Invoke(BuildPlayerOptions options)
            {
                Validate();
                BuildRunner.MarkNativeInvocation(id);
                return BuildPipeline.BuildPlayer(options);
            }

            internal void Validate() => ValidateNativeAdmission(id);

            public void Dispose() => BuildRunner.ReleaseNative(id);
        }

        private static PlayScenarioBuildJournal OpenJournal(string project) =>
#pragma warning disable CS0618
            new(
                project,
                () => PlayerSettings.GetScriptingBackend(BuildTargetGroup.Standalone),
                value => PlayerSettings.SetScriptingBackend(BuildTargetGroup.Standalone, value)
            );
#pragma warning restore CS0618

        [InitializeOnLoadMethod]
        private static void ScheduleRecovery()
        {
            double nextCheck = 0;
            EditorApplication.update += Check;
            void Check()
            {
                if (EditorApplication.timeSinceStartup < nextCheck)
                    return;
                nextCheck = EditorApplication.timeSinceStartup + 1;
                string project = Path.GetDirectoryName(Application.dataPath);
                try
                {
                    // A domain reload retains the old session marker. A project reopen does
                    // not. Wait for startup imports and other owned jobs before changing assets.
                    if (PlayScenarioBuildJournal.Pending(project) && string.IsNullOrEmpty(SessionState.GetString(PlayScenarioBuildJournal.ActiveKey, "")))
                    {
                        if (EditorApplication.isPlayingOrWillChangePlaymode || NativeOperationBarriers.TryGetBuildContinuationBlocker(null, null, out _))
                            return;
                        using var reservation = new Reservation();
                        using var journal = OpenJournal(project);
                        journal.Recover();
                    }
                }
                catch (Exception error)
                {
                    Debug.LogWarning("Scenario Player build recovery requires inspection: " + error.Message);
                }
                EditorApplication.update -= Check;
            }
        }

        public static PlayScenarioPlayerBuildResult Build(
            string savedName,
            string outputDirectory,
            string buildSourceRevision = null,
            string target = "windows64",
            string backend = "mono"
        )
        {
            PlayScenarioReproduction.ValidateSourceRevision(buildSourceRevision);
            EnsureEditorReady();
            string project = Path.GetDirectoryName(Application.dataPath);
            PlayScenarioDefinition definition = new PlayScenarioStore(project).Get(savedName);
            definition = PlayScenarioParameters.Materialize(definition);
            PlayScenarioPlayerCapabilities.Validate(definition, MCPForUnity.Editor.Tools.Input.ManageInput.UguiBackend != null);
            string package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(PlayScenarioPlayerBuild).Assembly)?.version ?? "unknown";
            JObject manifest = PlayScenarioPlayerBundle.Create(definition, Application.unityVersion, package, buildSourceRevision: buildSourceRevision);
            return BuildFrozen(manifest, PlayScenarioPlayerBundle.Scenes(definition), outputDirectory, target, backend);
        }

        public static PlayScenarioPlayerBuildResult BuildFrozen(
            JObject manifest,
            string[] scenes,
            string outputDirectory,
            string target = "windows64",
            string backend = "mono"
        )
        {
            PlayScenarioPlayerPlatform.Validate(target, backend);
            string project = PlayScenarioPlayerFiles.CheckedAbsolute(Path.GetDirectoryName(Application.dataPath));
            string output = PlayScenarioPlayerFiles.CheckedAbsolute(outputDirectory);
            if (
                (PlayScenarioPlayerFiles.IsWithin(project, output) && PlayScenarioPlayerFiles.IsWithin(output, project))
                || PlayScenarioPlayerFiles.IsWithin(Application.dataPath, output)
            )
                throw new ArgumentException("Player output must be an explicit empty directory outside Assets and the project root.");
            string journalDirectory = Path.Combine(project, PlayScenarioBuildJournal.DirectoryName);
            if (PlayScenarioPlayerFiles.IsWithin(output, journalDirectory) || PlayScenarioPlayerFiles.IsWithin(journalDirectory, output))
                throw new ArgumentException("Player output cannot overlap the scenario build recovery journal.");
            if (Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any())
                throw new IOException("Player output directory must be empty.");
            BuildTarget buildTarget = Target(target);
            bool hasUntitledScene = EnsureEditorReady();
            PreflightBackend(target, backend, buildTarget);
            foreach (string path in scenes)
                if (AssetDatabase.LoadAssetAtPath<SceneAsset>(path) == null)
                    throw new ArgumentException("Scenario scene asset is unavailable: " + path);
            manifest = (JObject)manifest.DeepClone();
            manifest["build_target"] = target;
            manifest["scripting_backend"] = backend;
            manifest["executable"] = PlayScenarioPlayerPlatform.Executable(target);
            string manifestJson = manifest.ToString(Formatting.None);
            PlayScenarioPlayerBundle parsed = PlayScenarioPlayerBundle.Parse(manifestJson);
            if (!scenes.SequenceEqual(parsed.ScenePaths, StringComparer.Ordinal))
                throw new ArgumentException("Build scene inventory must match the frozen bundle.");
            Scene bootstrap = default;
            Scene previousActive = SceneManager.GetActiveScene();
            ScriptingImplementation selectedBackend = backend == "il2cpp" ? ScriptingImplementation.IL2CPP : ScriptingImplementation.Mono2x;
            using var reservation = new Reservation();
            using var journal = OpenJournal(project);
            if (PlayScenarioBuildJournal.Pending(project))
            {
                journal.Recover();
                reservation.Validate();
            }
            bool pinSource = Array.IndexOf(Environment.GetCommandLineArgs(), "--mcp-pin-build-source") >= 0;
            JObject source = pinSource ? ReadBuildSource(project, manifest) : null;
            journal.Begin(selectedBackend);
            string temporaryAssets = journal.AssetFolder;
            try
            {
                Directory.CreateDirectory(output);
                journal.ApplyBackend();
                journal.RequireAbsent("");
                string folderGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(temporaryAssets));
                if (string.IsNullOrEmpty(folderGuid) || AssetDatabase.GUIDToAssetPath(folderGuid) != temporaryAssets)
                    throw new IOException("Could not create the temporary Player asset folder.");
                journal.Capture("");
                journal.RequireAbsent("Resources");
                string resourceFolderGuid = AssetDatabase.CreateFolder(temporaryAssets, "Resources");
                if (string.IsNullOrEmpty(resourceFolderGuid) || AssetDatabase.GUIDToAssetPath(resourceFolderGuid) != temporaryAssets + "/Resources")
                    throw new IOException("Could not create the temporary Player resource folder.");
                journal.Capture("Resources");
                string linker = temporaryAssets + "/link.xml";
                string resource = temporaryAssets + "/" + PlayScenarioBuildJournal.Resource;
                string descriptor = CreateLinkerDescriptor();
                journal.RequireAbsent("link.xml");
                PlayScenarioPlayerFiles.WriteNew(Path.Combine(project, linker), descriptor, 65536);
                AssetDatabase.ImportAsset(linker, ImportAssetOptions.ForceSynchronousImport);
                journal.Capture("link.xml", descriptor);
                journal.RequireAbsent(PlayScenarioBuildJournal.Resource);
                PlayScenarioPlayerFiles.WriteNew(Path.Combine(project, resource), manifestJson, PlayScenarioPlayerBundle.Limit);
                AssetDatabase.ImportAsset(resource, ImportAssetOptions.ForceSynchronousImport);
                journal.Capture(PlayScenarioBuildJournal.Resource, manifestJson);
                bootstrap = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, hasUntitledScene ? NewSceneMode.Single : NewSceneMode.Additive);
                string bootstrapPath = temporaryAssets + "/Bootstrap.unity";
                journal.RequireAbsent("Bootstrap.unity");
                if (!EditorSceneManager.SaveScene(bootstrap, bootstrapPath))
                    throw new IOException("Could not save the dedicated Player bootstrap scene.");
                journal.Capture("Bootstrap.unity");
                EditorSceneManager.CloseScene(bootstrap, true);
                bootstrap = default;
                if (previousActive.IsValid() && previousActive.isLoaded)
                    SceneManager.SetActiveScene(previousActive);
                string executable = Path.Combine(output, PlayScenarioPlayerPlatform.Executable(target));
                BuildReport report = reservation.Invoke(
                    new BuildPlayerOptions
                    {
                        scenes = new[] { bootstrapPath }.Concat(scenes).ToArray(),
                        locationPathName = Path.Combine(output, PlayScenarioPlayerPlatform.BundleLocation(target)),
                        target = buildTarget,
                        options = BuildOptions.None,
                        extraScriptingDefines = new[] { "MCP_FOR_UNITY_PLAY_SCENARIOS" },
                    }
                );
                if (report.summary.result != BuildResult.Succeeded || !File.Exists(executable))
                    throw new InvalidOperationException("Player scenario build failed: " + report.summary.result);
            }
            finally
            {
                try
                {
                    if (bootstrap.IsValid() && bootstrap.isLoaded)
                        EditorSceneManager.CloseScene(bootstrap, true);
                    if (previousActive.IsValid() && previousActive.isLoaded)
                        SceneManager.SetActiveScene(previousActive);
                }
                finally
                {
                    journal.Finish();
                }
            }
            if (pinSource)
            {
                if (!JToken.DeepEquals(source, ReadBuildSource(project, manifest)))
                    throw new InvalidOperationException("The resolved package or project checkout changed during the native build.");
                PlayScenarioPlayerFiles.WriteNew(Path.Combine(output, "build-source.json"), source.ToString(Formatting.None), 4096);
            }
            PlayScenarioPlayerPayload.Attach(manifest, output);
            manifestJson = manifest.ToString(Formatting.None);
            PlayScenarioPlayerBundle.Parse(manifestJson);
            string bundlePath = Path.Combine(output, PlayScenarioPlayerBundle.ManifestName);
            PlayScenarioPlayerFiles.WriteNew(bundlePath, manifestJson, PlayScenarioPlayerBundle.Limit);
            return new PlayScenarioPlayerBuildResult
            {
                BundlePath = bundlePath,
                ExecutablePath = Path.Combine(output, PlayScenarioPlayerPlatform.Executable(target)),
                DefinitionHash = (string)(manifest["definition_hash"] ?? manifest["suite_hash"]),
            };
        }

        private static JObject ReadBuildSource(string project, JObject manifest)
        {
            string package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(PlayScenarioPlayerBuild).Assembly)?.resolvedPath;
            if (string.IsNullOrEmpty(package))
                throw new InvalidOperationException("The Editor could not resolve the installed MCP package for this build.");
            if (Git(package, "rev-parse --show-prefix") != "MCPForUnity/")
                throw new InvalidOperationException("Point the project's package dependency at the tracked MCPForUnity folder of the reviewed Git checkout.");
            if (
                Git(package, "status --porcelain --untracked-files=normal -- .").Length != 0
                || Git(package, "ls-files --others -- .").Length != 0
                || Git(project, "status --porcelain --untracked-files=normal -- Assets Packages ProjectSettings").Length != 0
            )
                throw new InvalidOperationException("Native build provenance requires clean project sources and a clean resolved MCP package.");
            return new JObject
            {
                ["schema_version"] = 1,
                ["kind"] = "verification_player_build_source",
                ["build_id"] = manifest["build_id"],
                ["source_revision"] = manifest["build_source_revision"],
                ["project_commit"] = Git(project, "rev-parse --verify HEAD^{commit}"),
                ["package_commit"] = Git(package, "rev-parse --verify HEAD^{commit}"),
                ["package_tree"] = Git(package, "rev-parse --verify HEAD:MCPForUnity"),
                ["package_checkout_sha256"] = PackageCheckoutHash(package),
            };
        }

        private static string PackageCheckoutHash(string package)
        {
            string[] rows = Git(package, "ls-tree -r --full-tree -z HEAD:MCPForUnity", 4 * 1024 * 1024)
                .Split(new[] { '\0' }, StringSplitOptions.RemoveEmptyEntries);
            if (rows.Length < 1 || rows.Length > 8192)
                throw new InvalidOperationException("Resolved package exceeds its file inventory limit.");
            var arguments = new StringBuilder();
            var blobs = new List<string>(rows.Length);
            var actual = new StringBuilder();
            long total = 0;
            foreach (string row in rows)
            {
                int tab = row.IndexOf('\t');
                if (tab < 0)
                    throw new InvalidDataException("Unexpected package tree record.");
                string[] entry = row.Substring(0, tab).Split(' ');
                string relative = row.Substring(tab + 1);
                PlayScenarioPlayerPayload.ValidatePath(relative);
                if (entry.Length != 3 || (entry[0] != "100644" && entry[0] != "100755") || entry[1] != "blob")
                    throw new InvalidDataException("Expected a regular tracked package file.");
                if (arguments.Length + relative.Length > 8192)
                {
                    CheckPackageBlobs(package, arguments, blobs);
                    arguments.Clear();
                    blobs.Clear();
                }
                arguments.Append(" \"").Append(relative).Append('"');
                blobs.Add(entry[2]);
                string path = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(package, relative));
                using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                if (file.Length > 64 * 1024 * 1024 || (total += file.Length) > 512L * 1024 * 1024)
                    throw new InvalidDataException("Resolved package exceeds the source byte limit.");
                using var hash = SHA256.Create();
                actual.Append(relative).Append('\n').Append(BitConverter.ToString(hash.ComputeHash(file)).Replace("-", "").ToLowerInvariant()).Append('\n');
            }
            // Hash every working file through Git's own attributes, bypassing index stat, skip-worktree and assume-unchanged hints.
            // Keep the exact byte fingerprint separately so legitimate EOL conversion is visible.
            CheckPackageBlobs(package, arguments, blobs);
            return PlayScenarioPlayerPayload.HashText(actual.ToString());
        }

        private static void CheckPackageBlobs(string package, StringBuilder arguments, List<string> blobs)
        {
            string hashes = Git(package, "hash-object --" + arguments, 1024 * 1024);
            if (!hashes.Replace("\r\n", "\n").Split('\n').SequenceEqual(blobs))
                throw new InvalidDataException("Resolved package bytes differ from the recorded Git tree.");
        }

        private static async Task<string> ReadGitOutput(StreamReader reader, int limit)
        {
            var text = new StringBuilder();
            var buffer = new char[4096];
            int count;
            while ((count = await reader.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) != 0)
            {
                if (text.Length + count > limit)
                    throw new IOException("Native build Git output exceeds its limit.");
                text.Append(buffer, 0, count);
            }
            return text.ToString();
        }

        private static string Git(string directory, string arguments, int limit = 4096)
        {
            string name = Path.DirectorySeparatorChar == '\\' ? "git.exe" : "git";
            string executable = (Environment.GetEnvironmentVariable("PATH") ?? "")
                .Split(Path.PathSeparator)
                .Select(path => path.Trim('"'))
                .Where(path => Path.IsPathRooted(path))
                .Select(path => Path.Combine(path, name))
                .FirstOrDefault(File.Exists);
            if (executable == null)
                throw new InvalidOperationException("Git is required to pin native build sources.");
            var start = new System.Diagnostics.ProcessStartInfo(executable, "--no-pager -c core.fsmonitor=false " + arguments)
            {
                WorkingDirectory = directory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = new UTF8Encoding(false, true),
            };
            foreach (
                string key in start.EnvironmentVariables.Keys.Cast<string>().Where(key => key.StartsWith("GIT_", StringComparison.OrdinalIgnoreCase)).ToArray()
            )
                start.EnvironmentVariables.Remove(key);
            start.EnvironmentVariables["GIT_OPTIONAL_LOCKS"] = "0";
            start.EnvironmentVariables["GIT_NO_REPLACE_OBJECTS"] = "1";
            start.EnvironmentVariables["GIT_NO_LAZY_FETCH"] = "1";
            using var process = System.Diagnostics.Process.Start(start);
            var stdout = ReadGitOutput(process.StandardOutput, limit);
            var stderr = ReadGitOutput(process.StandardError, 65536);
            if (!process.WaitForExit(30000))
            {
                process.Kill();
                throw new TimeoutException("Native build Git inspection timed out or exceeded its output pipe.");
            }
            if (process.ExitCode != 0)
                throw new InvalidOperationException("Native build Git inspection failed: " + arguments);
            if (!Task.WaitAll(new Task[] { stdout, stderr }, 5000))
                throw new TimeoutException("Native build Git output timed out.");
            return stdout.Result.TrimEnd('\r', '\n');
        }

        private static string CreateLinkerDescriptor() =>
            "<linker>"
            + "<assembly fullname=\"MCPForUnity.Runtime\" preserve=\"all\" />"
            // Optional backend assemblies have no scene references; retain their runtime registration roots.
            + "<assembly fullname=\"MCPForUnity.Input.UGUI.Runtime\" ignoreIfMissing=\"1\">"
            + "<type fullname=\"MCPForUnity.Editor.Tools.Input.UguiScenarioDispatch\" preserve=\"all\" />"
            + "</assembly>"
            + "<assembly fullname=\"MCPForUnity.Input.InputSystem.Runtime\" ignoreIfMissing=\"1\">"
            + "<type fullname=\"MCPForUnity.Runtime.PlayScenarios.PlayScenarioInputSystemRegistration\" preserve=\"all\" />"
            + "</assembly>"
            + "</linker>";

        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string name = Argument(args, "--mcp-scenario-name");
            string output = Argument(args, "--mcp-scenario-output");
            int revisionIndex = Array.IndexOf(args, "--mcp-scenario-source-revision");
            string revision = revisionIndex < 0 ? null : Argument(args, "--mcp-scenario-source-revision");
            PlayScenarioPlayerBuildResult result = Build(
                name,
                output,
                revision,
                OptionalArgument(args, "--mcp-scenario-target", "windows64"),
                OptionalArgument(args, "--mcp-scenario-backend", "mono")
            );
            Debug.Log("Built scenario Player bundle: " + result.BundlePath);
        }

        private static BuildTarget Target(string target)
        {
            switch (target)
            {
                case "windows64":
                    return BuildTarget.StandaloneWindows64;
                case "linux64":
                    return BuildTarget.StandaloneLinux64;
                case "macos":
                    return BuildTarget.StandaloneOSX;
                default:
                    throw new ArgumentException("Unsupported standalone target.");
            }
        }

        private static bool EnsureEditorReady()
        {
            ValidateNativeAdmission();
            if (
                PlayScenarioService.IsBusy
                || EditorApplication.isPlayingOrWillChangePlaymode
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating
                || BuildPipeline.isBuildingPlayer
            )
                throw new InvalidOperationException("Wait for scenario execution, Play Mode, compilation, importing and builds to finish.");
            bool hasUntitledScene = false;
            bool pristineBatchStartup = Application.isBatchMode && SceneManager.sceneCount > 0;
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene scene = SceneManager.GetSceneAt(index);
                if (!scene.isLoaded)
                    continue;
                hasUntitledScene |= string.IsNullOrEmpty(scene.path);
                pristineBatchStartup &= string.IsNullOrEmpty(scene.path) && !scene.isDirty;
            }
            if (hasUntitledScene && !pristineBatchStartup)
                throw new InvalidOperationException(
                    "Save every loaded untitled scene before building a scenario Player; only an unmodified batch startup can be replaced."
                );
            return hasUntitledScene;
        }

        private static void PreflightBackend(string target, string backend, BuildTarget buildTarget)
        {
            if (!BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.Standalone, buildTarget))
                throw new InvalidOperationException("Standalone build module is unavailable for " + target + ".");
            if (backend != "il2cpp")
                return;
            RuntimePlatform required =
                target == "windows64" ? RuntimePlatform.WindowsEditor
                : target == "macos" ? RuntimePlatform.OSXEditor
                : RuntimePlatform.LinuxEditor;
            if (Application.platform != required)
                throw new InvalidOperationException("IL2CPP scenario builds require a native " + target + " Editor and toolchain.");
            string module =
                target == "windows64" ? "WindowsStandaloneSupport"
                : target == "macos" ? "MacStandaloneSupport"
                : "LinuxStandaloneSupport";
            string variations = Path.Combine(EditorApplication.applicationContentsPath, "PlaybackEngines", module, "Variations");
            if (!Directory.Exists(variations) || !Directory.EnumerateDirectories(variations, "*il2cpp*", SearchOption.TopDirectoryOnly).Any())
                throw new InvalidOperationException(
                    "The IL2CPP standalone module is unavailable for " + target + "; install the matching Editor module before building."
                );
        }

        private static string OptionalArgument(string[] args, string flag, string fallback) => Array.IndexOf(args, flag) < 0 ? fallback : Argument(args, flag);

        private static string Argument(string[] args, string flag)
        {
            int index = Array.IndexOf(args, flag);
            if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, flag) != index)
                throw new ArgumentException("Provide exactly one " + flag + ".");
            return args[index + 1];
        }
    }
}
