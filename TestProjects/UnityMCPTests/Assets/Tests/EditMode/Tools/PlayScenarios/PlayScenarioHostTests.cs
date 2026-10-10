using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioHostTests
    {
        private Scene _previousActive;
        private Scene _ownedScene;
        private Scene _otherOwnedScene;
        private IUguiInputSimulationBackend _previousBackend;
        private UnityPlayScenarioHost _host;
        private string _ownedFolder;
        private string _ownedFolderGuid;
        private string _baselinePath;
        private bool _baselineSaved;

        [SetUp]
        public void SetUp()
        {
            _previousActive = SceneManager.GetActiveScene();
            _previousBackend = ManageInput.UguiBackend;
            _host = new UnityPlayScenarioHost();
            _ownedScene = default;
            _otherOwnedScene = default;
            _ownedFolder = null;
            _ownedFolderGuid = null;
            _baselinePath = null;
            _baselineSaved = false;
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                Assert.Ignore("Host fixture requires stable Edit Mode.");
            bool hasUntitled = false;
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                if (scene.isLoaded && string.IsNullOrEmpty(scene.path))
                    hasUntitled = true;
            }
            bool emptyUntitled =
                SceneManager.sceneCount == 1
                && _previousActive.IsValid()
                && _previousActive.isLoaded
                && string.IsNullOrEmpty(_previousActive.path)
                && _previousActive.rootCount == 0
                && !_previousActive.isDirty;
            if (hasUntitled && !emptyUntitled)
                Assert.Ignore("Preserving existing untitled scene content or changes; use a named scene or a sole empty unmodified untitled scene.");

            string folderName = "__McpPlayScenarioHostTests_" + Guid.NewGuid().ToString("N");
            string requestedFolder = "Assets/" + folderName;
            Assert.That(
                AssetDatabase.IsValidFolder(requestedFolder)
                    || Directory.Exists(Path.Combine(Application.dataPath, folderName))
                    || File.Exists(Path.Combine(Application.dataPath, folderName)),
                Is.False,
                "The unique test folder must not already exist."
            );
            string createdGuid = AssetDatabase.CreateFolder("Assets", folderName);
            Assert.That(string.IsNullOrEmpty(createdGuid), Is.False, "Could not create the owned test folder.");
            _ownedFolder = AssetDatabase.GUIDToAssetPath(createdGuid);
            _ownedFolderGuid = createdGuid;
            Assert.That(_ownedFolder, Is.EqualTo(requestedFolder));

            if (emptyUntitled)
            {
                // Only a verified empty, unmodified sole scene may temporarily acquire a name.
                _baselinePath = _ownedFolder + "/Baseline.unity";
                Assert.That(EditorSceneManager.SaveScene(_previousActive, _baselinePath), Is.True);
                _baselineSaved = true;
            }
            _ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.That(EditorSceneManager.SaveScene(_ownedScene, _ownedFolder + "/Owned.unity"), Is.True);
            SceneManager.SetActiveScene(_ownedScene);
        }

        [TearDown]
        public void TearDown()
        {
            ManageInput.UguiBackend = _previousBackend;
            _host?.Release();
            if (_previousActive.IsValid() && _previousActive.isLoaded)
                SceneManager.SetActiveScene(_previousActive);
            CloseOwnedScene(_otherOwnedScene);
            CloseOwnedScene(_ownedScene);
            if (_baselineSaved)
            {
                if (
                    !_previousActive.IsValid()
                    || !_previousActive.isLoaded
                    || _previousActive.path != _baselinePath
                    || _previousActive.rootCount != 0
                    || _previousActive.isDirty
                    || SceneManager.sceneCount != 1
                    || SceneManager.GetSceneAt(0).handle != _previousActive.handle
                )
                {
                    Debug.LogWarning("The temporary blank baseline changed unexpectedly; preserving its scene and assets instead of discarding content.");
                    return;
                }
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            }
            if (string.IsNullOrEmpty(_ownedFolder) || string.IsNullOrEmpty(_ownedFolderGuid) || AssetDatabase.AssetPathToGUID(_ownedFolder) != _ownedFolderGuid)
                return;
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                if (SceneManager.GetSceneAt(i).path.StartsWith(_ownedFolder + "/", StringComparison.Ordinal))
                    return;
            }
            Assert.That(AssetDatabase.DeleteAsset(_ownedFolder), Is.True, "Could not remove the folder created by this test.");
        }

        private static void CloseOwnedScene(Scene scene)
        {
            if (scene.IsValid() && scene.isLoaded)
                EditorSceneManager.CloseScene(scene, true);
        }

        private static GameObject CreateTarget()
        {
            var root = new GameObject("ScenarioRoot");
            var player = new GameObject("Player");
            player.transform.SetParent(root.transform);
            return player;
        }

        private static PlayScenarioStep ObjectStep(string action = "wait_object", string target = "ScenarioRoot/Player") =>
            new PlayScenarioStep
            {
                Name = "target",
                Action = action,
                Target = target,
            };

        [Test]
        public void MissingAndInactiveTargetsWaitThenObserveFreshState()
        {
            var step = ObjectStep();
            Assert.That(_host.Evaluate(step, true).Ready, Is.False);
            GameObject player = CreateTarget();
            player.SetActive(false);
            Assert.That(_host.Evaluate(step, false).Ready, Is.False);
            player.SetActive(true);
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            UnityEngine.Object.DestroyImmediate(player);
            Assert.That(_host.Evaluate(step, false).Ready, Is.False);
        }

        [Test]
        public void OnlyActiveSceneParticipatesInTargetResolution()
        {
            CreateTarget();
            _otherOwnedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(_otherOwnedScene);
            CreateTarget();
            SceneManager.SetActiveScene(_ownedScene);
            Assert.That(_host.Evaluate(ObjectStep(), true).Ready, Is.True);
        }

        [Test]
        public void InactiveDuplicateIsStillAmbiguous()
        {
            GameObject player = CreateTarget();
            var duplicate = new GameObject("Player");
            duplicate.transform.SetParent(player.transform.parent);
            duplicate.SetActive(false);
            Assert.Throws<InvalidOperationException>(() => _host.Evaluate(ObjectStep(), true));
        }

        [Test]
        public void RootRelativePathNeverMatchesOnlyATrailingName()
        {
            CreateTarget();
            Assert.That(_host.Evaluate(ObjectStep(target: "Player"), true).Ready, Is.False);
        }

        [TestCase("/ScenarioRoot/Player")]
        [TestCase("ScenarioRoot//Player")]
        [TestCase("ScenarioRoot/../Player")]
        public void InvalidTargetFailsBeforeBackendExecution(string target)
        {
            var backend = new SpyBackend();
            ManageInput.UguiBackend = backend;
            Assert.Throws<ArgumentException>(() => _host.Evaluate(ObjectStep("click_ui", target), true));
            Assert.That(backend.Clicks, Is.Zero);
        }

        [Test]
        public void WaitSceneDoesNotAcceptUnrelatedActiveScene()
        {
            Assert.That(_host.Evaluate(new PlayScenarioStep { Action = "wait_scene", Scene = "Assets/Synthetic.unity" }, true).Ready, Is.False);
        }

        [Test]
        public void OptionalBackendAbsenceFailsWithoutWaitingForTarget()
        {
            ManageInput.UguiBackend = null;
            Assert.Throws<InvalidOperationException>(() => _host.Evaluate(ObjectStep("click_ui"), true));
        }

        [Test]
        public void ReadinessWaitsSendNoClickAndReadySendsOne()
        {
            GameObject player = CreateTarget();
            var backend = new WaitingBackend();
            ManageInput.UguiBackend = backend;
            var step = ObjectStep("click_ui");
            Assert.That(_host.Evaluate(step, true).Ready, Is.False);
            Assert.That(_host.Evaluate(step, false).Ready, Is.False);
            Assert.That(backend.Clicks, Is.Zero);
            backend.Ready = true;
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            Assert.That(backend.Clicks, Is.EqualTo(1));
            Assert.That(backend.Target, Is.SameAs(player));
        }

        [Test]
        public void BackendErrorAndEventExceptionAreHardFailures()
        {
            CreateTarget();
            var backend = new SpyBackend { Result = new ErrorResponse("synthetic failure") };
            ManageInput.UguiBackend = backend;
            Assert.Throws<InvalidOperationException>(() => _host.Evaluate(ObjectStep("click_ui"), true));
            Assert.That(backend.Clicks, Is.EqualTo(1));
            backend.Fault = new Exception("synthetic listener");
            Assert.Throws<Exception>(() => _host.Evaluate(ObjectStep("click_ui"), true));
            Assert.That(backend.Clicks, Is.EqualTo(2));
        }

        [TestCase(LogType.Error)]
        [TestCase(LogType.Assert)]
        [TestCase(LogType.Exception)]
        public void LoggedCallbackErrorsFailEvenWhenBackendReturnsSuccess(LogType type)
        {
            CreateTarget();
            const string message = "synthetic logged callback failure";
            var backend = new SpyBackend
            {
                OnClick = () =>
                {
                    if (type == LogType.Exception)
                        Debug.LogException(new InvalidOperationException(message));
                    else
                        Debug.unityLogger.Log(type, message);
                },
            };
            ManageInput.UguiBackend = backend;
            LogAssert.Expect(type, type == LogType.Exception ? "InvalidOperationException: " + message : message);
            var error = Assert.Throws<InvalidOperationException>(() => _host.Evaluate(ObjectStep("click_ui"), true));
            Assert.That(error.Message, Does.Contain(message));
            Assert.That(backend.Clicks, Is.EqualTo(1));
            backend.OnClick = null;
            Assert.That(_host.Evaluate(ObjectStep("click_ui"), true).Ready, Is.True, "An earlier click error must not contaminate a later dispatch.");
        }

        [Test]
        public void WarningsAndErrorsOutsideClickDispatchDoNotFailTheClick()
        {
            CreateTarget();
            var backend = new SpyBackend { OnClick = () => Debug.LogWarning("synthetic click warning") };
            ManageInput.UguiBackend = backend;
            LogAssert.Expect(LogType.Error, "unrelated earlier error");
            Debug.LogError("unrelated earlier error");
            LogAssert.Expect(LogType.Warning, "synthetic click warning");
            Assert.That(_host.Evaluate(ObjectStep("click_ui"), true).Ready, Is.True);
            LogAssert.Expect(LogType.Error, "unrelated later error");
            Debug.LogError("unrelated later error");
            backend.OnClick = null;
            Assert.That(_host.Evaluate(ObjectStep("click_ui"), true).Ready, Is.True);
        }

        [Test]
        public void ReleaseIsIdempotentAndNeverDestroysBorrowedObjects()
        {
            GameObject player = CreateTarget();
            Assert.That(_host.Evaluate(ObjectStep(), true).Ready, Is.True);
            _host.Release();
            _host.Release();
            Assert.That(player != null, Is.True);
            Assert.That(_host.Evaluate(ObjectStep(), true).Ready, Is.True);
        }

        private class SpyBackend : IUguiInputSimulationBackend
        {
            public int Clicks;
            public GameObject Target;
            public object Result = new SuccessResponse("clicked");
            public Exception Fault;
            public Action OnClick;

            public object Click(GameObject target)
            {
                Clicks++;
                Target = target;
                OnClick?.Invoke();
                if (Fault != null)
                    throw Fault;
                return Result;
            }
        }

        private sealed class WaitingBackend : SpyBackend, IUguiScenarioClickBackend
        {
            public bool Ready;

            public bool TryClick(GameObject target, out object result, out string detail)
            {
                result = null;
                detail = "Waiting for interactability.";
                if (!Ready)
                    return false;
                result = Click(target);
                return true;
            }
        }
    }
}
