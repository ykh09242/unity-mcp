using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.PlayScenarios
{
    public class PlayScenarioOperationsTests
    {
        private sealed class Host : IPlayScenarioHost, IPlayScenarioResourceHost
        {
            public Func<PlayScenarioStep, PlayScenarioObservation> EvaluateStep;
            public Func<PlayScenarioRegisteredResources> Capture;
            public readonly List<string> Events = new List<string>();
            public int Releases;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
            {
                Events.Add(step.Name);
                return EvaluateStep?.Invoke(step) ?? new PlayScenarioObservation(true, "ready");
            }

            public PlayScenarioRegisteredResources CaptureResources()
            {
                Events.Add("capture");
                return Capture?.Invoke() ?? PlayScenarioResourceTracker.Capture();
            }

            public void Release() => Releases++;
        }

        private static readonly MethodInfo Reset = typeof(PlayScenarioResourceTracker).GetMethod(
            "ResetOnPlayEntry",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        private readonly List<UnityEngine.Object> _objects = new List<UnityEngine.Object>();
        private readonly List<IDisposable> _tokens = new List<IDisposable>();

        [SetUp]
        public void SetUp() => Reset.Invoke(null, null);

        [TearDown]
        public void TearDown()
        {
            foreach (IDisposable token in _tokens)
                token.Dispose();
            foreach (UnityEngine.Object resource in _objects)
                if (resource != null)
                    UnityEngine.Object.DestroyImmediate(resource);
            _tokens.Clear();
            _objects.Clear();
            Reset.Invoke(null, null);
        }

        private static JObject DefinitionJson() =>
            JObject.Parse(
                @"{
            'name':'operations','completion_stable_ms':0,
            'resources':{'enabled':true},
            'setup_steps':[{'name':'setup','action':'load_scene','scene':'Assets/Menu.unity'}],
            'steps':[{'name':'main','action':'wait_object','target_id':'Player.Ready','timeout_seconds':1}],
            'cleanup_steps':[{'name':'cleanup','action':'load_scene','scene':'Assets/Menu.unity'}]
        }"
            );

        private static PlayScenarioEngine Engine(Host host, int repeat = 1, PlayScenarioDefinition definition = null)
        {
            var run = PlayScenarioEngine.Create(definition ?? PlayScenarioDefinition.Parse(DefinitionJson()), new string('a', 32), repeat, 30, 1000);
            var engine = new PlayScenarioEngine(run, host);
            engine.EnteredPlayMode();
            return engine;
        }

        private static void Complete(PlayScenarioEngine engine)
        {
            for (long now = 1000; now <= 10000 && engine.Running; now += 250)
                engine.Tick(now, true);
        }

        private PlayScenarioRegisteredClone NewClone()
        {
            var clone = ScriptableObject.CreateInstance<PlayScenarioRegisteredClone>();
            _objects.Add(clone);
            PlayScenarioResourceTracker.RegisterScriptableObject(clone);
            return clone;
        }

        private IDisposable Handle()
        {
            IDisposable token = PlayScenarioResourceTracker.RegisterHandle();
            _tokens.Add(token);
            return token;
        }

        private IDisposable Subscription()
        {
            IDisposable token = PlayScenarioResourceTracker.RegisterSubscription();
            _tokens.Add(token);
            return token;
        }

        [Test]
        public void ResourcesAreCapturedBeforeSetupAndAfterCleanupOnlyEachIteration()
        {
            PlayScenarioRegisteredClone clone = null;
            IDisposable handle = null;
            IDisposable subscription = null;
            var host = new Host
            {
                EvaluateStep = step =>
                {
                    if (step.Name == "setup")
                    {
                        clone = NewClone();
                        handle = Handle();
                        subscription = Subscription();
                    }
                    if (step.Name == "cleanup")
                    {
                        UnityEngine.Object.DestroyImmediate(clone);
                        handle.Dispose();
                        subscription.Dispose();
                    }
                    return new PlayScenarioObservation(true, "ready");
                },
            };
            var engine = Engine(host, repeat: 2);
            Complete(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(host.Events, Is.EqualTo(new[] { "capture", "setup", "main", "cleanup", "capture", "capture", "setup", "main", "cleanup", "capture" }));
            Assert.That(engine.State.ResourceChecks.Count, Is.EqualTo(2));
            Assert.That(engine.State.ResourceChecks.All(check => check.Passed), Is.True);
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [TestCase("scriptable_object")]
        [TestCase("subscription")]
        [TestCase("handle")]
        public void RegisteredLeaksFailExplicitAssertions(string kind)
        {
            var host = new Host
            {
                EvaluateStep = step =>
                {
                    if (step.Name == "main")
                    {
                        if (kind == "scriptable_object")
                            NewClone();
                        if (kind == "subscription")
                            Subscription();
                        if (kind == "handle")
                            Handle();
                    }
                    return new PlayScenarioObservation(true, "ready");
                },
            };
            var engine = Engine(host, repeat: 2);
            Complete(engine);
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("resource_assertion_failed"));
            Assert.That(engine.State.Failure.Iteration, Is.EqualTo(1));
            Assert.That(engine.State.CleanupFailures.Single().Code, Is.EqualTo("resource_assertion_failed"));
            Assert.That(engine.State.ResourceChecks.Single().Passed, Is.False);
            Assert.That(engine.State.Steps.Where(step => step.Iteration == 2).All(step => step.Status == "skipped"), Is.True);
        }

        [Test]
        public void ReplacingAnOldHandleCannotMaskANewLeakedIdentity()
        {
            IDisposable baseline = Handle();
            var host = new Host
            {
                EvaluateStep = step =>
                {
                    if (step.Name == "main")
                    {
                        baseline.Dispose();
                        Handle();
                    }
                    return new PlayScenarioObservation(true, "ready");
                },
            };
            var engine = Engine(host);
            Complete(engine);
            Assert.That(engine.State.ResourceChecks.Single().NewHandles, Is.EqualTo(1));
            Assert.That(PlayScenarioResourceTracker.Capture().HandleCount, Is.EqualTo(1));
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
        }

        [TestCase("failed")]
        [TestCase("cancelled")]
        [TestCase("timed_out")]
        public void PrimaryOutcomeSurvivesResourceFailureAfterCleanup(string outcome)
        {
            var host = new Host { EvaluateStep = _ => new PlayScenarioObservation(false, "not ready") };
            var engine = Engine(host);
            engine.Tick(1000, true);
            Handle();
            if (outcome == "failed")
                engine.RequestFailure("unexpected", 1250);
            if (outcome == "cancelled")
                engine.Cancel(1250);
            if (outcome == "timed_out")
                engine.Tick(32000, true);
            host.EvaluateStep = _ => new PlayScenarioObservation(true, "cleaned");
            engine.Tick(32500, true);
            Assert.That(engine.State.Status, Is.EqualTo(outcome));
            Assert.That(
                engine.State.Failure.Code,
                Is.EqualTo(
                    outcome == "failed" ? "unexpected_log"
                    : outcome == "cancelled" ? "cancelled"
                    : "run_timeout"
                )
            );
            Assert.That(engine.State.CleanupFailures.Single().Code, Is.EqualTo("resource_assertion_failed"));
            Assert.That(engine.State.ResourceChecks.Single().NewHandles, Is.EqualTo(1));
        }

        [Test]
        public void MeasurementFailureFailsBeforeDispatchAndDisabledAssertionsNeverSample()
        {
            var host = new Host { Capture = () => throw new InvalidOperationException("measurement missing") };
            var engine = Engine(host);
            Complete(engine);
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("resource_measurement_failed"));
            Assert.That(host.Events, Is.EqualTo(new[] { "capture" }));
            var definition = PlayScenarioDefinition.Parse(DefinitionJson());
            definition.Resources.Enabled = false;
            host.Events.Clear();
            engine = Engine(host, definition: definition);
            Complete(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(host.Events, Does.Not.Contain("capture"));
        }

        [Test]
        public void DestroyedNativeCloneIsAbsentEvenWhenManagedWrapperRemainsReferenced()
        {
            PlayScenarioRegisteredClone clone = NewClone();
            PlayScenarioResourceTracker.RegisterScriptableObject(clone);
            Assert.That(PlayScenarioResourceTracker.Capture().ScriptableObjectIds.Length, Is.EqualTo(1));
            UnityEngine.Object.DestroyImmediate(clone);
            Assert.That(ReferenceEquals(clone, null), Is.False);
            Assert.That(PlayScenarioResourceTracker.Capture().ScriptableObjectIds, Is.Empty);
        }

        [Test]
        public void PersistentAssetsAreRejectedAndDeadRegistrationsArePrunedAtCapacity()
        {
            string assetPath = "Assets/ScenarioResourceAsset-" + Guid.NewGuid().ToString("N") + ".asset";
            var asset = ScriptableObject.CreateInstance<PlayScenarioRegisteredClone>();
            try
            {
                AssetDatabase.CreateAsset(asset, assetPath);
                Assert.Throws<ArgumentException>(() => PlayScenarioResourceTracker.RegisterScriptableObject(asset));
                Assert.That(PlayScenarioResourceTracker.Capture().ScriptableObjectCount, Is.Zero);
            }
            finally
            {
                AssetDatabase.DeleteAsset(assetPath);
                if (asset != null)
                    UnityEngine.Object.DestroyImmediate(asset);
            }
            for (int i = 0; i < PlayScenarioResourceTracker.RegistrationLimit; i++)
            {
                PlayScenarioRegisteredClone clone = NewClone();
                UnityEngine.Object.DestroyImmediate(clone);
            }
            Assert.DoesNotThrow(() => NewClone());
            Assert.That(PlayScenarioResourceTracker.Capture().ScriptableObjectCount, Is.EqualTo(1));
            Assert.That(PlayScenarioResourceTracker.Capture().RegistrationFailureCount, Is.Zero);
            var registrations = (IDictionary)
                typeof(PlayScenarioResourceTracker).GetField("ScriptableObjects", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            Assert.That(registrations.Count, Is.EqualTo(1));
            Assert.That(registrations.Keys.Cast<object>().All(key => key is ulong), Is.True);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MainThreadTokensReclaimDestroyedClonesAtCapacityWithoutAnIntermediateCapture(bool subscription)
        {
            for (int i = 0; i < PlayScenarioResourceTracker.RegistrationLimit; i++)
            {
                PlayScenarioRegisteredClone clone = NewClone();
                UnityEngine.Object.DestroyImmediate(clone);
            }
            Assert.DoesNotThrow(() =>
            {
                if (subscription)
                    Subscription();
                else
                    Handle();
            });
            PlayScenarioRegisteredResources snapshot = PlayScenarioResourceTracker.Capture();
            Assert.That(snapshot.ScriptableObjectCount, Is.Zero);
            Assert.That(snapshot.HandleCount, Is.EqualTo(subscription ? 0 : 1));
            Assert.That(snapshot.SubscriptionCount, Is.EqualTo(subscription ? 1 : 0));
            Assert.That(snapshot.RegistrationFailureCount, Is.Zero);
        }

        [Test]
        public void OffThreadTokenCapacityFailureDoesNotAttemptNativeReclamation()
        {
            for (int i = 0; i < PlayScenarioResourceTracker.RegistrationLimit; i++)
            {
                PlayScenarioRegisteredClone clone = NewClone();
                UnityEngine.Object.DestroyImmediate(clone);
            }
            Exception observed = null;
            var thread = new Thread(() =>
            {
                try
                {
                    PlayScenarioResourceTracker.RegisterHandle().Dispose();
                }
                catch (Exception error)
                {
                    observed = error;
                }
            });
            thread.Start();
            Assert.That(thread.Join(5000), Is.True, "Worker registration must complete within its bounded wait.");
            Assert.That(observed, Is.TypeOf<InvalidOperationException>());
            Assert.That(observed.Message, Does.Contain("off the Unity main thread"));
            var registrations = (IDictionary)
                typeof(PlayScenarioResourceTracker).GetField("ScriptableObjects", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            Assert.That(registrations.Count, Is.EqualTo(PlayScenarioResourceTracker.RegistrationLimit));
            PlayScenarioRegisteredResources snapshot = PlayScenarioResourceTracker.Capture();
            Assert.That(snapshot.ScriptableObjectCount, Is.Zero);
            Assert.That(snapshot.RegistrationFailureCount, Is.EqualTo(1));
            Assert.That(snapshot.HandleCount, Is.Zero);
        }

#if UNITY_6000_6_OR_NEWER
        [Test]
        public void FullNativeIdentitiesDoNotGrowTheUnboundedCompatibilityCache()
        {
            var cache = (IDictionary)
                typeof(MCPForUnity.Runtime.Helpers.UnityObjectIdCompat).GetField("_fullIdCache", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            int before = cache.Count;
            CloneNativeRegistrationBatch();
            Assert.That(cache.Count, Is.EqualTo(before));
        }

        private void CloneNativeRegistrationBatch()
        {
            for (int i = 0; i < 32; i++)
            {
                PlayScenarioRegisteredClone clone = NewClone();
                UnityEngine.Object.DestroyImmediate(clone);
            }
            Assert.That(PlayScenarioResourceTracker.Capture().ScriptableObjectIds, Is.Empty);
        }
#endif

        [Test]
        public void InterruptionAfterRestoreReportsUnavailableResourceVerification()
        {
            var host = new Host { EvaluateStep = _ => new PlayScenarioObservation(false, "waiting") };
            var engine = Engine(host);
            engine.Tick(1000, true);
            var restored = JObject.FromObject(engine.State).ToObject<PlayScenarioRun>();
            engine.Release();
            var resumed = new PlayScenarioEngine(restored, host);
            resumed.Interrupt("Assembly reload", 1250);
            Assert.That(resumed.State.Status, Is.EqualTo("failed"));
            Assert.That(resumed.State.Failure.Code, Is.EqualTo("interrupted"));
            Assert.That(resumed.State.ResourceChecks.Single().Error, Does.Contain("unavailable"));
            Assert.That(resumed.State.CleanupFailures.Any(failure => failure.Code == "resource_measurement_failed"), Is.True);
        }

        [Test]
        public void PositiveThresholdAllowsOnlyTheConfiguredNumberOfNewResources()
        {
            var definition = PlayScenarioDefinition.Parse(DefinitionJson());
            definition.Resources.MaxHandles = 1;
            var host = new Host
            {
                EvaluateStep = step =>
                {
                    if (step.Name == "main")
                        Handle();
                    return new PlayScenarioObservation(true, "ready");
                },
            };
            var engine = Engine(host, definition: definition);
            Complete(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(engine.State.ResourceChecks.Single().NewHandles, Is.EqualTo(1));
        }

        [Test]
        public void TokensAreIdempotentAndOldPlayTokensCannotReleaseNewResources()
        {
            IDisposable old = Handle();
            Reset.Invoke(null, null);
            IDisposable current = Handle();
            old.Dispose();
            old.Dispose();
            Assert.That(PlayScenarioResourceTracker.Capture().HandleCount, Is.EqualTo(1));
            current.Dispose();
            current.Dispose();
            Assert.That(PlayScenarioResourceTracker.Capture().HandleCount, Is.Zero);
        }

        [Test]
        public void RegistrationCapacityIsBoundedAndCannotSilentlyProduceAPassingCheck()
        {
            for (int i = 0; i < PlayScenarioResourceTracker.RegistrationLimit; i++)
                Handle();
            Assert.Throws<InvalidOperationException>(() => Handle());
            foreach (IDisposable token in _tokens)
                token.Dispose();
            var host = new Host();
            var engine = Engine(host);
            Complete(engine);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("resource_measurement_failed"));
            Assert.That(PlayScenarioResourceTracker.Capture().RegistrationFailureCount, Is.EqualTo(1));
        }

        [Test]
        public void TypedConditionTimeoutKeepsExpectedActualAndPrimaryAttribution()
        {
            var host = new Host
            {
                EvaluateStep = step =>
                    step.Name == "main"
                        ? new PlayScenarioObservation(
                            false,
                            "not ready",
                            new PlayScenarioFailure
                            {
                                Code = "property_mismatch",
                                Message = "Field differs.",
                                Expected = "true",
                                Actual = "false",
                                Component = "Example.Readiness",
                                PropertyPath = "ready",
                            }
                        )
                        : new PlayScenarioObservation(true, "ready"),
            };
            var engine = Engine(host);
            Complete(engine);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("property_mismatch"));
            Assert.That(engine.State.Failure.Target, Is.EqualTo("Player.Ready"));
            Assert.That(engine.State.Failure.Stage, Is.EqualTo("main"));
            Assert.That(engine.State.Failure.Expected, Is.EqualTo("true"));
            Assert.That(engine.State.Failure.Actual, Is.EqualTo("false"));
            Assert.That(engine.State.ResourceChecks.Single().Passed, Is.True);
        }

        [Test]
        public void TypedExceptionAndCleanupExceptionRemainSeparate()
        {
            var host = new Host
            {
                EvaluateStep = step =>
                    step.Name == "main" || step.Name == "cleanup"
                        ? throw new PlayScenarioException(
                            new PlayScenarioFailure
                            {
                                Code = step.Name == "main" ? "target_ambiguous" : "capability_unavailable",
                                Message = step.Name,
                                Expected = "1",
                                Actual = "2",
                            }
                        )
                        : new PlayScenarioObservation(true, "ready"),
            };
            var engine = Engine(host);
            Complete(engine);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("target_ambiguous"));
            Assert.That(engine.State.CleanupFailures.Single().Code, Is.EqualTo("capability_unavailable"));
            Assert.That(engine.State.ResourceChecks.Single().Passed, Is.True);
        }

        [Test]
        public void TagsResourcesAndTargetIdentifiersRoundTripWithStrictBounds()
        {
            JObject value = DefinitionJson();
            value["tags"] = new JArray("smoke", "runtime");
            var definition = PlayScenarioDefinition.Parse(value);
            Assert.That(definition.Tags, Is.EqualTo(new[] { "smoke", "runtime" }));
            Assert.That(PlayScenarioDefinition.Parse(JObject.FromObject(definition)).Steps[0].TargetId, Is.EqualTo("Player.Ready"));
            foreach (JToken tags in new JToken[] { JValue.CreateNull(), new JArray("smoke", "smoke"), new JArray("Upper"), new JArray(1) })
            {
                var wrong = DefinitionJson();
                wrong["tags"] = tags;
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(wrong));
            }
            foreach (JToken limit in new JToken[] { -1, 4097, 1.0, "1", JValue.CreateNull() })
            {
                var wrong = DefinitionJson();
                wrong["resources"]["max_handles"] = limit;
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(wrong));
            }
            value["steps"][0]["count"] = 2;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
        }

        [Test]
        public void PlainSceneTimeoutRetainsExpectedSceneAndLastActualObservation()
        {
            var definition = PlayScenarioDefinition.Parse(DefinitionJson());
            definition.SetupSteps[0].TimeoutSeconds = 1;
            var host = new Host { EvaluateStep = _ => new PlayScenarioObservation(false, "scene operation is unfinished") };
            var engine = Engine(host, definition: definition);
            engine.Tick(1000, true);
            engine.Tick(2000, true);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("step_timeout"));
            Assert.That(engine.State.Failure.Expected, Is.EqualTo("Assets/Menu.unity loaded and active"));
            Assert.That(engine.State.Failure.Actual, Is.EqualTo("scene operation is unfinished"));
            Assert.That(engine.State.Failure.Stage, Is.EqualTo("setup"));
            host.EvaluateStep = _ => new PlayScenarioObservation(true, "ready");
            engine.Tick(2250, true);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
        }

        [Test]
        public void GenericActionExceptionRetainsExpectedCompletionAndActualException()
        {
            var host = new Host
            {
                EvaluateStep = step =>
                    step.Name == "main" ? throw new InvalidOperationException("listener failed") : new PlayScenarioObservation(true, "ready"),
            };
            var engine = Engine(host);
            Complete(engine);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("action_exception"));
            Assert.That(engine.State.Failure.Expected, Is.EqualTo("wait_object completed successfully"));
            Assert.That(engine.State.Failure.Actual, Is.EqualTo("InvalidOperationException: listener failed"));
            Assert.That(engine.State.Failure.Stage, Is.EqualTo("main"));
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
        }

        [Test]
        public void ReproductionHashAndNewReportsAreBoundedAndLegacyFieldsDefault()
        {
            var definition = PlayScenarioDefinition.Parse(DefinitionJson());
            string hash = PlayScenarioReproduction.Hash(definition);
            Assert.That(hash, Does.Match("^[a-f0-9]{64}$"));
            Assert.That(PlayScenarioReproduction.Hash(PlayScenarioDefinition.Parse(JObject.FromObject(definition))), Is.EqualTo(hash));
            var engine = Engine(new Host());
            Complete(engine);
            string directory = Path.Combine(Path.GetTempPath(), "mcp-scenario-operations-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new PlayScenarioStore(directory);
                store.SaveReport(engine.State);
                Assert.That(store.GetReport(engine.State.JobId).ResourceChecks.Single().Passed, Is.True);
                string path = Path.Combine(directory, engine.State.ReportPath);
                JObject report = JObject.Parse(File.ReadAllText(path));
                foreach (string field in new[] { "failure", "cleanup_failures", "reproduction", "resource_checks" })
                    report.Remove(field);
                File.WriteAllText(path, report.ToString());
                Assert.That(store.GetReport(engine.State.JobId).ResourceChecks, Is.Empty);
                Assert.That(store.GetReport(engine.State.JobId).Reproduction, Is.Null);
            }
            finally
            {
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }
    }
}
