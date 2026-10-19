#if MCP_FOR_UNITY_PLAY_SCENARIOS && !UNITY_EDITOR
using System;
using System.IO;
using System.Diagnostics;
using Debug = UnityEngine.Debug;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Compiled and activated only by an explicit scenario test build.</summary>
    public sealed class PlayScenarioPlayerRunner : MonoBehaviour
    {
        private PlayScenarioPlayerBundle bundle;
        private PlayScenarioPlayerRequest request;
        private PlayScenarioRun run;
        private PlayScenarioEngine engine;
        private PlayScenarioLogBuffer logs;
        private string directory;
        private bool subscribed;
        private bool finished;
        private int processedUnexpectedLogs;
        private PlayScenarioPlayerProgress progress;
        private readonly Stopwatch elapsed = new Stopwatch();
        private long mainLoopSequence;
        private string progressError;
        private PlayScenarioPlayerSoakJournal soak;
        private string soakStopStatus;
        private string soakStopError;
        private bool iterationRecorded;
        private long checkpointBytes;
        private PlayScenarioEnvironment environment;
        private readonly PlayScenarioPlayerPersistence persistence = new PlayScenarioPlayerPersistence();
        private static long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void Bootstrap()
        {
            var owner = new GameObject("MCP Scenario Player Runner");
            DontDestroyOnLoad(owner);
            owner.AddComponent<PlayScenarioPlayerRunner>();
        }

        private void Start()
        {
            Application.runInBackground = true;
            elapsed.Start();
            try
            {
                string[] arguments = Environment.GetCommandLineArgs();
                string requestPath = null;
                for (int index = 0; index < arguments.Length; index++)
                {
                    if (arguments[index] != "--mcp-scenario-request")
                        continue;
                    if (requestPath != null || index + 1 >= arguments.Length)
                        throw new ArgumentException("Provide exactly one --mcp-scenario-request file.");
                    requestPath = arguments[++index];
                }
                requestPath = PlayScenarioPlayerFiles.CheckedAbsolute(requestPath);
                directory = Path.GetDirectoryName(requestPath);
                if (
                    File.Exists(Path.Combine(directory, "run.json"))
                    || File.Exists(Path.Combine(directory, "run.json.tmp"))
                    || File.Exists(Path.Combine(directory, "cancel"))
                    || File.Exists(Path.Combine(directory, "progress.json"))
                    || File.Exists(Path.Combine(directory, "progress.json.tmp"))
                )
                    throw new IOException("Player run directory contains stale output or cancellation evidence.");
                request = PlayScenarioPlayerRequest.Parse(PlayScenarioPlayerFiles.Read(requestPath, 4096));
                environment = new PlayScenarioEnvironmentCapture().CaptureOnce();
                TextAsset asset = Resources.Load<TextAsset>(PlayScenarioPlayerBundle.ResourceName);
                if (asset == null)
                    throw new ArgumentException("Embedded Player scenario bundle is missing.");
                try
                {
                    bundle = PlayScenarioPlayerBundle.Parse(asset.text);
                }
                finally
                {
                    Resources.UnloadAsset(asset);
                }
                bundle.SelectChild(request.ScenarioName, request.DefinitionHash);
                run = PlayScenarioEngine.Create(bundle.Definition, request.JobId, request.RepeatCount, request.TimeoutSeconds, Now);
                run.ExecutionEnvironment = "player";
                run.Reproduction.UnityVersion = Application.unityVersion;
                run.Reproduction.PackageVersion = bundle.PackageVersion;
                run.Reproduction.SourceRevision = request.SourceRevision;
                run.Reproduction.Environment = environment;
                request.ValidateBundle(bundle);
                if (request.SchemaVersion == 2 && PlayScenarioPlayerFiles.IsWithin(Path.GetDirectoryName(Application.dataPath), directory))
                    throw new ArgumentException("Version-2 Player requests and reports must be outside the immutable build root.");
                int processId;
                using (Process process = Process.GetCurrentProcess())
                    processId = process.Id;
                progress = new PlayScenarioPlayerProgress(directory, request, bundle, processId);
                try
                {
                    progress.PublishInitial(run);
                }
                catch (Exception error)
                {
                    RecordProgressError(error);
                    throw;
                }
                PlayScenarioPlayerCapabilities.Validate(bundle.Definition, PlayScenarioPlayerInput.Backend != null);
                foreach (string path in bundle.ScenePaths)
                {
                    bool included = false;
                    for (int index = 0; index < SceneManager.sceneCountInBuildSettings; index++)
                        included |= SceneUtility.GetScenePathByBuildIndex(index) == path;
                    if (!included)
                        throw new ArgumentException("Frozen Player scene is absent from the executable: " + path);
                }
                if (request.Soak != null)
                    soak = new PlayScenarioPlayerSoakJournal(directory, request, bundle, processId, run.StartedUnixMs, persistence);
                StartEngine();
            }
            catch (Exception error)
            {
                StartupFailure(error);
            }
        }

        private void StartEngine()
        {
            iterationRecorded = false;
            processedUnexpectedLogs = 0;
            logs = new PlayScenarioLogBuffer(bundle.Definition.LogPolicy);
            Application.logMessageReceivedThreaded += OnLog;
            subscribed = true;
            engine = new PlayScenarioEngine(
                run,
                new PlayScenarioPlayerHost(bundle.ScenePaths, () => subscribed ? 1 : 0, bundle.Definition.UsesExplicitSceneSemantics),
                onEvaluationCompleted: Drain,
                stepObserver: new PlayScenarioStepObserver(SaveCheckpoint)
            );
            engine.EnteredPlayMode();
        }

        private string SaveCheckpoint(PlayScenarioStepResult step, byte[] png)
        {
            PlayScenarioDefinition.ValidateName(step.Screenshot.Name);
            string filename =
                "checkpoint-"
                + (soak?.CurrentIteration ?? 1)
                + "-"
                + step.Iteration
                + "-"
                + step.Stage
                + "-"
                + step.StepIndex
                + "-"
                + step.Screenshot.Name
                + ".png";
            string path = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, filename));
            if (png == null || png.Length == 0 || png.Length > 4 * 1024 * 1024 || checkpointBytes + png.Length > 64 * 1024 * 1024)
                throw new IOException("Checkpoint PNG exceeds the four-MiB image or 64-MiB session artifact budget.");
            using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                stream.Write(png, 0, png.Length);
                stream.Flush(true);
            }
            checkpointBytes += png.Length;
            return filename;
        }

        private void OnLog(string message, string stack, LogType type) => logs?.Add(Now, type.ToString(), message, stack);

        private void Drain() => PlayScenarioPlayerLogDrain.Apply(logs, engine, ref processedUnexpectedLogs, Now);

        private void Update()
        {
            if (finished || engine == null)
                return;
            try
            {
                Drain();
                if (File.Exists(PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, "cancel"))))
                {
                    soakStopStatus = "cancelled";
                    soakStopError = "Cancelled by caller.";
                    engine.Cancel(Now);
                }
                if (soak != null && soak.DeadlineReached(Now) && soakStopStatus == null)
                {
                    soakStopStatus = "timed_out";
                    soakStopError = "Player soak exceeded its whole-session wall-clock deadline.";
                    engine.Cancel(Now);
                }
                engine.Tick(Now, true);
                Drain();
                mainLoopSequence++;
                try
                {
                    if (progress.PublishMainLoop(run, mainLoopSequence, Now, elapsed.ElapsedMilliseconds, soak?.CurrentIteration))
                        Debug.Log("PLAYER_MAIN_LOOP_HEARTBEAT:" + mainLoopSequence);
                }
                catch (Exception error)
                {
                    RecordProgressError(error);
                    engine.Interrupt(progressError, Now);
                    Complete(2);
                    return;
                }
                if (!engine.Running)
                {
                    if (soak != null)
                        CompleteSoakIteration();
                    else
                        Complete(PlayScenarioPlayerLogDrain.ExitCode(run));
                }
            }
            catch (Exception error)
            {
                engine.Interrupt("Player runner failed: " + error.GetType().Name + ": " + error.Message, Now);
                soakStopStatus = soakStopStatus ?? "failed";
                soakStopError = soakStopError ?? PlayScenarioEngine.Bounded(error.GetType().Name + ": " + error.Message, 2048);
                Complete(2);
            }
        }

        private void RecordProgressError(Exception error)
        {
            progressError =
                progressError ?? PlayScenarioEngine.Bounded("Player progress publication failed: " + error.GetType().Name + ": " + error.Message, 2048);
            if (run == null)
                return;
            run.ReportError = run.ReportError ?? progressError;
            if (engine == null || !engine.Running)
            {
                if (run.Status == "running" || run.Status == "succeeded")
                    run.Status = "failed";
                run.Error = run.Error ?? progressError;
                run.Failure = run.Failure ?? new PlayScenarioFailure { Code = "player_progress_failed", Message = progressError };
                run.FinishedUnixMs = Now;
            }
        }

        private void StartupFailure(Exception error)
        {
            string message = PlayScenarioEngine.Bounded(error.GetType().Name + ": " + error.Message, 4096);
            if (run == null && request != null)
            {
                run = new PlayScenarioRun
                {
                    JobId = request.JobId,
                    RepeatCount = request.RepeatCount,
                    StartedUnixMs = Now,
                    DeadlineUnixMs = Now + request.TimeoutSeconds * 1000L,
                    ExecutionEnvironment = "player",
                    Reproduction = new PlayScenarioReproduction
                    {
                        DefinitionHash = request.DefinitionHash,
                        SourceRevision = request.SourceRevision,
                        UnityVersion = Application.unityVersion,
                        Environment = environment,
                    },
                };
            }
            if (run != null)
            {
                run.Status = "failed";
                run.Phase = "finished";
                run.Error = run.Error ?? message;
                run.FinishedUnixMs = Now;
                run.RunnerResourcesReleased = true;
                run.Failure =
                    run.Failure
                    ?? (
                        error is PlayScenarioException scenario
                            ? scenario.Failure
                            : new PlayScenarioFailure { Code = "player_preflight_failed", Message = message }
                    );
                foreach (PlayScenarioStepResult step in run.Steps)
                {
                    step.Status = "skipped";
                    step.FinishedUnixMs = run.FinishedUnixMs;
                }
                if (run.IterationResults != null)
                    foreach (PlayScenarioIterationResult iteration in run.IterationResults)
                    {
                        iteration.Status = "skipped";
                        iteration.FinishedUnixMs = run.FinishedUnixMs;
                    }
            }
            Complete(2);
        }

        private void Close()
        {
            if (subscribed)
            {
                Application.logMessageReceivedThreaded -= OnLog;
                subscribed = false;
            }
            logs?.Close();
            Drain();
            engine?.Release();
            if (run != null)
                run.RunnerResourcesReleased = run.RunnerResourcesReleased != false && !subscribed;
        }

        private void CompleteSoakIteration()
        {
            Close();
            int iteration = soak.CurrentIteration;
            bool continueRunning;
            try
            {
                continueRunning = soak.Append(run, Report(PlayScenarioPlayerLogDrain.ExitCode(run)));
            }
            finally
            {
                iterationRecorded = soak.CurrentIteration > iteration;
            }
            if (soakStopStatus == null && soak.DeadlineReached(Now))
            {
                soakStopStatus = "timed_out";
                soakStopError = "Player soak exceeded its whole-session wall-clock deadline.";
            }
            if (!continueRunning && soakStopStatus == null && (run.Status == "cancelled" || run.Status == "timed_out"))
            {
                soakStopStatus = run.Status;
                soakStopError = run.Error;
            }
            if (!continueRunning || soakStopStatus != null)
            {
                Complete(PlayScenarioPlayerLogDrain.ExitCode(run));
                return;
            }
            run = PlayScenarioEngine.Create(bundle.Definition, request.JobId, 1, request.TimeoutSeconds, Now);
            run.ExecutionEnvironment = "player";
            run.Reproduction.UnityVersion = Application.unityVersion;
            run.Reproduction.PackageVersion = bundle.PackageVersion;
            run.Reproduction.SourceRevision = request.SourceRevision;
            run.Reproduction.Environment = environment;
            StartEngine();
        }

        private JObject Report(int exitCode)
        {
            if (run == null || directory == null)
                throw new InvalidOperationException("Player startup identity is unavailable; no report can be attributed.");
            if (run.Status == "running")
                throw new InvalidOperationException("A running Player cannot export a completed report.");
            run.ReportPath = "run.json";
            JObject report = JObject.FromObject(
                run,
                JsonSerializer.Create(new JsonSerializerSettings { NullValueHandling = NullValueHandling.Include, TypeNameHandling = TypeNameHandling.None })
            );
            request.AddReproduction(report, bundle);
            report["progress_error"] = progressError;
            report["finalization_state"] = "completed";
            report["exit_code"] = exitCode;
            report["timeout_seconds"] = request.TimeoutSeconds;
            return report;
        }

        private void Complete(int exitCode)
        {
            if (finished)
                return;
            finished = true;
            Close();
            if (exitCode == 0)
                exitCode = PlayScenarioPlayerLogDrain.ExitCode(run);
            try
            {
                JObject report = null;
                persistence.Execute("final_report", soak?.CurrentIteration, () => report = Report(exitCode));
                if (soak != null)
                {
                    try
                    {
                        if (!iterationRecorded && !persistence.Failed)
                        {
                            int iteration = soak.CurrentIteration;
                            try
                            {
                                soak.Append(run, report);
                            }
                            finally
                            {
                                iterationRecorded = soak.CurrentIteration > iteration;
                            }
                        }
                        JObject summary = soak.Finish(
                            exitCode == 2 ? "failed" : soakStopStatus,
                            soakStopError ?? progressError,
                            run.RunnerResourcesReleased == true,
                            Now
                        );
                        exitCode =
                            exitCode == 2 || persistence.Failed ? 2
                            : (string)summary["status"] == "succeeded" ? 0
                            : 1;
                        report["soak"] = summary;
                    }
                    catch (Exception error)
                    {
                        persistence.Record("journal_verify", null, error);
                        exitCode = 2;
                    }
                    if (soak.SelectedReportFile != null)
                    {
                        try
                        {
                            JObject selected = soak.SelectedReport();
                            selected["soak"] = report["soak"];
                            report = selected;
                        }
                        catch (Exception error)
                        {
                            persistence.Record("final_report", null, error);
                            exitCode = 2;
                        }
                    }
                    report["exit_code"] = exitCode;
                }
                persistence.Attach(report);
                persistence.Execute(
                    "final_report",
                    null,
                    () => PlayScenarioPlayerFiles.WriteNew(Path.Combine(directory, "run.json"), report.ToString(Formatting.None), 2 * 1024 * 1024)
                );
            }
            catch (Exception error)
            {
                persistence.Record("final_report", null, error);
                exitCode = 2;
            }
            finally
            {
                soak?.Dispose();
                if (persistence.Failed)
                {
                    Debug.LogError(persistence.Message);
                    exitCode = 2;
                }
            }
            Application.Quit(exitCode);
        }

        private void OnDestroy()
        {
            Close();
            soak?.Dispose();
        }
    }
}
#endif
