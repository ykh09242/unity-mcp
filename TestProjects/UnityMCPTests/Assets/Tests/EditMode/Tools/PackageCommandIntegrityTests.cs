using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.PackageManager.Requests;
using UnityEditor.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    // Author/compile only in this audit. No Client call, Resolve, project manifest,
    // native Request allocation, scene mutation or SessionState write is used.
    [Parallelizable(ParallelScope.None)]
    public class PackageCommandIntegrityTests
    {
        private string root;
        private bool ownsRoot,
            capturedCache;
        private readonly List<string> ownedFiles = new List<string>();
        private readonly List<string> ownedDirectories = new List<string>();
        private Dictionary<string, object> cache,
            originalCache;
        private Queue<string> order;
        private string[] originalOrder;

        [SetUp]
        public void SetUp()
        {
            ownsRoot = capturedCache = false;
            root = null;
            ownedFiles.Clear();
            ownedDirectories.Clear();
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("Do not disturb an unowned prefab stage.");
            cache = Field<Dictionary<string, object>>("CompletedQueryResults");
            order = Field<Queue<string>>("CompletedQueryOrder");
            originalCache = new Dictionary<string, object>(cache);
            originalOrder = order.ToArray();
            capturedCache = true;
            root = Path.Combine(Path.GetTempPath(), "McpPackageCommandIntegrity_" + Guid.NewGuid().ToString("N"));
            Assert.IsFalse(Directory.Exists(root));
            Assert.IsFalse(File.Exists(root));
            Directory.CreateDirectory(root);
            ownsRoot = true;
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (!ownsRoot)
                    return;
                foreach (string path in ownedFiles)
                {
                    GuardOwnedPath(path);
                    if (File.Exists(path))
                        File.Delete(path);
                }
                foreach (string path in ownedDirectories.OrderByDescending(p => p.Length))
                {
                    GuardOwnedPath(path);
                    if (Directory.Exists(path) && !Directory.EnumerateFileSystemEntries(path).Any())
                        Directory.Delete(path);
                }
                // Unexpected files (including recovery backups) are retained for diagnosis.
                if (Directory.Exists(root) && !Directory.EnumerateFileSystemEntries(root).Any())
                    Directory.Delete(root);
            }
            finally
            {
                if (capturedCache)
                {
                    cache.Clear();
                    foreach (var entry in originalCache)
                        cache.Add(entry.Key, entry.Value);
                    order.Clear();
                    foreach (string id in originalOrder)
                        order.Enqueue(id);
                }
            }
        }

        [TestCase("false")]
        [TestCase("true")]
        [TestCase("0")]
        [TestCase("1.5")]
        [TestCase("{}")]
        [TestCase("[]")]
        [TestCase("[1]")]
        public void AddRegistryRejectsRawNameBeforeUrlValidation(string json)
        {
            // Invalid URL guarantees the baseline also stops before real project IO or Client.Resolve.
            JObject input = CommandParams(
                new JObject
                {
                    ["action"] = "add_registry",
                    ["name"] = JToken.Parse(json),
                    ["url"] = "invalid",
                }
            );
            JObject result = JObject.FromObject(ManagePackages.HandleCommand(input));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual("'name' must be a string.", result.Value<string>("error"));
        }

        [TestCase("name", "false")]
        [TestCase("name", "0")]
        [TestCase("name", "{}")]
        [TestCase("name", "[]")]
        [TestCase("url", "false")]
        [TestCase("url", "0")]
        [TestCase("url", "{}")]
        [TestCase("url", "[]")]
        public void RegistryRemovalValidatorRejectsCoercedSelectorsWithoutProjectIO(string key, string json)
        {
            var parameters = new ToolParams(CommandParams(new JObject { [key] = JToken.Parse(json) }));
            var exception = Assert.Throws<TargetInvocationException>(() => Method("CheckRegistryStringToken").Invoke(null, new object[] { parameters, key }));
            Assert.IsInstanceOf<ArgumentException>(exception.InnerException);
            Assert.AreEqual($"'{key}' must be a string.", exception.InnerException.Message);
        }

        [TestCase("null")]
        [TestCase("\"\"")]
        [TestCase("\" \"")]
        [TestCase("\"False\"")]
        [TestCase("\"0\"")]
        [TestCase("\"{}\"")]
        public void RegistryStringValidatorPreservesNullAndStringSelectors(string json)
        {
            foreach (string key in new[] { "name", "url" })
            {
                var parameters = new ToolParams(new JObject { [key] = JToken.Parse(json) });
                string before = parameters.Get(key);
                Assert.DoesNotThrow(() => Method("CheckRegistryStringToken").Invoke(null, new object[] { parameters, key }));
                Assert.AreEqual(before, parameters.Get(key));
                Assert.DoesNotThrow(() => Method("CheckRegistryStringToken").Invoke(null, new object[] { new ToolParams(new JObject()), key }));
            }
        }

        [TestCase("ko-KR", "2026-10-09T12:34:56Z", JTokenType.Date)]
        [TestCase("en-US", "2026-10-09T12:34:56Z", JTokenType.Date)]
        [TestCase("de-DE", "2026-10-09T12:34:56+09:00", JTokenType.Date)]
        [TestCase("fr-FR", "2026-10-09T12:34:56+09:00", JTokenType.Date)]
        [TestCase("en-US", "2026-10-09", JTokenType.String)]
        [TestCase("en-US", "2026-10-09T12:34:56Z-label", JTokenType.String)]
        public void ActualCommandDateStringsRetainRegistrySelectorConversion(string culture, string wireValue, JTokenType expectedType)
        {
            CultureInfo original = CultureInfo.CurrentCulture,
                originalUi = CultureInfo.CurrentUICulture;
            try
            {
                CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo(culture);
                CultureInfo.CurrentUICulture = CultureInfo.GetCultureInfo(culture);
                JObject input = CommandParams(new JObject { ["name"] = wireValue, ["url"] = wireValue });
                Assert.AreEqual(expectedType, input["name"].Type);
                foreach (string key in new[] { "name", "url" })
                {
                    var parameters = new ToolParams(input);
                    string legacy = parameters.Get(key);
                    Assert.DoesNotThrow(() => Method("CheckRegistryStringToken").Invoke(null, new object[] { parameters, key }));
                    Assert.AreEqual(legacy, parameters.Get(key));
                }
            }
            finally
            {
                CultureInfo.CurrentCulture = original;
                CultureInfo.CurrentUICulture = originalUi;
            }
        }

        [TestCase("{\"action\":\"add_registry\",\"name\":null,\"url\":false}", "'name' parameter is required for add_registry.")]
        [TestCase(
            "{\"action\":\"add_registry\",\"name\":\"Fixture\",\"url\":false}",
            "'url' must be an absolute HTTP or HTTPS registry URL with a valid host and port."
        )]
        [TestCase("{\"action\":\"remove_registry\",\"name\":null,\"url\":null}", "Either 'name' or 'url' parameter is required for remove_registry.")]
        public void RegistryEmptyAndInvalidUrlErrorsStillStopBeforeProjectIO(string json, string expected)
        {
            JObject result = JObject.FromObject(ManagePackages.HandleCommand(JObject.Parse(json)));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(expected, result.Value<string>("error"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FixedManifestPathStaysInOwnedProjectWithoutCreatingMissingFolders(bool existingManifest)
        {
            string assets = Path.Combine(root, "Assets");
            Directory.CreateDirectory(assets);
            ownedDirectories.Add(assets);
            string packages = Path.Combine(root, "Packages");
            string path = Path.Combine(packages, "manifest.json");
            if (existingManifest)
            {
                Directory.CreateDirectory(packages);
                ownedDirectories.Add(packages);
                WriteOwned("Packages/manifest.json", "{\"untouched\":false}");
            }
            Assert.AreEqual(path, Method("GetContainedManifestPath").Invoke(null, new object[] { assets }));
            Assert.AreEqual(existingManifest, Directory.Exists(packages));
            Assert.AreEqual(existingManifest, File.Exists(path));
            if (existingManifest)
                Assert.AreEqual("{\"untouched\":false}", File.ReadAllText(path));
        }

        private static JObject CommandParams(JObject input) =>
            JsonConvert.DeserializeObject<Command>(new JObject { ["type"] = "manage_packages", ["params"] = input }.ToString(Formatting.None)).@params;

        [TestCase("\"broken\"")]
        [TestCase("{}")]
        [TestCase("false")]
        [TestCase("42")]
        public void InvalidExistingRegistryContainerIsNotChanged(string value)
        {
            string text = "{\"scopedRegistries\":" + value + ",\"untouched\":false}";
            string path = WriteOwned("input.json", text);
            var exception = Assert.Throws<TargetInvocationException>(() => Read(path));
            Assert.IsInstanceOf<FormatException>(exception.InnerException);
            Assert.AreEqual(text, File.ReadAllText(path));
        }

        [TestCase("not a URL", false)]
        [TestCase("registry.example.test", false)]
        [TestCase("/registry", false)]
        [TestCase("ftp://example.test", false)]
        [TestCase("https://", false)]
        [TestCase("http://localhost:invalid", false)]
        [TestCase("http://localhost:65536", false)]
        [TestCase("https://registry.example.test", true)]
        [TestCase("http://localhost:4873", true)]
        [TestCase("http://registry/packages?channel=stable", true)]
        [TestCase("http://127.0.0.1:4873", true)]
        [TestCase("http://[::1]:4873", true)]
        [TestCase("HTTPS://registry.example.test/npm/", true)]
        public void RegistryUrlValidationAllowsHttpRegistriesAndRejectsMalformedUrls(string url, bool expected)
        {
            Assert.AreEqual(expected, Method("IsValidRegistryUrl").Invoke(null, new object[] { url }));
        }

        [TestCase("[42]")]
        [TestCase("[\"com.fixture\",42]")]
        [TestCase("[true]")]
        [TestCase("[null]")]
        [TestCase("42")]
        [TestCase("false")]
        [TestCase("{}")]
        [TestCase("\"[42]\"")]
        [TestCase("[\"[42]\"]")]
        [TestCase("[[42]]")]
        public void RegistryScopesStrictBoundaryRejectsNonStringTokens(string json)
        {
            var parameters = new ToolParams(new JObject { ["scopes"] = JToken.Parse(json) });
            Assert.Throws<ArgumentException>(() => parameters.GetStringArray("scopes", strict: true));
        }

        [TestCase("\"com.fixture\"", "com.fixture")]
        [TestCase("[\"com.fixture\"]", "com.fixture")]
        [TestCase("\"[\\\"com.fixture\\\"]\"", "com.fixture")]
        [TestCase("[\"[\\\"com.fixture\\\"]\"]", "com.fixture")]
        [TestCase("[[\"com.fixture\"]]", "com.fixture")]
        [TestCase("\"42\"", "42")]
        public void RegistryScopesStrictBoundaryPreservesLegacyStrings(string json, string expected)
        {
            var parameters = new ToolParams(new JObject { ["scopes"] = JToken.Parse(json) });
            CollectionAssert.AreEqual(new[] { expected }, parameters.GetStringArray("scopes", strict: true));
        }

        [TestCase("null")]
        [TestCase("[]")]
        [TestCase("\"\"")]
        [TestCase("[\" \"]")]
        public void RegistryScopesStrictBoundaryRetainsEmptyDefaults(string json)
        {
            var parameters = new ToolParams(new JObject { ["scopes"] = JToken.Parse(json) });
            Assert.IsNull(parameters.GetStringArray("scopes", strict: true));
            Assert.IsNull(new ToolParams(new JObject()).GetStringArray("scopes", strict: true));
        }

        [TestCase("{\"keep\":1,\"keep\":2}")]
        [TestCase("{\"dependencies\":{\"com.fixture\":\"1\",\"com.fixture\":\"2\"}}")]
        public void DuplicatePropertiesAreNotSilentlyDiscarded(string text)
        {
            string path = WriteOwned("input.json", text);
            var exception = Assert.Throws<TargetInvocationException>(() => Read(path));
            Assert.IsInstanceOf<JsonReaderException>(exception.InnerException);
            Assert.AreEqual(text, File.ReadAllText(path));
        }

        [TestCase("{}")]
        [TestCase("{\"scopedRegistries\":null}")]
        [TestCase("{\"scopedRegistries\":[]}")]
        public void ExistingAbsentNullAndEmptyDefaultsRemainAccepted(string text)
        {
            string path = WriteOwned("input.json", text);
            Assert.IsTrue(JToken.DeepEquals(JObject.Parse(text), Read(path)));
            Assert.AreEqual(text, File.ReadAllText(path));
        }

        [Test]
        public void ValidManifestRetainsUnownedValues()
        {
            string text =
                "{\"zero\":0,\"enabled\":false,\"dependencies\":{\"com.fixture\":\"1\"},\"scopedRegistries\":[{\"name\":\"Fixture\",\"url\":\"https://example.test\",\"scopes\":[\"com.fixture\"],\"custom\":null}]}";
            string path = WriteOwned("input.json", text);
            Assert.IsTrue(JToken.DeepEquals(JObject.Parse(text), Read(path)));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TerminalQueryCacheRetainsTheSameResponseWithoutUPM(bool failure)
        {
            cache.Clear();
            order.Clear();
            string id = "mcp-test-query-" + Guid.NewGuid().ToString("N");
            object response = failure
                ? (object)new ErrorResponse("Owned query error")
                : new SuccessResponse("Owned query result", new { count = 0, packages = new object[0] });
            Cache(id, response);
            // Public status retry is exercised in the controlled full-source host.
            // A regressed public cache branch could initialize real SessionState recovery.
            Assert.AreSame(response, cache[id]);
            Assert.AreEqual(id, order.Peek());
        }

        [Test]
        public void TerminalCacheEvictsOnlyOldCompletedResponses()
        {
            cache.Clear();
            order.Clear();
            string first = null,
                last = null;
            for (int i = 0; i < 11; i++)
            {
                string id = "mcp-test-query-" + Guid.NewGuid().ToString("N");
                if (first == null)
                    first = id;
                last = id;
                Cache(id, new SuccessResponse("Owned query " + i));
            }
            Assert.AreEqual(10, cache.Count);
            Assert.AreEqual(10, order.Count);
            Assert.IsFalse(cache.ContainsKey(first));
            Assert.IsTrue(cache.ContainsKey(last));
            Assert.AreEqual(last, order.Last());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TerminalQueryCacheReleasesOnlyItsPendingRequest(bool search)
        {
            string id = "mcp-test-query-" + Guid.NewGuid().ToString("N");
            string active = "mcp-test-query-" + Guid.NewGuid().ToString("N");
            var lists = Field<Dictionary<string, ListRequest>>("PendingListRequests");
            var searches = Field<Dictionary<string, SearchRequest>>("PendingSearchRequests");
            // Null sentinels test bookkeeping without allocating native UPM requests.
            if (search)
            {
                searches.Add(id, null);
                searches.Add(active, null);
            }
            else
            {
                lists.Add(id, null);
                lists.Add(active, null);
            }
            try
            {
                Cache(id, new SuccessResponse("Owned terminal result"));
                Assert.IsFalse(lists.ContainsKey(id) || searches.ContainsKey(id));
                Assert.IsTrue(search ? searches.ContainsKey(active) : lists.ContainsKey(active));
            }
            finally
            {
                lists.Remove(id);
                lists.Remove(active);
                searches.Remove(id);
                searches.Remove(active);
            }
        }

        [Test]
        public void ExistingAtomicWriterPreservesUnownedSidecars()
        {
            string path = WriteOwned("manifest.txt", "old");
            string tmp = WriteOwned("manifest.txt.tmp", "unowned-temp");
            string backup = WriteOwned("manifest.txt.backup", "unowned-backup");
            McpConfigurationHelper.WriteAtomicFile(path, "new");
            Assert.AreEqual("new", File.ReadAllText(path));
            Assert.AreEqual("unowned-temp", File.ReadAllText(tmp));
            Assert.AreEqual("unowned-backup", File.ReadAllText(backup));
            CollectionAssert.AreEquivalent(new[] { path, tmp, backup }, Directory.GetFiles(root));
        }

        [Test]
        public void FailedAtomicReplacementPreservesOccupiedDirectoryAndSidecars()
        {
            string path = Path.Combine(root, "occupied.txt");
            Directory.CreateDirectory(path);
            ownedDirectories.Add(path);
            string marker = WriteOwned("occupied.txt/marker.txt", "owned-directory");
            string tmp = WriteOwned("occupied.txt.tmp", "unowned-temp");
            string backup = WriteOwned("occupied.txt.backup", "unowned-backup");
            Assert.Catch<Exception>(() => McpConfigurationHelper.WriteAtomicFile(path, "new"));
            Assert.IsTrue(Directory.Exists(path));
            Assert.AreEqual("owned-directory", File.ReadAllText(marker));
            Assert.AreEqual("unowned-temp", File.ReadAllText(tmp));
            Assert.AreEqual("unowned-backup", File.ReadAllText(backup));
        }

        private string WriteOwned(string relative, string text)
        {
            string path = Path.GetFullPath(Path.Combine(root, relative));
            GuardOwnedPath(path);
            Assert.IsFalse(File.Exists(path));
            File.WriteAllText(path, text);
            ownedFiles.Add(path);
            return path;
        }

        private void GuardOwnedPath(string path)
        {
            Assert.IsTrue(
                Path.GetFullPath(path).StartsWith(Path.GetFullPath(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase),
                "Path escaped this fixture's exact owned temporary root."
            );
        }

        private static JObject Read(string path) => (JObject)Method("ReadRegistryManifest").Invoke(null, new object[] { path });

        private static object Cache(string id, object response) => Method("CacheQueryResult").Invoke(null, new[] { id, response });

        private static MethodInfo Method(string name)
        {
            var method = typeof(ManagePackages).GetMethod(name, BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method, name);
            return method;
        }

        private static T Field<T>(string name)
        {
            var field = typeof(ManagePackages).GetField(name, BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(field, name);
            return (T)field.GetValue(null);
        }
    }
}
