using System;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    [TestFixture, Parallelizable(ParallelScope.None)]
    public sealed class ReferenceGraphCollectorTests
    {
        private string root;

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
