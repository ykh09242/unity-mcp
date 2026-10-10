using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Input;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>One-step Unity adapter. Scene objects are borrowed only during an evaluation.</summary>
    public sealed class UnityPlayScenarioHost : IPlayScenarioHost
    {
        private AsyncOperation _loadOperation;
        private string _loadPath;
        private string _targetPath;
        private string[] _targetSegments;

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
                    var target = ResolveTarget(out string detail);
                    if (target == null)
                        return new PlayScenarioObservation(false, detail);
                    if (step.Action == "wait_object")
                        return new PlayScenarioObservation(true, "Target is active in the active scene.");
                    return Click(target, backend);
                default:
                    throw new ArgumentException("Unsupported play scenario action.");
            }
        }

        private PlayScenarioObservation LoadScene(string scenePath, bool firstPoll)
        {
            if (firstPoll)
            {
                Release();
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

        private void PrepareTarget(string path, bool firstPoll)
        {
            if (!firstPoll && _targetSegments != null && path == _targetPath)
                return;
            if (string.IsNullOrWhiteSpace(path) || path.Length > 4096 || path.Any(char.IsControl) || path.IndexOf('\\') >= 0)
                throw new ArgumentException("Target must be a bounded exact relative hierarchy path.");
            string[] segments = path.Split('/');
            if (segments.Length > 128 || segments.Any(part => part.Length == 0 || part == "." || part == ".."))
                throw new ArgumentException("Target must be an exact relative hierarchy path with at most 128 levels.");
            _targetPath = path;
            _targetSegments = segments;
        }

        private GameObject ResolveTarget(out string detail)
        {
            detail = "Waiting for an active target in the active scene.";
            Scene scene = SceneManager.GetActiveScene();
            if (!scene.IsValid() || !scene.isLoaded)
                return null;
            GameObject match = null;
            foreach (GameObject root in scene.GetRootGameObjects())
            {
                if (root.name == _targetSegments[0])
                    Match(root.transform, 1, ref match);
            }
            return match != null && match.activeInHierarchy ? match : null;
        }

        private void Match(Transform parent, int index, ref GameObject match)
        {
            if (index == _targetSegments.Length)
            {
                if (match != null)
                    throw new InvalidOperationException("Target path is ambiguous in the active scene, including inactive objects.");
                match = parent.gameObject;
                return;
            }
            int childCount = parent.childCount;
            for (int i = 0; i < childCount; i++)
            {
                Transform child = parent.GetChild(i);
                if (child.name == _targetSegments[index])
                    Match(child, index + 1, ref match);
            }
        }

        private static PlayScenarioObservation Click(GameObject target, IUguiInputSimulationBackend backend)
        {
            object result;
            bool ready = true;
            string detail = null;
            string loggedError = null;
            void CaptureError(string message, string stackTrace, LogType type)
            {
                if (loggedError == null && (type == LogType.Error || type == LogType.Assert || type == LogType.Exception))
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
