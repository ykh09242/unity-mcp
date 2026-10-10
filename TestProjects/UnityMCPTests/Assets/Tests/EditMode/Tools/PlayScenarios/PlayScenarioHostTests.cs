using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public sealed class PlayScenarioConditionProbe : MonoBehaviour
    {
        public bool Ready;
        public long Counter;
        public float Ratio;
        public double Precise;
        public string Label;
        public Vector3 Unsupported;
        public int GetterReadCount;

        [SerializeField]
        private bool _privateReady;

        public bool DangerousGetter
        {
            get
            {
                GetterReadCount++;
                throw new InvalidOperationException("The condition must not execute a property getter.");
            }
        }

        public void SetPrivateReady(bool ready) => _privateReady = ready;
    }

    public class PlayScenarioHostTests
    {
        private UnityEngine.Object _previousSelection;
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
            _previousSelection = Selection.activeObject;
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
            Selection.activeObject = _previousSelection;
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

        private static PlayScenarioStep IdStep(string identifier = "player.ready", string action = "wait_object") =>
            new PlayScenarioStep
            {
                Name = "stable target",
                Action = action,
                TargetId = identifier,
            };

        [Test]
        public void StableIdSurvivesRenameReparentInactiveAndDestroyedStateWithoutSceneCache()
        {
            var target = CreateTarget();
            target.AddComponent<MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget>().TargetId = "player.ready";
            var step = IdStep();
            Assert.That(_host.Evaluate(step, true).Ready, Is.True);
            var parent = new GameObject("ReplacementParent");
            target.name = "RenamedPlayer";
            target.transform.SetParent(parent.transform);
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            target.SetActive(false);
            Assert.That(_host.Evaluate(step, false).Ready, Is.False);
            step.Active = false;
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            _host.Release();
            Assert.That(target != null, Is.True);
            UnityEngine.Object.DestroyImmediate(target);
            step.Active = null;
            step.Count = 0;
            Assert.That(_host.Evaluate(step, true).Ready, Is.True);
        }

        [TestCase(null)]
        [TestCase(0)]
        [TestCase(1)]
        public void DuplicateIdIncludingInactiveMarkerAlwaysFails(int? count)
        {
            CreateTarget().AddComponent<MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget>().TargetId = "player.ready";
            var duplicate = new GameObject("Duplicate");
            duplicate.AddComponent<MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget>().TargetId = "player.ready";
            duplicate.SetActive(false);
            var step = IdStep();
            step.Count = count;
            var error = Assert.Throws<PlayScenarioException>(() => _host.Evaluate(step, true));
            Assert.That(error.Failure.Code, Is.EqualTo("target_ambiguous"));
        }

        [Test]
        public void StableIdOnlySearchesActiveSceneAndIsCaseSensitive()
        {
            CreateTarget().AddComponent<MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget>().TargetId = "player.ready";
            Assert.That(_host.Evaluate(IdStep("Player.Ready"), true).Ready, Is.False);
            _otherOwnedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(_otherOwnedScene);
            CreateTarget().AddComponent<MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget>().TargetId = "player.ready";
            SceneManager.SetActiveScene(_ownedScene);
            Assert.That(_host.Evaluate(IdStep(), true).Ready, Is.True);
        }

        [TestCase("bad/id")]
        [TestCase("id\n")]
        [TestCase(" leading")]
        [TestCase("")]
        public void InvalidIdSelectorsFailBeforeBackendExecution(string identifier)
        {
            var backend = new SpyBackend();
            ManageInput.UguiBackend = backend;
            Assert.Throws<ArgumentException>(() => _host.Evaluate(IdStep(identifier, "click_ui"), true));
            Assert.That(backend.Clicks, Is.Zero);
        }

        [Test]
        public void StableIdRejectsBothSelectorsAndMultipleCountAndReportsMissingTarget()
        {
            var step = IdStep();
            step.Target = "ScenarioRoot/Player";
            Assert.Throws<ArgumentException>(() => _host.Evaluate(step, true));
            step.Target = null;
            step.Count = 2;
            Assert.Throws<ArgumentException>(() => _host.Evaluate(step, true));
            step.Count = null;
            PlayScenarioObservation observation = _host.Evaluate(step, true);
            Assert.That(observation.Failure.Code, Is.EqualTo("target_missing"));
            Assert.That(observation.Failure.Target, Is.EqualTo("player.ready"));
            Assert.That(observation.Failure.Expected, Is.EqualTo("1"));
            Assert.That(observation.Failure.Actual, Is.EqualTo("0"));
        }

        [Test]
        public void RaycastCapabilityFailureNeverFallsBackToDirectDispatch()
        {
            var backend = new SpyBackend();
            ManageInput.UguiBackend = backend;
            var step = ObjectStep("click_ui");
            step.ClickMode = "raycast";
            var error = Assert.Throws<PlayScenarioException>(() => _host.Evaluate(step, true));
            Assert.That(error.Failure.Code, Is.EqualTo("capability_unavailable"));
            Assert.That(backend.Clicks, Is.Zero);
        }

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
            Assert.Throws<PlayScenarioException>(() => _host.Evaluate(ObjectStep(), true));
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
            var observation = _host.Evaluate(new PlayScenarioStep { Action = "wait_scene", Scene = "Assets/Synthetic.unity" }, true);
            Assert.That(observation.Ready, Is.False);
            Assert.That(observation.Failure.Code, Is.EqualTo("condition_unmet"));
            Assert.That(observation.Failure.Expected, Is.EqualTo("Assets/Synthetic.unity; loaded=true; active=true"));
            Assert.That(observation.Failure.Actual, Is.EqualTo(_ownedScene.path + "; loaded=true; active=true"));
        }

        [Test]
        public void ReadySceneObservationDoesNotCarryFailureMetadata()
        {
            var observation = _host.Evaluate(new PlayScenarioStep { Action = "wait_scene", Scene = _ownedScene.path }, true);
            Assert.That(observation.Ready, Is.True);
            Assert.That(observation.Failure, Is.Null);
        }

        [TestCase(null)]
        [TestCase("direct")]
        [TestCase("raycast")]
        public void OptionalBackendAbsenceFailsWithoutWaitingForTarget(string clickMode)
        {
            ManageInput.UguiBackend = null;
            var step = ObjectStep("click_ui");
            step.ClickMode = clickMode;
            var error = Assert.Throws<PlayScenarioException>(() => _host.Evaluate(step, true));
            Assert.That(error.Failure.Code, Is.EqualTo("capability_unavailable"));
            Assert.That(error.Failure.Expected, Is.EqualTo((clickMode ?? "direct") + " uGUI click backend"));
            Assert.That(error.Failure.Actual, Is.EqualTo("unavailable"));
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

        [Test]
        public void InactiveConditionRequiresInactiveAndObservesReactivation()
        {
            GameObject player = CreateTarget();
            var step = ObjectStep();
            step.Active = false;
            Assert.That(_host.Evaluate(step, true).Ready, Is.False);
            player.SetActive(false);
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            player.SetActive(true);
            Assert.That(_host.Evaluate(step, false).Ready, Is.False);
        }

        [Test]
        public void ExplicitCountsIncludeInactiveMatchesAndRequireEveryRequestedState()
        {
            GameObject player = CreateTarget();
            var duplicate = new GameObject("Player");
            duplicate.transform.SetParent(player.transform.parent);
            duplicate.SetActive(false);
            var step = ObjectStep();
            step.Count = 2;
            PlayScenarioObservation mixed = _host.Evaluate(step, true);
            Assert.That(mixed.Ready, Is.False);
            Assert.That(mixed.Detail, Does.Contain("Exact path matches: 2"));
            duplicate.SetActive(true);
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            step.Active = false;
            Assert.That(_host.Evaluate(step, false).Ready, Is.False);
            duplicate.SetActive(false);
            player.SetActive(false);
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            step.Count = 1;
            Assert.That(_host.Evaluate(step, false).Ready, Is.False, "Explicit count mismatches wait rather than choosing the first duplicate.");
        }

        [Test]
        public void AbsenceConditionWaitsUntilEvenInactiveMatchesDisappear()
        {
            GameObject player = CreateTarget();
            player.SetActive(false);
            var step = ObjectStep();
            step.Count = 0;
            Assert.That(_host.Evaluate(step, true).Ready, Is.False);
            UnityEngine.Object.DestroyImmediate(player);
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
        }

        [Test]
        public void ComponentAppearingAndDisappearingIsObservedFresh()
        {
            GameObject player = CreateTarget();
            var step = ObjectStep();
            step.Component = typeof(PlayScenarioConditionProbe).FullName;
            Assert.That(_host.Evaluate(step, true).Ready, Is.False);
            var probe = player.AddComponent<PlayScenarioConditionProbe>();
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            UnityEngine.Object.DestroyImmediate(probe);
            Assert.That(_host.Evaluate(step, false).Ready, Is.False);
        }

        [TestCase("Transform")]
        [TestCase("System.String")]
        [TestCase("Synthetic.MissingComponent")]
        public void ComponentConditionRequiresExactValidComponentFullName(string component)
        {
            var step = ObjectStep();
            step.Component = component;
            Assert.Throws<ArgumentException>(() => _host.Evaluate(step, true));
        }

        private static PlayScenarioStep PropertyStep(string path, JToken expected)
        {
            var step = ObjectStep();
            step.Component = typeof(PlayScenarioConditionProbe).FullName;
            step.Property = new PlayScenarioPropertyCondition { Path = path, Equals = expected };
            return step;
        }

        [Test]
        public void PrivateSerializedFieldWaitsForEqualityWithoutCallingGetters()
        {
            var probe = CreateTarget().AddComponent<PlayScenarioConditionProbe>();
            var step = PropertyStep("_privateReady", new JValue(true));
            Assert.That(_host.Evaluate(step, true).Ready, Is.False);
            probe.SetPrivateReady(true);
            Assert.That(_host.Evaluate(step, false).Ready, Is.True);
            probe.SetPrivateReady(false);
            Assert.That(_host.Evaluate(step, false).Ready, Is.False);
            Assert.That(probe.GetterReadCount, Is.Zero);
        }

        [TestCase("Ready", "true")]
        [TestCase("Counter", "9223372036854775807")]
        [TestCase("Ratio", "0.1")]
        [TestCase("Precise", "0.1")]
        [TestCase("Label", "\"Case Sensitive\"")]
        public void ScalarConditionsUseTheirSerializedValueType(string path, string expectedJson)
        {
            var probe = CreateTarget().AddComponent<PlayScenarioConditionProbe>();
            probe.Ready = true;
            probe.Counter = long.MaxValue;
            probe.Ratio = 0.1f;
            probe.Precise = 0.1;
            probe.Label = "Case Sensitive";
            var step = PropertyStep(path, JToken.Parse(expectedJson));
            Assert.That(_host.Evaluate(step, true).Ready, Is.True);
            Assert.That(_host.Evaluate(step, false).Detail, Does.Contain("Serialized property " + path));
        }

        [TestCase("DangerousGetter")]
        [TestCase("ready")]
        [TestCase("Unsupported")]
        public void MissingGetterAndUnsupportedPropertyTypesFailWithoutExecutingUserCode(string path)
        {
            var probe = CreateTarget().AddComponent<PlayScenarioConditionProbe>();
            var step = PropertyStep(path, new JValue(true));
            Assert.Throws<ArgumentException>(() => _host.Evaluate(step, true));
            Assert.That(probe.GetterReadCount, Is.Zero);
        }

        [Test]
        public void ScalarConditionRejectsValueTypeMismatchAndReportsCaseSensitiveMismatch()
        {
            var probe = CreateTarget().AddComponent<PlayScenarioConditionProbe>();
            probe.Label = "Ready";
            Assert.Throws<ArgumentException>(() => _host.Evaluate(PropertyStep("Ready", new JValue(1)), true));
            Assert.That(_host.Evaluate(PropertyStep("Label", new JValue("ready")), true).Ready, Is.False);
        }

        [TestCase("strict")]
        [TestCase("log_only")]
        public void ClickLogPolicyAllowsExactMessagesButNeverDirectExceptions(string mode)
        {
            CreateTarget();
            const string allowed = "synthetic allowed callback";
            var policy = new PlayScenarioLogPolicy { Mode = mode };
            if (mode == "strict")
                policy.AllowedMessages.Add(allowed);
            var host = new UnityPlayScenarioHost(policy);
            var backend = new SpyBackend { OnClick = () => Debug.LogError(allowed) };
            ManageInput.UguiBackend = backend;
            LogAssert.Expect(LogType.Error, allowed);
            Assert.That(host.Evaluate(ObjectStep("click_ui"), true).Ready, Is.True);
            backend.OnClick = null;
            backend.Fault = new InvalidOperationException("direct callback exception");
            Assert.Throws<InvalidOperationException>(() => host.Evaluate(ObjectStep("click_ui"), true));
            host.Release();
        }

        [Test]
        public void ClickAllowlistMatchesFullMessagesBeforeTruncation()
        {
            CreateTarget();
            string prefix = new string('a', 1024);
            var policy = new PlayScenarioLogPolicy();
            policy.AllowedMessages.Add(prefix);
            var host = new UnityPlayScenarioHost(policy);
            string unexpected = prefix + " differs";
            ManageInput.UguiBackend = new SpyBackend { OnClick = () => Debug.LogError(unexpected) };
            LogAssert.Expect(LogType.Error, unexpected);
            Assert.Throws<InvalidOperationException>(() => host.Evaluate(ObjectStep("click_ui"), true));
            host.Release();
        }

        private PlayScenarioDefinition PreflightDefinition(PlayScenarioStep condition) =>
            new PlayScenarioDefinition
            {
                Name = "host-preflight",
                Steps =
                {
                    new PlayScenarioStep
                    {
                        Name = "load",
                        Action = "load_scene",
                        Scene = _ownedScene.path,
                    },
                    condition,
                },
            };

        [Test]
        public void PreflightInspectsCurrentCapabilitiesWithoutChangingEditorState()
        {
            var probe = CreateTarget().AddComponent<PlayScenarioConditionProbe>();
            probe.Ready = true;
            var definition = PreflightDefinition(PropertyStep("Ready", new JValue(true)));
            var backend = new SpyBackend();
            ManageInput.UguiBackend = backend;
            definition.CleanupSteps.Add(ObjectStep("click_ui"));
            Selection.activeGameObject = probe.gameObject;
            UnityEngine.Object selected = Selection.activeObject;
            int sceneCount = SceneManager.sceneCount;
            bool dirty = _ownedScene.isDirty;
            bool playing = EditorApplication.isPlaying;

            JObject result = UnityPlayScenarioHost.Preflight(definition);

            Assert.That(result["success"].Value<bool>(), Is.True);
            Assert.That(result["data"]["valid"].Value<bool>(), Is.True);
            Assert.That(result["data"]["checks"][1]["status"].Value<string>(), Is.EqualTo("passed"));
            Assert.That(result["data"]["checks"][2]["status"].Value<string>(), Is.EqualTo("deferred"));
            Assert.That(SceneManager.GetActiveScene().handle, Is.EqualTo(_ownedScene.handle));
            Assert.That(SceneManager.sceneCount, Is.EqualTo(sceneCount));
            Assert.That(_ownedScene.isDirty, Is.EqualTo(dirty));
            Assert.That(EditorApplication.isPlaying, Is.EqualTo(playing));
            Assert.That(Selection.activeObject, Is.SameAs(selected));
            Assert.That(backend.Clicks, Is.Zero);
            Assert.That(probe.GetterReadCount, Is.Zero);
            Assert.That(definition.Steps[1].Property.Path, Is.EqualTo("Ready"));
        }

        [Test]
        public void PreflightDefersMissingRuntimeTargetsButRejectsAmbiguityAndUnsupportedFields()
        {
            var definition = PreflightDefinition(ObjectStep());
            JObject missing = UnityPlayScenarioHost.Preflight(definition);
            Assert.That(missing["data"]["valid"].Value<bool>(), Is.True);
            Assert.That(missing["data"]["checks"][1]["status"].Value<string>(), Is.EqualTo("deferred"));
            GameObject player = CreateTarget();
            var duplicate = new GameObject("Player");
            duplicate.transform.SetParent(player.transform.parent);
            Assert.That(UnityPlayScenarioHost.Preflight(definition)["data"]["valid"].Value<bool>(), Is.False);
            UnityEngine.Object.DestroyImmediate(duplicate);
            var probe = player.AddComponent<PlayScenarioConditionProbe>();
            definition.Steps[1] = PropertyStep("DangerousGetter", new JValue(true));
            Assert.That(UnityPlayScenarioHost.Preflight(definition)["data"]["valid"].Value<bool>(), Is.False);
            Assert.That(probe.GetterReadCount, Is.Zero);
            definition.Steps[1] = PropertyStep("Unsupported", new JValue(0));
            Assert.That(UnityPlayScenarioHost.Preflight(definition)["data"]["valid"].Value<bool>(), Is.False);
            player.SetActive(false);
            Assert.That(
                UnityPlayScenarioHost.Preflight(definition)["data"]["valid"].Value<bool>(),
                Is.False,
                "Inactive components still permit safe capability inspection."
            );
        }

        [Test]
        public void PreflightRejectsUnsafeScenePathAndDescriptionStaysBounded()
        {
            var definition = PreflightDefinition(ObjectStep());
            definition.Steps[0].Scene = "Assets/Resources/Game" + "Data/Hidden.unity";
            Assert.That(UnityPlayScenarioHost.Preflight(definition)["data"]["valid"].Value<bool>(), Is.False);
            var step = ObjectStep(target: new string('x', 4097));
            Assert.That(UnityPlayScenarioHost.DescribeTarget(step).Length, Is.LessThanOrEqualTo(2048));
            Assert.That(UnityPlayScenarioHost.DescribeTarget(step), Does.Contain("failed"));
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
