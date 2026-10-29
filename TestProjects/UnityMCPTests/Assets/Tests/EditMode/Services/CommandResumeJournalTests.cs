using System;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class CommandResumeJournalTests
    {
        private string stored;
        private long now;
        private bool failWrite;
        private CommandResumeJournal journal;

        [SetUp]
        public void Setup()
        {
            stored = null;
            now = 1000;
            failWrite = false;
            journal = Open();
        }

        private CommandResumeJournal Open() =>
            new CommandResumeJournal(
                () => stored,
                value =>
                {
                    if (failWrite)
                        throw new InvalidOperationException("injected persistence failure");
                    stored = value;
                },
                () => now
            );

        private JObject Request(string action = "new", string id = null, long deadline = 10000) =>
            new JObject
            {
                ["version"] = 1,
                ["invocation_id"] = id ?? "11111111111111111111111111111111",
                ["editor_session_id"] = journal.SessionId,
                ["deadline_unix_ms"] = deadline,
                ["action"] = action,
            };

        [Test]
        public void QueuedReloadCanBeClaimedExactlyOnceAndStartedReloadCannotReplay()
        {
            var parameters = new JObject { ["value"] = 1 };
            var first = journal.Admit(Request(), "test", parameters);
            Assert.IsNotNull(first.Ticket);
            Assert.IsNull(journal.Admit(Request("resume"), "test", parameters).Ticket);
            journal = Open();
            var resumed = journal.Admit(Request("resume"), "test", parameters);
            Assert.IsNotNull(resumed.Ticket);
            Assert.IsTrue(journal.TryStart(resumed.Ticket));
            journal = Open();
            var uncertain = journal.Admit(Request("resume"), "test", parameters);
            Assert.AreEqual("unknown", uncertain.State);
            Assert.IsNull(uncertain.Ticket);
        }

        [Test]
        public void CompletionIsCachedWithoutInvokingAgainAndCanonicalArgumentsMatch()
        {
            var admitted = journal.Admit(Request(), "test", new JObject { ["b"] = 2, ["a"] = 1 });
            Assert.IsTrue(journal.TryStart(admitted.Ticket));
            journal.Complete(admitted.Ticket, new JObject { ["result"] = 42 });
            journal = Open();
            var replay = journal.Admit(Request("resume"), "test", new JObject { ["a"] = 1, ["b"] = 2 });
            Assert.AreEqual("completed", replay.State);
            Assert.AreEqual(42, (int)replay.Payload["result"]);
            Assert.IsNull(replay.Ticket);
        }

        [Test]
        public void CancelTombstoneSurvivesReloadAndCannotBeRecreated()
        {
            journal.Admit(Request(), "test", new JObject());
            Assert.AreEqual("canceled", journal.Admit(Request("cancel"), "test", new JObject()).State);
            journal = Open();
            Assert.AreEqual("canceled", journal.Admit(Request("resume"), "test", new JObject()).State);
            Assert.AreEqual("canceled", journal.Admit(Request(), "test", new JObject()).State);
        }

        [Test]
        public void MissingChangedDeadlineAndChangedArgumentsNeverGrantContinuation()
        {
            Assert.AreEqual("unknown", journal.Admit(Request("resume"), "test", new JObject()).State);
            journal.Admit(Request(), "test", new JObject());
            Assert.IsNull(journal.Admit(Request("resume", deadline: 11000), "test", new JObject()).Ticket);
            Assert.IsNull(journal.Admit(Request("resume"), "test", new JObject { ["different"] = true }).Ticket);
            now = 10001;
            Assert.AreEqual("expired", journal.Admit(Request("resume"), "test", new JObject()).State);
        }

        [Test]
        public void FailedStartedWritePreventsInvocationAndPoisonedStoreCannotGrantAdmission()
        {
            var admitted = journal.Admit(Request(), "test", new JObject());
            failWrite = true;
            Assert.IsFalse(journal.TryStart(admitted.Ticket));
            Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "test", new JObject()).State);
        }

        [Test]
        public void CorruptStoreAndCapacityFailClosedWithoutEvictingSafetyEntries()
        {
            for (int i = 0; i < CommandResumeJournal.MaxEntries; i++)
                Assert.IsNotNull(journal.Admit(Request(id: i.ToString("x32")), "test", new JObject()).Ticket);
            Assert.AreEqual("unavailable", journal.Admit(Request(id: "ffffffffffffffffffffffffffffffff"), "test", new JObject()).State);
            stored = "{broken";
            journal = Open();
            Assert.AreEqual("unavailable", journal.Admit(Request(), "test", new JObject()).State);
        }

        [Test]
        public void ConcurrentDuplicateDeliveryGrantsOnlyOneRuntimeClaim()
        {
            int claims = 0;
            Parallel.For(
                0,
                16,
                _ =>
                {
                    if (journal.Admit(Request(), "test", new JObject()).Ticket != null)
                        System.Threading.Interlocked.Increment(ref claims);
                }
            );
            Assert.AreEqual(1, claims);
        }

        [Test]
        public void UnstartedDisconnectReleasesClaimWithoutCancelingIntent()
        {
            var first = journal.Admit(Request(), "test", new JObject());
            journal.Release(first.Ticket);
            Assert.IsNotNull(journal.Admit(Request("resume"), "test", new JObject()).Ticket);
        }

        [Test]
        public void LateCompletionCannotEraseCancellationAndLargeResultsNeverReplay()
        {
            var admitted = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admitted.Ticket));
            journal.Admit(Request("cancel"), "test", new JObject());
            Assert.AreEqual("canceled", journal.Complete(admitted.Ticket, new JObject { ["done"] = true }).State);
            var other = journal.Admit(Request(id: "22222222222222222222222222222222"), "test", new JObject());
            Assert.IsTrue(journal.TryStart(other.Ticket));
            Assert.AreEqual("completed", journal.Complete(other.Ticket, new JValue(new string('x', CommandResumeJournal.MaxResultBytes + 1))).State);
            journal = Open();
            Assert.AreEqual("unavailable", journal.Admit(Request("resume", "22222222222222222222222222222222"), "test", new JObject()).State);
        }

        [Test]
        public void LocalObjectIdsAreNotReinterpretedAcrossReloadWhileSymbolicSelectorsStayEligible()
        {
            var unsafeParameters = new JObject { ["commands"] = new JArray(new JObject { ["params"] = new JObject { ["target"] = -123 } }) };
            journal.Admit(Request(), "test", unsafeParameters);
            var symbolic = new JObject { ["target"] = "Player/Camera", ["distance"] = 2 };
            journal.Admit(Request(id: "22222222222222222222222222222222"), "test", symbolic);
            journal = Open();
            Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "test", unsafeParameters).State);
            Assert.IsNotNull(journal.Admit(Request("resume", "22222222222222222222222222222222"), "test", symbolic).Ticket);
        }

        [Test]
        public void ContinuationPolicyIsRevalidatedImmediatelyBeforeStartedFence()
        {
            bool allowed = true;
            journal = new CommandResumeJournal(() => stored, value => stored = value, () => now, (_, __) => allowed);
            journal.Admit(Request(), "test", new JObject());
            journal = new CommandResumeJournal(() => stored, value => stored = value, () => now, (_, __) => allowed);
            var resumed = journal.Admit(Request("resume"), "test", new JObject());
            Assert.IsNotNull(resumed.Ticket);
            allowed = false;
            Assert.IsFalse(journal.TryStart(resumed.Ticket));
            Assert.AreEqual("unavailable", journal.Inspect(resumed.Ticket).State);
        }

        [Test]
        public void NewProcessIdentityAndMalformedEnvelopeNeverGrantExecution()
        {
            var request = Request();
            request["editor_session_id"] = "22222222222222222222222222222222";
            Assert.IsNull(journal.Admit(request, "test", new JObject()).Ticket);
            request = Request();
            request["deadline_unix_ms"] = "10000";
            Assert.IsNull(journal.Admit(request, "test", new JObject()).Ticket);
            request = Request();
            request["invocation_id"] = new JObject();
            Assert.IsNull(journal.Admit(request, "test", new JObject()).Ticket);
        }

        [Test]
        public void ValidJsonCheckpointCorruptionFailsClosedAndDateStringsRemainExact()
        {
            var admitted = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admitted.Ticket));
            const string iso = "2025-01-01T12:30:00.1234567+03:00";
            journal.Complete(admitted.Ticket, new JObject { ["timestamp"] = iso });
            journal = Open();
            Assert.AreEqual(iso, (string)journal.Admit(Request("resume"), "test", new JObject()).Payload["timestamp"]);
            stored = stored.Replace("\"phase\":\"completed\"", "\"phase\":\"queued\"");
            journal = Open();
            Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "test", new JObject()).State);
        }

        [Test]
        public void RawTokensAndFloatingNumbersRoundtripWithoutFalseIntegrityFailure()
        {
            var admission = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admission.Ticket));
            journal.Complete(
                admission.Ticket,
                new JObject { ["raw"] = new JRaw("{ \"value\" : 1, \"timestamp\" : \"2025-01-01T12:30:00.1234567+03:00\" }"), ["floating"] = 1.0 }
            );
            journal = Open();
            var restored = journal.Admit(Request("resume"), "test", new JObject());
            Assert.AreEqual("completed", restored.State);
            Assert.AreEqual(1, (int)restored.Payload["raw"]["value"]);
            Assert.AreEqual("2025-01-01T12:30:00.1234567+03:00", (string)restored.Payload["raw"]["timestamp"]);
            Assert.AreEqual(1.0, (double)restored.Payload["floating"]);
        }

        [Test]
        public void ActualNumericSceneSelectorAliasesCannotContinueAfterReloadIncludingNestedBatches()
        {
            var cases = new[]
            {
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "move_relative",
                        ["target"] = "Player",
                        ["reference_object"] = 123,
                        ["distance"] = 1,
                    }
                ),
                (
                    "gameobject_positioning",
                    new JObject
                    {
                        ["action"] = "look_at",
                        ["target"] = "Player",
                        ["look_at_target"] = "123",
                    }
                ),
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "look_at",
                        ["target"] = "Player",
                        ["lookAtTarget"] = 123,
                    }
                ),
                (
                    "gameobject_duplicate",
                    new JObject
                    {
                        ["action"] = "duplicate",
                        ["target"] = "Player",
                        ["parent"] = "123",
                    }
                ),
                ("manage_gameobject", new JObject { ["action"] = "modify", ["name"] = "123" }),
                ("gameobject_edit", new JObject { ["action"] = "delete", ["name"] = "123" }),
                ("gameobject_edit", new JObject { ["action"] = "delete", ["name"] = 123.0 }),
                ("gameobject_placement", new JObject { ["action"] = "placement", ["targets"] = new JArray("Player", 123) }),
                (
                    "manage_cameras",
                    new JObject
                    {
                        ["action"] = "set_target",
                        ["target"] = "Camera",
                        ["properties"] = new JObject { ["follow"] = "123" },
                    }
                ),
                (
                    "manage_cinemachine",
                    new JObject
                    {
                        ["action"] = "set_target",
                        ["target"] = "Camera",
                        ["properties"] = new JObject { ["lookAt"] = 123 },
                    }
                ),
                (
                    "manage_cameras",
                    new JObject
                    {
                        ["action"] = "set_target",
                        ["target"] = "Camera",
                        ["properties"] = "{\"look_at\":\"123\"}",
                    }
                ),
                (
                    "manage_camera",
                    new JObject
                    {
                        ["action"] = "ensure_brain",
                        ["properties"] = new JObject { ["camera"] = "123" },
                    }
                ),
                ("manage_scene", new JObject { ["action"] = "screenshot", ["viewTarget"] = 123 }),
                ("scene_inspect", new JObject { ["action"] = "scene_view_frame", ["sceneViewTarget"] = "123" }),
                (
                    "manage_physics_joints",
                    new JObject
                    {
                        ["action"] = "add_joint",
                        ["target"] = "Player",
                        ["connected_body"] = "123",
                    }
                ),
                (
                    "diagnose_physics",
                    new JObject
                    {
                        ["action"] = "diagnose_pair",
                        ["target"] = "Player",
                        ["other_target"] = 123,
                    }
                ),
                (
                    "manage_ui",
                    new JObject
                    {
                        ["action"] = "attach_ui_document",
                        ["target"] = new JObject { ["find"] = "123" },
                    }
                ),
                ("get_gameobject_component", new JObject { ["id"] = 123, ["component"] = "Transform" }),
                ("manage_render_environment", new JObject { ["action"] = "skybox_set_sun", ["name"] = "123" }),
            };
            foreach (var item in cases)
            {
                stored = null;
                journal = Open();
                var admitted = journal.Admit(Request(), item.Item1, item.Item2);
                Assert.IsNotNull(admitted.Ticket, "Initial commands preserve their existing validation: " + item.Item2);
                journal.Release(admitted.Ticket);
                Assert.IsNotNull(journal.Admit(Request("resume"), item.Item1, item.Item2).Ticket, "Same-domain admission remains eligible.");
                journal = Open();
                Assert.AreEqual("unavailable", journal.Admit(Request("resume"), item.Item1, item.Item2).State, item.Item1 + " " + item.Item2);

                stored = null;
                journal = Open();
                var batch = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = item.Item1, ["params"] = item.Item2.DeepClone() }) };
                journal.Admit(Request(), "batch_execute", batch);
                journal = Open();
                Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "batch_execute", batch).State, "Nested: " + item.Item1 + " " + item.Item2);
            }
        }

        [Test]
        public void ComponentAssignmentObjectReferencesAndEncodedContainersRequireFreshRequests()
        {
            var cases = new[]
            {
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = "Player",
                        ["componentProperties"] = new JObject { ["Custom"] = new JObject { ["arbitraryReference"] = 123 } },
                    }
                ),
                (
                    "gameobject_create",
                    new JObject
                    {
                        ["action"] = "create",
                        ["name"] = "Player",
                        ["componentProperties"] = "{\"Custom\":{\"arbitraryReference\":\"123\"}}",
                    }
                ),
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "create",
                        ["name"] = "Player",
                        ["componentsToAdd"] = new JArray(
                            new JObject
                            {
                                ["typeName"] = "Custom",
                                ["properties"] = new JObject { ["arbitraryReference"] = 123 },
                            }
                        ),
                    }
                ),
                (
                    "manage_components",
                    new JObject
                    {
                        ["action"] = "set_property",
                        ["target"] = "Player",
                        ["property"] = "arbitraryReference",
                        ["value"] = 123,
                    }
                ),
                (
                    "manage_components_selected",
                    new JObject
                    {
                        ["action"] = "set_property",
                        ["target"] = "Player",
                        ["properties"] = new JObject { ["arbitraryReference"] = "123" },
                    }
                ),
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = "Player",
                        ["componentProperties"] = "{invalid",
                    }
                ),
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = "Player",
                        ["componentProperties"] = new string('[', 70) + "123" + new string(']', 70),
                    }
                ),
                (
                    "manage_input",
                    new JObject { ["action"] = "ui_click", ["target"] = "mcp-oh-v2.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }
                ),
            };
            foreach (var item in cases)
            {
                stored = null;
                journal = Open();
                Assert.IsNotNull(journal.Admit(Request(), item.Item1, item.Item2).Ticket);
                journal = Open();
                Assert.AreEqual("unavailable", journal.Admit(Request("resume"), item.Item1, item.Item2).State, item.Item1 + " " + item.Item2);
                stored = null;
                journal = Open();
                var nested = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = item.Item1, ["params"] = item.Item2.DeepClone() }) };
                journal.Admit(Request(), "batch_execute", nested);
                journal = Open();
                Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "batch_execute", nested).State, "Nested component assignment: " + item.Item1);
            }
        }

        [Test]
        public void OrdinaryNumericValuesCreationNamesAndLookAtVectorsRemainEligible()
        {
            var parameters = new JObject
            {
                ["action"] = "create",
                ["name"] = "123",
                ["position"] = new JArray(1, 2, 3),
                ["count"] = 3,
                ["distance"] = 1,
                ["parent"] = "Environment",
                ["componentProperties"] = new JObject { ["Custom"] = new JObject { ["enabled"] = true } },
            };
            journal.Admit(Request(), "gameobject_create", parameters);
            journal = Open();
            Assert.IsNotNull(journal.Admit(Request("resume"), "gameobject_create", parameters).Ticket);
            stored = null;
            journal = Open();
            parameters = new JObject
            {
                ["action"] = "look_at",
                ["target"] = "Player",
                ["look_at_target"] = new JArray(1, 2, 3),
                ["look_at_up"] = new JArray(0, 1, 0),
            };
            journal.Admit(Request(), "gameobject_positioning", parameters);
            journal = Open();
            Assert.IsNotNull(journal.Admit(Request("resume"), "gameobject_positioning", parameters).Ticket);
            stored = null;
            journal = Open();
            parameters = new JObject { ["action"] = "create_camera", ["properties"] = "{\"name\":\"123\",\"fieldOfView\":60}" };
            journal.Admit(Request(), "manage_cameras", parameters);
            journal = Open();
            Assert.IsNotNull(journal.Admit(Request("resume"), "manage_cameras", parameters).Ticket);
        }
    }
}
