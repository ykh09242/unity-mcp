using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Linq.Expressions;
using System.Reflection;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [Parallelizable(ParallelScope.None)]
    public class ClientConfigurationChangeTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "unity-mcp-config-change-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            Assert.IsTrue(Path.GetFullPath(root).StartsWith(Path.GetFullPath(Path.GetTempPath()), StringComparison.OrdinalIgnoreCase));
            Directory.Delete(root, true);
        }

        [TestCase("json", false)]
        [TestCase("json", true)]
        [TestCase("toml", false)]
        [TestCase("toml", true)]
        public void Prepare_WritesNothingForExistingOrMissingParent(string format, bool existing)
        {
            var path = Path.Combine(root, existing ? "config" : "missing/config");
            if (existing)
                File.WriteAllText(path, Document(format));
            var before = Tree(root);
            var harness = new Harness(path, format);
            var preview = harness.Prepare();
            Assert.AreEqual("prepared", Value<string>(preview, "Code"));
            Assert.IsTrue(Value<bool>(preview, "CanApply"));
            CollectionAssert.AreEquivalent(before, Tree(root));
            Assert.AreEqual(0, harness.Client.ForbiddenCalls);
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Preview_RedactsAllScalarsAndUnknownNames(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var preview = harness.Prepare();
            var display = Value<string>(preview, "DisplayText");
            StringAssert.DoesNotContain("synthetic-sensitive", display);
            StringAssert.DoesNotContain(root, display);
            Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(display), 16384);
            StringAssert.Contains("http", display.ToLowerInvariant());
            Assert.IsFalse(preview.GetType().GetProperties().Any(p => p.Name is "OriginalBytes" or "ProposedContents" or "Headers"));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Apply_RejectsChangedFileAndChangedContextWithoutConfigure(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var preview = harness.Prepare();
            File.AppendAllText(path, format == "json" ? " " : "\n# external owned edit\n");
            var changed = File.ReadAllBytes(path);
            Assert.AreEqual("stale_file", Value<string>(harness.Apply(preview), "Code"));
            CollectionAssert.AreEqual(changed, File.ReadAllBytes(path));
            preview = harness.Prepare();
            harness.ChangeContext("https://example.invalid/changed");
            Assert.AreEqual("stale_context", Value<string>(harness.Apply(preview), "Code"));
            CollectionAssert.AreEqual(changed, File.ReadAllBytes(path));
            Assert.AreEqual(0, harness.Client.ForbiddenCalls);
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Apply_RaceAtFinalComparePreservesExternalBytesAndCleansOwnedSidecars(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var preview = harness.Prepare();
            var field = harness.Service.GetType().GetField("beforeCommitComparison", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(field);
            var external = Document(format) + (format == "json" ? "\n" : "\n# final-check race\n");
            field.SetValue(harness.Service, new Action(() => File.WriteAllText(path, external)));
            var result = harness.Apply(preview);
            Assert.AreEqual("stale_file", Value<string>(result, "Code"));
            Assert.AreEqual(external, File.ReadAllText(path));
            Assert.IsNull(Value<object>(result, "Receipt"));
            CollectionAssert.AreEquivalent(new[] { path }, Directory.GetFiles(root));
        }

        [TestCase("json", false)]
        [TestCase("json", true)]
        [TestCase("toml", false)]
        [TestCase("toml", true)]
        public void Apply_ExistenceChangesAreStale(string format, bool existed)
        {
            var path = Path.Combine(root, "config");
            if (existed)
                File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var preview = harness.Prepare();
            if (existed)
                File.Delete(path);
            else
                File.WriteAllText(path, Document(format));
            var before = Tree(root);
            Assert.AreEqual("stale_file", Value<string>(harness.Apply(preview), "Code"));
            CollectionAssert.AreEquivalent(before, Tree(root));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Apply_WriteFailureIsNotSuccessfulChangeOrLaterRestore(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var preview = harness.Prepare();
            var before = Tree(root);
            harness
                .Service.GetType()
                .GetField("beforeCommitComparison", BindingFlags.Instance | BindingFlags.NonPublic)
                .SetValue(harness.Service, new Action(() => throw new IOException("synthetic-sensitive-failure")));
            var result = harness.Apply(preview);
            Assert.AreEqual("write_failed", Value<string>(result, "Code"));
            Assert.IsNull(Value<object>(result, "Receipt"));
            CollectionAssert.AreEquivalent(before, Tree(root));
            StringAssert.DoesNotContain("synthetic-sensitive", Value<string>(result, "Code"));
        }

        [TestCase("json", false)]
        [TestCase("json", true)]
        [TestCase("toml", false)]
        [TestCase("toml", true)]
        public void Restore_OnlyChangesOwnedEntryAndPreservesLaterOtherServerEdits(string format, bool hadEntry)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format, hadEntry));
            var harness = new Harness(path, format);
            var applied = harness.Apply(harness.Prepare());
            Assert.AreEqual("applied", Value<string>(applied, "Code"));
            var receipt = Value<object>(applied, "Receipt");
            Assert.IsNotNull(receipt);
            AddOtherServerEdit(path, format);
            var restored = harness.Restore(receipt);
            Assert.AreEqual("restored", Value<string>(restored, "Code"));
            var contents = File.ReadAllText(path);
            StringAssert.Contains("later-user-edit", contents);
            Assert.AreEqual(hadEntry, contents.Contains("original-owned-endpoint"));
            StringAssert.DoesNotContain("synthetic-sensitive-endpoint", contents);
            Assert.IsTrue(File.Exists(path));
            Assert.AreEqual("stale_handle", Value<string>(harness.Restore(receipt), "Code"));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Restore_RefusesChangedOwnedEntryAndDoesNotReplaceUserSettings(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var result = harness.Apply(harness.Prepare());
            var receipt = Value<object>(result, "Receipt");
            var modified = File.ReadAllText(path).Replace("synthetic-sensitive-endpoint", "user-owned-endpoint");
            File.WriteAllText(path, modified);
            Assert.AreEqual("owned_entry_changed", Value<string>(harness.Restore(receipt), "Code"));
            Assert.AreEqual(modified, File.ReadAllText(path));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void UnchangedApply_WritesNothingAndKeepsPriorReceipt(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var first = harness.Apply(harness.Prepare());
            var receipt = Value<object>(first, "Receipt");
            var before = Tree(root);
            var second = harness.Apply(harness.Prepare());
            Assert.AreEqual("unchanged", Value<string>(second, "Code"));
            CollectionAssert.AreEquivalent(before, Tree(root));
            Assert.AreEqual("restored", Value<string>(harness.Restore(receipt), "Code"));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Prepare_InvalidOrOversizedDocumentRefusesBeforeCapturingInputs(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, format == "json" ? "{broken" : "[broken");
            var harness = new Harness(path, format);
            Assert.AreEqual("invalid_configuration", Value<string>(harness.Prepare(), "Code"));
            Assert.AreEqual(0, harness.Context.Reads);
            var oversized = new string('x', 1024 * 1024 + 1);
            File.WriteAllText(path, oversized);
            Assert.AreEqual("input_limit", Value<string>(harness.Prepare(), "Code"));
            Assert.AreEqual(0, harness.Context.Reads);
            Assert.AreEqual(oversized, File.ReadAllText(path));
        }

        [Test]
        public void Prepare_ColdCodexCapabilityAndLockAreExplicitWithoutProbe()
        {
            var path = Path.Combine(root, "config");
            var harness = new Harness(path, "toml");
            harness.Context.Value = Inputs(remote: false, capability: null);
            Assert.AreEqual("capability_unavailable", Value<string>(harness.Prepare(), "Code"));
            harness.Context.Value = Inputs(locked: true);
            Assert.AreEqual("locked", Value<string>(harness.Prepare(), "Code"));
            Assert.AreEqual(0, Directory.GetFiles(root).Length);
            Assert.AreEqual(0, harness.Client.ForbiddenCalls);
        }

        [Test]
        public void Prepare_DirectoryTargetRefusesBeforeContextCapture()
        {
            var harness = new Harness(root, "json");
            Assert.AreEqual("unsupported_path", Value<string>(harness.Prepare(), "Code"));
            Assert.AreEqual(0, harness.Context.Reads);
        }

        [Test]
        public void Apply_OwnerClientAndPathCannotBeRebound()
        {
            var path = Path.Combine(root, "config");
            var first = new Harness(path, "json");
            var preview = first.Prepare();
            var other = new Harness(path, "json");
            Assert.AreEqual("stale_handle", Value<string>(other.Apply(preview), "Code"));
            Assert.AreEqual("stale_handle", Value<string>(Invoke(first.Service, "Apply", preview, other.Client), "Code"));
            first.Target.Value = Target(first.Client, Path.Combine(root, "elsewhere"), "json");
            Assert.AreEqual("stale_target", Value<string>(first.Apply(preview), "Code"));
            Assert.AreEqual(0, Directory.GetFiles(root).Length);
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Apply_SupersededPlanAndReceiptCannotReplay(string format)
        {
            var path = Path.Combine(root, "config");
            var harness = new Harness(path, format);
            var oldPlan = harness.Prepare();
            var current = harness.Prepare();
            Assert.AreEqual("stale_handle", Value<string>(harness.Apply(oldPlan), "Code"));
            var firstReceipt = Value<object>(harness.Apply(current), "Receipt");
            harness.ChangeContext("https://example.invalid/second-owned-endpoint");
            var secondReceipt = Value<object>(harness.Apply(harness.Prepare()), "Receipt");
            Assert.AreEqual("stale_handle", Value<string>(harness.Restore(firstReceipt), "Code"));
            var other = new Harness(path, format);
            Assert.AreEqual("stale_handle", Value<string>(other.Restore(secondReceipt), "Code"));
            Assert.AreEqual("restored", Value<string>(harness.Restore(secondReceipt), "Code"));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void MissingFile_RestoreRemovesOnlyCreatedEntryAndRetainsNewFile(string format)
        {
            var path = Path.Combine(root, "nested/config");
            var harness = new Harness(path, format);
            var receipt = Value<object>(harness.Apply(harness.Prepare()), "Receipt");
            Assert.IsNotNull(receipt);
            if (format == "json")
            {
                var document = JObject.Parse(File.ReadAllText(path));
                document["unrelated"] = new JObject { ["user"] = "later-user-edit" };
                File.WriteAllText(path, document.ToString());
            }
            else
                File.AppendAllText(path, "\n[unrelated]\nuser = 'later-user-edit'\n");
            Assert.AreEqual("restored", Value<string>(harness.Restore(receipt), "Code"));
            Assert.IsTrue(File.Exists(path));
            StringAssert.Contains("later-user-edit", File.ReadAllText(path));
            StringAssert.DoesNotContain("synthetic-sensitive-endpoint", File.ReadAllText(path));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Stdio_UsesCapturedCommandWithoutHttpFields(string format)
        {
            var path = Path.Combine(root, "config");
            var harness = new Harness(path, format);
            harness.Context.Value = Activator.CreateInstance(
                Type("ClientConfigurationInputs"),
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new object[] { false, null, "owned-command", new[] { "owned-argument" }, new Dictionary<string, string>(), null, false, null, null, false },
                null
            );
            var applied = harness.Apply(harness.Prepare());
            Assert.AreEqual("applied", Value<string>(applied, "Code"));
            string written = File.ReadAllText(path);
            StringAssert.Contains("owned-command", written);
            StringAssert.Contains("owned-argument", written);
            StringAssert.DoesNotContain("http_headers_helper", written);
            Assert.AreEqual(0, harness.Client.ForbiddenCalls);
        }

        [TestCase("{\"mcpServers\":{},\"mcpServers\":{}}")]
        [TestCase("{\"mcpServers\":false}")]
        [TestCase("{\"mcpServers\":{\"unityMCP\":null}}")]
        [TestCase("{\"mcpServers\":{},\"servers\":{\"unityMCP\":{}}}")]
        [TestCase("{\"mcpServers\":{\"unityMCP\":{\"headersHelper\":\"synthetic-sensitive-helper\"}}}")]
        public void Json_InvalidOrAmbiguousLayoutRefusesBeforeInputCapture(string document)
        {
            string path = Path.Combine(root, "config");
            File.WriteAllText(path, document);
            var harness = new Harness(path, "json");
            Assert.AreEqual("invalid_configuration", Value<string>(harness.Prepare(), "Code"));
            Assert.AreEqual(0, harness.Context.Reads);
            Assert.AreEqual(document, File.ReadAllText(path));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void CapturedStdio_EmptyCommandRefusesWithoutLegacyLookup(string format)
        {
            var path = Path.Combine(root, "config");
            var harness = new Harness(path, format);
            harness.Context.Value = Activator.CreateInstance(
                Type("ClientConfigurationInputs"),
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new object[] { false, null, null, Array.Empty<string>(), new Dictionary<string, string>(), null, false, null, null, false },
                null
            );
            Assert.AreEqual("invalid_configuration", Value<string>(harness.Prepare(), "Code"));
            Assert.AreEqual(0, Directory.GetFiles(root).Length);
            Assert.AreEqual(0, harness.Client.ForbiddenCalls);
        }

        [TestCase("json", "{\"mcpServers\":{\"unityMCP\":{\"headers\":false}}}")]
        [TestCase("json", "{\"mcpServers\":{\"unityMCP\":{\"headers\":null}}}")]
        [TestCase("json", "{\"mcpServers\":{\"unityMCP\":{\"headersHelper\":\"synthetic-sensitive-helper\"}}}")]
        [TestCase("toml", "[mcp_servers.unityMCP]\nenv = false\n")]
        [TestCase("toml", "[mcp_servers.unityMCP]\nhttp_headers = false\n")]
        [TestCase("toml", "[mcp_servers.unityMCP]\nenv_http_headers = false\n")]
        [TestCase("toml", "[mcp_servers.unityMCP]\nhttp_headers_helper = 'synthetic-sensitive-helper'\n")]
        [TestCase("toml", "[mcp_servers.unityMCP.env_http_headers]\nX-API-Key = 'synthetic-sensitive-header'\n")]
        public void Prepare_NestedOrCustomAuthPreflightRunsBeforeThrowingCapture(string format, string document)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, document);
            var harness = new Harness(path, format);
            harness.Context.Error = new IOException("synthetic-sensitive-capture-must-not-run");
            var before = Tree(root);
            var preview = harness.Prepare();
            Assert.AreEqual("invalid_configuration", Value<string>(preview, "Code"));
            Assert.AreEqual(0, harness.Context.Reads);
            CollectionAssert.AreEquivalent(before, Tree(root));
            StringAssert.DoesNotContain("synthetic-sensitive", Value<string>(preview, "DisplayText"));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Restore_DoesNotCaptureBrokenDesiredEndpointOrCredentials(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var receipt = Value<object>(harness.Apply(harness.Prepare()), "Receipt");
            Assert.IsNotNull(receipt);
            AddOtherServerEdit(path, format);
            int beforeReads = harness.Context.Reads;
            harness.Context.Error = new IOException("synthetic-sensitive-broken-desired-auth");
            Assert.AreEqual("restored", Value<string>(harness.Restore(receipt), "Code"));
            Assert.AreEqual(beforeReads, harness.Context.Reads);
            StringAssert.Contains("original-owned-endpoint", File.ReadAllText(path));
            StringAssert.Contains("later-user-edit", File.ReadAllText(path));
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void Restore_LockOnlyRefusalDoesNotCaptureDesiredSettings(string format)
        {
            var path = Path.Combine(root, "config");
            File.WriteAllText(path, Document(format));
            var harness = new Harness(path, format);
            var receipt = Value<object>(harness.Apply(harness.Prepare()), "Receipt");
            Assert.IsNotNull(receipt);
            var lockField = harness.Service.GetType().GetField("readLock", BindingFlags.Instance | BindingFlags.NonPublic);
            int lockReads = 0;
            if (lockField != null)
            {
                lockField.SetValue(
                    harness.Service,
                    new Func<bool>(() =>
                    {
                        lockReads++;
                        return true;
                    })
                );
                harness.Context.Error = new IOException("synthetic-sensitive-auth-must-not-run");
            }
            else
                harness.Context.Value = Inputs(locked: true); // Existing v2 call shape: prove its unwanted capture, not missing new facility.
            int beforeReads = harness.Context.Reads;
            var before = Tree(root);
            Assert.AreEqual("locked", Value<string>(harness.Restore(receipt), "Code"));
            Assert.AreEqual(beforeReads, harness.Context.Reads);
            if (lockField != null)
                Assert.AreEqual(1, lockReads);
            CollectionAssert.AreEquivalent(before, Tree(root));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExistingAtomicWriter_ControlPreservesUnrelatedSidecars(bool exists)
        {
            var path = Path.Combine(root, "config");
            if (exists)
                File.WriteAllText(path, "original");
            File.WriteAllText(path + ".tmp", "unrelated");
            File.WriteAllText(path + ".backup", "unrelated");
            McpConfigurationHelper.WriteAtomicFile(path, "new-owned-value");
            Assert.AreEqual("unrelated", File.ReadAllText(path + ".tmp"));
            Assert.AreEqual("unrelated", File.ReadAllText(path + ".backup"));
        }

        internal static string Document(string format, bool hadEntry = true) =>
            format == "json"
                ? "{\"mcpServers\":{\"other\":{\"value\":false}"
                    + (
                        hadEntry
                            ? ",\"unityMCP\":{\"url\":\"original-owned-endpoint\",\"custom\":0,\"synthetic-sensitive-key\":\"synthetic-sensitive-value\"}"
                            : ""
                    )
                    + "}}"
                : "# preserved neighbor\n[mcp_servers.other]\nvalue = false\n"
                    + (
                        hadEntry
                            ? "[mcp_servers.unityMCP]\nurl = 'original-owned-endpoint'\ncustom = 0\n\"synthetic-sensitive-key\" = 'synthetic-sensitive-value'\n"
                            : ""
                    );

        internal static void AddOtherServerEdit(string path, string format)
        {
            var content = File.ReadAllText(path);
            if (format == "json")
            {
                var document = JObject.Parse(content);
                document["mcpServers"]["other"]["user"] = "later-user-edit";
                File.WriteAllText(path, document.ToString());
            }
            else
                File.WriteAllText(path, content + "\n[mcp_servers.later]\nuser = 'later-user-edit'\n");
        }

        internal static T Value<T>(object value, string property) => (T)value.GetType().GetProperty(property).GetValue(value);

        internal static object Invoke(object target, string method, params object[] args) => target.GetType().GetMethod(method).Invoke(target, args);

        private static string[] Tree(string path) =>
            Directory
                .GetFiles(path, "*", SearchOption.AllDirectories)
                .Select(file => file + ":" + Convert.ToBase64String(File.ReadAllBytes(file)))
                .Concat(Directory.GetDirectories(path, "*", SearchOption.AllDirectories))
                .ToArray();

        internal sealed class Box
        {
            internal object Value;
            internal int Reads;
            internal Exception Error = null;

            public object Read()
            {
                Reads++;
                if (Error != null)
                    throw Error;
                return Value;
            }
        }

        internal sealed class Harness
        {
            internal object Service;
            internal Box Context = new Box();
            internal Box Target = new Box();
            internal OwnedClient Client = new OwnedClient();

            internal Harness(string path, string format)
            {
                var serviceType = Type("ClientConfigurationChangeService");
                Context.Value = Inputs();
                Target.Value = ClientConfigurationChangeTests.Target(Client, path, format);
                var inputDelegate = Provider(typeof(Func<,>).MakeGenericType(typeof(string), Type("ClientConfigurationInputs")), Context);
                var targetDelegate = Provider(typeof(Func<,>).MakeGenericType(typeof(IMcpClientConfigurator), Type("ClientConfigurationFileTarget")), Target);
                Service = Activator.CreateInstance(
                    serviceType,
                    BindingFlags.Instance | BindingFlags.NonPublic,
                    null,
                    new object[] { inputDelegate, targetDelegate },
                    null
                );
            }

            internal object Prepare() => Invoke(Service, "Prepare", Client);

            internal object Apply(object preview) => Invoke(Service, "Apply", preview, Client);

            internal object Restore(object receipt) => Invoke(Service, "Restore", receipt, Client);

            internal void ChangeContext(string endpoint) => Context.Value = Inputs(endpoint: endpoint);
        }

        internal static Type Type(string name)
        {
            var type = typeof(McpConfigurationHelper).Assembly.GetType("MCPForUnity.Editor.Services." + name);
            Assert.IsNotNull(
                type,
                "Zero-write preview/guarded apply/selective restore facility is required; Configure must never be used as a preview fallback"
            );
            return type;
        }

        internal static object Inputs(
            string endpoint = "https://example.invalid/synthetic-sensitive-endpoint",
            bool remote = true,
            bool? capability = true,
            bool locked = false
        ) =>
            Activator.CreateInstance(
                Type("ClientConfigurationInputs"),
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new object[]
                {
                    true,
                    endpoint,
                    "synthetic-sensitive-command",
                    new[] { "synthetic-sensitive-argument" },
                    new Dictionary<string, string> { ["X-API-Key"] = "synthetic-sensitive-header" },
                    "synthetic-sensitive-systemroot",
                    remote,
                    capability,
                    "synthetic-sensitive-helper",
                    locked,
                },
                null
            );

        internal static object Target(IMcpClientConfigurator client, string path, string format) =>
            Activator.CreateInstance(
                Type("ClientConfigurationFileTarget"),
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new object[]
                {
                    client,
                    client.Id,
                    path,
                    format,
                    new McpClient { name = "Owned Preview" },
                },
                null
            );

        private static Delegate Provider(Type delegateType, Box box)
        {
            var parameter = Expression.Parameter(delegateType.GenericTypeArguments[0]);
            return Expression
                .Lambda(
                    delegateType,
                    Expression.Convert(Expression.Call(Expression.Constant(box), nameof(Box.Read), null), delegateType.GenericTypeArguments[1]),
                    parameter
                )
                .Compile();
        }

        internal sealed class OwnedClient : IMcpClientConfigurator
        {
            internal int ForbiddenCalls;
            public string Id => "owned-preview";
            public string DisplayName => "Owned Preview";
            public McpStatus Status => McpStatus.NotConfigured;
            public ConfiguredTransport ConfiguredTransport => ConfiguredTransport.Unknown;
            public bool SupportsAutoConfigure => true;
            public bool SupportsSkills => false;
            public bool IsInstalled => true;
            public IReadOnlyList<ConfiguredTransport> SupportedTransports => new[] { ConfiguredTransport.Http, ConfiguredTransport.Stdio };

            public string GetConfigureActionLabel() => "Configure";

            public string GetConfigPath() => throw Forbidden();

            public string GetManualSnippet() => throw Forbidden();

            public string GetSkillInstallPath() => throw Forbidden();

            public IList<string> GetInstallationSteps() => throw Forbidden();

            public McpStatus CheckStatus(bool attemptAutoRewrite = true) => throw Forbidden();

            public void Configure() => throw Forbidden();

            public void Unregister() => throw Forbidden();

            private Exception Forbidden()
            {
                ForbiddenCalls++;
                return new InvalidOperationException("Only injected file target and captured inputs are allowed");
            }
        }
    }
}
