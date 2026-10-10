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
            if (step == null || string.IsNullOrEmpty(step.Target))
                return "This step has no hierarchy target.";
            try
            {
                string[] segments = PlayScenarioObjectCondition.ParseTarget(step.Target);
                Scene scene = SceneManager.GetActiveScene();
                var matches = PlayScenarioObjectCondition.Resolve(scene, segments);
                string summary = "Exact path matches: " + matches.Count + "; active matches: " + matches.ActiveCount + ".";
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
                    PrepareTarget(step.Target, firstPoll);
                    IUguiInputSimulationBackend backend = step.Action == "click_ui" ? ManageInput.UguiBackend : null;
                    if (step.Action == "click_ui" && backend == null)
                        throw new InvalidOperationException("Scenario UI clicks require the optional uGUI input backend; UI Toolkit clicks are unsupported.");
                    Type componentType = PlayScenarioObjectCondition.ValidateCondition(step);
                    Scene scene = SceneManager.GetActiveScene();
                    if (!scene.IsValid() || !scene.isLoaded)
                        return new PlayScenarioObservation(false, "Waiting for a loaded active scene.");
                    var matches = PlayScenarioObjectCondition.Resolve(scene, _targetSegments);
                    PlayScenarioObservation observation = PlayScenarioObjectCondition.Observe(step, matches, componentType);
                    if (!observation.Ready || step.Action == "wait_object")
                        return observation;
                    return Click(matches.Target, backend);
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
            return new PlayScenarioObservation(ready, ready ? "Expected scene is loaded and active." : "Waiting for the expected active scene.");
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

        private void PrepareTarget(string path, bool firstPoll)
        {
            if (!firstPoll && _targetSegments != null && path == _targetPath)
                return;
            _targetSegments = PlayScenarioObjectCondition.ParseTarget(path);
            _targetPath = path;
        }

        private PlayScenarioObservation Click(GameObject target, IUguiInputSimulationBackend backend)
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
                if (backend is IUguiScenarioClickBackend scenarioBackend)
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
                return new PlayScenarioObservation(false, detail);
            if (!(result is IMcpResponse response) || !response.Success)
                throw new InvalidOperationException(result is ErrorResponse error ? error.Error : "The UI backend did not confirm a successful click.");
            return new PlayScenarioObservation(true, "UI click dispatched successfully.");
        }

        public void Release()
        {
            // Releasing a reference does not cancel Unity's scene load or destroy scene objects.
            _loadOperation = null;
            _loadPath = null;
            _targetPath = null;
            _targetSegments = null;
        }
    }
}
