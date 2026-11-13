using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    [TestFixture]
    public sealed class ReferenceGraphAssetBatchTests
    {
        private static JObject Describe(JArray paths) =>
            (JObject)
                typeof(ReferenceGraphCollector)
                    .GetMethod("DescribePaths", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(null, new object[] { new JObject { ["paths"] = paths } });

        [Test]
        public void DescriptionsPreserveInputOrderAndMissingPaths()
        {
            var paths = new JArray("Assets/NotFoundBatchA.png", "Assets/NotFoundBatchB.png", "Assets/NotFoundBatchA.png");
            JArray rows = (JArray)Describe(paths)["assets"];
            CollectionAssert.AreEqual(paths.Values<string>(), rows.Select(row => row.Value<string>("path")));
            Assert.IsTrue(rows.All(row => row["asset"].Value<bool>("present") == false));
        }

        [Test]
        public void DescriptionsRejectInvalidOrOversizedRequests()
        {
            Assert.Throws<TargetInvocationException>(() => Describe(new JArray()));
            Assert.Throws<TargetInvocationException>(() => Describe(new JArray(Enumerable.Repeat("Assets/A.png", 33))));
            Assert.Throws<TargetInvocationException>(() => Describe(new JArray("Assets/../Outside.png")));
        }
    }

    [TestFixture, Parallelizable(ParallelScope.None)]
    public sealed class ReferenceGraphIgnoreRulesTests
    {
        private string root;
        private ReferenceGraphIgnoreRules rules;
        private System.Collections.Generic.Dictionary<string, string> candidates;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "McpIgnoreRules_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "Assets", "Nested"));
            candidates = new System.Collections.Generic.Dictionary<string, string>(StringComparer.Ordinal)
            {
                [".gitignore"] = Path.Combine(root, ".gitignore"),
                ["Assets/.gitignore"] = Path.Combine(root, "Assets", ".gitignore"),
                ["Assets/Nested/.gitignore"] = Path.Combine(root, "Assets", "Nested", ".gitignore"),
            };
            rules = new ReferenceGraphIgnoreRules(root);
        }

        [TearDown]
        public void TearDown()
        {
            rules?.Dispose();
            Assert.IsTrue(Path.GetFileName(root).StartsWith("McpIgnoreRules_", StringComparison.Ordinal));
            Directory.Delete(root, true);
        }

        private JObject Acquire()
        {
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            for (; ; )
            {
                try
                {
                    return rules.Rules(new JObject { ["validate"] = true }, 1, "epoch", 1, () => candidates);
                }
                catch (InvalidOperationException) when (elapsed.ElapsedMilliseconds < 15000)
                {
                    System.Threading.Thread.Sleep(20);
                }
            }
        }

        private JObject Read(JObject page, int offset = 0, bool validate = false) =>
            rules.Rules(
                new JObject
                {
                    ["snapshot"] = page["snapshot"],
                    ["offset"] = offset,
                    ["validate"] = validate,
                },
                1,
                "epoch",
                2,
                () => throw new Exception("Cached rules must not rediscover assets.")
            );

        private void PauseWatchers()
        {
            foreach (
                FileSystemWatcher watcher in (System.Collections.IEnumerable)
                    typeof(ReferenceGraphIgnoreRules).GetField("watchers", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(rules)
            )
                watcher.EnableRaisingEvents = false;
        }

        private void CreateHashingFixture()
        {
            rules.Dispose();
            rules = new ReferenceGraphIgnoreRules(root, 1, _ => throw new IOException("Owned event-free fixture"));
            using (var stream = new FileStream(candidates[".gitignore"], FileMode.Create, FileAccess.Write, FileShare.Read))
                stream.SetLength(32L * 1024 * 1024);
            for (int index = 0; index < 128; index++)
            {
                string logical = "Assets/Owned" + index + "/.gitignore";
                string path = Path.Combine(root, logical);
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.WriteAllText(path, new string('x', 4096));
                candidates.Add(logical, path);
            }
        }

        private static async Task WaitForHashRead(ManualResetEventSlim entered)
        {
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            while (!entered.IsSet && elapsed.ElapsedMilliseconds < 15000)
                await Task.Delay(1);
            Assert.IsTrue(entered.IsSet, "The real file hashing worker must reach its first read.");
        }

        [Test]
        public void RegisteredReferenceGraphUsesCancellableAsyncEntryPoint()
        {
            MethodInfo handler = CommandRegistry.GetCommandMethod(typeof(ManageReferenceGraph));
            Assert.IsNotNull(handler);
            CollectionAssert.AreEqual(
                new[] { typeof(JObject), typeof(CancellationToken) },
                handler.GetParameters().Select(parameter => parameter.ParameterType)
            );
            Assert.AreEqual(typeof(Task<object>), handler.ReturnType);
        }

        [TestCase("source")]
        [TestCase("dependencies")]
        public async Task RegisteredAssetGuardsHashOffThreadAndCancelBeforePayloadDelivery(string action)
        {
            CreateHashingFixture();
            JObject status = ReferenceGraphCollector.Handle(new JObject { ["action"] = "status" });
            string epoch = status.Value<string>("epoch");
            long revision = status.Value<long>("revision");
            await rules.RulesAsync(new JObject(), 1000, epoch, revision, () => candidates, () => true, CancellationToken.None);
            string digest = await rules.ValidateCurrentAsync(epoch, revision, () => true, CancellationToken.None);
            const BindingFlags flags = BindingFlags.Static | BindingFlags.NonPublic;
            FieldInfo owner = typeof(ReferenceGraphCollector).GetField("ignoreRules", flags);
            FieldInfo candidateField = typeof(ReferenceGraphCollector).GetField("ignoreCandidates", flags);
            FieldInfo candidateEpoch = typeof(ReferenceGraphCollector).GetField("ignoreCandidateEpoch", flags);
            object previousOwner = owner.GetValue(null);
            object previousCandidates = candidateField.GetValue(null);
            object previousEpoch = candidateEpoch.GetValue(null);
            owner.SetValue(null, rules);
            candidateField.SetValue(null, candidates);
            candidateEpoch.SetValue(null, epoch);
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            using var cancellation = new CancellationTokenSource();
            int editorThread = Thread.CurrentThread.ManagedThreadId;
            int workerThread = editorThread;
            rules.FingerprintReadForTests = (_, __) =>
            {
                workerThread = Thread.CurrentThread.ManagedThreadId;
                entered.Set();
                if (!release.Wait(15000))
                    throw new TimeoutException("Registered guard hash read gate timed out.");
            };
            string path = "Assets/Tests/EditMode/Tools/ReferenceGraphCollectorTests.cs";
            var request = new JObject
            {
                ["action"] = action,
                ["guid"] = AssetDatabase.AssetPathToGUID(path),
                ["expected_instance_id"] = status["instance_id"],
                ["expected_project_id"] = status["project_id"],
                ["asset_guard"] = new JObject
                {
                    ["path"] = path,
                    ["epoch"] = epoch,
                    ["revision"] = revision,
                    ["ignore_digest"] = digest,
                },
            };
            Task<object> operation = null;
            try
            {
                MethodInfo handler = CommandRegistry.GetCommandMethod(typeof(ManageReferenceGraph));
                operation = (Task<object>)handler.Invoke(null, new object[] { request, cancellation.Token });
                await WaitForHashRead(entered);
                Assert.AreNotEqual(editorThread, workerThread, "Registered asset guards must use the owned background hash worker.");
                Assert.IsFalse(operation.IsCompleted);
                cancellation.Cancel();
                release.Set();
                try
                {
                    await operation;
                }
                catch (OperationCanceledException) { }
                Assert.IsTrue(operation.IsCanceled);
                int synchronousReads = 0;
                rules.FingerprintReadForTests = (_, __) =>
                {
                    if (Thread.CurrentThread.ManagedThreadId == editorThread)
                        Interlocked.Increment(ref synchronousReads);
                };
                JObject delivered = JObject.FromObject(await (Task<object>)handler.Invoke(null, new object[] { request, CancellationToken.None }));
                Assert.IsTrue(delivered.Value<bool>("success"), delivered.ToString());
                Assert.IsTrue(delivered["data"].Value<bool>("asset_guard_current"), delivered.ToString());
                Assert.AreEqual(0, synchronousReads, "A validated async guard must not fall back to synchronous hashing at payload delivery.");
                Assert.IsNotNull(delivered["data"][action == "source" ? "bytes_base64" : "targets"]);
                if (action == "source")
                {
                    request["fingerprint"] = "stale-source-fingerprint";
                    JObject badSource = JObject.FromObject(await (Task<object>)handler.Invoke(null, new object[] { request, CancellationToken.None }));
                    Assert.IsFalse(badSource.Value<bool>("success"), "Async ignore validation must preserve the source continuation fingerprint fence.");
                    request.Remove("fingerprint");
                }
                string rulePath = candidates[".gitignore"];
                DateTime stamp = File.GetLastWriteTimeUtc(rulePath);
                using (var stream = new FileStream(rulePath, FileMode.Open, FileAccess.Write, FileShare.ReadWrite))
                    stream.WriteByte(1);
                File.SetLastWriteTimeUtc(rulePath, stamp);
                JObject stale = JObject.FromObject(await (Task<object>)handler.Invoke(null, new object[] { request, CancellationToken.None }));
                Assert.IsTrue(stale.Value<bool>("success"), stale.ToString());
                Assert.IsFalse(stale["data"].Value<bool>("asset_guard_current"));
                Assert.IsNull(stale["data"]["bytes_base64"]);
                Assert.IsNull(stale["data"]["targets"]);
            }
            finally
            {
                release.Set();
                try
                {
                    if (operation != null)
                        await operation;
                }
                catch (OperationCanceledException) { }
                rules.FingerprintReadForTests = null;
                owner.SetValue(null, previousOwner);
                candidateField.SetValue(null, previousCandidates);
                candidateEpoch.SetValue(null, previousEpoch);
            }
            Assert.IsTrue(operation.IsCanceled, "Registered source/dependency guard cancellation must propagate before payload publication.");
        }

        [Test]
        public async Task AsyncCaptureManyLargeRulesYieldsAndCancellationRetainsOwnershipUntilWorkerSettles()
        {
            CreateHashingFixture();
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            using var cancellation = new CancellationTokenSource();
            int editorThread = Thread.CurrentThread.ManagedThreadId;
            int workerThread = editorThread;
            rules.FingerprintReadForTests = (_, __) =>
            {
                workerThread = Thread.CurrentThread.ManagedThreadId;
                entered.Set();
                if (!release.Wait(15000))
                    throw new TimeoutException("Owned hash read gate timed out.");
            };
            Task<JObject> operation = rules.RulesAsync(new JObject(), 1000, "epoch", 1, () => candidates, () => true, cancellation.Token);
            try
            {
                await WaitForHashRead(entered);
                int updates = 0;
                double maximumGap = 0;
                var frame = System.Diagnostics.Stopwatch.StartNew();
                void Update()
                {
                    maximumGap = Math.Max(maximumGap, frame.Elapsed.TotalMilliseconds);
                    frame.Restart();
                    updates++;
                }
                EditorApplication.update += Update;
                try
                {
                    var timeout = System.Diagnostics.Stopwatch.StartNew();
                    while (updates < 3 && timeout.ElapsedMilliseconds < 15000)
                        await Task.Delay(1);
                }
                finally
                {
                    EditorApplication.update -= Update;
                }
                Assert.GreaterOrEqual(updates, 3, "Editor callbacks must continue while a file read is outstanding.");
                Assert.AreNotEqual(editorThread, workerThread);
                TestContext.WriteLine("Ignore hash blocked-read Editor updates=" + updates + "; maximum interval ms=" + maximumGap);
                cancellation.Cancel();
                Assert.IsFalse(operation.IsCompleted, "Cancellation must retain the real worker until its read settles.");
                bool occupied = false;
                try
                {
                    await rules.RulesAsync(new JObject(), 1, "epoch", 1, () => candidates, () => true, CancellationToken.None);
                }
                catch (InvalidOperationException)
                {
                    occupied = true;
                }
                Assert.IsTrue(occupied, "A canceled but outstanding worker must still own the instance's capacity.");
            }
            finally
            {
                release.Set();
                try
                {
                    await operation;
                }
                catch (OperationCanceledException) { }
                rules.FingerprintReadForTests = null;
            }
            Assert.IsTrue(operation.IsCanceled);
            int hashUpdates = 0;
            double maximumHashGap = 0;
            var hashFrame = System.Diagnostics.Stopwatch.StartNew();
            void HashUpdate()
            {
                maximumHashGap = Math.Max(maximumHashGap, hashFrame.Elapsed.TotalMilliseconds);
                hashFrame.Restart();
                hashUpdates++;
            }
            EditorApplication.update += HashUpdate;
            JObject page;
            try
            {
                page = await rules.RulesAsync(new JObject(), 1000, "epoch", 1, () => candidates, () => true, CancellationToken.None);
            }
            finally
            {
                EditorApplication.update -= HashUpdate;
            }
            TestContext.WriteLine("Ignore hash 32 MiB plus 128 rule files Editor updates=" + hashUpdates + "; maximum interval ms=" + maximumHashGap);
            Assert.Greater(hashUpdates, 0, "The real capture must yield while hashing the large/many-file fixture.");
            Assert.AreEqual(129, ((JArray)page["files"]).Count);
            Assert.IsNotNull(await rules.ValidateCurrentAsync("epoch", 1, () => true, CancellationToken.None));
        }

        [TestCase("epoch", false)]
        [TestCase("observer", false)]
        [TestCase("dispose", false)]
        [TestCase("source", false)]
        [TestCase("epoch", true)]
        [TestCase("observer", true)]
        [TestCase("dispose", true)]
        [TestCase("source", true)]
        public async Task AsyncCaptureRejectsStaleResultsBeforePublication(string change, bool validate)
        {
            CreateHashingFixture();
            if (validate)
                await rules.RulesAsync(new JObject(), 1, "epoch", 1, () => candidates, () => true, CancellationToken.None);
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            bool current = true;
            rules.FingerprintReadForTests = (_, __) =>
            {
                entered.Set();
                if (!release.Wait(15000))
                    throw new TimeoutException("Owned hash read gate timed out.");
            };
            Task operation = validate
                ? rules.ValidateCurrentAsync("epoch", 1, () => current, CancellationToken.None)
                : rules.RulesAsync(new JObject(), 1, "epoch", 1, () => candidates, () => current, CancellationToken.None);
            bool rejected = false;
            try
            {
                await WaitForHashRead(entered);
                if (change == "epoch")
                    rules.Status("new-epoch", 2);
                else if (change == "observer")
                    typeof(ReferenceGraphIgnoreRules).GetMethod("MarkChanged", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(rules, null);
                else if (change == "dispose")
                    rules.Dispose();
                else
                    current = false;
            }
            finally
            {
                release.Set();
                try
                {
                    await operation;
                }
                catch (OperationCanceledException)
                {
                    rejected = true;
                }
                catch (InvalidOperationException)
                {
                    rejected = true;
                }
                rules.FingerprintReadForTests = null;
            }
            Assert.IsTrue(rejected, "Stale background evidence must never publish a snapshot.");
            Assert.IsNull(typeof(ReferenceGraphIgnoreRules).GetField("snapshot", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(rules));
        }

        [Test]
        public async Task AsyncValidationCancelsAndStillDetectsSameStampEdits()
        {
            CreateHashingFixture();
            JObject page = await rules.RulesAsync(new JObject(), 1000, "epoch", 1, () => candidates, () => true, CancellationToken.None);
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            using var cancellation = new CancellationTokenSource();
            rules.FingerprintReadForTests = (_, __) =>
            {
                entered.Set();
                if (!release.Wait(15000))
                    throw new TimeoutException("Owned hash validation gate timed out.");
            };
            Task<string> validation = rules.ValidateCurrentAsync("epoch", 1, () => true, cancellation.Token);
            try
            {
                await WaitForHashRead(entered);
                cancellation.Cancel();
            }
            finally
            {
                release.Set();
                try
                {
                    await validation;
                }
                catch (OperationCanceledException) { }
                rules.FingerprintReadForTests = null;
            }
            Assert.IsTrue(validation.IsCanceled);
            string path = candidates[".gitignore"];
            DateTime stamp = File.GetLastWriteTimeUtc(path);
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.ReadWrite))
                stream.WriteByte(1);
            File.SetLastWriteTimeUtc(path, stamp);
            bool rejected = false;
            try
            {
                await rules.ValidateCurrentAsync("epoch", 1, () => true, CancellationToken.None);
            }
            catch (InvalidOperationException)
            {
                rejected = true;
            }
            Assert.IsTrue(rejected, "An equal-length/equal-time edit must fail fresh digest validation.");
            Assert.AreEqual("unavailable", rules.Status("epoch", 1).Value<string>("ignore_state"));
        }

        [Test]
        public void MissingCandidatesAndNestedRulesAreCheckedAtPublication()
        {
            JObject page = Acquire();
            Assert.IsEmpty((JArray)page["files"]);
            PauseWatchers();
            File.WriteAllText(candidates["Assets/Nested/.gitignore"], "secret/\n");
            Assert.Throws<InvalidOperationException>(() => Read(page, validate: true));
            JObject changed = Acquire();
            Assert.AreEqual("Assets/Nested/.gitignore", changed["files"][0].Value<string>("path"));
            Assert.Greater(changed.Value<long>("ignore_revision"), page.Value<long>("ignore_revision"));
            File.Delete(candidates["Assets/Nested/.gitignore"]);
            Assert.Throws<InvalidOperationException>(() => Read(changed, validate: true));
        }

        [Test]
        public void MetadataPagesAreStableLogicalOnlyAndUnrelatedImportsDoNotRediscover()
        {
            File.WriteAllText(candidates[".gitignore"], "Assets/Hidden/\n");
            File.WriteAllText(candidates["Assets/.gitignore"], "Nested/*.asset\n");
            JObject first = Acquire();
            foreach (
                FileSystemWatcher watcher in (System.Collections.IEnumerable)
                    typeof(ReferenceGraphIgnoreRules).GetField("watchers", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(rules)
            )
            {
                Assert.AreEqual("*.gitignore", watcher.Filter);
                if (watcher.Path == root)
                    Assert.IsFalse(watcher.IncludeSubdirectories, "Root observation must not subscribe Library and Temp trees.");
            }
            JObject second = Read(first, 1, true);
            Assert.AreEqual(first["snapshot"], second["snapshot"]);
            Assert.AreEqual(".gitignore", first["files"][0].Value<string>("path"));
            Assert.AreEqual("Assets/.gitignore", second["files"][0].Value<string>("path"));
            Assert.AreEqual(JTokenType.Null, second["next_offset"].Type);
            StringAssert.DoesNotContain(root, first.ToString());
            Assert.AreEqual(first.Value<long>("ignore_revision"), rules.Status("epoch", 100).Value<long>("ignore_revision"));
            Assert.Throws<ArgumentException>(() => Read(first, 3));
            Assert.Throws<ArgumentException>(() => rules.Rules(new JObject { ["offset"] = 1 }, 1, "epoch", 2, () => candidates));
        }

        [Test]
        public void SourceRequiresExactManifestEntryAndChunksLargeRulesWithoutAFileCeiling()
        {
            byte[] expected = System.Text.Encoding.UTF8.GetBytes(new string('x', 150001));
            File.WriteAllBytes(candidates[".gitignore"], expected);
            JObject page = Acquire();
            JObject file = (JObject)page["files"][0];
            var request = new JObject
            {
                ["snapshot"] = page["snapshot"],
                ["path"] = file["path"],
                ["fingerprint"] = file["fingerprint"],
            };
            using (var output = new MemoryStream())
            {
                for (; ; )
                {
                    JObject chunk = rules.Source(request, "epoch", 99);
                    byte[] bytes = Convert.FromBase64String(chunk.Value<string>("bytes_base64"));
                    Assert.LessOrEqual(bytes.Length, 65536);
                    output.Write(bytes, 0, bytes.Length);
                    if (chunk["next_offset"].Type == JTokenType.Null)
                        break;
                    request["offset"] = chunk["next_offset"];
                }
                CollectionAssert.AreEqual(expected, output.ToArray());
            }
            foreach (string path in new[] { "Assets/Nested/other.txt", "../.gitignore", root + "/.gitignore", "Assets\\.gitignore" })
            {
                request["path"] = path;
                Assert.Throws<ArgumentException>(() => rules.Source(request, "epoch", 99));
            }
        }

        [Test]
        public void FingerprintsCatchSameStampEditsAndMovedRules()
        {
            string path = candidates["Assets/.gitignore"];
            File.WriteAllText(path, "one\n");
            JObject before = Acquire();
            PauseWatchers();
            DateTime stamp = File.GetLastWriteTimeUtc(path);
            File.WriteAllText(path, "two\n");
            File.SetLastWriteTimeUtc(path, stamp);
            Assert.Throws<InvalidOperationException>(() => Read(before, validate: true));
            JObject changed = Acquire();
            File.Move(path, candidates["Assets/Nested/.gitignore"]);
            Assert.Throws<InvalidOperationException>(() => Read(changed, validate: true));
        }

        [Test]
        public void LateCallbacksOverflowAndEpochChangesCannotPublishOldProof()
        {
            JObject page = Acquire();
            typeof(ReferenceGraphIgnoreRules).GetMethod("MarkChanged", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(rules, null);
            Assert.Throws<InvalidOperationException>(() => Read(page));
            page = Acquire();
            typeof(ReferenceGraphIgnoreRules)
                .GetMethod("Failed", BindingFlags.Instance | BindingFlags.NonPublic)
                .Invoke(rules, new object[] { null, new ErrorEventArgs(new InternalBufferOverflowException()) });
            Assert.AreEqual("available", rules.Status("epoch", 1).Value<string>("ignore_state"));
            Assert.IsFalse(rules.Status("epoch", 1).Value<bool>("ignore_watchers"));
            Assert.Throws<InvalidOperationException>(() => Read(page, validate: true));
            page = Acquire();
            Assert.AreEqual("available", rules.Status("epoch", 1).Value<string>("ignore_state"));
            Assert.Throws<InvalidOperationException>(() => rules.Rules(new JObject { ["snapshot"] = page["snapshot"] }, 1, "new-epoch", 1, () => candidates));
            rules.Dispose();
            Assert.IsEmpty(
                (System.Collections.ICollection)
                    typeof(ReferenceGraphIgnoreRules).GetField("watchers", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(rules)
            );
            Assert.Throws<ObjectDisposedException>(() => rules.Status("epoch", 1));
        }

        [Test]
        public void InvalidSnapshotOffsetsFingerprintsAndOutsideCandidatesAreRejected()
        {
            File.WriteAllText(candidates[".gitignore"], "owned\n");
            JObject page = Acquire();
            var request = new JObject
            {
                ["snapshot"] = page["snapshot"],
                ["path"] = ".gitignore",
                ["fingerprint"] = page["files"][0]["fingerprint"],
            };
            foreach (JToken offset in new JToken[] { -1, "0", long.MaxValue })
            {
                request["offset"] = offset;
                Assert.Throws<ArgumentException>(() => rules.Source(request, "epoch", 1));
            }
            request["offset"] = 0;
            request["fingerprint"] = "wrong";
            Assert.Throws<ArgumentException>(() => rules.Source(request, "epoch", 1));
            request.Remove("snapshot");
            Assert.Throws<InvalidOperationException>(() => rules.Source(request, "epoch", 1));
            Assert.AreEqual(
                page["ignore_revision"],
                rules.Status("epoch", 1)["ignore_revision"],
                "Invalid caller arguments must not invalidate shared evidence."
            );
            Assert.AreEqual("available", rules.Status("epoch", 1).Value<string>("ignore_state"));
            Assert.DoesNotThrow(() => Read(page, validate: true));
            rules.Invalidate();
            candidates["Assets/Nested/.gitignore"] = Path.Combine(Path.GetDirectoryName(root), ".gitignore");
            Assert.Throws<InvalidOperationException>(() => rules.Rules(new JObject(), 1, "epoch", 1, () => candidates));
            Assert.AreEqual("unavailable", rules.Status("epoch", 1).Value<string>("ignore_state"));
        }

        [Test]
        public void NestedRuleCreateEditMoveDeleteUsesDeclaredWatcherCapability()
        {
            JObject page = Acquire();
            bool automatic = page.Value<bool>("ignore_watchers");
            string nested = candidates["Assets/Nested/.gitignore"];
            string parent = candidates["Assets/.gitignore"];
            string renamed = Path.Combine(root, "Assets", "renamed-rule.txt");
            foreach (
                Action change in new Action[]
                {
                    () => File.WriteAllText(nested, "created\n"),
                    () => File.AppendAllText(nested, "edited\n"),
                    () => File.Move(nested, parent),
                    () => File.Move(parent, renamed),
                    () => File.Move(renamed, parent),
                    () => File.Delete(parent),
                }
            )
            {
                long before = rules.Status("epoch", 1).Value<long>("ignore_revision");
                change();
                if (automatic)
                {
                    var elapsed = System.Diagnostics.Stopwatch.StartNew();
                    while (rules.Status("epoch", 1).Value<long>("ignore_revision") == before && elapsed.ElapsedMilliseconds < 15000)
                        System.Threading.Thread.Sleep(10);
                }
                else
                {
                    Assert.AreEqual(
                        before,
                        rules.Status("epoch", 1).Value<long>("ignore_revision"),
                        "Degraded observation must not claim automatic invalidation."
                    );
                    Assert.Throws<InvalidOperationException>(
                        () => Read(page, validate: true),
                        "Explicit validation must reject changed nested rules before delivery."
                    );
                }
                Assert.Greater(
                    rules.Status("epoch", 1).Value<long>("ignore_revision"),
                    before,
                    automatic
                        ? "Nested changes must invalidate without a census or explicit validation."
                        : "Changed rules must invalidate through declared explicit validation."
                );
                page = Acquire();
                Assert.AreEqual(automatic, page.Value<bool>("ignore_watchers"));
            }
        }

        [Test]
        public void PollingUnknownAndUninspectableMonoBackendsRequireExplicitValidation()
        {
            foreach (string backend in new[] { "System.IO.DefaultWatcher", "System.IO.NullFileWatcher", "System.IO.UnknownWatcher", null })
                Assert.IsFalse(ReferenceGraphIgnoreRules.CanObserveWithoutPolling(true, () => backend));
            Assert.IsFalse(ReferenceGraphIgnoreRules.CanObserveWithoutPolling(true, () => throw new InvalidOperationException("Owned inspection failure")));
            foreach (string backend in new[] { "System.IO.CoreFXFileSystemWatcherProxy", "System.IO.FAMWatcher", "System.IO.KeventWatcher" })
                Assert.IsTrue(ReferenceGraphIgnoreRules.CanObserveWithoutPolling(true, () => backend));
            Assert.IsTrue(ReferenceGraphIgnoreRules.CanObserveWithoutPolling(false, () => throw new Exception("Non-Mono inspection must not run.")));
        }

        [Test]
        public void SuffixWatcherCallbacksInvalidateOnlyExactRuleNamesAndBothRenameSides()
        {
            Acquire();
            PauseWatchers();
            MethodInfo changed = typeof(ReferenceGraphIgnoreRules).GetMethod("Changed", BindingFlags.Instance | BindingFlags.NonPublic);
            MethodInfo renamed = typeof(ReferenceGraphIgnoreRules).GetMethod("Renamed", BindingFlags.Instance | BindingFlags.NonPublic);
            long before = rules.Status("epoch", 1).Value<long>("ignore_revision");
            changed.Invoke(rules, new object[] { null, new FileSystemEventArgs(WatcherChangeTypes.Changed, root, "Assets/Nested/other.gitignore") });
            renamed.Invoke(
                rules,
                new object[] { null, new RenamedEventArgs(WatcherChangeTypes.Renamed, root, "Assets/new.gitignore", "Assets/old.gitignore") }
            );
            Assert.AreEqual(before, rules.Status("epoch", 1).Value<long>("ignore_revision"));
            foreach (WatcherChangeTypes change in new[] { WatcherChangeTypes.Created, WatcherChangeTypes.Changed })
            foreach (string name in new[] { "Assets/Nested/.gitignore", ".gitignore", "Assets/Nested/.GITIGNORE" })
            {
                before = rules.Status("epoch", 1).Value<long>("ignore_revision");
                changed.Invoke(rules, new object[] { null, new FileSystemEventArgs(change, root, name) });
                long after = rules.Status("epoch", 1).Value<long>("ignore_revision");
                Assert.AreEqual(before + 1, after, "Rule-name hints must invalidate for either casing, independently of filesystem path comparison.");
            }
            foreach (string rule in new[] { "Assets/.gitignore", "Assets/.GITIGNORE" })
            foreach (bool intoRule in new[] { false, true })
            {
                before = rules.Status("epoch", 1).Value<long>("ignore_revision");
                renamed.Invoke(
                    rules,
                    new object[]
                    {
                        null,
                        new RenamedEventArgs(WatcherChangeTypes.Renamed, root, intoRule ? rule : "Assets/renamed.txt", intoRule ? "Assets/renamed.txt" : rule),
                    }
                );
                Assert.Greater(rules.Status("epoch", 1).Value<long>("ignore_revision"), before);
            }
        }

        [Test]
        public void WatcherFactoryFailureUsesFreshExplicitValidationWithoutAllowingStaleRules()
        {
            rules.Dispose();
            rules = new ReferenceGraphIgnoreRules(root, 10, _ => throw new IOException("Owned watcher factory failure"));
            JObject status = rules.Status("epoch", 1);
            Assert.AreEqual("available", status.Value<string>("ignore_state"));
            Assert.IsFalse(status.Value<bool>("ignore_watchers"));
            JObject initial = Acquire();
            Assert.IsFalse(initial.Value<bool>("ignore_watchers"));
            File.WriteAllText(candidates["Assets/Nested/.gitignore"], "secret/\n");
            Assert.Throws<InvalidOperationException>(() => Read(initial, validate: true));
            JObject updated = Acquire();
            Assert.Greater(updated.Value<long>("ignore_revision"), initial.Value<long>("ignore_revision"));
            Assert.AreEqual("Assets/Nested/.gitignore", updated["files"][0].Value<string>("path"));
            Assert.DoesNotThrow(() => Read(updated, validate: true));
            Assert.IsFalse(rules.Status("epoch", 1).Value<bool>("ignore_watchers"));
        }

        [Test]
        public void SourceReadFailureInvalidatesAndLatchesUnavailableUntilFreshCapture() => CheckLockedFileFailure(false);

        [Test]
        public void ValidationReadFailureInvalidatesAndLatchesUnavailableUntilFreshCapture() => CheckLockedFileFailure(true);

        private void CheckLockedFileFailure(bool validation)
        {
            string physical = candidates["Assets/Nested/.gitignore"];
            File.WriteAllText(physical, "private/\n");
            JObject page = Acquire();
            PauseWatchers();
            var source = new JObject
            {
                ["snapshot"] = page["snapshot"],
                ["path"] = page["files"][0]["path"],
                ["fingerprint"] = page["files"][0]["fingerprint"],
            };
            using (File.Open(physical, FileMode.Open, FileAccess.Read, FileShare.None))
            {
                Assert.Catch(() =>
                {
                    if (validation)
                        Read(page, validate: true);
                    else
                        rules.Source(source, "epoch", 1);
                });
                JObject failed = rules.Status("epoch", 1);
                Assert.AreEqual("unavailable", failed.Value<string>("ignore_state"), "A real file-read failure must not leave cached evidence current.");
                Assert.Greater(failed.Value<long>("ignore_revision"), page.Value<long>("ignore_revision"));
                Assert.IsNotEmpty(failed.Value<string>("ignore_error"));
                Assert.Throws<InvalidOperationException>(() => Read(page));
            }
            Assert.AreEqual("unavailable", rules.Status("epoch", 1).Value<string>("ignore_state"), "Releasing a lock alone must not republish old proof.");
            JObject recovered = Acquire();
            Assert.AreEqual("available", rules.Status("epoch", 1).Value<string>("ignore_state"));
            Assert.AreNotEqual(page["snapshot"], recovered["snapshot"]);
            Assert.DoesNotThrow(() => Read(recovered, validate: true));
        }
    }

    [TestFixture, Parallelizable(ParallelScope.None)]
    public sealed class ReferenceGraphCollectorTests
    {
        private string root;
        private static readonly FieldInfo InventoryCache = typeof(ReferenceGraphCollector).GetField(
            "inventories",
            BindingFlags.NonPublic | BindingFlags.Static
        );

        internal static ReferenceGraphInventories Inventories
        {
            get => (ReferenceGraphInventories)InventoryCache.GetValue(null);
            set => InventoryCache.SetValue(null, value);
        }

        internal static JObject Call(string action, params object[] pairs)
        {
            var type = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphCollector");
            Assert.IsNotNull(type, "The incremental reference collector must be available without optional packages.");
            var request = new JObject { ["action"] = action };
            for (int i = 0; i < pairs.Length; i += 2)
                request[(string)pairs[i]] = JToken.FromObject(pairs[i + 1]);
            return (JObject)type.GetMethod("Handle", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { request });
        }

        [SetUp]
        public void SetUp()
        {
            root = "Assets/__McpReferenceGraph_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
        }

        [TearDown]
        public void TearDown()
        {
            if (root != null)
            {
                StringAssert.StartsWith("Assets/__McpReferenceGraph_", root);
                AssetDatabase.DeleteAsset(root);
            }
        }

        private string CreateText(string name, string value)
        {
            string path = root + "/" + name;
            File.WriteAllText(path, value);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            return path;
        }

        [Test]
        public void IgnoreParentsGrowFromImportsWithoutReplacingTheCachedCensus()
        {
            CreateText("initial.txt", "initial");
            Call("ignore_rules", "validate", true);
            string beforeToken = Inventories.Current.Token;
            long before = Call("status").Value<long>("ignore_revision");
            CreateText("same-parent.txt", "same parent");
            Assert.AreEqual(before, Call("status").Value<long>("ignore_revision"));
            Assert.AreEqual(beforeToken, Inventories.Current.Token, "Known-parent imports must reuse the cached GUID census.");
            AssetDatabase.CreateFolder(root, "NewParent");
            CreateText("NewParent/new.txt", "new parent");
            Assert.Greater(Call("status").Value<long>("ignore_revision"), before);
            File.WriteAllText(root + "/NewParent/.gitignore", "new.txt\n");
            JObject page = Call("ignore_rules", "validate", true, "page_size", 1000);
            Assert.IsTrue(((JArray)page["files"]).Any(row => row.Value<string>("path") == root + "/NewParent/.gitignore"));
            Assert.AreEqual(beforeToken, Inventories.Current.Token, "New-parent discovery must extend the existing census candidates.");
        }

        [Test]
        public void ExplicitNewCensusRefreshesJournalChangesWithoutRefreshingOrdinaryReads()
        {
            CreateText("census-before.txt", "before");
            JObject before = Call("inventory", "page_size", 1);
            string snapshot = before.Value<string>("snapshot");
            string[] original = Inventories.Current.Items;
            Assert.AreEqual(snapshot, Call("inventory", "page_size", 1).Value<string>("snapshot"), "An unchanged explicit census must reuse its snapshot.");
            AssetDatabase.CreateFolder(root, "CensusAdded");
            string path = CreateText("CensusAdded/census-after.txt", "after");
            string guid = AssetDatabase.AssetPathToGUID(path);
            Assert.AreEqual(snapshot, Inventories.Current.Token, "Import notifications must not rebuild the shared census.");
            Call("status");
            Call("ignore_rules", "validate", true);
            Assert.AreEqual(snapshot, Inventories.Current.Token, "Status and implicit ignore discovery must not refresh the census.");
            Assert.IsFalse(original.Contains(guid));
            Assert.AreEqual(snapshot, Call("inventory", "snapshot", snapshot, "offset", 0, "page_size", 1).Value<string>("snapshot"));
            JObject fresh = Call("inventory", "page_size", 1);
            Assert.AreNotEqual(snapshot, fresh.Value<string>("snapshot"));
            int index = Array.IndexOf(Inventories.Current.Items, guid);
            Assert.GreaterOrEqual(index, 0, "A new explicit census must discover the new imported GUID.");
            JObject row = Call("inventory", "snapshot", fresh.Value<string>("snapshot"), "offset", index, "page_size", 1);
            Assert.AreEqual(guid, row["assets"][0].Value<string>("guid"));
            Assert.AreEqual(path, row["assets"][0].Value<string>("path"));
            Assert.AreEqual(fresh["snapshot"], Call("inventory", "page_size", 1)["snapshot"]);
            if (original.Length <= ReferenceGraphInventories.MaximumPreviousGuids)
            {
                JObject resumed = Call("inventory", "snapshot", snapshot, "offset", 1, "page_size", 1);
                Assert.AreEqual(before["revision"], resumed["revision"]);
                Assert.AreEqual(before["epoch"], resumed["epoch"]);
                Assert.AreEqual(before["snapshot"], resumed["snapshot"]);
                Assert.AreSame(original, Inventories.Previous.Items);
            }
            CreateText("census-third.txt", "third");
            Call("inventory", "page_size", 1);
            var stale = Assert.Throws<TargetInvocationException>(() => Call("inventory", "snapshot", snapshot, "offset", 0, "page_size", 1));
            Assert.IsInstanceOf<InvalidOperationException>(stale.InnerException);
            Assert.AreEqual("InvalidInventorySnapshotException", stale.InnerException.GetType().Name);
            StringAssert.Contains("restart the census", stale.InnerException.Message);
        }

        [Test]
        public void InventoryRetentionBoundsHistoryBySizeIdleTimeAndJournalEpoch()
        {
            var cache = new ReferenceGraphInventories(2);
            cache.Publish(new[] { "a", "b", "c" }, "oversized", "epoch", 1, 0);
            Assert.IsNotNull(cache.Find("oversized", "epoch", 0));
            cache.Publish(new[] { "d", "e" }, "exact", "epoch", 2, 10);
            Assert.IsNull(cache.Find("oversized", "epoch", 10));
            cache.Publish(new[] { "f" }, "latest", "epoch", 3, 20);
            cache.Publish(new[] { "f" }, "other-waiter", "epoch", 3, 20);
            Assert.IsNotNull(cache.Find("exact", "epoch", 610));
            Assert.IsNull(cache.Find("latest", "epoch", 621));
            Assert.IsNotNull(cache.Find("exact", "epoch", 621));
            Assert.IsNull(cache.Find("exact", "epoch", 1222));
            cache.Publish(new[] { "g" }, "fourth", "epoch", 4, 1300);
            cache.Publish(new[] { "h" }, "fifth", "epoch", 5, 1400);
            Assert.IsNull(cache.Find("fourth", "different-epoch", 1401));
            Assert.IsNull(cache.Find("fifth", "different-epoch", 1401));
        }

        [Test]
        public void ReleasingIgnoreObserversAdvancesRevisionAndExpiresTheirSnapshot()
        {
            CreateText("reload.txt", "owned");
            JObject page = Call("ignore_rules", "validate", true);
            JObject before = Call("status");
            var type = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphCollector");
            type.GetMethod("ReleaseIgnoreRules", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            JObject after = Call("status");
            Assert.AreEqual(before["instance_id"], after["instance_id"]);
            Assert.AreEqual(before["epoch"], after["epoch"]);
            Assert.Greater(after.Value<long>("ignore_revision"), before.Value<long>("ignore_revision"));
            var error = Assert.Throws<TargetInvocationException>(() => Call("ignore_rules", "snapshot", page.Value<string>("snapshot"), "validate", true));
            Assert.IsInstanceOf<InvalidOperationException>(error.InnerException);
        }

        [TestCase("Packages/com.example/C:/Windows/win.ini")]
        [TestCase("Packages/com.example/C:Windows/win.ini")]
        [TestCase("Packages/com.example/file.asset:stream")]
        [TestCase("Assets/file.asset:stream")]
        [TestCase("Packages/com.example/../outside")]
        [TestCase("Packages/com.example//outside")]
        [TestCase("Packages/com.example/\\\\host\\share")]
        [TestCase("asset:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
        public void MutationProbeRejectsEscapingLogicalPathsBeforeResolution(string path)
        {
            var error = Assert.Throws<TargetInvocationException>(() => Call("probe", "paths", new[] { path }));
            Assert.IsInstanceOf<ArgumentException>(error.InnerException);
        }

        [Test]
        public void RegisteredPackageResolutionKeepsItsExactPlainRoot()
        {
            var type = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphCollector");
            var resolve = type.GetMethod("ResolveRegisteredPackagePath", BindingFlags.NonPublic | BindingFlags.Static);
            string physicalRoot = Path.GetFullPath(root);
            string resolved = (string)resolve.Invoke(null, new object[] { "Packages/com.example/Runtime/File.asset", "Packages/com.example", physicalRoot });
            Assert.AreEqual(Path.Combine(physicalRoot, "Runtime", "File.asset"), resolved);
            Assert.AreEqual(physicalRoot, resolve.Invoke(null, new object[] { "Packages/com.example", "Packages/com.example", physicalRoot }));
            foreach (string path in new[] { "Packages/com.example-other/File.asset", "Packages/com.example/C:/outside", "Packages/com.example/../outside" })
            {
                var error = Assert.Throws<TargetInvocationException>(() => resolve.Invoke(null, new object[] { path, "Packages/com.example", physicalRoot }));
                Assert.IsInstanceOf<ArgumentException>(error.InnerException);
            }
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [TestCase(false)]
        [TestCase(true)]
        public void RegisteredPackageResolutionRejectsLinkedRootsAndDescendants(bool linkedRoot)
        {
            var type = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphCollector");
            var resolve = type.GetMethod("ResolveRegisteredPackagePath", BindingFlags.NonPublic | BindingFlags.Static);
            string physicalRoot = Path.GetFullPath(root);
            string target = Path.Combine(physicalRoot, "plain");
            string link = Path.Combine(physicalRoot, "linked");
            Directory.CreateDirectory(target);
            bool created = Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(link, target, 3) : symlink(target, link) == 0;
            try
            {
                if (!created)
                    Assert.Ignore("Symlink creation unavailable: " + Marshal.GetLastWin32Error());
                string path = "Packages/com.example/" + (linkedRoot ? "File.asset" : "linked/File.asset");
                var error = Assert.Throws<TargetInvocationException>(() =>
                    resolve.Invoke(null, new object[] { path, "Packages/com.example", linkedRoot ? link : physicalRoot })
                );
                Assert.IsInstanceOf<InvalidOperationException>(error.InnerException);
            }
            finally
            {
                if (created)
                    Directory.Delete(link);
                Directory.Delete(target);
            }
        }

        [Test]
        public void MutationProbeObservesUnimportedBytesWithoutSchedulingImport()
        {
            string path = CreateText("receipt.txt", "before");
            string guid = AssetDatabase.AssetPathToGUID(path);
            JObject original = (JObject)Call("probe", "paths", new[] { path })["targets"][0];
            JObject dependencies = Call("dependencies", "guid", guid);
            Assert.AreEqual(original["stamp"], dependencies["file_stamp"]);
            Assert.AreEqual(original["dependency"], dependencies["fingerprint"]);
            JObject checkpoint = Call("status");
            File.WriteAllText(path, "changed before a deferred import");
            JObject changed = (JObject)Call("probe", "paths", new[] { path })["targets"][0];
            Assert.AreNotEqual(original["stamp"], changed["stamp"]);
            Assert.AreEqual(checkpoint["revision"], Call("status")["revision"], "A read-only probe must not cause an import.");
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            JObject events = Call("changes", "epoch", checkpoint.Value<string>("epoch"), "after", checkpoint.Value<long>("revision"));
            Assert.IsTrue(events["changes"].Any(row => row.Value<string>("path") == path));
            JObject noop = Call("probe", "paths", new[] { path });
            Assert.AreEqual(noop["targets"][0]["stamp"], events["changes"].Last(row => row.Value<string>("path") == path)["file_stamp"]);
            Assert.IsTrue(JToken.DeepEquals(noop, Call("probe", "paths", new[] { path })));
        }

        [Test]
        public void MutationProbeTracksCreateMoveDeleteAndFolderWithoutLoadingAssets()
        {
            string path = root + "/created.txt";
            Assert.IsFalse(Call("probe", "paths", new[] { path })["targets"][0].Value<bool>("present"));
            CreateText("created.txt", "owned");
            string guid = AssetDatabase.AssetPathToGUID(path);
            string moved = root + "/moved.txt";
            Assert.AreEqual("", AssetDatabase.MoveAsset(path, moved));
            JObject page = Call("probe", "paths", new[] { path, moved, root });
            Assert.IsFalse(page["targets"][0].Value<bool>("present"));
            Assert.AreEqual(guid, page["targets"][1].Value<string>("guid"));
            Assert.IsTrue(page["targets"][2].Value<bool>("folder"));
            AssetDatabase.DeleteAsset(moved);
            Assert.IsFalse(Call("probe", "paths", new[] { moved })["targets"][0].Value<bool>("present"));
        }

        [Test]
        public void AssetIdentityReportsMainTypeWithoutClaimingSubassetCompleteness()
        {
            string path = CreateText("identity.txt", "owned");
            JObject row = Call("asset", "guid", AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(typeof(TextAsset).FullName, row.Value<string>("main_type"));
            Assert.AreEqual("main_type_only", row.Value<string>("identity_coverage"));
            Assert.IsNull(row["subassets"]);
        }

        [Test]
        public void CopyDestinationPreparationIsReadOnlyAndExplicitCopyDoesNotOverwrite()
        {
            string source = CreateText("copy.txt", "original");
            JObject before = Call("status");
            JObject prepared = Call("copy_destination", "path", source);
            string destination = prepared.Value<string>("path");
            Assert.AreNotEqual(source, destination);
            Assert.IsFalse(File.Exists(destination));
            Assert.AreEqual(before["revision"], prepared["revision"]);
            var copied = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "duplicate",
                        ["path"] = source,
                        ["destination"] = destination,
                    }
                )
            );
            Assert.IsTrue(copied.Value<bool>("success"), copied.ToString());
            Assert.AreEqual(destination, copied["data"].Value<string>("path"));
            File.WriteAllText(destination, "concurrent");
            var collision = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "duplicate",
                        ["path"] = source,
                        ["destination"] = destination,
                    }
                )
            );
            Assert.IsFalse(collision.Value<bool>("success"));
            Assert.AreEqual("concurrent", File.ReadAllText(destination));
        }

        [Test]
        public void IndependentConsumersSeeTheSameImportMoveAndDeleteHistory()
        {
            JObject start = Call("status");
            string path = CreateText("created.txt", "owned");
            string moved = root + "/moved.txt";
            Assert.AreEqual("", AssetDatabase.MoveAsset(path, moved));
            AssetDatabase.DeleteAsset(moved);
            JObject first = Call("changes", "epoch", start.Value<string>("epoch"), "after", start.Value<long>("revision"), "page_size", 100);
            JObject second = Call("changes", "epoch", start.Value<string>("epoch"), "after", start.Value<long>("revision"), "page_size", 100);
            Assert.IsTrue(JToken.DeepEquals(first, second));
            Assert.IsTrue(first["changes"].Any(row => row.Value<string>("path") == path));
            Assert.IsTrue(first["changes"].Any(row => row.Value<string>("path") == moved && row.Value<bool>("deleted")));
            Assert.IsFalse(first.Value<bool>("requires_census"));
        }

        [Test]
        public void CompleteCensusAndDependencyPagesPreserveGuidIdentity()
        {
            string path = CreateText("target.txt", "owned");
            string guid = AssetDatabase.AssetPathToGUID(path);
            var asset = ScriptableObject.CreateInstance<ReferenceDiagnosticFixture>();
            asset.reference = AssetDatabase.LoadAssetAtPath<TextAsset>(path);
            string owner = root + "/owner.asset";
            AssetDatabase.CreateAsset(asset, owner);
            AssetDatabase.SaveAssets();
            string ownerGuid = AssetDatabase.AssetPathToGUID(owner);
            JObject first = Call("inventory", "page_size", 31);
            string token = first.Value<string>("snapshot");
            var rows = (JArray)first["assets"].DeepClone();
            JObject page = first;
            while (page["next_offset"].Type != JTokenType.Null)
            {
                page = Call("inventory", "snapshot", token, "offset", page.Value<int>("next_offset"), "page_size", 31);
                foreach (JToken row in page["assets"])
                    rows.Add(row);
            }
            Assert.AreEqual(1, rows.Count(row => row.Value<string>("guid") == guid));
            Assert.AreEqual(1, rows.Count(row => row.Value<string>("guid") == ownerGuid));
            JObject dependencies = Call("dependencies", "guid", ownerGuid, "page_size", 1);
            var targets = (JArray)dependencies["targets"].DeepClone();
            while (dependencies["next_offset"].Type != JTokenType.Null)
            {
                dependencies = Call(
                    "dependencies",
                    "guid",
                    ownerGuid,
                    "snapshot",
                    dependencies.Value<string>("snapshot"),
                    "offset",
                    dependencies.Value<int>("next_offset"),
                    "page_size",
                    1
                );
                foreach (JToken target in dependencies["targets"])
                    targets.Add(target);
            }
            Assert.IsTrue(targets.Values<string>().Contains(guid));
            Assert.IsFalse(targets.Values<string>().Contains(ownerGuid));
            string scriptPath = AssetDatabase.GetAssetPath(MonoScript.FromScriptableObject(asset));
            var proofs = (JArray)Call("probe", "paths", new[] { owner, scriptPath }, "static_proof", true)["targets"];
            Assert.AreEqual(targets.Count, proofs[0].Value<int>("dependency_count"));
            Assert.AreEqual(dependencies["fingerprint"], proofs[0]["dependency"]);
            JObject source = Call("source", "guid", AssetDatabase.AssetPathToGUID(scriptPath));
            Assert.AreEqual(source["compilation_context"]["binding_revision"], proofs[1]["binding"]);
            Assert.IsNull(Call("probe", "paths", new[] { owner })["targets"][0]["dependency_count"]);
        }

        [Test]
        public void JournalRestoreAndExpiredEpochRequireExplicitRecovery()
        {
            JObject start = Call("status");
            CreateText("journal.txt", "owned");
            var type = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphJournal");
            Assert.IsNotNull(type);
            var flags = BindingFlags.Static | BindingFlags.NonPublic;
            type.GetMethod("Save", flags).Invoke(null, null);
            type.GetMethod("Restore", flags).Invoke(null, null);
            JObject status = Call("status");
            Assert.AreEqual(start["instance_id"], status["instance_id"]);
            Assert.AreEqual(start["epoch"], status["epoch"]);
            JObject changes = Call("changes", "epoch", start.Value<string>("epoch"), "after", start.Value<long>("revision"));
            Assert.IsNotEmpty(changes["changes"]);
            Assert.IsTrue(Call("changes", "epoch", "expired", "after", 0).Value<bool>("requires_census"));
        }

        [Test]
        public void NoopReadsDoNotChangeAssetRevisionAndEscapingSourcesAreRefused()
        {
            JObject before = Call("status");
            Call("inventory", "page_size", 10);
            JObject after = Call("status");
            Assert.AreEqual(before["revision"], after["revision"]);
            Assert.AreEqual(before["instance_id"], after["instance_id"]);
            var error = Assert.Throws<TargetInvocationException>(() => Call("source", "guid", "../../outside"));
            Assert.IsInstanceOf<ArgumentException>(error.InnerException);
        }

        [Test]
        public void InterleavedConsumersRetainInventoryAndDependencyContinuation()
        {
            string firstPath = CreateText("first.txt", "owned first");
            string secondPath = CreateText("second.txt", "owned second");
            string firstGuid = AssetDatabase.AssetPathToGUID(firstPath);
            string secondGuid = AssetDatabase.AssetPathToGUID(secondPath);
            var asset = ScriptableObject.CreateInstance<ReferenceDiagnosticFixture>();
            asset.reference = AssetDatabase.LoadAssetAtPath<TextAsset>(firstPath);
            string ownerPath = root + "/interleaved-owner.asset";
            AssetDatabase.CreateAsset(asset, ownerPath);
            AssetDatabase.SaveAssetIfDirty(asset);
            string ownerGuid = AssetDatabase.AssetPathToGUID(ownerPath);
            JObject one = Call("inventory", "page_size", 1);
            JObject two = Call("inventory", "page_size", 1);
            Assert.AreEqual(one["snapshot"], two["snapshot"]);
            Call("inventory", "snapshot", one.Value<string>("snapshot"), "offset", 1, "page_size", 1);
            JObject expected = Call("dependencies", "guid", ownerGuid);
            Assert.Greater(expected["targets"].Count(), 1, "The owner references both its script and the text fixture.");
            Assert.IsTrue(expected["targets"].Values<string>().Contains(firstGuid));
            JObject dependencies = Call("dependencies", "guid", ownerGuid, "page_size", 1);
            Call("dependencies", "guid", secondGuid);
            Assert.AreEqual(1, dependencies.Value<int>("next_offset"));
            JObject resumed = Call("dependencies", "guid", ownerGuid, "snapshot", dependencies.Value<string>("snapshot"), "offset", 1);
            var targets = (JArray)dependencies["targets"].DeepClone();
            foreach (JToken target in resumed["targets"])
                targets.Add(target);
            Assert.IsTrue(JToken.DeepEquals(expected["targets"], targets));
        }

        [Test]
        public void SourcePagesRoundTripExistingLargeScriptWithoutExtraRefresh()
        {
            string sourceGuid = AssetDatabase
                .FindAssets("ManageScript t:MonoScript")
                .Single(id => AssetDatabase.GUIDToAssetPath(id).EndsWith("/ManageScript.cs", StringComparison.Ordinal));
            string path = AssetDatabase.GUIDToAssetPath(sourceGuid);
            JObject before = Call("status");
            using var bytes = new MemoryStream();
            JObject page = Call("source", "guid", sourceGuid);
            Assert.AreEqual("available", page["compilation_context"].Value<string>("state"));
            Assert.AreEqual(before["compilation_context"]["revision"], page["compilation_context"]["revision"]);
            Assert.IsTrue(page["compilation_context"]["defines"].Values<string>().Contains("UNITY_EDITOR"));
            while (true)
            {
                byte[] part = Convert.FromBase64String(page.Value<string>("bytes_base64"));
                bytes.Write(part, 0, part.Length);
                if (page["next_offset"].Type == JTokenType.Null)
                    break;
                page = Call("source", "guid", sourceGuid, "offset", page.Value<long>("next_offset"), "fingerprint", page.Value<string>("fingerprint"));
                Assert.AreEqual(JTokenType.Null, page["compilation_context"].Type, "Assembly metadata is sent once per source read.");
            }
            CollectionAssert.AreEqual(File.ReadAllBytes(path), bytes.ToArray());
            Assert.Greater(bytes.Length, 65536, "Exercise real source continuation across transport pages.");
            Assert.AreEqual(before["revision"], Call("status")["revision"]);
        }

        [Test]
        public void CompilationContextIsDeterministicCachedAndReleasedWhenIdle()
        {
            var type = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphCompilation", true);
            var flags = BindingFlags.NonPublic | BindingFlags.Static;
            var status = type.GetMethod("Status", flags);
            var invalidate = type.GetMethod("Invalidate", flags);
            JObject first = (JObject)status.Invoke(null, null);
            Assert.AreEqual("available", first.Value<string>("state"));
            Assert.AreEqual(EditorUserBuildSettings.activeBuildTarget.ToString(), first.Value<string>("target"));
            var sourceMap = type.GetField("sources", flags).GetValue(null);
            long calibration = GC.GetAllocatedBytesForCurrentThread();
            GC.KeepAlive(new byte[4096]);
            bool counterAvailable = GC.GetAllocatedBytesForCurrentThread() > calibration;
            var clock = System.Diagnostics.Stopwatch.StartNew();
            long allocated = GC.GetAllocatedBytesForCurrentThread();
            for (int i = 0; i < 1000; i++)
                status.Invoke(null, null);
            allocated = GC.GetAllocatedBytesForCurrentThread() - allocated;
            clock.Stop();
            Assert.AreSame(sourceMap, type.GetField("sources", flags).GetValue(null));
            TestContext.WriteLine(
                "REFERENCE_COMPILATION_WARM "
                    + new JObject
                    {
                        ["calls"] = 1000,
                        ["milliseconds"] = clock.Elapsed.TotalMilliseconds,
                        ["allocated_bytes"] = counterAvailable ? (JToken)allocated : JValue.CreateNull(),
                    }
            );
            invalidate.Invoke(null, new object[] { null });
            clock.Restart();
            allocated = GC.GetAllocatedBytesForCurrentThread();
            JObject rebuilt = (JObject)status.Invoke(null, null);
            allocated = GC.GetAllocatedBytesForCurrentThread() - allocated;
            clock.Stop();
            Assert.IsTrue(JToken.DeepEquals(first, rebuilt));
            Assert.AreNotSame(sourceMap, type.GetField("sources", flags).GetValue(null));
            TestContext.WriteLine(
                "REFERENCE_COMPILATION_REBUILD "
                    + new JObject
                    {
                        ["milliseconds"] = clock.Elapsed.TotalMilliseconds,
                        ["allocated_bytes"] = counterAvailable ? (JToken)allocated : JValue.CreateNull(),
                    }
            );
            type.GetMethod("ReleaseExpired", flags).Invoke(null, new object[] { EditorApplication.timeSinceStartup + 601 });
            Assert.IsNull(type.GetField("sources", flags).GetValue(null));
            Assert.IsFalse((bool)type.GetProperty("Retained", flags).GetValue(null));
            Assert.IsTrue(JToken.DeepEquals(first, (JObject)status.Invoke(null, null)));
        }

        [Test]
        public void CompilationPagesMatchSourceContextAndRejectStaleRevision()
        {
            string guid = AssetDatabase
                .FindAssets("ManageScript t:MonoScript")
                .Single(id => AssetDatabase.GUIDToAssetPath(id).EndsWith("/ManageScript.cs", StringComparison.Ordinal));
            string path = AssetDatabase.GUIDToAssetPath(guid);
            JObject source = Call("source", "guid", guid);
            string revision = source["compilation_context"].Value<string>("revision");
            JObject page = Call("compilation", "paths", new JArray(path), "revision", revision);
            Assert.AreEqual(revision, page.Value<string>("revision"));
            Assert.AreEqual(source["compilation_context"]["binding_revision"], page["bindings"][0]["signature"]);
            var stale = Assert.Throws<TargetInvocationException>(() => Call("compilation", "paths", new JArray(path), "revision", "old"));
            Assert.IsInstanceOf<InvalidOperationException>(stale.InnerException);
            var escaped = Assert.Throws<TargetInvocationException>(() => Call("compilation", "paths", new JArray("../outside.cs"), "revision", revision));
            Assert.IsInstanceOf<ArgumentException>(escaped.InnerException);
        }

        [TestCase("Assets/Resources/GameData/example.asset")]
        [TestCase("Assets/Resources/GenericContent/example.asset")]
        [TestCase("Packages/com.example.library/Runtime/Example.cs")]
        public void CollectionAdmissionDoesNotAssumeAnApplicationFolderLayout(string path)
        {
            var type = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphCollector");
            Assert.IsTrue((bool)type.GetMethod("Admitted", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { path }));
        }

        [Test]
        public void RetentionOverflowRequestsCensusInsteadOfReturningIncompleteChanges()
        {
            JObject before = Call("status");
            var type = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphJournal");
            string[] paths = Enumerable.Range(0, 65537).Select(i => "Assets/Generated/" + i + ".bytes").ToArray();
            type.GetMethod("Record", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { paths, Array.Empty<string>(), Array.Empty<string>(), Array.Empty<string>() });
            JObject after = Call("changes", "epoch", before.Value<string>("epoch"), "after", before.Value<long>("revision"));
            Assert.IsTrue(after.Value<bool>("requires_census"));
            Assert.AreNotEqual(before["epoch"], after["epoch"]);
        }

        [Test]
        public void LegacyAddressableLookupPreservesStoredConfigAndFindsKeys()
        {
            Type settingsType = Type.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings, Unity.Addressables.Editor");
            if (settingsType == null)
                Assert.Ignore("Addressables is an optional project package.");
            Type defaults = Type.GetType("UnityEditor.AddressableAssets.AddressableAssetSettingsDefaultObject, Unity.Addressables.Editor", true);
            string currentKey = (string)defaults.GetField("kDefaultConfigObjectName").GetValue(null);
            string legacyKey = (string)defaults.GetField("kDefaultConfigAssetName").GetValue(null);
            EditorBuildSettings.TryGetConfigObject<UnityEngine.Object>(currentKey, out var oldCurrent);
            EditorBuildSettings.TryGetConfigObject<UnityEngine.Object>(legacyKey, out var oldLegacy);
            try
            {
                var settings = (UnityEngine.Object)
                    settingsType
                        .GetMethod("Create", new[] { typeof(string), typeof(string), typeof(bool), typeof(bool) })
                        .Invoke(null, new object[] { root, "Settings", false, false });
                AssetDatabase.CreateAsset(settings, root + "/Settings.asset");
                AssetDatabase.SaveAssetIfDirty(settings);
                UnityEngine.Resources.UnloadAsset(settings);
                settings = AssetDatabase.LoadAssetAtPath(root + "/Settings.asset", settingsType);
                object Invoke(object value, string name, params object[] args) =>
                    MCPForUnityTests.Editor.Tools.AddressablesAuthoringTests.Invoke(value, name, args);
                object group = Invoke(settings, "CreateGroup", "Owned", false, false, false, null, Array.Empty<Type>());
                string path = CreateText("addressable.txt", "owned");
                string guid = AssetDatabase.AssetPathToGUID(path);
                object entry = Invoke(settings, "CreateOrMoveEntry", guid, group, false, false);
                Invoke(entry, "SetAddress", "owned/addressable", false);
                Invoke(settings, "AddLabel", "owned-label", false);
                Invoke(entry, "SetLabel", "owned-label", true, false, false);
                EditorBuildSettings.RemoveConfigObject(currentKey);
                EditorBuildSettings.AddConfigObject(legacyKey, settings, true);
                // Force a genuine import observation after installing the fixture config.
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                byte[] before = File.ReadAllBytes(root + "/Settings.asset");
                JObject asset = Call("asset", "guid", guid);
                Assert.AreEqual("available", asset.Value<string>("addressables"), asset.ToString());
                Assert.IsTrue(asset["keys"].Any(key => key.Value<string>("scheme") == "addressables" && key.Value<string>("value") == "owned/addressable"));
                Assert.IsTrue(asset["keys"].Any(key => key.Value<string>("scheme") == "addressables_label" && key.Value<string>("value") == "owned-label"));
                Assert.IsFalse(EditorBuildSettings.TryGetConfigObject<UnityEngine.Object>(currentKey, out _));
                Assert.IsTrue(EditorBuildSettings.TryGetConfigObject<UnityEngine.Object>(legacyKey, out var retained));
                Assert.AreEqual(settings, retained);
                CollectionAssert.AreEqual(before, File.ReadAllBytes(root + "/Settings.asset"));
                JObject prior = Call("status");
                Invoke(entry, "SetAddress", "owned/changed", true);
                JObject changed = Call("changes", "epoch", prior.Value<string>("epoch"), "after", prior.Value<long>("revision"));
                Assert.IsTrue(changed["changes"].Any(row => row.Value<string>("kind") == "addressables_catalog"), changed.ToString());
                Assert.IsTrue(Call("asset", "guid", guid)["keys"].Any(key => key.Value<string>("value") == "owned/changed"));
            }
            finally
            {
                EditorBuildSettings.RemoveConfigObject(currentKey);
                EditorBuildSettings.RemoveConfigObject(legacyKey);
                if (oldCurrent != null)
                    EditorBuildSettings.AddConfigObject(currentKey, oldCurrent, true);
                if (oldLegacy != null)
                    EditorBuildSettings.AddConfigObject(legacyKey, oldLegacy, true);
            }
        }
    }
}
