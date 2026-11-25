using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool(
        "manage_build",
        AutoRegister = false,
        RequiresExplicitConsent = true,
        Group = "core",
        RequiresPolling = true,
        PollAction = "status",
        MaxPollSeconds = 600
    )]
    public static class ManageBuild
    {
        private static readonly string[] ValidActions =
        {
            "build",
            "plan",
            "build_from_plan",
            "status",
            "platform",
            "settings",
            "scenes",
            "profiles",
            "batch",
            "cancel",
            "clean_output",
            "report_history",
            "report",
            "compare_reports",
        };

        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");

            var p = new ToolParams(@params);
            var actionResult = p.GetRequired("action");
            if (!actionResult.IsSuccess)
                return new ErrorResponse(actionResult.ErrorMessage);

            string action = actionResult.Value.ToLowerInvariant();

            if (!ValidActions.Contains(action))
                return new ErrorResponse($"Unknown action '{action}'. Valid actions: {string.Join(", ", ValidActions)}");

            if (BuildReportStore.IsAction(action))
                return BuildReportStore.Handle(@params);
            if (BuildReportStore.HasOptions(@params))
                return new ErrorResponse("invalid_build_report_request");

            try
            {
                switch (action)
                {
                    case "plan":
                        return HandleBuildPlan(@params, apply: false);
                    case "build_from_plan":
                        return HandleBuildPlan(@params, apply: true);
                    case "build":
                        return HandleBuild(p);
                    case "status":
                        return HandleStatus(p);
                    case "platform":
                        return HandlePlatform(p);
                    case "settings":
                        return HandleSettings(p);
                    case "scenes":
                        return HandleScenes(p);
                    case "profiles":
                        return HandleProfiles(p);
                    case "batch":
                        return HandleBatch(p);
                    case "cancel":
                        return HandleCancel(p);
                    case "clean_output":
                        var dryRunToken = p.GetRaw("dry_run");
                        if (dryRunToken != null && dryRunToken.Type != JTokenType.Null && dryRunToken.Type != JTokenType.Boolean)
                            return new ErrorResponse("'dry_run' must be a boolean.");
                        return BuildOutputCleaner.Clean(
                            System.IO.Path.GetFullPath(System.IO.Path.Combine(UnityEngine.Application.dataPath, "..")),
                            p.Get("output_path"),
                            p.GetBool("dry_run", true),
                            BuildPipeline.isBuildingPlayer || BuildJobStore.ActiveJobId != null
                        );
                    default:
                        return new ErrorResponse($"Unknown action: '{action}'");
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse(ex.Message, new { stackTrace = ex.StackTrace });
            }
        }

        // ── build ──────────────────────────────────────────────────────

        private static object HandleBuild(ToolParams p)
        {
            if (BuildPipeline.isBuildingPlayer)
                return new ErrorResponse("A build is already in progress.");
            if (p.GetStringArray("profiles", strict: true)?.Length > 0)
                return new ErrorResponse("Use 'profile' for a single build or 'batch' with 'profiles'.");

            string targetName = p.Get("target");
            if (!BuildTargetMapping.TryResolveBuildTarget(targetName, out var target))
                return new ErrorResponse(BuildTargetMapping.GetUnknownBuildTargetMessage(targetName));

            string profilePath = p.Get("profile");
            bool useProfile = !string.IsNullOrEmpty(profilePath);
            if (useProfile)
                BuildProfileApi.Installed.RequireBuild();
            var group = BuildTargetMapping.GetTargetGroup(target);
            if (!useProfile && !BuildPipeline.IsBuildTargetSupported(group, target))
                return new ErrorResponse($"Platform '{target}' is not installed. Install it via Unity Hub.");

            string outputPath = p.Get("output_path") ?? (useProfile ? null : BuildTargetMapping.GetDefaultOutputPath(target, PlayerSettings.productName));
            ValidateSceneArgument(p.GetRaw("scenes"));
            string[] scenes = p.GetStringArray("scenes");
            bool development = p.GetBool("development");
            var buildOptions = BuildRunner.ParseBuildOptions(ReadBuildOptionNames(p.GetRaw("options")), development);
            int subtarget = ReadSubtarget(p);
            string scriptingBackend = p.Get("scripting_backend");
            if (outputPath != null)
                BuildRunner.ValidateOutputPath(outputPath);
            BuildRunner.ValidateScenePaths(scenes);

            // Validate first; apply this persistent setting only after build preparation succeeds.
            ScriptingImplementation? scriptingImplementation = null;
            if (!string.IsNullOrEmpty(scriptingBackend))
            {
                string backendLower = scriptingBackend.ToLowerInvariant();
                if (backendLower != "il2cpp" && backendLower != "mono")
                    return new ErrorResponse($"Unknown scripting_backend '{scriptingBackend}'. Valid: mono, il2cpp");
                scriptingImplementation = backendLower == "il2cpp" ? ScriptingImplementation.IL2CPP : ScriptingImplementation.Mono2x;
            }

            if (useProfile)
                return HandleProfileBuild(profilePath, outputPath, buildOptions, scriptingImplementation);

            var options = BuildRunner.CreateBuildOptions(target, outputPath, scenes, buildOptions, subtarget);

            string jobId = BuildJobStore.CreateJobId();
            var job = new BuildJob(jobId, target, outputPath);
            return BuildRunner.SchedulePreparedBuild(job, options, scriptingImplementation);
        }

        private static object HandleBuildPlan(JObject request, bool apply)
        {
            var p = new ToolParams(request);
            string expected = p.Get("expected_plan_sha256");
            if (apply && (p.GetRaw("expected_plan_sha256")?.Type != JTokenType.String || !BuildPlan.IsExpectedHash(expected)))
                return new ErrorResponse(
                    "build_plan_hash_invalid",
                    new { message = "expected_plan_sha256 must be exactly 64 hexadecimal characters. Request a fresh plan." }
                );
            if (!apply && p.GetRaw("expected_plan_sha256") != null)
                return new ErrorResponse("build_plan_scope_unsupported", new { message = "expected_plan_sha256 is supported only for build_from_plan." });
            try
            {
                BuildPlan.ValidateRequestSize(request);
                // Retain no caller-owned token/array references across the next-update boundary.
                var detached = (JObject)request.DeepClone();
                BuildPlan plan = BuildPlan.Capture(detached);
                if (!apply)
                    return new SuccessResponse("Build plan observed. No settings or build effects were applied.", plan.ToData());
                if (!string.Equals(expected, plan.Sha256, StringComparison.OrdinalIgnoreCase))
                    return new ErrorResponse(
                        "build_plan_stale",
                        new { message = "Build observations differ from the expected plan. Request a fresh plan before applying." }
                    );
                BuildPlayerOptions options = plan.CopyOptions();
                var job = new BuildJob(BuildJobStore.CreateJobId(), options.target, options.locationPathName);
                return BuildRunner.SchedulePreparedBuild(job, options, plan.RequestedBackend, detached, plan);
            }
            catch (BuildPlanException error)
            {
                return new ErrorResponse(error.Code, error.ToData());
            }
        }

        internal static int ReadSubtarget(ToolParams p)
        {
            var token = p.GetRaw("subtarget");
            if (token == null || token.Type == JTokenType.Null)
                return BuildTargetMapping.ResolveSubtarget(null);
            if (token.Type != JTokenType.String)
                throw new ArgumentException("'subtarget' must be player or server.");
            return BuildTargetMapping.ResolveSubtarget(token.Value<string>());
        }

        internal static string[] ReadBuildOptionNames(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
                return null;
            JArray array;
            try
            {
                if (token.Type == JTokenType.String)
                {
                    string value = token.Value<string>();
                    string trimmed = value.Trim();
                    if (!trimmed.StartsWith("[", StringComparison.Ordinal) || !trimmed.EndsWith("]", StringComparison.Ordinal))
                        return new[] { value };
                    array = JArray.Parse(trimmed);
                }
                else if (token is JArray values)
                {
                    array = values;
                    // Preserve exactly the single wrapper forms accepted by ToolParams.
                    if (array.Count == 1 && array[0] is JArray nested)
                        array = nested;
                    else if (array.Count == 1 && array[0].Type == JTokenType.String)
                    {
                        string inner = array[0].Value<string>().Trim();
                        if (inner.StartsWith("[", StringComparison.Ordinal) && inner.EndsWith("]", StringComparison.Ordinal))
                            array = JArray.Parse(inner);
                    }
                }
                else
                    throw new ArgumentException("'options' must be a string or an array of BuildOptions names.");
            }
            catch (Newtonsoft.Json.JsonException ex)
            {
                throw new ArgumentException("'options' must contain supported BuildOptions names.", ex);
            }
            if (array.Any(item => item.Type != JTokenType.String))
                throw new ArgumentException("'options' must contain only BuildOptions names as strings.");
            return array.Select(item => item.Value<string>()).ToArray();
        }

        internal static void ValidateSceneArgument(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
                return;
            if (token.Type == JTokenType.String)
            {
                string value = token.ToString().Trim();
                if (string.IsNullOrEmpty(value))
                    throw new ArgumentException("'scenes' must contain non-empty scene asset paths.");
                if (value.StartsWith("[", StringComparison.Ordinal) && value.EndsWith("]", StringComparison.Ordinal))
                {
                    try
                    {
                        ValidateSceneArgument(JArray.Parse(value));
                    }
                    catch (Newtonsoft.Json.JsonException ex)
                    {
                        throw new ArgumentException("'scenes' must contain scene asset paths.", ex);
                    }
                }
                return;
            }
            if (token is not JArray array)
                throw new ArgumentException("'scenes' must be an array of scene asset paths.");

            // Preserve the stringified/single nested array forms accepted by ToolParams.
            if (array.Count == 1 && (array[0].Type == JTokenType.String || array[0].Type == JTokenType.Array))
            {
                ValidateSceneArgument(array[0]);
                return;
            }
            foreach (JToken scene in array)
                if (scene.Type != JTokenType.String || string.IsNullOrWhiteSpace(scene.ToString()))
                    throw new ArgumentException("'scenes' must contain non-empty scene asset paths.");
        }

        private static object HandleProfileBuild(
            string profilePath,
            string outputPath,
            BuildOptions buildOptions,
            ScriptingImplementation? scriptingImplementation
        )
        {
            profilePath = AssetPathUtility.GetContainedAssetPath(profilePath);
            var profile = BuildProfileApi.Installed.Load(profilePath);
            if (profile == null)
                return new ErrorResponse($"Build profile not found at: {profilePath}");

            if (!TryGetProfileBuildTarget(profile, out var target, out var subtarget, out var targetError))
                return new ErrorResponse(targetError);
            var targetGroup = BuildPipeline.GetBuildTargetGroup(target);
            if (!BuildPipeline.IsBuildTargetSupported(targetGroup, target))
                return new ErrorResponse($"Platform '{target}' is not installed. Install it via Unity Hub.");
            outputPath ??= BuildTargetMapping.GetDefaultOutputPath(target, PlayerSettings.productName);
            BuildRunner.ValidateOutputPath(outputPath);

            var options = new BuildPlayerOptions
            {
                target = target,
                subtarget = (int)subtarget,
                locationPathName = outputPath,
                options = buildOptions,
            };

            string jobId = BuildJobStore.CreateJobId();
            var job = new BuildJob(jobId, target, outputPath);
            return BuildRunner.SchedulePreparedProfileBuild(job, profile, options, scriptingImplementation);
        }

        private static bool TryGetProfileBuildTarget(
            UnityEngine.Object profile,
            out BuildTarget target,
            out StandaloneBuildSubtarget subtarget,
            out string error
        )
        {
            // Unity 6 stores this target in the profile; its CLR getter is internal.
            using var serializedProfile = new SerializedObject(profile);
            using var targetProperty = serializedProfile.FindProperty("m_BuildTarget");
            target = targetProperty != null ? (BuildTarget)targetProperty.intValue : BuildTarget.NoTarget;
            using var subtargetProperty = serializedProfile.FindProperty("m_Subtarget");
            subtarget = subtargetProperty != null ? (StandaloneBuildSubtarget)subtargetProperty.intValue : StandaloneBuildSubtarget.Player;
            if (targetProperty == null || !Enum.IsDefined(typeof(BuildTarget), target) || BuildPipeline.GetBuildTargetGroup(target) == BuildTargetGroup.Unknown)
            {
                error = "Build profile does not contain a supported build target.";
                return false;
            }
            if (
                BuildPipeline.GetBuildTargetGroup(target) == BuildTargetGroup.Standalone
                && (subtargetProperty == null || !Enum.IsDefined(typeof(StandaloneBuildSubtarget), subtarget))
            )
            {
                error = "Build profile does not contain a supported standalone subtarget.";
                return false;
            }
            error = null;
            return true;
        }

        // ── status ─────────────────────────────────────────────────────

        private static object HandleStatus(ToolParams p)
        {
            string jobId = p.Get("job_id");
            if (string.IsNullOrEmpty(jobId))
                jobId = BuildJobStore.ActiveJobId;

            if (string.IsNullOrEmpty(jobId))
            {
                var last = BuildJobStore.LastCompletedJob;
                if (last != null)
                    return new SuccessResponse("Last completed build.", last.ToStatusResponse());

                var persisted = BuildReportStore.FindStatus();
                if (persisted != null)
                    return new SuccessResponse("Last retained completed build observation.", persisted);
                var latestReport = BuildReportStore.GetLatestReport?.Invoke();
                if (latestReport != null)
                {
                    var s = latestReport.summary;
                    return new SuccessResponse(
                        "Last build report from Unity.",
                        new
                        {
                            result = s.result.ToString().ToLowerInvariant(),
                            platform = s.platform.ToString(),
                            output_path = s.outputPath,
                            total_size_mb = Math.Round(s.totalSize / (1024.0 * 1024.0), 2),
                            duration_seconds = s.totalTime.TotalSeconds,
                            errors = s.totalErrors,
                            warnings = s.totalWarnings,
                        }
                    );
                }
                return new ErrorResponse("No build jobs found.");
            }

            // Check batch jobs first
            var batchJob = BuildJobStore.GetBatchJob(jobId);
            if (batchJob != null)
            {
                if (batchJob.State == BuildJobState.Building || batchJob.State == BuildJobState.Pending)
                    return new PendingResponse(
                        $"Batch {batchJob.State.ToString().ToLowerInvariant()}...",
                        pollIntervalSeconds: 10.0,
                        data: batchJob.ToStatusResponse()
                    );
                return new SuccessResponse($"Batch {batchJob.State}.", batchJob.ToStatusResponse());
            }

            var buildJob = BuildJobStore.GetBuildJob(jobId);
            if (buildJob == null)
            {
                var persisted = BuildReportStore.FindStatus(jobId);
                return persisted != null
                    ? new SuccessResponse("Retained completed build observation.", persisted)
                    : new ErrorResponse($"No job found with ID: {jobId}");
            }

            if (buildJob.State == BuildJobState.Building || buildJob.State == BuildJobState.Pending)
            {
                return new PendingResponse(
                    $"Build {buildJob.State.ToString().ToLowerInvariant()}...",
                    pollIntervalSeconds: 5.0,
                    data: buildJob.ToStatusResponse()
                );
            }

            return new SuccessResponse($"Build {buildJob.State}.", buildJob.ToStatusResponse());
        }

        // ── platform ───────────────────────────────────────────────────

        private static object HandlePlatform(ToolParams p)
        {
            int subtarget = ReadSubtarget(p);
            string targetName = p.Get("target");

            if (string.IsNullOrEmpty(targetName))
            {
                // Read current platform
                return new SuccessResponse(
                    "Current platform.",
                    new
                    {
                        target = EditorUserBuildSettings.activeBuildTarget.ToString(),
                        target_group = BuildTargetMapping.GetTargetGroup(EditorUserBuildSettings.activeBuildTarget).ToString(),
                        subtarget = EditorUserBuildSettings.standaloneBuildSubtarget.ToString(),
                    }
                );
            }

            // Switch platform
            if (!BuildTargetMapping.TryResolveBuildTarget(targetName, out var target))
                return new ErrorResponse(BuildTargetMapping.GetUnknownBuildTargetMessage(targetName));

            var group = BuildTargetMapping.GetTargetGroup(target);
            if (!BuildPipeline.IsBuildTargetSupported(group, target))
                return new ErrorResponse($"Platform '{target}' is not installed. Install it via Unity Hub.");

            var subtargetToken = p.GetRaw("subtarget");
            var previousSubtarget = EditorUserBuildSettings.standaloneBuildSubtarget;
            bool changeSubtarget = subtargetToken != null && subtargetToken.Type != JTokenType.Null && previousSubtarget != (StandaloneBuildSubtarget)subtarget;
            if (EditorUserBuildSettings.activeBuildTarget == target && !changeSubtarget)
                return new SuccessResponse("Already on this platform.", new { target = target.ToString() });

            string previousTarget = EditorUserBuildSettings.activeBuildTarget.ToString();
            bool switched = false;
            try
            {
                // The public switch API reads the selected subtarget, including same-target changes.
                if (changeSubtarget)
                    EditorUserBuildSettings.standaloneBuildSubtarget = (StandaloneBuildSubtarget)subtarget;
                switched = EditorUserBuildSettings.SwitchActiveBuildTarget(group, target);
                if (!switched)
                    return new ErrorResponse($"Failed to switch to build platform '{target}'.");

                return new SuccessResponse(
                    $"Switched to {target}. Assets reimported for new platform.",
                    new { target = target.ToString(), previous = previousTarget }
                );
            }
            finally
            {
                if (!switched && changeSubtarget)
                    EditorUserBuildSettings.standaloneBuildSubtarget = previousSubtarget;
            }
        }

        // ── settings ───────────────────────────────────────────────────

        private static object HandleSettings(ToolParams p)
        {
            var propertyResult = p.GetRequired("property");
            if (!propertyResult.IsSuccess)
                return new ErrorResponse(propertyResult.ErrorMessage);

            string property = propertyResult.Value.ToLowerInvariant();
            string targetName = p.Get("target");
            string value = p.Get("value");

            // Resolve target
            string err = BuildTargetMapping.TryResolveNamedBuildTarget(targetName, out var namedTarget);
            if (err != null)
                return new ErrorResponse(err);

            var valueToken = p.GetRaw("value");
            if (valueToken == null || valueToken.Type == JTokenType.Null)
            {
                // Read
                var result = BuildSettingsHelper.ReadProperty(property, namedTarget);
                if (result == null)
                    return new ErrorResponse($"Unknown property '{property}'. Valid: {string.Join(", ", BuildSettingsHelper.ValidProperties)}");
                return new SuccessResponse($"Read {property}.", result);
            }

            // Write
            string writeErr = BuildSettingsHelper.WriteProperty(property, value, namedTarget);
            if (writeErr != null)
                return new ErrorResponse(writeErr);
            return new SuccessResponse($"Set {property} = {value}.", BuildSettingsHelper.ReadProperty(property, namedTarget));
        }

        // ── scenes ─────────────────────────────────────────────────────

        private static object HandleScenes(ToolParams p)
        {
            var scenesRaw = p.GetRaw("scenes");

            if (scenesRaw == null || scenesRaw.Type == JTokenType.Null)
            {
                // Read current scene list
                var scenes = EditorBuildSettings
                    .scenes.Select(s => new
                    {
                        path = s.path,
                        enabled = s.enabled,
                        guid = s.guid.ToString(),
                    })
                    .ToArray();

                return new SuccessResponse($"Build scenes ({scenes.Length}).", new { scenes });
            }

            // Handle string input: try JSON parse, then comma-separated
            if (scenesRaw.Type == JTokenType.String)
            {
                string scenesStr = scenesRaw.ToString();
                try
                {
                    scenesRaw = JArray.Parse(scenesStr);
                }
                catch (Newtonsoft.Json.JsonException)
                {
                    if (scenesStr.TrimStart().StartsWith("[", StringComparison.Ordinal))
                        return new ErrorResponse("'scenes' must contain a valid JSON array.");
                    // Treat as comma-separated paths
                    var paths = scenesStr.Split(',').Select(s => s.Trim()).Where(s => !string.IsNullOrEmpty(s)).ToArray();
                    if (paths.Length == 0)
                        return new ErrorResponse("'scenes' string contained no valid paths.");
                    var fromStr = paths.Select(sp => new EditorBuildSettingsScene(sp, true)).ToArray();
                    EditorBuildSettings.scenes = fromStr;
                    return new SuccessResponse(
                        $"Updated build scenes ({fromStr.Length}).",
                        new { scenes = fromStr.Select(s => new { path = s.path, enabled = s.enabled }).ToArray() }
                    );
                }
            }

            // Write scene list — accepts array of strings or array of {path, enabled} objects
            var sceneArray = scenesRaw as JArray;
            if (sceneArray == null)
                return new ErrorResponse("'scenes' must be an array of scene paths or {path, enabled} objects.");

            var newScenes = new List<EditorBuildSettingsScene>();
            foreach (var item in sceneArray)
            {
                var row = item as JObject;
                var pathToken = row == null ? item : row["path"];
                if (pathToken?.Type != JTokenType.String || string.IsNullOrWhiteSpace((string)pathToken))
                    return new ErrorResponse("Each scene must have a nonempty string path.");
                string path = (string)pathToken;
                bool enabled = row?["enabled"]?.ReadScalar<bool?>() ?? true;
                newScenes.Add(new EditorBuildSettingsScene(path, enabled));
            }

            EditorBuildSettings.scenes = newScenes.ToArray();
            return new SuccessResponse(
                $"Updated build scenes ({newScenes.Count}).",
                new { scenes = newScenes.Select(s => new { path = s.path, enabled = s.enabled }).ToArray() }
            );
        }

        // ── profiles ───────────────────────────────────────────────────

        private static object HandleProfiles(ToolParams p)
        {
            string profilePath = p.Get("profile");
            bool activate = p.GetBool("activate");
            var api = BuildProfileApi.Installed;

            if (string.IsNullOrEmpty(profilePath))
            {
                var active = api.GetActive();
                var guids = AssetDatabase.FindAssets("t:BuildProfile", new[] { "Assets" });
                var profiles = guids
                    .Select(guid => AssetPathUtility.GetAssetPathFromGuid(guid))
                    .Where(path => api.Load(path) != null)
                    .Select(path => new { path, name = System.IO.Path.GetFileNameWithoutExtension(path) })
                    .ToArray();

                return new SuccessResponse(
                    $"Found {profiles.Length} build profiles.",
                    new { profiles, active_profile = active != null ? AssetDatabase.GetAssetPath(active) : null }
                );
            }

            profilePath = AssetPathUtility.GetContainedAssetPath(profilePath);
            var loadedProfile = api.Load(profilePath);
            if (loadedProfile == null)
                return new ErrorResponse($"Build profile not found at: {profilePath}");

            if (activate)
            {
                api.SetActive(loadedProfile);
                return new SuccessResponse($"Activated build profile: {profilePath}", new { profile = profilePath, activated = true });
            }

            var profileScenes = api.Scenes(loadedProfile).Select(s => s.path).ToArray();
            return new SuccessResponse($"Profile: {profilePath}", new { profile = profilePath, scenes = profileScenes });
        }

        // ── batch ──────────────────────────────────────────────────────

        private static object HandleBatch(ToolParams p)
        {
            if (BuildPipeline.isBuildingPlayer)
                return new ErrorResponse("A build is already in progress.");

            string[] targets = p.GetStringArray("targets", strict: true);
            string[] profiles = p.GetStringArray("profiles", strict: true);
            if (targets?.Length > BuildQueueJournal.MaxChildren || profiles?.Length > BuildQueueJournal.MaxChildren)
                return new ErrorResponse($"A batch accepts at most {BuildQueueJournal.MaxChildren} targets or profiles.");
            if (!string.IsNullOrEmpty(p.Get("profile")))
                return new ErrorResponse("Use 'profiles' for a batch or 'build' with 'profile'.");
            string outputDir = p.Get("output_dir") ?? "Builds";
            bool development = p.GetBool("development");
            var buildOpts = BuildRunner.ParseBuildOptions(ReadBuildOptionNames(p.GetRaw("options")), development);

            if ((targets == null || targets.Length == 0) && (profiles == null || profiles.Length == 0))
                return new ErrorResponse("'targets' or 'profiles' is required for batch builds.");
            if (targets != null && targets.Length > 0 && profiles != null && profiles.Length > 0)
                return new ErrorResponse("Provide 'targets' or 'profiles', not both.");
            BuildRunner.ValidateOutputPath(outputDir, "output_dir");

            // Validate all targets/profiles upfront before creating store entries
            if (targets != null && targets.Length > 0)
            {
                var resolvedTargets = new List<(BuildTarget bt, string path)>();
                foreach (var t in targets)
                {
                    if (!BuildTargetMapping.TryResolveBuildTarget(t, out var bt))
                        return new ErrorResponse(BuildTargetMapping.GetUnknownBuildTargetMessage(t));
                    var btGroup = BuildTargetMapping.GetTargetGroup(bt);
                    if (!BuildPipeline.IsBuildTargetSupported(btGroup, bt))
                        return new ErrorResponse($"Platform '{bt}' is not installed. Install it via Unity Hub.");
                    string defaultPath = BuildTargetMapping.GetDefaultOutputPath(bt, PlayerSettings.productName);
                    string path = defaultPath.StartsWith("Builds/") ? $"{outputDir}/{defaultPath.Substring(7)}" : $"{outputDir}/{defaultPath}";
                    BuildRunner.CreateBuildOptions(bt, path, null, BuildOptions.None, (int)StandaloneBuildSubtarget.Player);
                    resolvedTargets.Add((bt, path));
                }

                string collision = FindBatchOutputCollision(resolvedTargets.Select(item => item.path));
                if (collision != null)
                    return new ErrorResponse($"Batch builds have conflicting output locations: {collision}");

                string batchId = BuildJobStore.CreateBatchId();
                var batch = new BatchJob(batchId);
                batch.State = BuildJobState.Building;
                var checkpoint = new BuildQueueEntry
                {
                    Id = batchId,
                    Batch = true,
                    DeadlineUnixMs = DateTimeOffset.UtcNow.AddHours(2).ToUnixTimeMilliseconds(),
                };

                foreach (var (bt, path) in resolvedTargets)
                {
                    var child = new BuildJob(BuildJobStore.CreateJobId(), bt, path);
                    batch.Children.Add(child);
                    var options = BuildRunner.CreateBuildOptions(bt, path, null, buildOpts, (int)StandaloneBuildSubtarget.Player);
                    BuildQueueChild frozen = BuildQueueChild.Capture(child, options);
                    frozen.SwitchTarget = true;
                    checkpoint.Children.Add(frozen);
                }

                BuildQueueCheckpoint.Enqueue(checkpoint, jobs: batch.Children, batch: batch);

                return new PendingResponse(
                    $"Batch build started ({batch.Children.Count} builds).",
                    pollIntervalSeconds: 10.0,
                    data: new { job_id = batchId, total = batch.Children.Count }
                );
            }
            if (profiles != null && profiles.Length > 0)
            {
                BuildProfileApi.Installed.RequireBuild();
                // Validate all profiles exist before creating any store entries
                var loadedProfiles = new List<UnityEngine.Object>();
                var profileTargets = new List<BuildTarget>();
                var profilePaths = new List<string>();
                foreach (var profilePath in profiles)
                {
                    var profile = BuildProfileApi.Installed.Load(AssetPathUtility.GetContainedAssetPath(profilePath));
                    if (profile == null)
                        return new ErrorResponse($"Profile not found: {profilePath}");
                    if (!TryGetProfileBuildTarget(profile, out var target, out _, out var targetError))
                        return new ErrorResponse(targetError);
                    if (!BuildPipeline.IsBuildTargetSupported(BuildPipeline.GetBuildTargetGroup(target), target))
                        return new ErrorResponse($"Platform '{target}' is not installed. Install it via Unity Hub.");
                    string name = System.IO.Path.GetFileNameWithoutExtension(profilePath);
                    string fileName = System.IO.Path.GetFileName(BuildTargetMapping.GetDefaultOutputPath(target, PlayerSettings.productName));
                    string path = $"{outputDir}/{name}/{fileName}";
                    BuildRunner.ValidateOutputPath(path);
                    profilePaths.Add(path);
                    loadedProfiles.Add(profile);
                    profileTargets.Add(target);
                }

                string collision = FindBatchOutputCollision(profilePaths);
                if (collision != null)
                    return new ErrorResponse($"Batch builds have conflicting output locations: {collision}");

                string batchId = BuildJobStore.CreateBatchId();
                var batch = new BatchJob(batchId);
                batch.State = BuildJobState.Building;
                var checkpoint = new BuildQueueEntry
                {
                    Id = batchId,
                    Batch = true,
                    DeadlineUnixMs = DateTimeOffset.UtcNow.AddHours(2).ToUnixTimeMilliseconds(),
                };

                for (int i = 0; i < profiles.Length; i++)
                {
                    var target = profileTargets[i];
                    string path = profilePaths[i];
                    var child = new BuildJob(BuildJobStore.CreateJobId(), target, path);
                    batch.Children.Add(child);
                    BuildQueueChild frozen = BuildQueueChild.Capture(
                        child,
                        new BuildPlayerOptions
                        {
                            target = target,
                            locationPathName = path,
                            options = buildOpts,
                        }
                    );
                    BuildRunner.CaptureProfile(frozen, loadedProfiles[i]);
                    checkpoint.Children.Add(frozen);
                }

                BuildQueueCheckpoint.Enqueue(checkpoint, jobs: batch.Children, batch: batch);

                return new PendingResponse(
                    $"Batch build started ({batch.Children.Count} builds).",
                    pollIntervalSeconds: 10.0,
                    data: new { job_id = batchId, total = batch.Children.Count }
                );
            }

            return new ErrorResponse("'targets' or 'profiles' is required for batch builds.");
        }

        private static string FindBatchOutputCollision(IEnumerable<string> paths)
        {
            // Use a conservative case-insensitive comparison for portable build outputs.
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string path in paths)
            {
                string normalized = System.IO.Path.GetFullPath(path).TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar);
                if (!seen.Add(normalized))
                    return normalized;
            }
            return null;
        }

        // ── cancel ─────────────────────────────────────────────────────

        private static object HandleCancel(ToolParams p)
        {
            var jobIdResult = p.GetRequired("job_id");
            if (!jobIdResult.IsSuccess)
                return new ErrorResponse(jobIdResult.ErrorMessage);

            string jobId = jobIdResult.Value;

            if (BuildQueueCheckpoint.Cancel(jobId))
                return new SuccessResponse(
                    "Build cancellation recorded. An invoked native build will finish; queued work will not start.",
                    new { job_id = jobId, state = "cancelled" }
                );

            var batchJob = BuildJobStore.GetBatchJob(jobId);
            if (batchJob != null)
            {
                if (batchJob.State == BuildJobState.Building)
                {
                    batchJob.State = BuildJobState.Cancelled;
                    BuildReportStore.Complete(batchJob);
                    return new SuccessResponse(
                        "Batch cancelled. The current build will finish but no more builds will start.",
                        new { job_id = jobId, state = "cancelled" }
                    );
                }
                return new ErrorResponse($"Batch is already {batchJob.State}.");
            }

            var buildJob = BuildJobStore.GetBuildJob(jobId);
            if (buildJob != null)
            {
                if (buildJob.State == BuildJobState.Building)
                    return new ErrorResponse(
                        "Cannot cancel a single build in progress. BuildPipeline.BuildPlayer is " + "synchronous and blocks the editor until completion."
                    );
                return new ErrorResponse($"Build is already {buildJob.State}.");
            }

            return new ErrorResponse($"No job found with ID: {jobId}");
        }
    }
}
