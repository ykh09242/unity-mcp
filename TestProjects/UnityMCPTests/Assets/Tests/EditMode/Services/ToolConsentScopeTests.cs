using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// Isolated storage/identity transaction tests. Simulated SessionState or a
    /// fresh process-state object is not evidence of an actual Unity reload or restart.
    /// </summary>
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class ToolConsentScopeTests
    {
        private const string Tool = "execute_code";
        private const string Nonce = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

        private sealed class Context
        {
            internal readonly Store Storage = new Store();
            internal readonly ToolConsentProcessState Domain;
            internal readonly int MainThread = Thread.CurrentThread.ManagedThreadId;
            internal string Root;
            internal int IdentityReads;
            internal int SessionIdsCreated;
            internal bool ThreadInitialized = true;
            internal Action<int> OnIdentityRead;

            internal Context(ToolConsentProcessState domain = null)
            {
                Domain = domain ?? new ToolConsentProcessState();
                Root = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "core12-isolated-" + Guid.NewGuid().ToString("N"), "A"));
            }

            internal ToolConsentKeys Keys => ToolConsentService.KeysFor(ToolConsentService.NormalizeProjectRoot(Root), Tool);

            internal ToolConsentService Service() =>
                new ToolConsentService(
                    Storage,
                    () => ThreadInitialized && Thread.CurrentThread.ManagedThreadId == MainThread,
                    () =>
                    {
                        IdentityReads++;
                        OnIdentityRead?.Invoke(IdentityReads);
                        return Root;
                    },
                    Domain,
                    () =>
                    {
                        SessionIdsCreated++;
                        return Nonce;
                    }
                );

            internal void SeedBothScopes()
            {
                ToolConsentKeys keys = Keys;
                Storage.Strings[(ToolConsentStorageScope.Session, keys.SessionId)] = Nonce;
                Storage.Bools[(ToolConsentStorageScope.Project, keys.ProjectGrant)] = true;
                Storage.Bools[(ToolConsentStorageScope.Session, keys.SessionGrant(Nonce))] = true;
                Storage.Bools[(ToolConsentStorageScope.Project, keys.Enabled)] = true;
            }
        }

        public enum Fault
        {
            None,
            ThrowBefore,
            NoOp,
            CommitThenThrow,
            WrongRead,
        }

        private sealed class Call
        {
            internal string Method;
            internal ToolConsentStorageScope Scope;
            internal string Key;
            internal bool ChangesValue;
        }

        private sealed class Store : IToolConsentStorage
        {
            internal readonly Dictionary<(ToolConsentStorageScope, string), bool> Bools = new Dictionary<(ToolConsentStorageScope, string), bool>();
            internal readonly Dictionary<(ToolConsentStorageScope, string), string> Strings = new Dictionary<(ToolConsentStorageScope, string), string>();
            internal readonly List<Call> Calls = new List<Call>();
            internal int FaultAt = -1;
            internal Fault FaultMode;
            internal bool FaultHit;
            internal bool FailEveryOperation;
            internal Action<Call> OnCall;
            internal Func<Call, Fault> PersistentFault;

            private Fault Begin(string method, ToolConsentStorageScope scope, string key, bool changes = false)
            {
                var call = new Call
                {
                    Method = method,
                    Scope = scope,
                    Key = key,
                    ChangesValue = changes,
                };
                Calls.Add(call);
                OnCall?.Invoke(call);
                Fault mode =
                    FailEveryOperation ? Fault.ThrowBefore
                    : Calls.Count - 1 == FaultAt ? FaultMode
                    : PersistentFault?.Invoke(call) ?? Fault.None;
                if (mode != Fault.None)
                    FaultHit = true;
                if (mode == Fault.ThrowBefore)
                    throw new IOException("Owned injected storage failure");
                return mode;
            }

            public bool GetBool(ToolConsentStorageScope scope, string key)
            {
                Fault mode = Begin("GetBool", scope, key);
                bool value = Bools.TryGetValue((scope, key), out bool stored) && stored;
                return mode == Fault.WrongRead ? !value : value;
            }

            public void SetBool(ToolConsentStorageScope scope, string key, bool value)
            {
                bool before = Bools.TryGetValue((scope, key), out bool stored) && stored;
                Fault mode = Begin("SetBool", scope, key, before != value);
                if (mode != Fault.NoOp)
                    Bools[(scope, key)] = value;
                if (mode == Fault.CommitThenThrow)
                    throw new IOException("Owned committed bool write then failure");
            }

            public void DeleteBool(ToolConsentStorageScope scope, string key)
            {
                Fault mode = Begin("DeleteBool", scope, key, Bools.TryGetValue((scope, key), out bool stored) && stored);
                if (mode != Fault.NoOp)
                    Bools.Remove((scope, key));
                if (mode == Fault.CommitThenThrow)
                    throw new IOException("Owned committed deletion then failure");
            }

            public string GetString(ToolConsentStorageScope scope, string key)
            {
                Fault mode = Begin("GetString", scope, key);
                string value = Strings.TryGetValue((scope, key), out string stored) ? stored : null;
                return mode == Fault.WrongRead ? "not-a-valid-session-nonce" : value;
            }

            public void SetString(ToolConsentStorageScope scope, string key, string value)
            {
                Fault mode = Begin("SetString", scope, key, !Strings.TryGetValue((scope, key), out string stored) || stored != value);
                if (mode != Fault.NoOp)
                    Strings[(scope, key)] = value;
                if (mode == Fault.CommitThenThrow)
                    throw new IOException("Owned committed nonce write then failure");
            }

            internal void ClearSessionOnly()
            {
                foreach (var key in Bools.Keys.Where(key => key.Item1 == ToolConsentStorageScope.Session).ToArray())
                    Bools.Remove(key);
                foreach (var key in Strings.Keys.Where(key => key.Item1 == ToolConsentStorageScope.Session).ToArray())
                    Strings.Remove(key);
            }
        }

        private static void AssertGranted(ToolConsentService service, ToolConsentScope scope)
        {
            ToolConsentStatus status = service.GetStatus(Tool);
            Assert.IsTrue(status.HasGrant);
            Assert.IsTrue(status.Enabled);
            Assert.AreEqual(scope, status.Scope);
            Assert.IsFalse(status.RequiresExplicitGrant);
        }

        private static void AssertDenied(ToolConsentService service)
        {
            ToolConsentStatus status = service.GetStatus(Tool);
            Assert.IsFalse(status.HasGrant);
            Assert.IsFalse(status.Enabled);
        }

        [TestCase(ToolConsentScope.EditorSession)]
        [TestCase(ToolConsentScope.Project)]
        public void ProjectGrantDoesNotAuthorizeAnotherRoot(ToolConsentScope scope)
        {
            var context = new Context();
            ToolConsentService service = context.Service();
            string a = context.Root;
            ToolConsentKeys aKeys = context.Keys;
            Assert.IsTrue(service.Grant(Tool, scope).Succeeded);
            AssertGranted(service, scope);
            context.Root = Path.Combine(Path.GetDirectoryName(a), "B");
            ToolConsentKeys bKeys = context.Keys;
            Assert.AreNotEqual(aKeys.ProjectId, bKeys.ProjectId);
            AssertDenied(service);
            int writes = context.Storage.Calls.Count(call => call.Method.StartsWith("Set", StringComparison.Ordinal) || call.Method == "DeleteBool");
            service.GetStatus(Tool);
            Assert.AreEqual(
                writes,
                context.Storage.Calls.Count(call => call.Method.StartsWith("Set", StringComparison.Ordinal) || call.Method == "DeleteBool")
            );
            Assert.IsFalse(context.Storage.Bools.ContainsKey((ToolConsentStorageScope.Project, bKeys.Enabled)));
            context.Root = a;
            AssertGranted(service, scope);
        }

        [Test]
        public void SessionStateSimulationSurvivesServiceReplacementButExpiresWhenSessionStoreIsCleared()
        {
            var context = new Context();
            Assert.IsTrue(context.Service().Grant(Tool, ToolConsentScope.EditorSession).Succeeded);
            AssertGranted(context.Service(), ToolConsentScope.EditorSession);
            Assert.AreEqual(1, context.SessionIdsCreated);
            context.Storage.ClearSessionOnly();
            var simulatedNewDomain = new ToolConsentService(context.Storage, () => true, () => context.Root, new ToolConsentProcessState(), () => Nonce);
            AssertDenied(simulatedNewDomain);
            Assert.AreEqual(1, context.SessionIdsCreated, "Passive status reads must not mint another Editor-session nonce.");
        }

        [Test]
        public void RememberedProjectGrantSurvivesSimulatedNewDomainAndSessionStoreClear()
        {
            var context = new Context();
            Assert.IsTrue(context.Service().Grant(Tool, ToolConsentScope.Project).Succeeded);
            context.Storage.ClearSessionOnly();
            var simulatedNewDomain = new ToolConsentService(context.Storage, () => true, () => context.Root, new ToolConsentProcessState(), () => Nonce);
            AssertGranted(simulatedNewDomain, ToolConsentScope.Project);
        }

        [Test]
        public void LegacyGlobalTrueDoesNotGrantAndReadsNeverMigrateOrWriteDefaults()
        {
            var context = new Context();
            string oldEnabled = EditorPrefKeys.ToolEnabledPrefix + Tool;
            string oldConsent = EditorPrefKeys.ToolEnabledPrefix + "ExplicitConsent." + Tool;
            context.Storage.Bools[(ToolConsentStorageScope.Project, oldEnabled)] = true;
            context.Storage.Bools[(ToolConsentStorageScope.Project, oldConsent)] = true;
            ToolConsentService service = context.Service();
            ToolConsentStatus status = service.GetStatus(Tool);
            AssertDenied(service);
            Assert.IsTrue(status.LegacyGrantIgnored);
            Assert.IsTrue(context.Storage.Calls.All(call => call.Method.StartsWith("Get", StringComparison.Ordinal)));
            Assert.IsTrue(context.Storage.Bools[(ToolConsentStorageScope.Project, oldEnabled)]);
            Assert.IsTrue(context.Storage.Bools[(ToolConsentStorageScope.Project, oldConsent)]);
            Assert.AreEqual(0, context.SessionIdsCreated);
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("default")]
        [TestCase("relative/project")]
        public void UnknownIdentityNeverUsesDefaultOrWorkingDirectory(string invalidRoot)
        {
            var context = new Context { Root = invalidRoot };
            ToolConsentService service = context.Service();
            AssertDenied(service);
            Assert.IsFalse(service.Grant(Tool, ToolConsentScope.Project).Succeeded);
            Assert.IsFalse(service.Revoke(Tool).DeniedInCurrentDomain);
            Assert.AreEqual(0, context.Storage.Calls.Count);
        }

        [Test]
        public void WindowsRelativeIdentitiesNeverUseWorkingDirectoryAndAbsoluteVolumeRootIsPreserved()
        {
            if (Path.DirectorySeparatorChar != '\\')
                Assert.Ignore("This row covers Windows drive-relative and rooted-without-volume syntax.");
            string volume = Path.GetPathRoot(Path.GetTempPath());
            Assert.IsTrue(volume.Length >= 3 && volume[1] == ':', "This Windows row requires the actual temporary directory's local drive root.");
            foreach (string root in new[] { volume.Substring(0, 2) + "relative", "\\relative" })
            {
                Assert.IsNull(ToolConsentService.NormalizeProjectRoot(root));
                var context = new Context { Root = root };
                ToolConsentService service = context.Service();
                AssertDenied(service);
                foreach (
                    ToolConsentChangeResult result in new[]
                    {
                        service.Grant(Tool, ToolConsentScope.Project),
                        service.Revoke(Tool),
                        service.SetEnabled(Tool, true),
                        service.SetEnabled(Tool, false),
                    }
                )
                {
                    Assert.IsFalse(result.Succeeded);
                    Assert.IsFalse(result.DeniedInCurrentDomain, "An invalid identity is not an applied revocation.");
                    Assert.AreEqual("project_identity_unavailable", result.ErrorCode);
                }
                Assert.AreEqual(0, context.Storage.Calls.Count);
                Assert.AreEqual(0, context.SessionIdsCreated);
            }
            string expected = volume.Replace('\\', '/').ToLowerInvariant();
            Assert.AreEqual(expected, ToolConsentService.NormalizeProjectRoot(volume));
            Assert.AreEqual(expected, ToolConsentService.NormalizeProjectRoot(expected));
            Assert.AreEqual(expected, ToolConsentService.NormalizeProjectRoot(ToolConsentService.NormalizeProjectRoot(volume)));
        }

        [Test]
        public void RevocationDuringFinalStatusIdentityReadCannotReturnAnOldGrantedSnapshot()
        {
            var context = new Context();
            ToolConsentService service = context.Service();
            Assert.IsTrue(service.Grant(Tool, ToolConsentScope.Project).Succeeded);
            AssertGranted(service, ToolConsentScope.Project);
            context.IdentityReads = 0;
            ToolConsentChangeResult? revoked = null;
            context.OnIdentityRead = ordinal =>
            {
                if (ordinal != 2)
                    return;
                // Clear before the nested operation's own identity reads.
                context.OnIdentityRead = null;
                revoked = service.Revoke(Tool);
            };
            ToolConsentStatus outer = service.GetStatus(Tool);
            Assert.IsTrue(revoked.HasValue, "The final, second identity accessor must actually revoke the same key.");
            Assert.IsTrue(revoked.Value.Succeeded);
            Assert.IsFalse(outer.HasGrant);
            Assert.IsFalse(outer.Enabled);
            Assert.AreEqual("consent_state_changed", outer.ErrorCode);
            AssertDenied(service);
            AssertDenied(context.Service());
        }

        [Test]
        public async Task OffThreadStatusGrantRevokeAndSetterMakeZeroStorageOrIdentityCalls()
        {
            var context = new Context();
            ToolConsentService service = context.Service();
            context.Storage.FailEveryOperation = true;
            await Task.Run(() =>
            {
                ToolConsentService worker = context.Service();
                ToolConsentStatus status = worker.GetStatus(Tool);
                Assert.AreEqual("main_thread_required", status.ErrorCode);
                Assert.IsFalse(status.Enabled);
                foreach (
                    ToolConsentChangeResult result in new[]
                    {
                        worker.Grant(Tool, ToolConsentScope.Project),
                        worker.Revoke(Tool),
                        worker.SetEnabled(Tool, true),
                        worker.SetEnabled(Tool, false),
                    }
                )
                {
                    Assert.IsFalse(result.Succeeded);
                    Assert.IsFalse(result.DeniedInCurrentDomain);
                    Assert.AreEqual("main_thread_required", result.ErrorCode);
                }
            });
            Assert.AreEqual(0, context.Storage.Calls.Count);
            Assert.AreEqual(0, context.IdentityReads);
            context.Storage.FailEveryOperation = false;
            Assert.IsTrue(service.Grant(Tool, ToolConsentScope.Project).Succeeded, "The same identity succeeds on the actual creating thread.");
        }

        [Test]
        public void UnknownThreadInitializationDeniesBeforeIdentityAndStorage()
        {
            var context = new Context { ThreadInitialized = false };
            ToolConsentService service = context.Service();
            AssertDenied(service);
            Assert.IsFalse(service.Grant(Tool, ToolConsentScope.EditorSession).Succeeded);
            Assert.IsFalse(service.Revoke(Tool).DeniedInCurrentDomain);
            Assert.AreEqual(0, context.IdentityReads);
            Assert.AreEqual(0, context.Storage.Calls.Count);
        }

        [Test]
        public void DefaultSetterGrantsSessionAndDoesNotDemoteRememberedProjectConsent()
        {
            var context = new Context();
            ToolConsentService service = context.Service();
            Assert.IsTrue(service.SetEnabled(Tool, true).Succeeded);
            AssertGranted(service, ToolConsentScope.EditorSession);
            Assert.IsTrue(service.Grant(Tool, ToolConsentScope.Project).Succeeded);
            Assert.IsTrue(service.SetEnabled(Tool, true).Succeeded);
            AssertGranted(service, ToolConsentScope.Project);
            Assert.IsFalse(
                context.Storage.Bools.TryGetValue((ToolConsentStorageScope.Session, context.Keys.SessionGrant(Nonce)), out bool alternative) && alternative
            );
        }

        [Test]
        public void SessionDowngradeRemovesRememberedGrantAndRevokeOnlyChangesCurrentProject()
        {
            var context = new Context();
            ToolConsentService service = context.Service();
            string a = context.Root;
            Assert.IsTrue(service.Grant(Tool, ToolConsentScope.Project).Succeeded);
            ToolConsentKeys aKeys = context.Keys;
            context.Root = Path.Combine(Path.GetDirectoryName(a), "B");
            Assert.IsTrue(service.Grant(Tool, ToolConsentScope.Project).Succeeded);
            context.Root = a;
            Assert.IsTrue(service.Grant(Tool, ToolConsentScope.EditorSession).Succeeded);
            Assert.IsFalse(context.Storage.Bools.TryGetValue((ToolConsentStorageScope.Project, aKeys.ProjectGrant), out bool project) && project);
            ToolConsentChangeResult result = service.Revoke(Tool);
            Assert.IsTrue(result.Succeeded);
            Assert.IsTrue(result.DeniedInCurrentDomain);
            Assert.IsTrue(result.ReloadDenialConfirmed);
            Assert.IsTrue(result.RestartDenialConfirmed);
            AssertDenied(service);
            context.Root = Path.Combine(Path.GetDirectoryName(a), "B");
            AssertGranted(service, ToolConsentScope.Project);
        }

        [Test]
        public void PreferenceReadFailureDeniesWithoutWriting()
        {
            var context = new Context();
            context.SeedBothScopes();
            context.Storage.FailEveryOperation = true;
            AssertDenied(context.Service());
            Assert.IsTrue(context.Storage.Calls.Count > 0);
            Assert.IsTrue(context.Storage.Calls.All(call => call.Method.StartsWith("Get", StringComparison.Ordinal)));
        }

        [TestCase(ToolConsentScope.EditorSession)]
        [TestCase(ToolConsentScope.Project)]
        public void SuccessfulGrantStagesDenialBeforeWritesAndPublishesOnlyVerifiedFinalState(ToolConsentScope scope)
        {
            var context = new Context();
            context.SeedBothScopes();
            ToolConsentService service = context.Service();
            bool observing = false;
            context.Storage.OnCall = call =>
            {
                if (observing)
                    return;
                if (call.Method == "SetBool" || call.Method == "DeleteBool" || call.Method == "SetString")
                {
                    observing = true;
                    try
                    {
                        AssertDenied(service);
                    }
                    finally
                    {
                        observing = false;
                    }
                }
            };
            ToolConsentChangeResult result = service.Grant(Tool, scope);
            context.Storage.OnCall = null;
            Assert.IsTrue(result.Succeeded);
            AssertGranted(service, scope);
            ToolConsentKeys keys = context.Keys;
            Assert.IsFalse(context.Storage.Bools.TryGetValue((ToolConsentStorageScope.Session, keys.Denied), out bool denied) && denied);
            Assert.IsTrue(context.Storage.Bools[(ToolConsentStorageScope.Project, keys.Enabled)]);
            Assert.AreEqual(
                scope == ToolConsentScope.Project,
                context.Storage.Bools.TryGetValue((ToolConsentStorageScope.Project, keys.ProjectGrant), out bool remembered) && remembered
            );
            Assert.AreEqual(
                scope == ToolConsentScope.EditorSession,
                context.Storage.Bools.TryGetValue((ToolConsentStorageScope.Session, keys.SessionGrant(Nonce)), out bool temporary) && temporary
            );
        }

        [TestCase(ToolConsentScope.EditorSession)]
        [TestCase(ToolConsentScope.Project)]
        public void GrantFaultAtEveryStorageStepNeverPublishesOrRetriesThroughSetter(ToolConsentScope scope)
        {
            foreach (bool existingNonce in new[] { false, true })
            {
                var traceContext = new Context();
                if (existingNonce)
                    traceContext.SeedBothScopes();
                ToolConsentChangeResult success = traceContext.Service().Grant(Tool, scope);
                Assert.IsTrue(success.Succeeded);
                Call[] trace = traceContext.Storage.Calls.ToArray();
                Assert.Greater(trace.Length, 0);
                for (int step = 0; step < trace.Length; step++)
                {
                    Call target = trace[step];
                    bool read = target.Method.StartsWith("Get", StringComparison.Ordinal);
                    bool nonceReadback = target.Method == "GetString" && step > 0 && trace[step - 1].Method == "SetString" && trace[step - 1].Key == target.Key;
                    Fault[] faults = read
                        ? target.Method == "GetBool" || nonceReadback
                            ? new[] { Fault.ThrowBefore, Fault.WrongRead }
                            : new[] { Fault.ThrowBefore }
                        : target.ChangesValue
                            ? new[] { Fault.ThrowBefore, Fault.NoOp, Fault.CommitThenThrow }
                            : new[] { Fault.ThrowBefore, Fault.CommitThenThrow };
                    foreach (Fault fault in faults)
                    {
                        var context = new Context { Root = traceContext.Root };
                        if (existingNonce)
                            context.SeedBothScopes();
                        context.Storage.FaultAt = step;
                        context.Storage.FaultMode = fault;
                        ToolConsentService service = context.Service();
                        ToolConsentChangeResult result = service.Grant(Tool, scope);
                        string label = $"{scope}/nonce={existingNonce}/step={step}/{target.Method}/{fault}";
                        Assert.IsTrue(context.Storage.FaultHit, label);
                        Assert.IsFalse(result.Succeeded, label);
                        Assert.IsTrue(result.DeniedInCurrentDomain, label);
                        AssertDenied(service);
                        AssertDenied(context.Service());
                        int before = context.Storage.Calls.Count;
                        ToolConsentChangeResult implicitRetry = context.Service().SetEnabled(Tool, true);
                        Assert.IsFalse(implicitRetry.Succeeded, label);
                        Assert.IsTrue(context.Service().GetStatus(Tool).RequiresExplicitGrant, label);
                        Assert.IsTrue(
                            context.Storage.Calls.Skip(before).All(call => call.Method.StartsWith("Get", StringComparison.Ordinal)),
                            label + " compatibility setter must not retry any write"
                        );
                    }
                }
            }
        }

        [Test]
        public void SameKeyReentrantMutationInvalidatesOuterGrantAndExplicitRetryCanRecover()
        {
            var context = new Context();
            ToolConsentService service = context.Service();
            ToolConsentChangeResult? nested = null;
            bool once = false;
            context.Storage.OnCall = call =>
            {
                if (once || call.Method != "SetBool")
                    return;
                once = true;
                nested = service.Grant(Tool, ToolConsentScope.Project);
            };
            ToolConsentChangeResult outer = service.Grant(Tool, ToolConsentScope.EditorSession);
            context.Storage.OnCall = null;
            Assert.IsTrue(once);
            Assert.IsNotNull(nested);
            Assert.IsFalse(nested.Value.Succeeded);
            Assert.IsFalse(outer.Succeeded);
            Assert.IsTrue(outer.DeniedInCurrentDomain);
            AssertDenied(service);
            Assert.IsFalse(service.SetEnabled(Tool, true).Succeeded);
            Assert.IsTrue(service.Grant(Tool, ToolConsentScope.Project).Succeeded);
            AssertGranted(service, ToolConsentScope.Project);
        }

        [Test]
        public void ProjectIdentityChangeDuringGrantCannotPublishAnotherProjectsAuthority()
        {
            var context = new Context();
            ToolConsentService service = context.Service();
            string a = context.Root;
            bool changed = false;
            context.Storage.OnCall = call =>
            {
                if (!changed && call.Method == "SetBool")
                {
                    changed = true;
                    context.Root = Path.Combine(Path.GetDirectoryName(a), "B");
                }
            };
            ToolConsentChangeResult result = service.Grant(Tool, ToolConsentScope.Project);
            context.Storage.OnCall = null;
            Assert.IsTrue(changed);
            Assert.IsFalse(result.Succeeded);
            AssertDenied(service);
            context.Root = a;
            AssertDenied(service);
        }

        [TestCase(ToolConsentScope.EditorSession)]
        [TestCase(ToolConsentScope.Project)]
        public void EveryFinalIdentityFenceRejectsAChangedProjectBeforePublication(ToolConsentScope scope)
        {
            var successful = new Context();
            successful.SeedBothScopes();
            Assert.IsTrue(successful.Service().Grant(Tool, scope).Succeeded);
            Assert.Greater(successful.IdentityReads, 1);
            for (int fence = 2; fence <= successful.IdentityReads; fence++)
            {
                var context = new Context { Root = successful.Root };
                context.SeedBothScopes();
                string original = context.Root;
                int selectedFence = fence;
                context.OnIdentityRead = ordinal =>
                {
                    if (ordinal == selectedFence)
                        context.Root = Path.Combine(Path.GetDirectoryName(original), "B");
                };
                ToolConsentChangeResult result = context.Service().Grant(Tool, scope);
                Assert.IsFalse(result.Succeeded, $"identity fence {fence}");
                Assert.IsTrue(result.DeniedInCurrentDomain, $"identity fence {fence}");
                context.OnIdentityRead = null;
                AssertDenied(context.Service());
                context.Root = original;
                AssertDenied(context.Service());
            }
        }

        [Test]
        public void RevokeFaultAtEveryStorageStepKeepsDomainDenialAndDoesNotSkipOtherCleanup()
        {
            var successful = new Context();
            successful.SeedBothScopes();
            Assert.IsTrue(successful.Service().Revoke(Tool).Succeeded);
            Call[] trace = successful.Storage.Calls.ToArray();
            for (int step = 0; step < trace.Length; step++)
            {
                Call target = trace[step];
                bool read = target.Method.StartsWith("Get", StringComparison.Ordinal);
                Fault[] faults = read
                    ? target.Method == "GetBool"
                        ? new[] { Fault.ThrowBefore, Fault.WrongRead }
                        : new[] { Fault.ThrowBefore }
                    : target.ChangesValue
                        ? new[] { Fault.ThrowBefore, Fault.NoOp, Fault.CommitThenThrow }
                        : new[] { Fault.ThrowBefore, Fault.CommitThenThrow };
                foreach (Fault fault in faults)
                {
                    var context = new Context { Root = successful.Root };
                    context.SeedBothScopes();
                    context.Storage.FaultAt = step;
                    context.Storage.FaultMode = fault;
                    ToolConsentChangeResult result = context.Service().Revoke(Tool);
                    string label = $"step={step}/{target.Method}/{fault}";
                    Assert.IsTrue(context.Storage.FaultHit, label);
                    Assert.IsTrue(result.DeniedInCurrentDomain, label);
                    AssertDenied(context.Service());
                    ToolConsentKeys keys = context.Keys;
                    Assert.IsTrue(context.Storage.Calls.Any(call => call.Method == "SetBool" && call.Key == keys.Denied), label);
                    Assert.IsTrue(context.Storage.Calls.Any(call => call.Method == "SetBool" && call.Key == keys.Enabled), label);
                    Assert.IsTrue(context.Storage.Calls.Any(call => call.Method == "DeleteBool" && call.Key == keys.ProjectGrant), label);
                    Assert.IsTrue(context.Storage.Calls.Any(call => call.Method == "DeleteBool" && call.Key == keys.SessionGrant(Nonce)), label);
                    // A committed-write-then-throw may still be confirmed by later
                    // readback. This is not permission to infer persistence from writes.
                    bool projectAbsent = !context.Storage.Bools.TryGetValue((ToolConsentStorageScope.Project, keys.ProjectGrant), out bool project) || !project;
                    bool disabled = !context.Storage.Bools.TryGetValue((ToolConsentStorageScope.Project, keys.Enabled), out bool enabled) || !enabled;
                    bool sessionAbsent =
                        !context.Storage.Bools.TryGetValue((ToolConsentStorageScope.Session, keys.SessionGrant(Nonce)), out bool session) || !session;
                    if (result.RestartDenialConfirmed)
                        Assert.IsTrue(projectAbsent && disabled, label);
                    if (result.Succeeded)
                        Assert.IsTrue(projectAbsent && disabled && sessionAbsent, label);
                    int before = context.Storage.Calls.Count;
                    Assert.IsFalse(context.Service().SetEnabled(Tool, true).Succeeded, label);
                    Assert.IsTrue(context.Storage.Calls.Skip(before).All(call => call.Method.StartsWith("Get", StringComparison.Ordinal)), label);
                }
            }
        }

        [Test]
        public void DenialCapacityFailsClosedWithoutEvictingAnEarlierTarget()
        {
            var context = new Context(new ToolConsentProcessState(1));
            ToolConsentService service = context.Service();
            Assert.IsTrue(service.Grant(Tool, ToolConsentScope.Project).Succeeded);
            string a = context.Root;
            context.Root = Path.Combine(Path.GetDirectoryName(a), "B");
            ToolConsentChangeResult overflow = service.Grant(Tool, ToolConsentScope.Project);
            Assert.IsFalse(overflow.Succeeded);
            Assert.AreEqual("consent_capacity", overflow.ErrorCode);
            AssertDenied(service);
            context.Root = a;
            AssertDenied(service);
            Assert.AreEqual("consent_capacity", service.GetStatus(Tool).ErrorCode);
            Assert.IsFalse(service.Grant(Tool, ToolConsentScope.Project).Succeeded);
        }

        [TestCase("session-only", true, false)]
        [TestCase("grant-removal", true, true)]
        [TestCase("all-fail", false, false)]
        public void RevokeReportsOnlyThePersistenceActuallyVerified(string failure, bool reload, bool restart)
        {
            var context = new Context();
            context.SeedBothScopes();
            ToolConsentKeys keys = context.Keys;
            context.Storage.Bools.Remove((ToolConsentStorageScope.Session, keys.SessionGrant(Nonce)));
            if (failure == "session-only")
                context.Storage.PersistentFault = call =>
                    call.Method.StartsWith("Get", StringComparison.Ordinal) || call.Key == keys.Denied ? Fault.None : Fault.ThrowBefore;
            if (failure == "grant-removal")
                context.Storage.PersistentFault = call => call.Key == keys.Denied ? Fault.ThrowBefore : Fault.None;
            if (failure == "all-fail")
                context.Storage.FailEveryOperation = true;
            ToolConsentService service = context.Service();
            ToolConsentChangeResult result = service.Revoke(Tool);
            Assert.IsTrue(result.DeniedInCurrentDomain);
            Assert.AreEqual(reload, result.ReloadDenialConfirmed);
            Assert.AreEqual(restart, result.RestartDenialConfirmed);
            Assert.AreEqual(failure == "grant-removal", result.Succeeded);
            AssertDenied(service);
            AssertDenied(context.Service());
            Assert.IsTrue(context.Storage.Calls.Any(call => call.Key == keys.Denied));
            Assert.IsTrue(context.Storage.Calls.Any(call => call.Key == keys.ProjectGrant && call.Method == "DeleteBool"));
            if (failure != "all-fail")
                Assert.IsTrue(context.Storage.Calls.Any(call => call.Key == keys.SessionGrant(Nonce) && call.Method == "DeleteBool"));
            Assert.IsTrue(context.Storage.Calls.Any(call => call.Key == keys.Enabled && call.Method == "SetBool"));
            if (failure == "all-fail")
            {
                Assert.AreEqual("storage_unconfirmed", result.ErrorCode);
                context.Storage.FailEveryOperation = false;
                var simulatedLostDomain = new ToolConsentService(context.Storage, () => true, () => context.Root, new ToolConsentProcessState(), () => Nonce);
                AssertGranted(simulatedLostDomain, ToolConsentScope.Project);
            }
        }

        [TestCase(Fault.ThrowBefore)]
        [TestCase(Fault.NoOp)]
        [TestCase(Fault.CommitThenThrow)]
        [TestCase(Fault.WrongRead)]
        public void FailedDenialPersistenceCannotBypassLatchAcrossReplacementAndLocatorReset(Fault denialFault)
        {
            // The injected domain exercises the same non-service-owned latch
            // without consuming slots in production's bounded shared dictionary.
            var context = new Context();
            ToolConsentProcessState productionShared = ToolConsentProcessState.Shared;
            context.SeedBothScopes();
            ToolConsentKeys keys = context.Keys;
            context.Storage.PersistentFault = call =>
            {
                if (call.Key == keys.Denied)
                    return denialFault == Fault.WrongRead
                        ? call.Method == "GetBool"
                            ? Fault.WrongRead
                            : Fault.None
                        : call.Method == "SetBool"
                            ? denialFault
                            : Fault.None;
                return call.Method == "DeleteBool" || call.Method == "SetBool" ? Fault.ThrowBefore : Fault.None;
            };
            FieldInfo[] fields = typeof(MCPServiceLocator).GetFields(BindingFlags.NonPublic | BindingFlags.Static).Where(field => !field.IsInitOnly).ToArray();
            object[] saved = fields.Select(field => field.GetValue(null)).ToArray();
            try
            {
                // Detach unrelated owned services so Reset cannot dispose their work.
                foreach (FieldInfo field in fields)
                    field.SetValue(null, null);
                ToolConsentService first = context.Service();
                MCPServiceLocator.Register<IToolConsentService>(first);
                ToolConsentChangeResult result = first.Revoke(Tool);
                Assert.IsTrue(result.DeniedInCurrentDomain);
                Assert.IsFalse(result.Succeeded);
                Assert.AreEqual(denialFault == Fault.CommitThenThrow, result.ReloadDenialConfirmed);
                Assert.IsFalse(result.RestartDenialConfirmed);
                AssertDenied(first);
                MCPServiceLocator.Reset();
                Assert.AreSame(productionShared, ToolConsentProcessState.Shared);
                ToolConsentService replacement = context.Service();
                MCPServiceLocator.Register<IToolConsentService>(replacement);
                AssertDenied(replacement);
                Assert.IsFalse(replacement.SetEnabled(Tool, true).Succeeded);
                Assert.IsTrue(replacement.GetStatus(Tool).RequiresExplicitGrant);
                // Recovery is an explicit action, never Reset or the setter.
                context.Storage.PersistentFault = null;
                Assert.IsTrue(replacement.Grant(Tool, ToolConsentScope.Project).Succeeded);
                AssertGranted(replacement, ToolConsentScope.Project);
            }
            finally
            {
                context.Storage.PersistentFault = null;
                context.Service().Grant(Tool, ToolConsentScope.Project);
                for (int i = 0; i < fields.Length; i++)
                    fields[i].SetValue(null, saved[i]);
            }
        }
    }
}
