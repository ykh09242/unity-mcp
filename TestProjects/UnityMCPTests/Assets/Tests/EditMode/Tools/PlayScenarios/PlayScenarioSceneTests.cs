using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public sealed class PlayScenarioSceneTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private string fixtureFolder;

        [OneTimeSetUp]
        public void PrepareRunner() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunner() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void PrepareOwnedBase()
        {
            string nonce = Guid.NewGuid().ToString("N");
            fixtureFolder = "Assets/ScenarioScopeBase-" + nonce;
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(fixtureFolder));
            Scene owned = sceneFixture.Create("ScenarioScopeBase_", nonce);
            Assert.That(EditorSceneManager.SaveScene(owned, fixtureFolder + "/" + owned.name + ".unity"), Is.True);
        }

        [TearDown]
        public void ReleaseOwnedBase()
        {
            try
            {
                sceneFixture.Close();
            }
            finally
            {
                if (!string.IsNullOrEmpty(fixtureFolder))
                    AssetDatabase.DeleteAsset(fixtureFolder);
            }
        }

        private const BindingFlags Flags = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

        private static JObject Definition() =>
            JObject.Parse(
                @"{'name':'scene-proof','setup_steps':[{'name':'Anchor','action':'load_scene','scene':'Assets/A.unity'}],'steps':[{'name':'Add','action':'load_scene','scene':'Assets/B.unity','load_mode':'additive'},{'name':'Loaded','action':'wait_scene','scene':'Assets/B.unity','scene_state':'loaded'},{'name':'Target','action':'wait_object','scene':'Assets/B.unity','target_id':'shared'},{'name':'Active','action':'set_active_scene','scene':'Assets/B.unity'},{'name':'Unload','action':'unload_scene','scene':'Assets/A.unity'}]}"
            );

        [Test]
        public void ExplicitScopeFindsLateSceneInSubstantialLoadedSet()
        {
            Scene previous = SceneManager.GetActiveScene();
            var scenes = new System.Collections.Generic.List<Scene>();
            string folder = "Assets/ScenarioScope-" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            try
            {
                for (int index = 0; index < 33; index++)
                {
                    Scene created = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    scenes.Add(created);
                    Assert.That(EditorSceneManager.SaveScene(created, folder + "/scene-" + index + ".unity"), Is.True);
                }
                Scene last = scenes[scenes.Count - 1];
                string path = folder + "/last.unity";
                Assert.That(EditorSceneManager.SaveScene(last, path), Is.True);
                Assert.That(PlayScenarioSceneScope.Find(path), Is.EqualTo(last));
            }
            finally
            {
                SceneManager.SetActiveScene(previous);
                for (int index = scenes.Count - 1; index >= 0; index--)
                    EditorSceneManager.CloseScene(scenes[index], true);
                AssetDatabase.DeleteAsset(folder);
            }
        }

        [Test]
        public void DefinitionRoundTripPreservesFieldsAndWholeRunFeatureMode()
        {
            PlayScenarioDefinition value = PlayScenarioDefinition.Parse(Definition());
            PropertyInfo mode = typeof(PlayScenarioDefinition).GetProperty("UsesExplicitSceneSemantics");
            Assert.That(mode, Is.Not.Null);
            Assert.That(mode.GetValue(value), Is.True);
            JObject json = JObject.Parse(JsonConvert.SerializeObject(value, new JsonSerializerSettings { NullValueHandling = NullValueHandling.Include }));
            Assert.That(json["setup_steps"][0]["load_mode"], Is.Null);
            Assert.That(json["setup_steps"][0]["scene_state"], Is.Null);
            Assert.That((string)json["steps"][0]["load_mode"], Is.EqualTo("additive"));
            Assert.That((string)json["steps"][1]["scene_state"], Is.EqualTo("loaded"));
            Assert.That(
                PlayScenarioDefinition.Parse((JObject)typeof(PlayScenarioStore).GetMethod("ToJson", Flags).Invoke(null, new object[] { value })).Steps.Count,
                Is.EqualTo(5)
            );
            string temporary = Path.Combine(Path.GetTempPath(), "play08-store-" + Guid.NewGuid().ToString("N"));
            try
            {
                Directory.CreateDirectory(temporary);
                var store = new PlayScenarioStore(temporary);
                store.Save(value);
                PlayScenarioDefinition restored = store.Get(value.Name);
                Assert.That(mode.GetValue(restored), Is.True);
                string stored = File.ReadAllText(Path.Combine(temporary, "ProjectSettings/MCPForUnity/PlayScenarios/scene-proof.json"));
                Assert.That((string)JObject.Parse(stored)["steps"][0]["load_mode"], Is.EqualTo("additive"));
                Assert.That(JObject.Parse(stored)["setup_steps"][0]["load_mode"], Is.Null);
            }
            finally
            {
                if (Directory.Exists(temporary))
                    Directory.Delete(temporary, true);
            }
        }

        [TestCase("load_mode", "null")]
        [TestCase("load_mode", "true")]
        [TestCase("load_mode", "'ADD'")]
        [TestCase("scene_state", "'loaded'")]
        public void InvalidSceneOptionsAreRejected(string key, string token)
        {
            JObject value = Definition();
            value["steps"][0][key] = JToken.Parse(token.Replace("'", "\""));
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
        }

        [Test]
        public void FirstAdditiveAnchorIsRejectedAndLegacyModeRemainsFalse()
        {
            JObject value = Definition();
            value["setup_steps"][0]["load_mode"] = "additive";
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
            value = Definition();
            value["steps"] = new JArray(
                new JObject
                {
                    ["name"] = "Wait",
                    ["action"] = "wait_scene",
                    ["scene"] = "Assets/A.unity",
                }
            );
            PlayScenarioDefinition legacy = PlayScenarioDefinition.Parse(value);
            Assert.That(typeof(PlayScenarioDefinition).GetProperty("UsesExplicitSceneSemantics").GetValue(legacy), Is.False);
        }

        private sealed class PendingSceneHost : IPlayScenarioHost
        {
            private readonly object session;
            private readonly MethodInfo evaluate;
            private readonly MethodInfo release;
            public int Dispatches;
            public bool OriginalDone;
            public bool CleanupDone;

            public PendingSceneHost(Type control)
            {
                ConstructorInfo constructor = control.GetConstructor(
                    BindingFlags.Instance | BindingFlags.NonPublic,
                    null,
                    new[] { typeof(bool), typeof(Func<string, LoadSceneMode, AsyncOperation>), typeof(Func<AsyncOperation, bool>) },
                    null
                );
                Assert.That(constructor, Is.Not.Null, "A narrow completion-reader seam must prevent native isDone calls on fake operations.");
                var first = new AsyncOperation();
                var second = new AsyncOperation();
                session = constructor.Invoke(
                    new object[]
                    {
                        true,
                        (Func<string, LoadSceneMode, AsyncOperation>)((path, mode) => ++Dispatches == 1 ? first : second),
                        (Func<AsyncOperation, bool>)(operation => ReferenceEquals(operation, first) ? OriginalDone : CleanupDone),
                    }
                );
                evaluate = control.GetMethod("Evaluate");
                release = control.GetMethod("Release");
            }

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
            {
                try
                {
                    return (PlayScenarioObservation)evaluate.Invoke(session, new object[] { step, firstPoll });
                }
                catch (TargetInvocationException error)
                {
                    throw error.InnerException;
                }
            }

            public void Release() => release.Invoke(session, null);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void EngineCancelCleanupWaitsForOwnedOperationWithoutDeferredDispatch(bool expire)
        {
            Type control = Control();
            Scene previous = SceneManager.GetActiveScene(),
                owned = default;
            string folder = "Assets/Play08Cleanup_" + Guid.NewGuid().ToString("N");
            PlayScenarioEngine engine = null;
            try
            {
                Call(control, "ResetForTests");
                AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
                owned = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                Assert.That(EditorSceneManager.SaveScene(owned, folder + "/A.unity"), Is.True);
                SceneManager.SetActiveScene(owned);
                var definition = PlayScenarioDefinition.Parse(
                    new JObject
                    {
                        ["name"] = "cleanup-wait",
                        ["poll_interval_ms"] = 100,
                        ["cleanup_timeout_seconds"] = 1,
                        ["completion_stable_ms"] = 0,
                        ["steps"] = new JArray(
                            new JObject
                            {
                                ["name"] = "Anchor",
                                ["action"] = "load_scene",
                                ["scene"] = owned.path,
                                ["load_mode"] = "single",
                            }
                        ),
                        ["cleanup_steps"] = new JArray(
                            new JObject
                            {
                                ["name"] = "Cleanup",
                                ["action"] = "load_scene",
                                ["scene"] = owned.path,
                                ["timeout_seconds"] = 1,
                            }
                        ),
                    }
                );
                var host = new PendingSceneHost(control);
                engine = new PlayScenarioEngine(PlayScenarioEngine.Create(definition, new string('d', 32), 1, 30, 1000), host);
                engine.EnteredPlayMode();
                engine.Tick(1000, true);
                Assert.That(host.Dispatches, Is.EqualTo(1));
                engine.Cancel(1050);
                engine.Tick(1100, true);
                Assert.That(engine.State.Phase, Is.EqualTo("cleaning"));
                Assert.That(engine.State.Steps[1].Status, Is.EqualTo("running"));
                Assert.That(host.Dispatches, Is.EqualTo(1));
                if (expire)
                {
                    engine.Tick(2200, true);
                    Assert.That(engine.Running, Is.False);
                    Assert.That(engine.State.Steps[1].Status, Is.EqualTo("timed_out"));
                    host.OriginalDone = true;
                    engine.Tick(2300, true);
                    Assert.That(host.Dispatches, Is.EqualTo(1));
                }
                else
                {
                    host.OriginalDone = true;
                    engine.Tick(1200, true);
                    Assert.That(host.Dispatches, Is.EqualTo(2));
                    host.CleanupDone = true;
                    engine.Tick(1300, true);
                    Assert.That(engine.State.Steps[1].Status, Is.EqualTo("passed"));
                    Assert.That(engine.State.Status, Is.EqualTo("cancelled"));
                    engine.Tick(1400, true);
                    Assert.That(host.Dispatches, Is.EqualTo(2));
                }
            }
            finally
            {
                engine?.Release();
                Call(control, "ResetForTests");
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
                if (owned.IsValid() && owned.isLoaded)
                    EditorSceneManager.CloseScene(owned, true);
                AssetDatabase.DeleteAsset(folder);
            }
        }

        private sealed class ExistingSceneWaitHost : IPlayScenarioHost
        {
            private readonly UnityPlayScenarioHost host = new UnityPlayScenarioHost(null, true);

            // The fixture has already created its owned scene: no Single load may disturb an outer runner.
            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll) =>
                step.Action == "load_scene" ? new PlayScenarioObservation(true, "Owned fixture scene already loaded.") : host.Evaluate(step, firstPoll);

            public void Release() => host.Release();
        }

        [TestCase("active")]
        [TestCase("loaded")]
        public void WaitSceneRejectsReplacementWithinActiveOrStableLoadedStep(string state)
        {
            Scene previous = SceneManager.GetActiveScene(),
                a = default,
                b = default;
            string folder = "Assets/Play08Wait_" + Guid.NewGuid().ToString("N");
            UnityPlayScenarioHost host = null;
            PlayScenarioPlayerHost player = null;
            PlayScenarioEngine stability = null;
            try
            {
                AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
                int sceneCountBefore = SceneManager.sceneCount;
                a = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                var ownedSceneHandle = a.handle;
                Assert.That(a.IsValid() && a.isLoaded && a.path.Length == 0 && a.rootCount == 0, Is.True);
                Assert.That(ownedSceneHandle, Is.Not.EqualTo(previous.handle));
                Assert.That(SceneManager.sceneCount, Is.EqualTo(sceneCountBefore + 1));
                Assert.That(EditorSceneManager.SaveScene(a, folder + "/A.unity"), Is.True);
                Assert.That(a.handle, Is.EqualTo(ownedSceneHandle));
                Assert.That(a.rootCount, Is.Zero);
                Assert.That(a.path, Is.EqualTo(folder + "/A.unity"));
                b = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                Assert.That(EditorSceneManager.SaveScene(b, folder + "/B.unity"), Is.True);
                SceneManager.SetActiveScene(a);
                host = new UnityPlayScenarioHost(null, true);
                player = new PlayScenarioPlayerHost(new[] { a.path, b.path }, null, true);
                var step = new PlayScenarioStep
                {
                    Name = "Bound wait",
                    Action = "wait_scene",
                    Scene = b.path,
                    StableForMs = 500,
                };
                typeof(PlayScenarioStep).GetField("SceneState").SetValue(step, state);
                Assert.That(host.Evaluate(step, true).Ready, Is.EqualTo(state == "loaded"));
                Assert.That(player.Evaluate(step, true).Ready, Is.EqualTo(state == "loaded"));
                if (state == "loaded")
                {
                    var definition = PlayScenarioDefinition.Parse(
                        new JObject
                        {
                            ["name"] = "stable-scene",
                            ["poll_interval_ms"] = 100,
                            ["steps"] = new JArray(
                                new JObject
                                {
                                    ["name"] = "Anchor",
                                    ["action"] = "load_scene",
                                    ["scene"] = a.path,
                                },
                                new JObject
                                {
                                    ["name"] = "Stable loaded",
                                    ["action"] = "wait_scene",
                                    ["scene"] = b.path,
                                    ["scene_state"] = "loaded",
                                    ["stable_for_ms"] = 500,
                                }
                            ),
                        }
                    );
                    stability = new PlayScenarioEngine(PlayScenarioEngine.Create(definition, new string('e', 32), 1, 30, 1000), new ExistingSceneWaitHost());
                    stability.EnteredPlayMode();
                    stability.Tick(1000, true);
                    stability.Tick(1100, true);
                    Assert.That(stability.State.Steps[1].StableSinceUnixMs, Is.EqualTo(1100));
                    Assert.That(stability.Running, Is.True);
                }
                string path = b.path;
                EditorSceneManager.CloseScene(b, true);
                b = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
                SceneManager.SetActiveScene(b);
                Assert.Throws<InvalidOperationException>(() => host.Evaluate(step, false));
                Assert.Throws<InvalidOperationException>(() => player.Evaluate(step, false));
                if (stability != null)
                {
                    stability.Tick(1200, true);
                    Assert.That(stability.State.Status, Is.EqualTo("failed"));
                    Assert.That(stability.State.Steps[1].Status, Is.EqualTo("failed"));
                    Assert.That(stability.State.Failure.Code, Is.EqualTo("action_exception"));
                }
                var later = new PlayScenarioStep
                {
                    Name = "Later authored wait",
                    Action = "wait_scene",
                    Scene = path,
                };
                typeof(PlayScenarioStep).GetField("SceneState").SetValue(later, state);
                Assert.That(host.Evaluate(later, true).Ready, Is.True);
                Assert.That(player.Evaluate(later, true).Ready, Is.True);
            }
            finally
            {
                stability?.Release();
                host?.Release();
                player?.Release();
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
                if (b.IsValid() && b.isLoaded)
                    EditorSceneManager.CloseScene(b, true);
                if (a.IsValid() && a.isLoaded)
                    EditorSceneManager.CloseScene(a, true);
                AssetDatabase.DeleteAsset(folder);
            }
        }

        private static Type Control()
        {
            Type type = typeof(PlayScenarioPlayerHost).Assembly.GetType("MCPForUnity.Runtime.PlayScenarios.PlayScenarioSceneOperation");
            Assert.That(type, Is.Not.Null, "Bounded scene operation facility is required.");
            return type;
        }

        private static object Call(Type type, string name, params object[] args) => type.GetMethod(name, Flags).Invoke(null, args);

        [Test]
        public void PendingLeaseWaitsWithoutDispatchThenKnownCompletionClears()
        {
            Type type = Control();
            bool done = false;
            int completionReads = 0;
            try
            {
                Call(type, "ResetForTests");
                Call(
                    type,
                    "TrackCompletion",
                    (Func<bool>)(
                        () =>
                        {
                            completionReads++;
                            return done;
                        }
                    )
                );
                Assert.That(Call(type, "TryAdmit", true), Is.False);
                Assert.That(Call(type, "TryAdmit", false), Is.True, "Wholly legacy admission remains compatible.");
                done = true;
                Assert.That(Call(type, "TryAdmit", true), Is.True);
                Assert.That(Call(type, "TryAdmit", true), Is.True);
                Assert.That(completionReads, Is.EqualTo(2));
            }
            finally
            {
                Call(type, "ResetForTests");
            }
        }

        [Test]
        public void TerminalDetachDoesNotEraseLeaseAndReloadUncertaintySurvivesRestore()
        {
            Type type = Control();
            try
            {
                Call(type, "ResetForTests");
                Call(type, "TrackCompletion", (Func<bool>)(() => false));
                Assert.That(Call(type, "CaptureReloadUnknown"), Is.True);
                Call(type, "RestoreReloadUnknown", false);
                TargetInvocationException failure = Assert.Throws<TargetInvocationException>(() => Call(type, "TryAdmit", true));
                Assert.That(failure.InnerException.Message, Does.Contain("scene_operation_state_unknown"));
                int dispatches = 0;
                object anchor = Activator.CreateInstance(
                    type,
                    new object[]
                    {
                        true,
                        (Func<string, LoadSceneMode, AsyncOperation>)(
                            (path, mode) =>
                            {
                                dispatches++;
                                return null;
                            }
                        ),
                    }
                );
                Assert.Throws<TargetInvocationException>(() =>
                    type.GetMethod("Evaluate")
                        .Invoke(
                            anchor,
                            new object[]
                            {
                                new PlayScenarioStep
                                {
                                    Name = "Anchor",
                                    Action = "load_scene",
                                    Scene = "Assets/A.unity",
                                },
                                true,
                            }
                        )
                );
                Assert.That(dispatches, Is.Zero, "Unknown admission cannot bypass through an omitted Single anchor.");
                Assert.That(Call(type, "TryAdmit", false), Is.True);
                Call(type, "ResetForTests");
                Assert.That(Call(type, "TryAdmit", true), Is.True, "Fresh session recovery.");
            }
            finally
            {
                Call(type, "ResetForTests");
            }
        }

        [Test]
        public void OmittedAnchorWaiterCannotDispatchAfterItIsReleased()
        {
            Type type = Control();
            bool done = false;
            int dispatches = 0;
            object waiter = null;
            try
            {
                Call(type, "ResetForTests");
                Call(type, "TrackCompletion", (Func<bool>)(() => done));
                var load =
                    (Func<string, LoadSceneMode, AsyncOperation>)(
                        (path, mode) =>
                        {
                            dispatches++;
                            return null;
                        }
                    );
                waiter = Activator.CreateInstance(type, new object[] { true, load });
                var anchor = new PlayScenarioStep
                {
                    Name = "Omitted anchor",
                    Action = "load_scene",
                    Scene = "Assets/A.unity",
                };
                var evaluate = type.GetMethod("Evaluate");
                Assert.That(((PlayScenarioObservation)evaluate.Invoke(waiter, new object[] { anchor, true })).Ready, Is.False);
                Assert.That(((PlayScenarioObservation)evaluate.Invoke(waiter, new object[] { anchor, false })).Ready, Is.False);
                Assert.That(dispatches, Is.Zero);
                type.GetMethod("Release").Invoke(waiter, null);
                done = true;
                Assert.That(Call(type, "TryAdmit", true), Is.True);
                Assert.That(dispatches, Is.Zero, "There is no deferred dispatch callback after cancel/release.");
                object next = Activator.CreateInstance(type, new object[] { true, load });
                TargetInvocationException refused = Assert.Throws<TargetInvocationException>(() => evaluate.Invoke(next, new object[] { anchor, true }));
                Assert.That(refused.InnerException, Is.TypeOf<InvalidOperationException>());
                Assert.That(dispatches, Is.EqualTo(1), "Only the new live caller attempted the native adapter.");
            }
            finally
            {
                if (waiter != null)
                    type.GetMethod("Release").Invoke(waiter, null);
                Call(type, "ResetForTests");
            }
        }

        [Test]
        public void KnownCompletedLeaseDoesNotInventReloadUncertainty()
        {
            Type type = Control();
            try
            {
                Call(type, "ResetForTests");
                Call(type, "TrackCompletion", (Func<bool>)(() => true));
                Assert.That(Call(type, "CaptureReloadUnknown"), Is.False);
                Assert.That(Call(type, "TryAdmit", true), Is.True);
            }
            finally
            {
                Call(type, "ResetForTests");
            }
        }

        [Test]
        public void SelectedSceneSeparatesIdenticalIdsAndAlreadyActiveIsSuccess()
        {
            Scene previous = SceneManager.GetActiveScene();
            Scene a = default,
                b = default;
            string folder = "Assets/Play08Scope_" + Guid.NewGuid().ToString("N");
            try
            {
                AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
                int sceneCountBefore = SceneManager.sceneCount;
                a = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                var ownedSceneHandle = a.handle;
                Assert.That(a.IsValid() && a.isLoaded && a.path.Length == 0 && a.rootCount == 0, Is.True);
                Assert.That(ownedSceneHandle, Is.Not.EqualTo(previous.handle));
                Assert.That(SceneManager.sceneCount, Is.EqualTo(sceneCountBefore + 1));
                Assert.That(EditorSceneManager.SaveScene(a, folder + "/A.unity"), Is.True);
                Assert.That(a.handle, Is.EqualTo(ownedSceneHandle));
                Assert.That(a.rootCount, Is.Zero);
                Assert.That(a.path, Is.EqualTo(folder + "/A.unity"));
                b = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                Assert.That(EditorSceneManager.SaveScene(b, folder + "/B.unity"), Is.True);
                GameObject first = new GameObject("Root");
                SceneManager.MoveGameObjectToScene(first, a);
                first.AddComponent<PlayScenarioTarget>().TargetId = "shared";
                GameObject second = new GameObject("Root");
                SceneManager.MoveGameObjectToScene(second, b);
                second.AddComponent<PlayScenarioTarget>().TargetId = "shared";
                second.SetActive(false);
                SceneManager.SetActiveScene(a);
                ConstructorInfo constructor = typeof(UnityPlayScenarioHost).GetConstructor(new[] { typeof(PlayScenarioLogPolicy), typeof(bool) });
                Assert.That(constructor, Is.Not.Null);
                var host = (UnityPlayScenarioHost)constructor.Invoke(new object[] { null, true });
                try
                {
                    var step = new PlayScenarioStep
                    {
                        Name = "B only",
                        Action = "wait_object",
                        Scene = b.path,
                        TargetId = "shared",
                        Active = false,
                    };
                    Assert.That(host.Evaluate(step, true).Ready, Is.True);
                    var playerConstructor = typeof(PlayScenarioPlayerHost).GetConstructor(
                        new[] { typeof(System.Collections.Generic.IEnumerable<string>), typeof(Func<int>), typeof(bool) }
                    );
                    Assert.That(playerConstructor, Is.Not.Null);
                    var player = (PlayScenarioPlayerHost)
                        playerConstructor.Invoke(new object[] { new[] { a.path, b.path, folder + "/Missing.unity" }, null, true });
                    try
                    {
                        Assert.That(player.Evaluate(step, true).Ready, Is.True);
                        var missing = new PlayScenarioStep
                        {
                            Name = "Missing scope",
                            Action = "wait_object",
                            Scene = folder + "/Missing.unity",
                            TargetId = "shared",
                            Count = 0,
                        };
                        Assert.That(host.Evaluate(missing, true).Ready, Is.False, "A missing scene is not a loaded scene with no target.");
                        Assert.That(player.Evaluate(missing, true).Ready, Is.False);
                    }
                    finally
                    {
                        player.Release();
                    }
                    Assert.That(
                        host.Evaluate(
                            new PlayScenarioStep
                            {
                                Name = "Same",
                                Action = "set_active_scene",
                                Scene = a.path,
                            },
                            true
                        ).Ready,
                        Is.True
                    );
                    Assert.Throws<InvalidOperationException>(() =>
                        host.Evaluate(
                            new PlayScenarioStep
                            {
                                Name = "Refuse",
                                Action = "unload_scene",
                                Scene = a.path,
                            },
                            true
                        )
                    );
                    Assert.That(
                        host.Evaluate(
                            new PlayScenarioStep
                            {
                                Name = "Loaded",
                                Action = "wait_scene",
                                Scene = b.path,
                            },
                            true
                        ).Ready,
                        Is.False,
                        "Legacy active state is still active, not merely loaded."
                    );
                    Type scopeType = typeof(PlayScenarioPlayerHost).Assembly.GetType("MCPForUnity.Runtime.PlayScenarios.PlayScenarioSceneScope");
                    object scope = Activator.CreateInstance(scopeType);
                    MethodInfo resolve = scopeType.GetMethod("Resolve");
                    string selectedPath = b.path;
                    resolve.Invoke(scope, new object[] { selectedPath, true });
                    EditorSceneManager.CloseScene(b, true);
                    b = EditorSceneManager.OpenScene(selectedPath, OpenSceneMode.Additive);
                    var replacement = Assert.Throws<TargetInvocationException>(() => resolve.Invoke(scope, new object[] { selectedPath, false }));
                    Assert.That(replacement.InnerException, Is.TypeOf<InvalidOperationException>());
                }
                finally
                {
                    host.Release();
                }
            }
            finally
            {
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
                if (b.IsValid() && b.isLoaded)
                    EditorSceneManager.CloseScene(b, true);
                if (a.IsValid() && a.isLoaded)
                    EditorSceneManager.CloseScene(a, true);
                AssetDatabase.DeleteAsset(folder);
            }
        }
    }
}
