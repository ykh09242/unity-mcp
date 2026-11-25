using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using UnityEditor;

// Executes full production command/runner/mapping/job/parameter sources. Unity native APIs
// are replaced by observable managed seams; DEV14 invokes only managed fake callbacks.
internal static class BuildPreflightHarness
{
    private static int failures;
    private static int checks;
    private static string outputRoot;
    private static bool disablePlanGuardControl;

    private static void Check(bool value, string name)
    {
        checks++;
        if (value)
            return;
        failures++;
        Console.WriteLine("FAIL: " + name);
    }

    private static IDictionary Store(string name) =>
        (IDictionary)typeof(BuildJobStore).GetField(name, BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);

    private static void Reset()
    {
        Store("_buildJobs").Clear();
        Store("_batchJobs").Clear();
        EditorApplication.update = null;
        EditorApplication.delayCall = null;
        SessionState.Clear();
        var flags = BindingFlags.Static | BindingFlags.NonPublic;
        typeof(BuildQueueCheckpoint).GetField("entries", flags).SetValue(null, null);
        ((HashSet<string>)typeof(BuildQueueCheckpoint).GetField("Scheduled", flags).GetValue(null)).Clear();
        ((IDictionary)typeof(BuildQueueCheckpoint).GetField("Preparations", flags).GetValue(null)).Clear();
        ((HashSet<string>)typeof(BuildRunner).GetField("NativeOwners", flags).GetValue(null)).Clear();
        ((HashSet<string>)typeof(BuildRunner).GetField("InvokedOwners", flags).GetValue(null)).Clear();
        PlayerSettings.Writes = 0;
        EditorUserBuildSettings.Switches = 0;
        EditorUserBuildSettings.SwitchSucceeds = true;
        EditorUserBuildSettings.ThrowOnSwitch = false;
        BuildPipeline.TargetSupported = true;
        EditorUserBuildSettings.activeBuildTarget = BuildTarget.StandaloneWindows64;
        EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Player;
        EditorUserBuildSettings.SubtargetWrites = 0;
        BuildPipeline.Builds = 0;
        BuildPipeline.ThrowSupport = false;
        BuildPipeline.LastOptions = default;
        AssetDatabase.Saves = 0;
        AssetDatabase.SceneGuid = "11111111111111111111111111111111";
        PlayerSettings.productName = "Game";
        PlayerSettings.companyName = "Company";
        PlayerSettings.bundleVersion = "1.0";
        PlayerSettings.Identifier = "com.fixture.game";
        PlayerSettings.Defines = "FIXTURE;A";
        PlayerSettings.Architecture = 0;
        PlayerSettings.Backend = ScriptingImplementation.Mono2x;
        EditorUserBuildSettings.buildAppBundle = false;
        UnityEngine.Application.dataPath = Path.GetFullPath("Assets");
        UnityEngine.Application.unityVersion = "synthetic";
        EditorBuildSettings.scenes = new[]
        {
            new EditorBuildSettingsScene("Assets/First.unity", true),
            new EditorBuildSettingsScene("Assets/Second.unity", false),
        };
    }

    private static void Reject(JObject request, string diagnostic)
    {
        Reset();
        request["output_path"] = Path.Combine(outputRoot, "Game.exe");
        request["output_dir"] = outputRoot;
        var response = JObject.FromObject(ManageBuild.HandleCommand(request));
        string name = request.ToString(Newtonsoft.Json.Formatting.None);
        Check(!response.Value<bool>("success"), name + " rejects");
        string error = response.Value<string>("error") ?? "";
        Check(error.Contains(diagnostic), name + " expected " + diagnostic + ", received " + error);
        Check(PlayerSettings.Writes == 0, name + " backend untouched");
        Check(Store("_buildJobs").Count == 0 && Store("_batchJobs").Count == 0, name + " inventory untouched");
        Check(EditorApplication.update == null, name + " no scheduled callbacks");
        Check(EditorUserBuildSettings.SubtargetWrites == 0, name + " no subtarget writes");
        Check(
            EditorUserBuildSettings.Switches == 0 && EditorUserBuildSettings.standaloneBuildSubtarget == StandaloneBuildSubtarget.Player,
            name + " platform untouched"
        );
        Check(BuildPipeline.Builds == 0 && !Directory.Exists(outputRoot), name + " no build/output folder");
    }

    private static JObject PlanRequest() =>
        new JObject
        {
            ["action"] = "plan",
            ["scripting_backend"] = "il2cpp",
            ["options"] = new JArray("clean_build", "auto_run"),
        };

    private static JObject Call(JObject request)
    {
        // Deliberately unsafe routing exists only in this managed fake-Unity oracle control.
        if (disablePlanGuardControl && request.Value<string>("action") == "build_from_plan")
        {
            var unsafeRequest = (JObject)request.DeepClone();
            unsafeRequest["action"] = "build";
            return JObject.FromObject(ManageBuild.HandleCommand(unsafeRequest));
        }
        return JObject.FromObject(ManageBuild.HandleCommand(request));
    }

    private static string PlanHash(JObject request)
    {
        JObject preview = (JObject)request.DeepClone();
        preview["action"] = "plan";
        preview.Remove("expected_plan_sha256");
        return Call(preview).SelectToken("data.plan_sha256")?.Value<string>();
    }

    private static bool ReadOnly() =>
        PlayerSettings.Writes == 0
        && BuildPipeline.Builds == 0
        && AssetDatabase.Saves == 0
        && EditorUserBuildSettings.Switches == 0
        && EditorUserBuildSettings.SubtargetWrites == 0
        && Store("_buildJobs").Count == 0
        && Store("_batchJobs").Count == 0
        && EditorApplication.update == null
        && !Directory.Exists(outputRoot);

    private static void RunUpdate()
    {
        Action update = EditorApplication.update;
        update?.Invoke();
    }

    private static void LostBatchStatus()
    {
        Reset();
        JObject started = Call(new JObject { ["action"] = "batch", ["targets"] = new JArray("windows64", "linux64") });
        string id = (string)started["data"]["job_id"];
        var flags = BindingFlags.Static | BindingFlags.NonPublic;
        var entries = (List<BuildQueueEntry>)typeof(BuildQueueCheckpoint).GetField("entries", flags).GetValue(null);
        var child = entries[0].Children[0];
        RunUpdate();
        BuildQueueCheckpoint.MarkNativeStarted(child);
        string journal = SessionState.GetString(BuildQueueCheckpoint.StorageKey, "");
        Reset();
        SessionState.SetString(BuildQueueCheckpoint.StorageKey, journal);
        SessionState.SetBool("MCPForUnity.Build.NativeOwnerV1", true);
        typeof(BuildQueueCheckpoint).GetMethod("Restore", flags).Invoke(null, null);
        JObject status = Call(new JObject { ["action"] = "status", ["job_id"] = id });
        Check((string)status["data"]["result"] == "failed", "lost batch status stops polling; observed " + status["data"]["result"]);
        Check((bool?)status["data"]["recovery_required"] == true, "lost batch status keeps the native recovery requirement");
        Check((string)status["data"]["builds"][1]["result"] == "skipped", "uninvoked sibling is skipped; observed " + status["data"]["builds"][1]["result"]);
        Check(BuildRunner.NativeRecoveryRequired && BuildPipeline.Builds == 0, "lost batch retains the fence without replay");
    }

    private static void PlanContracts()
    {
        Reset();
        var unguarded = PlanRequest();
        unguarded["action"] = "build";
        Call(unguarded);
        Check(
            !ReadOnly() && PlayerSettings.Writes == 0 && Store("_buildJobs").Count == 1,
            "negative control: old build route fails read-only oracle before actual execution"
        );
        RunUpdate();
        Check(PlayerSettings.Writes == 1 && BuildPipeline.Builds == 0, "negative control: checkpoint prepares the backend before native build");
        RunUpdate();
        Check(BuildPipeline.Builds == 1 && AssetDatabase.Saves == 1, "negative control: unguarded route really executes fake build");

        Reset();
        JObject request = PlanRequest();
        JObject preview = Call(request);
        Check(preview.Value<bool>("success"), "DEV14 plan facility exists: " + preview.ToString(Newtonsoft.Json.Formatting.None));
        if (!preview.Value<bool>("success"))
            return; // baseline is missing-facility RED, not fabricated deeper behavioral evidence
        Check(ReadOnly(), "valid plan has zero writes/jobs/schedule/save/build/output");
        Check(preview.SelectToken("data.schema_version")?.Value<string>() == "unity-mcp/build-plan@1", "versioned plan schema");
        string hash = preview.SelectToken("data.plan_sha256")?.Value<string>();
        Check(hash != null && System.Text.RegularExpressions.Regex.IsMatch(hash, "^[0-9a-f]{64}$"), "canonical hash syntax");
        Check(hash == PlanHash(request), "unchanged plan hash deterministic");
        Check(preview.SelectToken("data.ready_to_schedule")?.Value<bool>() == true, "valid plan ready only for scheduling");
        Check(preview.SelectToken("data.job_id") == null, "plan has no job identity");
        Check(
            preview.SelectToken("data.observed.scripting_backend.raw")?.Value<int>() == (int)ScriptingImplementation.Mono2x,
            "raw observed backend preserved"
        );
        Check(preview.SelectToken("data.scenes[0].path")?.Value<string>() == "Assets/First.unity", "enabled default scene resolved");
        Check(preview.SelectToken("data.effects.scripting_backend_write")?.Value<bool>() == true, "requested backend effect explained but unapplied");

        foreach (JToken badHash in new JToken[] { JValue.CreateNull(), "bad", new string('a', 63), new string('g', 64), 123 })
        {
            Reset();
            var invalidHash = PlanRequest();
            invalidHash["action"] = "build_from_plan";
            invalidHash["expected_plan_sha256"] = badHash;
            JObject result = Call(invalidHash);
            Check(
                !result.Value<bool>("success") && result.Value<string>("code") == "build_plan_hash_invalid" && ReadOnly(),
                "malformed guarded hash has zero effects: " + badHash
            );
        }
        Reset();
        var mobileRequest = PlanRequest();
        mobileRequest["target"] = "android";
        mobileRequest["subtarget"] = "server";
        JObject mobile = Call(mobileRequest);
        Check(
            mobile.Value<bool>("success")
                && mobile.SelectToken("data.target.subtarget")?.Value<int>() == 0
                && mobile.SelectToken("data.target.requested_subtarget")?.Value<int>() == (int)StandaloneBuildSubtarget.Server,
            "non-Standalone plan reports actual platform default and separate requested subtarget"
        );
        Check(ReadOnly(), "mobile plan does not switch/apply requested Standalone subtarget");

        foreach (
            string json in new[]
            {
                "{scenes:[]}",
                "{scenes:['Assets/Missing.unity']}",
                "{scenes:['Assets/not-scene.txt']}",
                "{target:'invalid'}",
                "{subtarget:'sevrer'}",
                "{options:['invalid']}",
                "{scripting_backend:'unknown'}",
                "{profile:'Assets/Valid.asset'}",
                "{profiles:['Assets/Valid.asset']}",
                "{targets:['windows64']}",
                "{property:'defines'}",
                "{value:''}",
                "{activate:false}",
                "{output_dir:'Builds'}",
                "{job_id:'old'}",
                "{dry_run:true}",
            }
        )
        {
            Reset();
            JObject invalid = JObject.Parse(json);
            invalid["action"] = "plan";
            JObject result = Call(invalid);
            Check(!result.Value<bool>("success") && result.SelectToken("data.plan_sha256") == null, "invalid plan has no usable hash: " + json);
            Check(ReadOnly(), "invalid plan has zero effects: " + json);
        }
        foreach (bool throws in new[] { false, true })
        {
            Reset();
            BuildPipeline.TargetSupported = false;
            BuildPipeline.ThrowSupport = throws;
            JObject result = Call(PlanRequest());
            Check(!result.Value<bool>("success") && result.SelectToken("data.plan_sha256") == null, "unsupported/unavailable module not ready");
            Check(ReadOnly(), "module failure read-only");
        }
        foreach (string field in new[] { "scenes", "options", "output_path" })
        {
            Reset();
            var invalid = PlanRequest();
            if (field == "output_path")
                invalid[field] = new string('a', 4097);
            else
                invalid[field] = new JArray(System.Linq.Enumerable.Repeat(field == "scenes" ? "Assets/First.unity" : "auto_run", field == "scenes" ? 129 : 33));
            JObject result = Call(invalid);
            Check(!result.Value<bool>("success") && result.SelectToken("data.plan_sha256") == null && ReadOnly(), "bounded plan rejects overflow: " + field);
        }

        Action[] drift =
        {
            () => PlayerSettings.productName = "Changed",
            () => PlayerSettings.companyName = "Changed",
            () => PlayerSettings.bundleVersion = "2",
            () => PlayerSettings.Identifier = "com.changed",
            () => PlayerSettings.Defines = "B",
            () => PlayerSettings.Architecture = 2,
            () => PlayerSettings.Backend = ScriptingImplementation.IL2CPP,
            () => PlayerSettings.Backend = (ScriptingImplementation)712,
            () => EditorUserBuildSettings.activeBuildTarget = BuildTarget.StandaloneLinux64,
            () =>
            {
                EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Server;
                EditorUserBuildSettings.SubtargetWrites = 0;
            },
            () => EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene("Assets/Second.unity", true) },
            () => AssetDatabase.SceneGuid = "22222222222222222222222222222222",
            () => UnityEngine.Application.dataPath = Path.GetFullPath("Other/Assets"),
            () => UnityEngine.Application.unityVersion = "changed",
        };
        foreach (bool deferred in new[] { false, true })
            for (int i = 0; i < drift.Length; i++)
            {
                Reset();
                var guarded = PlanRequest();
                string expected = PlanHash(guarded);
                guarded["action"] = "build_from_plan";
                guarded["expected_plan_sha256"] = expected;
                if (!deferred)
                    drift[i]();
                JObject result = Call(guarded);
                if (deferred)
                {
                    Check(
                        result.Value<string>("_mcp_status") == "pending" && PlayerSettings.Writes == 0,
                        "deferred guard schedules without backend write " + i
                    );
                    drift[i]();
                    RunUpdate();
                    var job = (BuildJob)Store("_buildJobs")[result.SelectToken("data.job_id").Value<string>()];
                    Check(
                        job.State == BuildJobState.Failed && job.ErrorMessage?.Contains("build_plan_stale") == true,
                        "deferred drift fails existing job " + i
                    );
                }
                else
                    Check(
                        !result.Value<bool>("success")
                            && result.Value<string>("code") == "build_plan_stale"
                            && Store("_buildJobs").Count == 0
                            && EditorApplication.update == null,
                        "immediate drift no job " + i
                    );
                Check(
                    PlayerSettings.Writes == 0 && BuildPipeline.Builds == 0 && AssetDatabase.Saves == 0,
                    "stale guard before settings/save/build " + i + deferred
                );
            }
        Reset();
        request = PlanRequest();
        request["target"] = "android";
        hash = PlanHash(request);
        EditorUserBuildSettings.buildAppBundle = true;
        Check(hash != PlanHash(request) && ReadOnly(), "Android output-mode observation participates in fingerprint");
        Reset();
        request = PlanRequest();
        hash = PlanHash(request);
        request["scenes"] = new JArray("Assets/First.unity", "Assets/Second.unity");
        Check(hash != PlanHash(request), "explicit ordered scenes/default provenance participates");
        hash = PlanHash(request);
        request["scenes"] = new JArray("Assets/Second.unity", "Assets/First.unity");
        Check(hash != PlanHash(request), "scene ordering participates");
        hash = PlanHash(request);
        request["options"] = new JArray("strict_mode");
        Check(hash != PlanHash(request), "option flags participate");
        hash = PlanHash(request);
        request["output_path"] = "Builds/Other.exe";
        Check(hash != PlanHash(request), "output participates");

        foreach (bool cancel in new[] { false, true })
        {
            Reset();
            request = PlanRequest();
            request["scenes"] = new JArray("Assets/First.unity", "Assets/Second.unity");
            hash = PlanHash(request);
            request["action"] = "build_from_plan";
            request["expected_plan_sha256"] = hash;
            JObject pending = Call(request);
            Check(
                pending.Value<string>("_mcp_status") == "pending" && PlayerSettings.Writes == 0 && BuildPipeline.Builds == 0,
                "matching guard awaits callback"
            );
            var job = (BuildJob)Store("_buildJobs")[pending.SelectToken("data.job_id").Value<string>()];
            // Caller mutation after admission cannot alter the retained request/options arrays.
            ((JArray)request["scenes"])[0] = "Assets/Missing.unity";
            ((JArray)request["options"])[0] = "invalid";
            request["scripting_backend"] = "mono";
            if (cancel)
                job.State = BuildJobState.Cancelled;
            RunUpdate();
            Check(BuildPipeline.Builds == 0 && AssetDatabase.Saves == 0, "checkpoint preparation must precede native effects " + cancel);
            RunUpdate();
            Check(
                BuildPipeline.Builds == (cancel ? 0 : 1) && PlayerSettings.Writes == (cancel ? 0 : 1) && AssetDatabase.Saves == (cancel ? 0 : 1),
                "matching/cancel effects exactly once " + cancel
            );
            if (!cancel)
            {
                Check(job.State == BuildJobState.Succeeded, "matching job succeeds through fake build: " + job.ErrorMessage);
                Check(
                    BuildPipeline.LastOptions.scenes?[0] == "Assets/First.unity" && BuildPipeline.LastOptions.options.HasFlag(BuildOptions.AutoRunPlayer),
                    "detached resolved scene/options passed unchanged"
                );
                Check(BuildPipeline.BackendAtBuild == ScriptingImplementation.IL2CPP, "backend applied after guard before build");
            }
        }
    }

    public static int Main(string[] args)
    {
        try
        {
            return RunFixture(args);
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static int RunFixture(string[] args)
    {
        outputRoot = Path.Combine(Path.GetFullPath(args[0]), "never-created-output");
        LostBatchStatus();
        Reject(JObject.Parse("{action:'batch',targets:{invalid:true},profiles:['Assets/Valid.asset']}"), "targets");
        Reset();
        var originalScenes = EditorBuildSettings.scenes;
        var invalidScenes = Call(JObject.Parse("{action:'scenes',scenes:[{path:123}]}"));
        Check(
            !invalidScenes.Value<bool>("success") && ReferenceEquals(originalScenes, EditorBuildSettings.scenes),
            "non-string scene path must not replace build settings"
        );
        disablePlanGuardControl = args.Length == 2 && args[1] == "--disable-plan-guard-control";
        foreach (
            string json in new[]
            {
                "{options:'strict_mdoe'}",
                "{options:['strict_mode','invalid']}",
                "{options:['strict_mode',123]}",
                "{options:['strict_mode',null]}",
                "{options:['strict_mode','']}",
                "{options:['strict_mode',' ']}",
                "{options:{}}",
                "{options:123}",
                "{options:''}",
                "{options:' '}",
                "{options:[[['strict_mode']]]}",
            }
        )
        {
            foreach (string action in new[] { "build", "batch" })
            {
                var request = JObject.Parse(json);
                request["action"] = action;
                request["target"] = "windows64";
                request["targets"] = new JArray("windows64");
                request["scenes"] = new JArray();
                request["scripting_backend"] = "il2cpp";
                Reject(request, "options");
            }
#if UNITY_6000_0_OR_NEWER
            foreach (string action in new[] { "build", "batch" })
            {
                var request = JObject.Parse(json);
                request["action"] = action;
                if (action == "build")
                    request["profile"] = "Assets/Valid.asset";
                else
                    request["profiles"] = new JArray("Assets/Valid.asset");
                request["scripting_backend"] = "il2cpp";
                Reject(request, "options");
            }
#endif
        }
        foreach (JToken subtarget in new JToken[] { "sevrer", "123", "", " ", 123, new JObject(), new JArray("player") })
        foreach (string action in new[] { "build", "platform" })
        foreach (string target in new[] { "windows64", "android" })
            Reject(
                new JObject
                {
                    ["action"] = action,
                    ["target"] = target,
                    ["scenes"] = new JArray(),
                    ["subtarget"] = subtarget.DeepClone(),
                    ["scripting_backend"] = "il2cpp",
                },
                "subtarget"
            );

        foreach (string name in new[] { "strict_mdoe", "", " ", null })
        {
            bool rejected = false;
            try
            {
                BuildRunner.ParseBuildOptions(new[] { "strict_mode", name }, true);
            }
            catch (ArgumentException)
            {
                rejected = true;
            }
            catch (Exception error)
            {
                Console.WriteLine("UNEXPECTED: " + error.GetType().Name);
            }
            Check(rejected, "parser rejects invalid options: " + name);
        }
        foreach (string name in new[] { "sevrer", "123", "", " " })
        {
            bool rejected = false;
            try
            {
                BuildTargetMapping.ResolveSubtarget(name);
            }
            catch (ArgumentException)
            {
                rejected = true;
            }
            catch (Exception error)
            {
                Console.WriteLine("UNEXPECTED: " + error.GetType().Name);
            }
            Check(rejected, "parser rejects invalid subtarget: " + name);
        }
        Check(BuildTargetMapping.ResolveSubtarget(null) == (int)StandaloneBuildSubtarget.Player, "omitted subtarget default");
        Check(BuildTargetMapping.ResolveSubtarget("PLAYER") == (int)StandaloneBuildSubtarget.Player, "player case insensitive");
        Check(BuildTargetMapping.ResolveSubtarget("SERVER") == (int)StandaloneBuildSubtarget.Server, "server case insensitive");
        Check(BuildRunner.ParseBuildOptions(null, true) == BuildOptions.Development, "development default");
        Check(
            BuildRunner
                .ParseBuildOptions(new[] { "STRICT_MODE", "allow_debugging", "connect_profiler", "scripts_only", "show_player", "include_tests" }, false)
                .HasFlag(BuildOptions.StrictMode),
            "supported aliases"
        );

        foreach (
            string json in new[]
            {
                "{options:null}",
                "{options:[]}",
                "{options:'STRICT_MODE'}",
                "{options:['strict_mode']}",
                "{options:[['strict_mode']]}",
                "{options:'[\"strict_mode\"]'}",
                "{options:['[\"strict_mode\"]']}",
            }
        )
        foreach (string action in new[] { "build", "batch" })
        {
            Reset();
            var request = JObject.Parse(json);
            request["action"] = action;
            request["target"] = "windows64";
            request["targets"] = new JArray("windows64");
            request["scenes"] = new JArray();
            request["output_path"] = Path.Combine(outputRoot, "Game.exe");
            request["output_dir"] = outputRoot;
            request["subtarget"] = "SERVER";
            var response = JObject.FromObject(ManageBuild.HandleCommand(request));
            Check(response.Value<bool>("success") && response.Value<string>("_mcp_status") == "pending", "valid control schedules: " + action + json);
            Check(EditorApplication.update != null && Store("_buildJobs").Count == 1, "valid control registers/schedules");
            Check(!Directory.Exists(outputRoot) && BuildPipeline.Builds == 0, "valid control awaits update");
        }
#if UNITY_6000_0_OR_NEWER
        foreach (string action in new[] { "build", "batch" })
        {
            Reset();
            var request = new JObject
            {
                ["action"] = action,
                ["options"] = new JArray("STRICT_MODE"),
                ["output_path"] = Path.Combine(outputRoot, "Game.exe"),
                ["output_dir"] = outputRoot,
                ["scripting_backend"] = "il2cpp",
            };
            if (action == "build")
                request["profile"] = "Assets/Valid.asset";
            else
                request["profiles"] = new JArray("Assets/Valid.asset");
            var response = JObject.FromObject(ManageBuild.HandleCommand(request));
            Check(response.Value<bool>("success") && response.Value<string>("_mcp_status") == "pending", "valid profile control schedules: " + response);
            Check(EditorApplication.update != null && Store("_buildJobs").Count == 1, "valid profile registers/schedules");
            Check(PlayerSettings.Writes == 0 && BuildPipeline.Builds == 0, "valid profile awaits checkpoint preparation");
        }
#endif
        foreach (JToken subtarget in new JToken[] { JValue.CreateNull(), "PLAYER", "SERVER" })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Server;
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "platform",
                        ["target"] = "android",
                        ["subtarget"] = subtarget,
                    }
                )
            );
            Check(response.Value<bool>("success") && EditorUserBuildSettings.Switches == 1, "valid platform switch");
            var expected =
                subtarget.Type == JTokenType.Null || subtarget.Value<string>() == "SERVER" ? StandaloneBuildSubtarget.Server : StandaloneBuildSubtarget.Player;
            Check(EditorUserBuildSettings.standaloneBuildSubtarget == expected, "valid platform subtarget/default preserved");
        }
        foreach (var previous in new[] { StandaloneBuildSubtarget.Player, StandaloneBuildSubtarget.Server })
        foreach (var requested in new[] { StandaloneBuildSubtarget.Player, StandaloneBuildSubtarget.Server })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = previous;
            EditorUserBuildSettings.SubtargetWrites = 0;
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "platform",
                        ["target"] = "windows64",
                        ["subtarget"] = requested.ToString(),
                    }
                )
            );
            string name = "same target " + previous + " -> " + requested;
            Check(response.Value<bool>("success"), name + " succeeds");
            Check(EditorUserBuildSettings.standaloneBuildSubtarget == requested, name + " applies requested state");
            Check(EditorUserBuildSettings.Switches == (previous == requested ? 0 : 1), name + " switches only on change");
            Check(EditorUserBuildSettings.SubtargetWrites == (previous == requested ? 0 : 1), name + " writes only on change");
            if (previous != requested)
                Check(EditorUserBuildSettings.SubtargetAtSwitch == requested, name + " supplies subtarget to switch");
        }
        foreach (string target in new[] { "windows64", "linux64" })
        foreach (bool explicitNull in new[] { false, true })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Server;
            EditorUserBuildSettings.SubtargetWrites = 0;
            var request = new JObject { ["action"] = "platform", ["target"] = target };
            if (explicitNull)
                request["subtarget"] = JValue.CreateNull();
            var response = JObject.FromObject(ManageBuild.HandleCommand(request));
            Check(response.Value<bool>("success"), "omitted/null platform subtarget succeeds");
            Check(
                EditorUserBuildSettings.standaloneBuildSubtarget == StandaloneBuildSubtarget.Server && EditorUserBuildSettings.SubtargetWrites == 0,
                "omitted/null preserves prior subtarget without writes"
            );
            Check(EditorUserBuildSettings.Switches == (target == "windows64" ? 0 : 1), "omitted/null preserves target switch behavior");
        }
        foreach (var previous in new[] { StandaloneBuildSubtarget.Player, StandaloneBuildSubtarget.Server })
        foreach (bool throwOnSwitch in new[] { false, true })
        foreach (string target in new[] { "windows64", "linux64" })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = previous;
            EditorUserBuildSettings.SubtargetWrites = 0;
            EditorUserBuildSettings.SwitchSucceeds = false;
            EditorUserBuildSettings.ThrowOnSwitch = throwOnSwitch;
            var requested = previous == StandaloneBuildSubtarget.Player ? StandaloneBuildSubtarget.Server : StandaloneBuildSubtarget.Player;
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "platform",
                        ["target"] = target,
                        ["subtarget"] = requested.ToString(),
                    }
                )
            );
            string name = "failed switch " + target + " " + previous + " -> " + requested + " throws=" + throwOnSwitch;
            Check(!response.Value<bool>("success"), name + " rejects");
            Check(EditorUserBuildSettings.Switches == 1 && EditorUserBuildSettings.SubtargetAtSwitch == requested, name + " attempts requested switch");
            Check(EditorUserBuildSettings.standaloneBuildSubtarget == previous, name + " restores prior subtarget");
            Check(
                PlayerSettings.Writes == 0 && Store("_buildJobs").Count == 0 && Store("_batchJobs").Count == 0 && EditorApplication.update == null,
                name + " leaves unrelated state unchanged"
            );
            Check(BuildPipeline.Builds == 0 && !Directory.Exists(outputRoot), name + " creates no build/output");
        }
        foreach (var previous in new[] { StandaloneBuildSubtarget.Player, StandaloneBuildSubtarget.Server })
        foreach (bool explicitNull in new[] { false, true })
        foreach (bool throwOnSwitch in new[] { false, true })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = previous;
            EditorUserBuildSettings.SubtargetWrites = 0;
            EditorUserBuildSettings.SwitchSucceeds = false;
            EditorUserBuildSettings.ThrowOnSwitch = throwOnSwitch;
            var request = new JObject { ["action"] = "platform", ["target"] = "linux64" };
            if (explicitNull)
                request["subtarget"] = JValue.CreateNull();
            var response = JObject.FromObject(ManageBuild.HandleCommand(request));
            Check(!response.Value<bool>("success"), "failed switch with omitted/null subtarget rejects");
            Check(EditorUserBuildSettings.Switches == 1, "failed switch with omitted/null subtarget attempts target switch");
            Check(
                EditorUserBuildSettings.standaloneBuildSubtarget == previous && EditorUserBuildSettings.SubtargetWrites == 0,
                "failed switch with omitted/null subtarget preserves prior state without writes"
            );
        }
        foreach (string target in new[] { "windows64", "linux64", "invalid" })
        {
            Reset();
            BuildPipeline.TargetSupported = false;
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "platform",
                        ["target"] = target,
                        ["subtarget"] = "server",
                    }
                )
            );
            Check(!response.Value<bool>("success"), "unsupported/invalid target rejects before mutation");
            Check(
                EditorUserBuildSettings.Switches == 0 && EditorUserBuildSettings.SubtargetWrites == 0,
                "unsupported/invalid target has no switch/subtarget writes"
            );
        }
        Reset();
        PlanContracts();
        Reset();
        Console.WriteLine("BUILD_PREFLIGHT: " + checks + " checks, " + failures + " failures");
        return failures == 0 ? 0 : 1;
    }
}

namespace UnityEngine
{
    public class Object { }

    public class ScriptableObject : Object { }

    public static class Application
    {
        public static string dataPath = Path.GetFullPath("Assets");
        public static string unityVersion = "synthetic";
    }

    public static class Debug
    {
        public static void Log(object value) { }

        public static void LogError(object value) { }

        public static void LogWarning(object value) { }
    }
}

namespace UnityEngine.SceneManagement
{
    public struct Scene
    {
        public bool isDirty;
        public string path;
        public string name;
    }

    public static class SceneManager
    {
        public static int sceneCount => 0;

        public static Scene GetSceneAt(int i) => default;
    }
}

namespace UnityEditor
{
    public enum BuildTarget
    {
        NoTarget = -2,
        StandaloneWindows,
        StandaloneWindows64,
        StandaloneOSX,
        StandaloneLinux64,
        Android,
        iOS,
        WebGL,
        WSAPlayer,
        tvOS,
        VisionOS,
    }

    public enum BuildTargetGroup
    {
        Unknown,
        Standalone,
        Android,
        iOS,
        WebGL,
        WSA,
        tvOS,
        VisionOS,
    }

    public enum StandaloneBuildSubtarget
    {
        Player,
        Server,
    }

    public enum ScriptingImplementation
    {
        Mono2x,
        IL2CPP,
    }

    [Flags]
    public enum BuildOptions
    {
        None = 0,
        Development = 1,
        CleanBuildCache = 2,
        AutoRunPlayer = 4,
        EnableDeepProfilingSupport = 8,
        CompressWithLz4 = 16,
        StrictMode = 32,
        DetailedBuildReport = 64,
        AllowDebugging = 128,
        ConnectWithProfiler = 256,
        BuildScriptsOnly = 512,
        ShowBuiltPlayer = 1024,
        IncludeTestAssemblies = 2048,
    }

    public struct BuildPlayerOptions
    {
        public BuildTarget target;
        public BuildTargetGroup targetGroup;
        public string locationPathName;
        public string[] scenes;
        public BuildOptions options;
        public int subtarget;
        public string assetBundleManifestPath;
        public string[] extraScriptingDefines;
    }

#if UNITY_6000_0_OR_NEWER
    public struct BuildPlayerWithProfileOptions
    {
        public Build.Profile.BuildProfile buildProfile { get; set; }
        public string locationPathName { get; set; }
        public string assetBundleManifestPath { get; set; }
        public BuildOptions options { get; set; }
    }
#endif

    public sealed class InitializeOnLoadAttribute : Attribute { }

    public static class EditorApplication
    {
        public static Action update;
        public static Action delayCall;
        public static bool isPlayingOrWillChangePlaymode;
        public static bool isCompiling;
        public static bool isUpdating;
    }

    public static class SessionState
    {
        private static readonly Dictionary<string, object> Values = new();

        public static bool GetBool(string key, bool fallback) => Values.TryGetValue(key, out var value) ? (bool)value : fallback;

        public static string GetString(string key, string fallback) => Values.TryGetValue(key, out var value) ? (string)value : fallback;

        public static void SetBool(string key, bool value) => Values[key] = value;

        public static void SetString(string key, string value) => Values[key] = value;

        internal static void Clear() => Values.Clear();
    }

    public static class EditorUserBuildSettings
    {
        public static BuildTarget activeBuildTarget = BuildTarget.StandaloneWindows64;
        private static StandaloneBuildSubtarget _standaloneBuildSubtarget;
        public static StandaloneBuildSubtarget standaloneBuildSubtarget
        {
            get => _standaloneBuildSubtarget;
            set
            {
                SubtargetWrites++;
                _standaloneBuildSubtarget = value;
            }
        }
        public static int SubtargetWrites;
        public static StandaloneBuildSubtarget SubtargetAtSwitch;
        public static bool buildAppBundle;
        public static int Switches;
        public static bool SwitchSucceeds = true;
        public static bool ThrowOnSwitch;

        public static bool SwitchActiveBuildTarget(BuildTargetGroup group, BuildTarget target)
        {
            Switches++;
            SubtargetAtSwitch = standaloneBuildSubtarget;
            if (ThrowOnSwitch)
                throw new InvalidOperationException("Synthetic platform switch failure");
            if (!SwitchSucceeds)
                return false;
            activeBuildTarget = target;
            return true;
        }
    }

    public static class PlayerSettings
    {
        public static string productName = "Game";
        public static string companyName = "Company";
        public static string bundleVersion = "1.0";
        public static string Identifier = "com.fixture.game";
        public static string Defines = "FIXTURE;A";
        public static int Architecture;
        public static ScriptingImplementation Backend;

        public static ScriptingImplementation GetScriptingBackend(Build.NamedBuildTarget target) => Backend;

        public static string GetApplicationIdentifier(Build.NamedBuildTarget target) => Identifier;

        public static string GetScriptingDefineSymbols(Build.NamedBuildTarget target) => Defines;

        public static int GetArchitecture(Build.NamedBuildTarget target) => Architecture;

        public static int Writes;

        public static void SetScriptingBackend(Build.NamedBuildTarget target, ScriptingImplementation backend)
        {
            Writes++;
            Backend = backend;
        }
    }

    public static class BuildPipeline
    {
        public static bool isBuildingPlayer;
        public static int Builds;

        public static bool TargetSupported = true;
        public static bool ThrowSupport;
        public static BuildPlayerOptions LastOptions;
        public static ScriptingImplementation BackendAtBuild;

        public static bool IsBuildTargetSupported(BuildTargetGroup group, BuildTarget target)
        {
            if (ThrowSupport)
                throw new InvalidOperationException("Synthetic unavailable module observation");
            return TargetSupported;
        }

        public static BuildTargetGroup GetBuildTargetGroup(BuildTarget target) => MCPForUnity.Editor.Tools.Build.BuildTargetMapping.GetTargetGroup(target);

        public static Build.Reporting.BuildReport BuildPlayer(BuildPlayerOptions options)
        {
            Builds++;
            LastOptions = options;
            BackendAtBuild = PlayerSettings.Backend;
            return new Build.Reporting.BuildReport
            {
                summary = new Build.Reporting.BuildSummary
                {
                    result = Build.Reporting.BuildResult.Succeeded,
                    platform = options.target,
                    outputPath = options.locationPathName,
                },
            };
        }

#if UNITY_6000_0_OR_NEWER
        public static Build.Reporting.BuildReport BuildPlayer(BuildPlayerWithProfileOptions options)
        {
            Builds++;
            throw new InvalidOperationException("Native profile build forbidden");
        }
#endif
    }

    public class SceneAsset : UnityEngine.Object { }

    public class EditorBuildSettingsScene
    {
        public string path;
        public bool enabled;
        public Guid guid;

        public EditorBuildSettingsScene(string p, bool e)
        {
            path = p;
            enabled = e;
        }
    }

    public static class EditorBuildSettings
    {
        public static EditorBuildSettingsScene[] scenes = Array.Empty<EditorBuildSettingsScene>();
        public static EditorBuildSettingsScene[] globalScenes => scenes;
    }

    public static class AssetDatabase
    {
        public static T LoadAssetAtPath<T>(string path)
            where T : UnityEngine.Object => (T)LoadAssetAtPath(path, typeof(T));

        public static UnityEngine.Object LoadAssetAtPath(string path, Type type)
        {
            if (type == typeof(SceneAsset) && (path == "Assets/First.unity" || path == "Assets/Second.unity"))
                return new SceneAsset();
#if UNITY_6000_0_OR_NEWER
            if (type == typeof(Build.Profile.BuildProfile) && path == "Assets/Valid.asset")
                return new Build.Profile.BuildProfile();
#endif
            return null;
        }

        public static int Saves;
        public static string SceneGuid = "11111111111111111111111111111111";

        public static string AssetPathToGUID(string path) => SceneGuid;

        public static string GUIDToAssetPath(string guid) => guid == SceneGuid ? "Assets/Valid.asset" : "";

        public static string GetAssetDependencyHash(string path) => "22222222222222222222222222222222";

        public static void SaveAssets()
        {
            Saves++;
        }

        public static string[] FindAssets(string filter, string[] folders = null) => Array.Empty<string>();

        public static string GetAssetPath(UnityEngine.Object obj) => "Assets/Valid.asset";
    }

    public sealed class SerializedObject : IDisposable
    {
        public SerializedObject(UnityEngine.Object obj) { }

        public SerializedProperty FindProperty(string name) =>
            new SerializedProperty { intValue = name == "m_BuildTarget" ? (int)BuildTarget.StandaloneWindows64 : (int)StandaloneBuildSubtarget.Player };

        public void Dispose() { }
    }

    public sealed class SerializedProperty : IDisposable
    {
        public int intValue;

        public void Dispose() { }
    }
}

namespace UnityEditor.Build
{
    public struct NamedBuildTarget
    {
        public static NamedBuildTarget Server => new NamedBuildTarget { TargetName = "Dedicated Server" };
        public string TargetName { get; private set; }

        public static NamedBuildTarget FromBuildTargetGroup(UnityEditor.BuildTargetGroup group) => new NamedBuildTarget { TargetName = group.ToString() };
    }
}

#if UNITY_6000_0_OR_NEWER
namespace UnityEditor.Build.Profile
{
    public class BuildProfile : UnityEngine.ScriptableObject
    {
        public UnityEditor.EditorBuildSettingsScene[] scenes { get; } = Array.Empty<UnityEditor.EditorBuildSettingsScene>();
        public bool overrideGlobalScenes => false;

        public static BuildProfile GetActiveBuildProfile() => null;

        public static void SetActiveBuildProfile(BuildProfile profile) { }
    }
}
#endif

namespace UnityEditor.Build.Reporting
{
    public enum BuildResult
    {
        Succeeded,
        Failed,
    }

    public class BuildSummary
    {
        public UnityEditor.BuildTarget platform;
        public string outputPath;
        public TimeSpan totalTime;
        public ulong totalSize;
        public int totalErrors;
        public int totalWarnings;
        public BuildResult result;
    }

    public class BuildReport
    {
        public static BuildReport GetLatestReport() => null;

        public BuildSummary summary;

        public string SummarizeErrors() => "synthetic failure";
    }
}

namespace UnityEditor.SceneManagement
{
    public static class EditorSceneManager
    {
        public static bool SaveScene(UnityEngine.SceneManagement.Scene scene) => true;
    }
}

namespace MCPForUnity.Editor.Helpers
{
    internal static class EditorStateCache
    {
        internal static bool IsDomainReloadPending => false;
    }

    public static class McpLog
    {
        public static void Warn(string message) { }
    }

    public static class AssetPathUtility
    {
        public static string GetContainedAssetPath(string path) => path;

        public static string GetAssetPathFromGuid(string guid) => guid;
    }
}

namespace MCPForUnity.Editor.Services
{
    internal static class OperationActivity
    {
        internal static void Invalidate() { }
    }

    internal sealed class OperationActivityRow
    {
        public string Owner;
        public string Id;
        public string Operation;
        public string State;
        public string Phase;
        public bool Active;
        public long Started;
        public long? Finished;
        public string Detail;
        public string Cancellation;
        public bool CanCancel;
        public string EvidenceId;
    }
}

namespace MCPForUnity.Editor.Services.Transport
{
    internal readonly struct NativeAdmissionBlocker
    {
        internal string Reason => null;
    }

    internal static class NativeOperationBarriers
    {
        internal static bool TryGetBuildContinuationBlocker(string jobId, string batchId, out NativeAdmissionBlocker blocker)
        {
            blocker = default;
            return false;
        }
    }
}

namespace MCPForUnity.Editor.Tools
{
    [AttributeUsage(AttributeTargets.Class)]
    public sealed class McpForUnityToolAttribute : Attribute
    {
        public McpForUnityToolAttribute(string name) { }

        public bool AutoRegister { get; set; }
        public bool RequiresExplicitConsent { get; set; }
        public string Group { get; set; }
        public bool RequiresPolling { get; set; }
        public string PollAction { get; set; }
        public int MaxPollSeconds { get; set; }
    }
}

namespace MCPForUnity.Editor.Tools.Build
{
    internal static class BuildConditionCapture
    {
        internal static UnityEditor.Build.Reporting.BuildReport Invoke(BuildJob job, Func<UnityEditor.Build.Reporting.BuildReport> build, out JObject result)
        {
            result = null;
            return build();
        }
    }

    internal static class BuildReportStore
    {
        internal static bool IsAction(string action) => false;

        internal static bool HasOptions(JObject request) => false;

        internal static object Handle(JObject request) => throw new InvalidOperationException("Report queries are outside this fixture");

        internal static object FindStatus(string jobId = null) => null;

        internal static Func<UnityEditor.Build.Reporting.BuildReport> GetLatestReport => null;
        internal static Func<UnityEditor.Build.Reporting.BuildReport, string> SummarizeErrors => null;

        internal static void Observe(BuildJob job) { }

        internal static void Observe(BatchJob job) { }

        internal static void Complete(BuildJob job, UnityEditor.Build.Reporting.BuildReport report = null) { }

        internal static void CompleteWithConditions(BuildJob job, UnityEditor.Build.Reporting.BuildReport report, JObject conditions) { }

        internal static void Complete(BatchJob job) { }
    }

    public static class BuildOutputCleaner
    {
        public static object Clean(string root, string path, bool dryRun, bool busy) => throw new InvalidOperationException("Unused seam");
    }

    public static class BuildSettingsHelper
    {
        public static string[] ValidProperties = Array.Empty<string>();

        public static object ReadProperty(string property, UnityEditor.Build.NamedBuildTarget target) => throw new InvalidOperationException("Unused seam");

        public static string WriteProperty(string property, string value, UnityEditor.Build.NamedBuildTarget target) =>
            throw new InvalidOperationException("Unused seam");
    }
}
