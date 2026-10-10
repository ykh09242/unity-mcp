using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>One-step Unity adapter. Scene objects are borrowed only during an evaluation.</summary>
    public sealed class UnityPlayScenarioHost : IPlayScenarioHost, IPlayScenarioMetricsHost
    {
        private readonly PlayScenarioLogPolicy _logPolicy;
        private AsyncOperation _loadOperation;
        private string _loadPath;
        private string _targetPath;
        private string _targetId;
        private string[] _targetSegments;

        public UnityPlayScenarioHost(PlayScenarioLogPolicy logPolicy = null)
        {
            _logPolicy = logPolicy ?? new PlayScenarioLogPolicy();
        }

        public PlayScenarioMetricsSnapshot CaptureMetrics(int iteration, long now) =>
            UnityPlayScenarioDiagnostics.CaptureMetrics(iteration, now, _loadOperation == null ? 0 : 1);

        public static JObject Preflight(PlayScenarioDefinition definition) => PlayScenarioPreflight.Check(definition);

        public static string DescribeTarget(PlayScenarioStep step)
        {
            if (step == null || (step.Target == null && step.TargetId == null))
                return "This step has no object target.";
            try
            {
                PlayScenarioObjectCondition.ValidateSelector(step);
                Scene scene = SceneManager.GetActiveScene();
                var matches =
                    step.TargetId != null
                        ? PlayScenarioObjectCondition.ResolveId(scene, step.TargetId)
                        : PlayScenarioObjectCondition.Resolve(scene, PlayScenarioObjectCondition.ParseTarget(step.Target));
                string summary =
                    (step.TargetId == null ? "Exact path matches: " : "Exact ID matches: ") + matches.Count + "; active matches: " + matches.ActiveCount + ".";
                Type componentType = PlayScenarioObjectCondition.ValidateCondition(step);
                if (componentType != null && matches.Count == 1)
                    summary += " " + PlayScenarioObjectCondition.Observe(step, matches, componentType).Detail;
                return PlayScenarioEngine.Bounded(summary, 2048);
            }
            catch (Exception exception)
            {
                return PlayScenarioEngine.Bounded("Target observation failed: " + exception.Message, 2048);
            }
        }

        public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
        {
            if (step == null)
                throw new ArgumentNullException(nameof(step));
            switch (step.Action)
            {
                case "load_scene":
                    return LoadScene(step.Scene, firstPoll);
                case "wait_scene":
                    return ObserveScene(step.Scene);
                case "wait_object":
                case "click_ui":
                    PrepareTarget(step, firstPoll);
                    IUguiInputSimulationBackend backend = step.Action == "click_ui" ? ManageInput.UguiBackend : null;
                    if (step.Action == "click_ui" && backend == null)
                        throw CapabilityFailure(step, "Scenario UI clicks require the optional uGUI input backend; UI Toolkit clicks are unsupported.");
                    if (step.Action == "click_ui")
                    {
                        if (step.ClickMode != null && step.ClickMode != "direct" && step.ClickMode != "raycast")
                            throw new ArgumentException("click_mode must be direct or raycast.");
                        if (step.ClickMode == "raycast" && !(backend is IUguiScenarioRaycastClickBackend))
                            throw CapabilityFailure(step, "The optional uGUI backend does not support raycast-verified clicks.");
                    }
                    Type componentType = PlayScenarioObjectCondition.ValidateCondition(step);
                    Scene scene = SceneManager.GetActiveScene();
                    if (!scene.IsValid() || !scene.isLoaded)
                        return new PlayScenarioObservation(false, "Waiting for a loaded active scene.");
                    var matches =
                        _targetId != null
                            ? PlayScenarioObjectCondition.ResolveId(scene, _targetId)
                            : PlayScenarioObjectCondition.Resolve(scene, _targetSegments);
                    PlayScenarioObservation observation = PlayScenarioObjectCondition.Observe(step, matches, componentType);
                    if (!observation.Ready || step.Action == "wait_object")
                        return observation;
                    return Click(step, matches.Target, backend);
                default:
                    throw new ArgumentException("Unsupported play scenario action.");
            }
        }

        private PlayScenarioObservation LoadScene(string scenePath, bool firstPoll)
        {
            if (firstPoll)
            {
                Release();
                string containedPath = RequireSceneAsset(scenePath);

                _loadOperation = EditorSceneManager.LoadSceneAsyncInPlayMode(containedPath, new LoadSceneParameters(LoadSceneMode.Single));
                if (_loadOperation == null)
                    throw new InvalidOperationException("The scenario scene load did not return an operation.");
                _loadPath = containedPath;
                return new PlayScenarioObservation(false, "Scene reload requested; waiting for its operation to finish.");
            }
            if (_loadPath == null)
                throw new InvalidOperationException("The scenario scene load has not been started.");
            if (_loadOperation != null)
            {
                if (!_loadOperation.isDone)
                    return new PlayScenarioObservation(false, "Waiting for the scene load operation.");
                _loadOperation = null;
            }
            return ObserveScene(_loadPath);
        }

        private static PlayScenarioObservation ObserveScene(string expectedPath)
        {
            Scene scene = SceneManager.GetActiveScene();
            bool ready = scene.IsValid() && scene.isLoaded && string.Equals(scene.path, expectedPath, StringComparison.Ordinal);
            string detail = ready ? "Expected scene is loaded and active." : "Waiting for the expected active scene.";
            return new PlayScenarioObservation(
                ready,
                detail,
                ready
                    ? null
                    : new PlayScenarioFailure
                    {
                        Code = "condition_unmet",
                        Expected = expectedPath + "; loaded=true; active=true",
                        Actual = scene.IsValid() ? scene.path + "; loaded=" + (scene.isLoaded ? "true" : "false") + "; active=true" : "no valid active scene",
                        Message = detail,
                    }
            );
        }

        internal static string RequireSceneAsset(string scenePath)
        {
            if (
                scenePath == null
                || !scenePath.StartsWith("Assets/", StringComparison.Ordinal)
                || !scenePath.EndsWith(".unity", StringComparison.Ordinal)
                || scenePath.Split('/').Any(part => part.Equals("GameData", StringComparison.OrdinalIgnoreCase))
            )
                throw new ArgumentException("Scene must be a permitted Assets/*.unity path.");
            string containedPath = AssetPathUtility.GetContainedAssetPath(scenePath);
            if (containedPath != scenePath)
                throw new ArgumentException("Scene must be a canonical Assets/*.unity path.");
            if (AssetDatabase.LoadAssetAtPath<SceneAsset>(containedPath) == null)
                throw new InvalidOperationException("The scenario scene asset was not found.");
            return containedPath;
        }

        private void PrepareTarget(PlayScenarioStep step, bool firstPoll)
        {
            PlayScenarioObjectCondition.ValidateSelector(step);
            if (!firstPoll && step.Target == _targetPath && step.TargetId == _targetId && (_targetSegments != null || _targetId != null))
                return;
            _targetSegments = step.TargetId == null ? PlayScenarioObjectCondition.ParseTarget(step.Target) : null;
            _targetPath = step.Target;
            _targetId = step.TargetId;
        }

        internal static PlayScenarioException CapabilityFailure(PlayScenarioStep step, string message) =>
            new PlayScenarioException(
                new PlayScenarioFailure
                {
                    Code = "capability_unavailable",
                    Target = step.TargetId ?? step.Target,
                    Expected = (step.ClickMode ?? "direct") + " uGUI click backend",
                    Actual = "unavailable",
                    Message = message,
                }
            );

        private PlayScenarioObservation Click(PlayScenarioStep step, GameObject target, IUguiInputSimulationBackend backend)
        {
            object result;
            bool ready = true;
            string detail = null;
            string loggedError = null;
            void CaptureError(string message, string stackTrace, LogType type)
            {
                if (loggedError == null && _logPolicy.IsUnexpected(type.ToString(), message))
                    loggedError = type + ": " + PlayScenarioEngine.Bounded(message, 1024);
            }

            // ExecuteEvents logs listener exceptions instead of propagating them. Only
            // observe this synchronous dispatch; unrelated and later logs are not click failures.
            Application.logMessageReceived += CaptureError;
            try
            {
                if (step.ClickMode == "raycast")
                    ready = ((IUguiScenarioRaycastClickBackend)backend).TryRaycastClick(target, out result, out detail);
                else if (backend is IUguiScenarioClickBackend scenarioBackend)
                    ready = scenarioBackend.TryClick(target, out result, out detail);
                else
                    result = backend.Click(target);
            }
            finally
            {
                Application.logMessageReceived -= CaptureError;
            }
            if (loggedError != null)
                throw new InvalidOperationException("UI click logged an error: " + loggedError);
            if (!ready)
                return new PlayScenarioObservation(
                    false,
                    detail,
                    new PlayScenarioFailure
                    {
                        Code = "input_blocked",
                        Target = step.TargetId ?? step.Target,
                        Expected = step.ClickMode == "raycast" ? "eligible first uGUI raycast hit at target center" : "ready direct uGUI click handler",
                        Actual = detail,
                        Message = detail,
                    }
                );
            if (!(result is IMcpResponse response) || !response.Success)
                throw new InvalidOperationException(result is ErrorResponse error ? error.Error : "The UI backend did not confirm a successful click.");
            return new PlayScenarioObservation(
                true,
                step.ClickMode == "raycast" ? "Raycast-verified UI click dispatched successfully." : "UI click dispatched successfully."
            );
        }

        public void Release()
        {
            // Releasing a reference does not cancel Unity's scene load or destroy scene objects.
            _loadOperation = null;
            _loadPath = null;
            _targetPath = null;
            _targetId = null;
            _targetSegments = null;
        }
    }
}
