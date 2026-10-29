using System;
using System.Collections;
using System.IO;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class CommandResumeActualReloadTests
    {
        private const string Prefix = "MCPForUnity.Tests.CommandResumeActualReload.";
        private static readonly string DomainNonce = Guid.NewGuid().ToString("N");
        private static readonly JObject Parameters = new JObject { ["value"] = 17 };

        [UnityTest, Explicit("Owned disposable batch Editor only; requires exact UNITY_MCP_COMMAND_RELOAD_PROJECT and performs real domain reloads.")]
        public IEnumerator QueuedIntentSurvivesRepeatedReloadAndCompletedResultIsCached()
        {
            Prepare("queued");
            try
            {
                ReloadWithProbe();
                yield return new WaitForDomainReload();
                AssertReloaded();
                var journal = Open();
                Assert.AreEqual("queued", journal.Admit(Request(journal, "status"), "owned_test", Parameters).State);
                Assert.AreEqual(0, SessionState.GetInt(Prefix + "invocations", 0));

                RememberDomain();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                journal = Open();
                var claimed = journal.Admit(Request(journal, "resume"), "owned_test", Parameters);
                Assert.IsNotNull(claimed.Ticket, "The original queued request must be claimable after two native reloads.");
                Assert.IsNull(journal.Admit(Request(journal, "resume"), "owned_test", Parameters).Ticket, "A duplicate delivery cannot claim the handler.");
                Assert.IsTrue(journal.TryStart(claimed.Ticket), "The started fence must persist before the simulated handler.");
                SessionState.SetInt(Prefix + "invocations", SessionState.GetInt(Prefix + "invocations", 0) + 1);
                Assert.AreEqual("completed", journal.Complete(claimed.Ticket, new JObject { ["result"] = 42 }).State);
                journal.Release(claimed.Ticket);

                RememberDomain();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                journal = Open();
                var cached = journal.Admit(Request(journal, "resume"), "owned_test", Parameters);
                Assert.AreEqual("completed", cached.State);
                Assert.AreEqual(42, (int)cached.Payload["result"]);
                Assert.IsNull(cached.Ticket);
                Assert.AreEqual(1, SessionState.GetInt(Prefix + "invocations", 0), "A cached response must not invoke the handler again.");
            }
            finally
            {
                Cleanup();
            }
        }

        [UnityTest, Explicit("Owned disposable batch Editor only; verifies a started fence across a real domain reload.")]
        public IEnumerator StartedIntentIsUnknownAndNeverReplayed()
        {
            Prepare("started");
            try
            {
                ReloadWithProbe();
                yield return new WaitForDomainReload();
                AssertReloaded();
                var journal = Open();
                var uncertain = journal.Admit(Request(journal, "resume"), "owned_test", Parameters);
                Assert.AreEqual("unknown", uncertain.State);
                Assert.IsNull(uncertain.Ticket);
                Assert.AreEqual(0, SessionState.GetInt(Prefix + "invocations", 0));
            }
            finally
            {
                Cleanup();
            }
        }

        [UnityTest, Explicit("Owned disposable batch Editor only; verifies first native admission for a callback queued before real domain reload.")]
        public IEnumerator PublicQueuedOriginCannotReinterpretOldReferencesOnFirstNativeAdmission()
        {
            Prepare("public_origin");
            try
            {
                ReloadWithProbe();
                yield return new WaitForDomainReload();
                AssertReloaded();
                var journal = Open();
                string origin = SessionState.GetString(Prefix + "origin", "");
                Assert.AreNotEqual(origin, NativeCommandArbitrator.Shared.Generation);
                var request = Request(journal, "new");
                request["origin_generation"] = origin;
                var stale = JObject.Parse("{\"action\":\"delete\",\"target\":\"-12345\"}");
                Assert.IsNull(journal.Admit(request, "gameobject_edit", stale).Ticket);
                request["invocation_id"] = Guid.NewGuid().ToString("N");
                var stable = JObject.Parse("{\"action\":\"create\",\"name\":\"123\",\"position\":[1,2,3]}");
                var accepted = journal.Admit(request, "manage_gameobject", stable);
                Assert.IsNotNull(accepted.Ticket);
                Assert.IsTrue(journal.TryStart(accepted.Ticket));
                SessionState.SetInt(Prefix + "invocations", SessionState.GetInt(Prefix + "invocations", 0) + 1);
                Assert.IsNull(journal.Admit(request, "manage_gameobject", stable).Ticket);
                Assert.AreEqual(1, SessionState.GetInt(Prefix + "invocations", 0));
                journal.Complete(accepted.Ticket, new JObject { ["success"] = true });
                journal.Release(accepted.Ticket);
            }
            finally
            {
                Cleanup();
            }
        }

        [UnityTest, Explicit("Owned disposable batch Editor only; verifies cancellation across a real domain reload.")]
        public IEnumerator CanceledIntentCannotResurrectAfterReload()
        {
            Prepare("canceled");
            try
            {
                ReloadWithProbe();
                yield return new WaitForDomainReload();
                AssertReloaded();
                var journal = Open();
                Assert.AreEqual("canceled", journal.Admit(Request(journal, "resume"), "owned_test", Parameters).State);
                Assert.AreEqual("canceled", journal.Admit(Request(journal, "new"), "owned_test", Parameters).State);
                Assert.AreEqual(0, SessionState.GetInt(Prefix + "invocations", 0));
            }
            finally
            {
                Cleanup();
            }
        }

        private static CommandResumeJournal Open() =>
            new CommandResumeJournal(
                () => SessionState.GetString(Prefix + "journal", ""),
                value => SessionState.SetString(Prefix + "journal", value),
                () => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            );

        private static JObject Request(CommandResumeJournal journal, string action) =>
            new JObject
            {
                ["version"] = 1,
                ["invocation_id"] = SessionState.GetString(Prefix + "id", ""),
                ["editor_session_id"] = journal.SessionId,
                ["deadline_unix_ms"] = long.Parse(SessionState.GetString(Prefix + "deadline", "0")),
                ["action"] = action,
            };

        private static void Prepare(string phase)
        {
            Assert.IsTrue(Application.isBatchMode, "This probe requires an owned batch Editor.");
            Assert.AreEqual(
                Path.GetFullPath(Path.GetDirectoryName(Application.dataPath)),
                Environment.GetEnvironmentVariable("UNITY_MCP_COMMAND_RELOAD_PROJECT"),
                "The caller must identify the exact disposable project."
            );
            Assert.IsFalse(SessionState.GetBool(Prefix + "active", false), "Preserve any unfinished owned probe.");
            SessionState.SetBool(Prefix + "active", true);
            SessionState.SetString(Prefix + "phase", phase);
            SessionState.SetString(Prefix + "id", Guid.NewGuid().ToString("N"));
            SessionState.SetString(Prefix + "deadline", DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeMilliseconds().ToString());
            SessionState.SetInt(Prefix + "invocations", 0);
            RememberDomain();
        }

        private static void RememberDomain() => SessionState.SetString(Prefix + "domain", DomainNonce);

        private static void ReloadWithProbe()
        {
            AssemblyReloadEvents.beforeAssemblyReload += WriteCheckpointAtReload;
            EditorUtility.RequestScriptReload();
        }

        private static void WriteCheckpointAtReload()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= WriteCheckpointAtReload;
            try
            {
                var journal = Open();
                string phase = SessionState.GetString(Prefix + "phase", "");
                if (phase == "public_origin")
                {
                    SessionState.SetString(Prefix + "origin", NativeCommandArbitrator.Shared.Generation);
                    SessionState.SetBool(Prefix + "checkpoint_written", true);
                    return;
                }
                var admitted = journal.Admit(Request(journal, "new"), "owned_test", Parameters);
                if (admitted.Ticket == null)
                    throw new InvalidOperationException("The old domain did not queue the command.");
                if (phase == "started" && !journal.TryStart(admitted.Ticket))
                    throw new InvalidOperationException("The old domain did not persist the started fence.");
                if (phase == "canceled" && journal.Admit(Request(journal, "cancel"), "owned_test", Parameters).State != "canceled")
                    throw new InvalidOperationException("The old domain did not persist cancellation.");
                SessionState.SetBool(Prefix + "checkpoint_written", true);
            }
            catch (Exception error)
            {
                SessionState.SetString(Prefix + "error", error.ToString());
            }
        }

        private static void AssertReloaded()
        {
            Assert.AreNotEqual(SessionState.GetString(Prefix + "domain", ""), DomainNonce, "The native domain must actually reload.");
            Assert.IsTrue(SessionState.GetBool(Prefix + "checkpoint_written", false), "The old-domain checkpoint callback must run.");
            Assert.IsEmpty(SessionState.GetString(Prefix + "error", ""), "The old-domain checkpoint must complete before unloading.");
            LogAssert.NoUnexpectedReceived();
        }

        private static void Cleanup()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= WriteCheckpointAtReload;
            if (!SessionState.GetBool(Prefix + "active", false))
                return;
            foreach (string key in new[] { "phase", "id", "deadline", "domain", "origin", "journal", "error" })
                SessionState.EraseString(Prefix + key);
            foreach (string key in new[] { "active", "checkpoint_written" })
                SessionState.EraseBool(Prefix + key);
            SessionState.EraseInt(Prefix + "invocations");
        }
    }
}
