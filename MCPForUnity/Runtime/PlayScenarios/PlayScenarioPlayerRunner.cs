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
                run = PlayScenarioEngine.Create(bundle.Definition, request.JobId, request.RepeatCount, request.TimeoutSeconds, Now);
                run.ExecutionEnvironment = "player";
                run.Reproduction.UnityVersion = Application.unityVersion;
                run.Reproduction.PackageVersion = bundle.PackageVersion;
                run.Reproduction.SourceRevision = request.SourceRevision;
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
                logs = new PlayScenarioLogBuffer(bundle.Definition.LogPolicy);
                Application.logMessageReceivedThreaded += OnLog;
                subscribed = true;
                engine = new PlayScenarioEngine(run, new PlayScenarioPlayerHost(bundle.ScenePaths), onEvaluationCompleted: Drain);
                engine.EnteredPlayMode();
            }
            catch (Exception error)
            {
                StartupFailure(error);
            }
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
                    engine.Cancel(Now);
                engine.Tick(Now, true);
                Drain();
                mainLoopSequence++;
                try
                {
                    if (progress.PublishMainLoop(run, mainLoopSequence, Now, elapsed.ElapsedMilliseconds))
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
                    Complete(PlayScenarioPlayerLogDrain.ExitCode(run));
            }
            catch (Exception error)
            {
                engine.Interrupt("Player runner failed: " + error.GetType().Name + ": " + error.Message, Now);
                Complete(1);
            }
        }

        private void RecordProgressError(Exception error)
        {
            progressError = PlayScenarioEngine.Bounded("Player progress publication failed: " + error.GetType().Name + ": " + error.Message, 2048);
            if (run == null)
                return;
            run.ReportError = run.ReportError ?? progressError;
            if (engine == null || !engine.Running)
            {
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
                    },
                };
            }
            if (run != null)
            {
                run.Status = "failed";
                run.Phase = "finished";
                run.Error = message;
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
                if (run == null || directory == null)
                    throw new InvalidOperationException("Player startup identity is unavailable; no report can be attributed.");
                if (run.Status == "running")
                    throw new InvalidOperationException("A running Player cannot export a completed report.");
                run.ReportPath = "run.json";
                JObject report = JObject.FromObject(
                    run,
                    JsonSerializer.Create(
                        new JsonSerializerSettings { NullValueHandling = NullValueHandling.Include, TypeNameHandling = TypeNameHandling.None }
                    )
                );
                request.AddReproduction(report, bundle);
                report["progress_error"] = progressError;
                report["finalization_state"] = "completed";
                report["exit_code"] = exitCode;
                report["timeout_seconds"] = request.TimeoutSeconds;
                PlayScenarioPlayerFiles.WriteNew(Path.Combine(directory, "run.json"), report.ToString(Formatting.None), 2 * 1024 * 1024);
            }
            catch (Exception error)
            {
                Debug.LogError("Player report persistence failed: " + error.GetType().Name + ": " + error.Message);
                exitCode = 2;
            }
            Application.Quit(exitCode);
        }

        private void OnDestroy() => Close();
    }
}
#endif
