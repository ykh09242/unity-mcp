using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

public static class VerifiedWorkflowUpgrade
{
    private const string ScenarioName = "stable-compatibility";
    private const string ProfileName = "stable-upgrade";
    private const string JobId = "0123456789abcdef0123456789abcdef";

    public static void Run()
    {
        string project = Path.GetDirectoryName(Application.dataPath);
        string[] args = Environment.GetCommandLineArgs();
        string session = ReadArgument(args, "--upgrade-session");
        if (!Application.isBatchMode || !Guid.TryParseExact(session, "N", out _) || File.ReadAllText(Path.Combine(project, ".upgrade-owner")) != session)
            throw new InvalidOperationException("The upgrade probe requires an owned disposable project.");

        var store = new PlayScenarioStore(project);
        string mode = ReadArgument(args, "--upgrade-mode");
        if (mode == "produce")
        {
            var scenario = PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = ScenarioName,
                    ["poll_interval_ms"] = 375,
                    ["steps"] = new JArray(
                        new JObject
                        {
                            ["action"] = "load_scene",
                            ["name"] = "Open app",
                            ["scene"] = "Assets/UpgradeApp.unity",
                        },
                        new JObject
                        {
                            ["action"] = "wait_object",
                            ["name"] = "App ready",
                            ["target"] = "UpgradeReady",
                        }
                    ),
                }
            );
            store.Save(scenario);
            ReadProfile(
                new JObject
                {
                    ["action"] = "set_profile",
                    ["profile_id"] = ProfileName,
                    ["expected_hash"] = "missing",
                    ["profile"] = new JObject
                    {
                        ["refresh_policy"] = "never",
                        ["analysis_required"] = false,
                        ["test_mode"] = "EditMode",
                    },
                }
            );
            store.SaveReport(
                new PlayScenarioRun
                {
                    JobId = JobId,
                    Scenario = scenario,
                    RepeatCount = 1,
                    Status = "failed",
                    Phase = "finished",
                    StartedUnixMs = 100,
                    DeadlineUnixMs = 1000,
                    FinishedUnixMs = 500,
                    Error = "Fixture target was missing.",
                    Reproduction = new PlayScenarioReproduction
                    {
                        DefinitionHash = PlayScenarioReproduction.Hash(scenario),
                        UnityVersion = Application.unityVersion,
                    },
                }
            );
        }
        else if (mode != "observe")
            throw new ArgumentException("Unknown upgrade mode.");

        var report = store.GetReport(JobId);
        var saved = store.Get(ScenarioName);
        var profile = ReadProfile(new JObject { ["action"] = "profiles" });
        var data = new JObject
        {
            ["session"] = session,
            ["unity_version"] = Application.unityVersion,
            ["package_version"] = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(PlayScenarioStore).Assembly)?.version,
            ["scenario"] = JObject.FromObject(saved),
            ["scenario_names"] = new JArray(store.List()),
            ["profile"] = profile,
            ["report"] = JObject.FromObject(report),
            ["report_ids"] = new JArray(store.ListReports(ScenarioName).Select(value => value.JobId)),
        };
        using var stream = new FileStream(ReadArgument(args, "--upgrade-evidence"), FileMode.CreateNew, FileAccess.Write);
        using var writer = new StreamWriter(stream);
        writer.WriteLine(data.ToString(Formatting.Indented));
    }

    private static JObject ReadProfile(JObject input)
    {
        JObject response = JObject.FromObject(ManageVerification.HandleCommand(input));
        if ((bool?)response["success"] != true || !(response["data"] is JObject data))
            throw new InvalidOperationException("Verification profile request failed: " + response.ToString(Formatting.None));
        return data;
    }

    private static string ReadArgument(string[] args, string option)
    {
        int index = Array.IndexOf(args, option);
        if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, option) != index)
            throw new ArgumentException("Expected one value for " + option);
        return args[index + 1];
    }
}
