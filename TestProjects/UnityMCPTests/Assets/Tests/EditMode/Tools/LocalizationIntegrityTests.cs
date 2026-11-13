using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class LocalizationIntegrityTests
    {
        private string folder;
        private string folderGuid;
        private Object en;
        private Object fr;
        private Object shared;
        private Object enTable;
        private Object frTable;
        private Object assetTable;
        private Texture2D icon;
        private Object addressableSettings;
        private Object previousCurrentConfig;
        private Object previousLegacyConfig;
        private object previousSettingsCache;
        private FieldInfo settingsCacheField;
        private string currentConfigKey;
        private string legacyConfigKey;
        private string originalDefaultFolderGuid;
        private AddressablesImportScope importScope;

        private sealed class AddressablesImportScope : IDisposable
        {
            private readonly IDictionary callbacks;
            private readonly Dictionary<int, Delegate[]> saved = new Dictionary<int, Delegate[]>();
            private readonly IList registrations;
            private readonly List<(int Index, object Value)> savedRegistrations = new List<(int, object)>();
            private readonly Type settingsType;
            public Object OwnedSettings;

            public AddressablesImportScope(Type settingsType)
            {
                this.settingsType = settingsType;
                object handler = ImportHandler();
                Type handlerType = handler.GetType();
                Assert.IsFalse((bool)handlerType.GetField("m_IsInvoking", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(handler));
                callbacks = (IDictionary)handlerType.GetField("m_SortedInvocationList", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(handler);
                registrations = (IList)handlerType.GetField("m_RegisterQueue", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(handler);
                for (int index = 0; index < registrations.Count; index++)
                    if (IsSettingsImport(RegistrationCallback(registrations[index])))
                        savedRegistrations.Add((index, registrations[index]));
                foreach (var registration in savedRegistrations.AsEnumerable().Reverse())
                    registrations.RemoveAt(registration.Index);
                foreach (int order in callbacks.Keys.Cast<int>().ToArray())
                {
                    Delegate[] original = ((Delegate)callbacks[order]).GetInvocationList();
                    if (!original.Any(IsSettingsImport))
                        continue;
                    saved.Add(order, original);
                    SetCallbacks(order, original.Where(callback => !IsSettingsImport(callback)));
                }
            }

            private bool IsSettingsImport(Delegate callback) =>
                settingsType.IsInstanceOfType(callback.Target) && callback.Method.Name == "OnPostprocessAllAssets";

            private static Delegate RegistrationCallback(object registration) => (Delegate)registration.GetType().GetField("Item2").GetValue(registration);

            private void SetCallbacks(int order, IEnumerable<Delegate> values)
            {
                Delegate combined = Delegate.Combine(values.ToArray());
                if (combined == null)
                    callbacks.Remove(order);
                else
                    callbacks[order] = combined;
            }

            public void Dispose()
            {
                // A delayed package initialization can register the temporary default.
                // Remove only that exact target, never unrelated import observers.
                foreach (int order in callbacks.Keys.Cast<int>().ToArray())
                    SetCallbacks(
                        order,
                        ((Delegate)callbacks[order])
                            .GetInvocationList()
                            .Where(callback => ReferenceEquals(OwnedSettings, null) || !ReferenceEquals(callback.Target, OwnedSettings))
                    );
                foreach (var bucket in saved)
                {
                    var current = callbacks.Contains(bucket.Key) ? ((Delegate)callbacks[bucket.Key]).GetInvocationList().ToList() : new List<Delegate>();
                    for (int index = 0; index < bucket.Value.Length; index++)
                    {
                        Delegate callback = bucket.Value[index];
                        if (!IsSettingsImport(callback))
                            continue;
                        Delegate next = bucket.Value.Skip(index + 1).FirstOrDefault(current.Contains);
                        int insertion = next == null ? current.Count : current.IndexOf(next);
                        current.Insert(insertion, callback);
                    }
                    SetCallbacks(bucket.Key, current);
                }
                for (int index = registrations.Count - 1; index >= 0; index--)
                    if (!ReferenceEquals(OwnedSettings, null) && ReferenceEquals(RegistrationCallback(registrations[index]).Target, OwnedSettings))
                        registrations.RemoveAt(index);
                foreach (var registration in savedRegistrations)
                    registrations.Insert(Math.Min(registration.Index, registrations.Count), registration.Value);
                saved.Clear();
                savedRegistrations.Clear();
            }
        }

        private static object ImportHandler() =>
            Type.GetType("UnityEditor.AddressableAssets.Settings.AddressablesAssetPostProcessor, Unity.Addressables.Editor", true)
                .GetProperty("OnPostProcess", BindingFlags.Static | BindingFlags.Public)
                .GetValue(null);

        private void ConfigureOwnedAddressables()
        {
            Type defaults = Type.GetType("UnityEditor.AddressableAssets.AddressableAssetSettingsDefaultObject, Unity.Addressables.Editor", true);
            Type settingsType = Type.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings, Unity.Addressables.Editor", true);
            currentConfigKey = (string)defaults.GetField("kDefaultConfigObjectName").GetValue(null);
            legacyConfigKey = (string)defaults.GetField("kDefaultConfigAssetName").GetValue(null);
            FieldInfo cache = defaults.GetField("s_DefaultSettingsObject", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(cache, "The fixture must preserve the package's cached default settings.");
            EditorBuildSettings.TryGetConfigObject(currentConfigKey, out previousCurrentConfig);
            EditorBuildSettings.TryGetConfigObject(legacyConfigKey, out previousLegacyConfig);
            previousSettingsCache = cache.GetValue(null);
            settingsCacheField = cache;
            originalDefaultFolderGuid = AssetDatabase.AssetPathToGUID("Assets/AddressableAssetsData");
            importScope = new AddressablesImportScope(settingsType);

            string settingsFolder = folder + "/Addressables";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(folder, "Addressables"));
            addressableSettings = (Object)
                settingsType
                    .GetMethod("Create", new[] { typeof(string), typeof(string), typeof(bool), typeof(bool) })
                    .Invoke(null, new object[] { settingsFolder, "Settings", false, false });
            string settingsPath = settingsFolder + "/Settings.asset";
            AssetDatabase.CreateAsset(addressableSettings, settingsPath);
            AssetDatabase.SaveAssetIfDirty(addressableSettings);
            UnityEngine.Resources.UnloadAsset(addressableSettings);
            addressableSettings = AssetDatabase.LoadAssetAtPath(settingsPath, settingsType);
            Assert.IsNotNull(addressableSettings);
            importScope.OwnedSettings = addressableSettings;

            // Installing a persisted current-format config avoids the public setter's
            // global-folder creation and the legacy getter's automatic migration.
            var config = ScriptableObject.CreateInstance(defaults);
            using (var serialized = new SerializedObject(config))
            {
                SerializedProperty guid = serialized.FindProperty("m_AddressableAssetSettingsGuid");
                Assert.IsNotNull(guid);
                guid.stringValue = AssetDatabase.AssetPathToGUID(settingsPath);
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            AssetDatabase.CreateAsset(config, settingsFolder + "/DefaultObject.asset");
            AssetDatabase.SaveAssetIfDirty(config);
            EditorBuildSettings.RemoveConfigObject(currentConfigKey);
            EditorBuildSettings.RemoveConfigObject(legacyConfigKey);
            EditorBuildSettings.AddConfigObject(currentConfigKey, config, true);
            cache.SetValue(null, addressableSettings);
            Assert.AreSame(addressableSettings, defaults.GetMethod("GetSettings").Invoke(null, new object[] { false }));
        }

        private void RestoreAddressables()
        {
            if (settingsCacheField == null)
                return;
            try
            {
                EditorBuildSettings.RemoveConfigObject(currentConfigKey);
                EditorBuildSettings.RemoveConfigObject(legacyConfigKey);
                if (previousCurrentConfig != null)
                    EditorBuildSettings.AddConfigObject(currentConfigKey, previousCurrentConfig, true);
                if (previousLegacyConfig != null)
                    EditorBuildSettings.AddConfigObject(legacyConfigKey, previousLegacyConfig, true);
            }
            finally
            {
                settingsCacheField.SetValue(null, previousSettingsCache);
                settingsCacheField = null;
            }
        }

        private static Type Kind(string name) =>
            AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("UnityEngine.Localization." + name)).FirstOrDefault(t => t != null);

        private static object Get(object target, string name) => target.GetType().GetProperty(name).GetValue(target);

        private static void Set(object target, string name, object value) => target.GetType().GetProperty(name).SetValue(target, value);

        private static object Call(object target, string name, Type[] signature, params object[] args) =>
            target.GetType().GetMethod(name, signature).Invoke(target, args);

        private string PathOf(Object obj) => AssetDatabase.GetAssetPath(obj);

        private Object Create(string name, string path)
        {
            var asset = ScriptableObject.CreateInstance(Kind(name));
            AssetDatabase.CreateAsset(asset, folder + "/" + path + ".asset");
            return asset;
        }

        private object Add(Object table, long id, string value) => Call(table, "AddEntry", new[] { typeof(long), typeof(string) }, id, value);

        private object Entry(Object table, long id) => Call(table, "GetEntry", new[] { typeof(long) }, id);

        private JObject Send(string action, JObject request = null)
        {
            request = request ?? new JObject();
            request["action"] = action;
            Type tool = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(a => a.GetType("MCPForUnity.Editor.Tools.Localization.ManageLocalization"))
                .FirstOrDefault(t => t != null);
            if (tool == null)
                return new JObject { ["success"] = false, ["message"] = "Localization tool absent from public baseline." };
            return JObject.FromObject(tool.GetMethod("HandleCommand").Invoke(null, new object[] { request }));
        }

        private JObject Inspect(params Object[] tables)
        {
            JObject result = Send("inspect", new JObject { ["table_paths"] = new JArray(tables.Select(PathOf)), ["argument_names"] = new JArray("player") });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            return (JObject)result["data"];
        }

        private string Revision(Object table) => Inspect(table)["tables"][0].Value<string>("revision");

        private JObject SendBatch(string action, JObject request)
        {
            CommandRegistry.Initialize();
            using var consent = new MCPForUnityTests.Editor.Services.ScopedConsentTestState("batch_execute", "manage_localization");
            using var availability = new MCPForUnityTests.Editor.Services.ScopedRegistryAvailabilityTestState(new[] { "manage_localization" });
            request = (JObject)request.DeepClone();
            request["action"] = action;
            var original = request.DeepClone();
            var envelope = JObject.FromObject(
                BatchExecute
                    .HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "manage_localization", ["params"] = request }) })
                    .GetAwaiter()
                    .GetResult()
            );
            Assert.IsTrue(JToken.DeepEquals(original, request));
            var result = envelope.SelectToken("data.results[0].result") as JObject;
            Assert.IsNotNull(result, envelope.ToString());
            return result;
        }

        [Test]
        public void BatchReadAndRevisionMutation_PreserveExactSnakeCaseFields()
        {
            JObject read = SendBatch("inspect", new JObject { ["table_paths"] = new JArray(PathOf(frTable)), ["argument_names"] = new JArray("player") });
            Assert.IsTrue(read.Value<bool>("success"), read.ToString());
            string revision = (string)read["data"]["tables"][0]["revision"];
            string guid = AssetDatabase.AssetPathToGUID(PathOf(frTable));
            JObject result = SendBatch(
                "set_string_entry",
                new JObject
                {
                    ["table_path"] = PathOf(frTable),
                    ["key_id"] = "102",
                    ["value"] = "Conserve",
                    ["is_smart"] = false,
                    ["expected_revision"] = revision,
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            string path = PathOf(frTable);
            Assert.IsTrue((bool)result["data"]["verification"]["complete"], result.ToString());
            Assert.AreEqual("102", (string)result["data"]["verification"]["observed_values"]["key_id"]);
            Assert.IsTrue((bool)result["data"]["verification"]["observed_values"]["matches_requested"]);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            frTable = AssetDatabase.LoadMainAssetAtPath(path);
            Assert.AreEqual("Conserve", Get(Entry(frTable, 102), "Value"));
            Assert.AreEqual("Bonjour", Get(Entry(frTable, 101), "Value"));
            Assert.IsFalse((bool)Get(Entry(frTable, 102), "IsSmart"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreNotEqual(revision, Revision(frTable));
        }

        [TestCase("expected_revision", "null")]
        [TestCase("expected_revision", "42")]
        [TestCase("key_id", "102")]
        [TestCase("is_smart", "\"false\"")]
        [TestCase("table_path", "[]")]
        [TestCase("expectedRevision", "\"alias\"")]
        public void BatchMalformedMutation_RefusesBeforeNativeOrSourceChanges(string field, string value)
        {
            string revision = Revision(frTable);
            string path = PathOf(frTable);
            byte[] before = System.IO.File.ReadAllBytes(path);
            var request = new JObject
            {
                ["table_path"] = path,
                ["key_id"] = "101",
                ["value"] = "Overwrite",
                ["is_smart"] = false,
                ["expected_revision"] = revision,
            };
            request[field] = JToken.Parse(value);
            JObject result = SendBatch("set_string_entry", request);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("Bonjour", Get(Entry(frTable, 101), "Value"));
            Assert.IsFalse(EditorUtility.IsDirty(frTable));
            CollectionAssert.AreEqual(before, System.IO.File.ReadAllBytes(path));
            Assert.AreEqual(revision, Revision(frTable));
        }

        [Test]
        public void LongLocalizedTextRoundTripsWithoutTruncation()
        {
            string value = new string('x', 16000);
            string revision = Revision(frTable);
            JObject result = Send(
                "set_string_entry",
                new JObject
                {
                    ["table_path"] = PathOf(frTable),
                    ["key_id"] = "101",
                    ["value"] = value,
                    ["expected_revision"] = revision,
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(value, Get(Entry(frTable, 101), "Value"));
            Assert.AreNotEqual(revision, Revision(frTable));
        }

        [Test]
        public void LargeLocalizationTableIncludesKeysBeyondTheOldLimit()
        {
            for (int index = 0; index < 5000; index++)
            {
                long id = 1000 + index;
                Call(shared, "AddKey", new[] { typeof(string), typeof(long) }, "Generated" + index, id);
                Add(enTable, id, "Translation " + index);
            }
            EditorUtility.SetDirty(shared);
            EditorUtility.SetDirty(enTable);
            AssetDatabase.SaveAssets();
            using (var stream = new System.IO.FileStream(PathOf(enTable), System.IO.FileMode.Append, System.IO.FileAccess.Write))
            {
                byte[] padding = System.Text.Encoding.ASCII.GetBytes("#" + new string('x', 8190) + "\n");
                for (int index = 0; index < 256; index++)
                    stream.Write(padding, 0, padding.Length);
            }
            JObject data = Inspect(enTable);
            Assert.IsTrue(data.ToString().Contains("Generated4999"));
            string first = (string)data["tables"][0]["revision"];
            Assert.AreEqual(first, Revision(enTable), "Unchanged large table revisions must remain stable.");
            Set(Entry(enTable, 5999L), "Value", "Changed final translation");
            Assert.AreNotEqual(first, Revision(enTable), "Late-entry edits must invalidate the complete revision.");
        }

        private JObject Page(Object table, int limit, int offset = 0, string revision = null, JArray arguments = null)
        {
            var request = new JObject
            {
                ["table_paths"] = new JArray(PathOf(table)),
                ["offset"] = offset,
                ["limit"] = limit,
            };
            if (revision != null)
                request["expected_revision"] = revision;
            if (arguments != null)
                request["argument_names"] = arguments;
            return Send("inspect", request);
        }

        [Test]
        public void PagedInspectionReturnsAll50001MissingKeysAndFindingsExactlyOnce()
        {
            Call(frTable, "RemoveEntry", new[] { typeof(long) }, 101L);
            var expected = new List<string> { "101", "102", "103" };
            var addKey = shared.GetType().GetMethod("AddKey", new[] { typeof(string), typeof(long) });
            for (long id = 1000; expected.Count < 50001; id++)
            {
                string text = id.ToString(System.Globalization.CultureInfo.InvariantCulture);
                addKey.Invoke(shared, new object[] { "key" + text, id });
                expected.Add(text);
            }
            EditorUtility.SetDirty(shared);
            AssetDatabase.SaveAssetIfDirty(shared);
            var keys = new List<string>();
            var findings = new List<string>();
            int? offset = 0;
            string revision = null;
            do
            {
                JObject page = Page(frTable, 4096, offset.Value, revision);
                Assert.IsTrue(page.Value<bool>("success"), page.Value<string>("error"));
                var data = (JObject)page["data"];
                revision ??= data.Value<string>("revision");
                Assert.AreEqual(revision, data.Value<string>("revision"));
                Assert.AreEqual(expected.Count, data.Value<int>("total"));
                var rows = (JArray)data["tables"][0]["keys"];
                Assert.That(rows.Count, Is.InRange(1, 4096));
                keys.AddRange(rows.Select(r => r.Value<string>("key_id")));
                foreach (JToken finding in data["findings"])
                {
                    Assert.AreEqual("missing_translation", finding.Value<string>("code"));
                    findings.Add(finding.Value<string>("key_id"));
                }
                offset = data.Value<int?>("next_offset");
                if (offset.HasValue)
                    Assert.AreEqual(keys.Count, offset.Value);
            } while (offset.HasValue);
            CollectionAssert.AreEqual(expected, keys);
            CollectionAssert.AreEqual(expected, findings);
        }

        [Test]
        public void DuplicateAndOrphanFindingsArePagedOnceAcrossKeyBoundaries()
        {
            Call(shared, "AddKey", new[] { typeof(string), typeof(long) }, "Greeting", 104L);
            IList entries = (IList)Get(shared, "Entries");
            entries.Add(entries[0]); // Deliberately malformed duplicate ID spanning pages.
            Add(frTable, 900, "orphan");
            Add(frTable, 700, "orphan");
            JObject full = Inspect(frTable);
            var keys = new JArray();
            var findings = new List<string>();
            int? offset = 0;
            string revision = null;
            do
            {
                JObject page = Page(frTable, 1, offset.Value, revision, new JArray("player"));
                Assert.IsTrue(page.Value<bool>("success"), page.ToString());
                var data = (JObject)page["data"];
                revision ??= data.Value<string>("revision");
                foreach (var key in data["tables"][0]["keys"])
                    keys.Add(key);
                findings.AddRange(data["findings"].Select(f => f.Value<string>("code") + "/" + f.Value<string>("key_id")));
                int? next = data.Value<int?>("next_offset");
                if (next.HasValue)
                    Assert.AreEqual(offset.Value + 1, next.Value);
                offset = next;
            } while (offset.HasValue);
            Assert.IsTrue(JToken.DeepEquals(full["tables"][0]["keys"], keys));
            CollectionAssert.AreEquivalent(full["findings"].Select(f => f.Value<string>("code") + "/" + f.Value<string>("key_id")), findings);
            CollectionAssert.AreEqual(new[] { "orphan_entry/700", "orphan_entry/900" }, findings.Where(f => f.StartsWith("orphan_entry/")));
        }

        [TestCase("table")]
        [TestCase("shared")]
        [TestCase("source")]
        [TestCase("arguments")]
        [TestCase("asset_target")]
        public void ContinuationRejectsChangedInspectionInputs(string change)
        {
            Object table = change == "asset_target" ? assetTable : enTable;
            var arguments = new JArray("player");
            JObject first = Page(table, 1, arguments: arguments);
            Assert.IsTrue(first.Value<bool>("success"), first.ToString());
            Assert.AreEqual(Revision(table), first["data"]["tables"][0].Value<string>("revision"));
            string revision = first["data"].Value<string>("revision");
            switch (change)
            {
                case "table":
                    Set(Entry(enTable, 103), "Value", "Unmarked edit outside page");
                    break;
                case "shared":
                    Call(shared, "AddKey", new[] { typeof(string), typeof(long) }, "Later", 999L);
                    break;
                case "source":
                    System.IO.File.AppendAllText(PathOf(table), "\n# external edit\n");
                    break;
                case "arguments":
                    arguments.Add("missing");
                    break;
                case "asset_target":
                    Assert.IsEmpty(AssetDatabase.MoveAsset(PathOf(icon), folder + "/MovedIcon.asset"));
                    break;
            }
            JObject later = Page(table, 1, 1, revision, arguments);
            Assert.IsFalse(later.Value<bool>("success"), later.ToString());
            StringAssert.Contains("stale_revision", later.Value<string>("error"));
        }

        [Test]
        public void EscapedAndMultibyteValuesFitInspectionPageBudgetWithoutTruncation()
        {
            string value = string.Concat(Enumerable.Repeat("界\u0001", 32768));
            for (long id = 201; id <= 208; id++)
            {
                Call(shared, "AddKey", new[] { typeof(string), typeof(long) }, "large" + id, id);
                Add(frTable, id, value);
            }
            var ids = new List<long>();
            string revision = null;
            int? offset = 0;
            do
            {
                JObject page = Page(frTable, 4096, offset.Value, revision);
                Assert.IsTrue(page.Value<bool>("success"), page.Value<string>("error"));
                Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(page.ToString(Newtonsoft.Json.Formatting.None)), 1024 * 1024);
                foreach (var key in page["data"]["tables"][0]["keys"])
                {
                    long id = key.Value<long>("key_id");
                    if (id >= 201)
                    {
                        Assert.AreEqual(value, key.Value<string>("value"));
                        ids.Add(id);
                    }
                }
                int? next = page["data"].Value<int?>("next_offset");
                if (revision == null)
                    Assert.IsTrue(next.HasValue, "Encoded byte budget must cut the first page before its item limit.");
                if (next.HasValue)
                    Assert.Greater(next.Value, offset.Value);
                offset = next;
                revision = page["data"].Value<string>("revision");
            } while (offset.HasValue);
            CollectionAssert.AreEqual(Enumerable.Range(201, 8).Select(i => (long)i), ids);
        }

        [Test]
        public void PagedInspectionRejectsUnboundAndInvalidRanges()
        {
            foreach (
                JObject invalid in new[]
                {
                    Page(frTable, 0),
                    Page(frTable, 4097),
                    Page(frTable, 1, -1),
                    Page(frTable, 1, 200001),
                    Page(frTable, 1, 1),
                    Page(frTable, 1, 1, "invalid"),
                    Send("inspect", new JObject { ["table_paths"] = new JArray(PathOf(enTable), PathOf(frTable)), ["limit"] = 1 }),
                }
            )
                Assert.IsFalse(invalid.Value<bool>("success"), invalid.ToString());
        }

        [SetUp]
        public void Setup()
        {
            if (Kind("Locale") == null)
                Assert.Ignore("Optional Localization package absent; positive fixture requires com.unity.localization 1.5.12.");
            Assert.AreEqual("1.5.12", UnityEditor.PackageManager.PackageInfo.FindForAssembly(Kind("Locale").Assembly).version);
            try
            {
                InitializeFixture();
            }
            catch
            {
                Cleanup();
                throw;
            }
        }

        private void InitializeFixture()
        {
            folder = "Assets/LocalizationIntegrity_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            Assert.IsNotEmpty(folderGuid);
            ConfigureOwnedAddressables();
            en = (Object)Kind("Locale").GetMethod("CreateLocale", new[] { typeof(string) }).Invoke(null, new object[] { "en" });
            fr = (Object)Kind("Locale").GetMethod("CreateLocale", new[] { typeof(string) }).Invoke(null, new object[] { "fr" });
            AssetDatabase.CreateAsset(en, folder + "/en.asset");
            AssetDatabase.CreateAsset(fr, folder + "/fr.asset");
            shared = Create("Tables.SharedTableData", "Shared");
            Set(shared, "TableCollectionName", "OwnedStrings");
            foreach (var pair in new[] { (101L, "Greeting"), (102L, "Missing"), (103L, "Smart") })
                Call(shared, "AddKey", new[] { typeof(string), typeof(long) }, pair.Item2, pair.Item1);
            enTable = Create("Tables.StringTable", "English");
            frTable = Create("Tables.StringTable", "French");
            Set(enTable, "SharedData", shared);
            Set(frTable, "SharedData", shared);
            Set(enTable, "LocaleIdentifier", Get(en, "Identifier"));
            Set(frTable, "LocaleIdentifier", Get(fr, "Identifier"));
            Add(enTable, 101, "Hello");
            Add(enTable, 102, "Retained");
            Set(Add(enTable, 103, "Hello {missing}"), "IsSmart", true);
            Add(frTable, 101, "Bonjour");
            Object assets = Create("Tables.SharedTableData", "AssetShared");
            Set(assets, "TableCollectionName", "OwnedAssets");
            Call(assets, "AddKey", new[] { typeof(string), typeof(long) }, "Broken", 201L);
            Call(assets, "AddKey", new[] { typeof(string), typeof(long) }, "Retained", 202L);
            assetTable = Create("Tables.AssetTable", "AssetEnglish");
            Set(assetTable, "SharedData", assets);
            Set(assetTable, "LocaleIdentifier", Get(en, "Identifier"));
            icon = new Texture2D(2, 2);
            AssetDatabase.CreateAsset(icon, folder + "/Icon.asset");
            Add(assetTable, 201, "11111111111111111111111111111111");
            Add(assetTable, 202, AssetDatabase.AssetPathToGUID(PathOf(icon)));
            foreach (Object obj in new[] { en, fr, shared, enTable, frTable, assets, assetTable, icon })
            {
                EditorUtility.SetDirty(obj);
                AssetDatabase.SaveAssetIfDirty(obj);
            }
            Assert.AreEqual("Bonjour", Get(Entry(frTable, 101), "Value"));
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(PathOf(icon)), Get(Entry(assetTable, 202), "Guid"));
        }

        [TearDown]
        public void Cleanup()
        {
            bool ownsFolder = folder != null && AssetDatabase.AssetPathToGUID(folder) == folderGuid;
            try
            {
                // Locale/table deletion callbacks must still see the owned settings,
                // especially when the saved user configuration is legacy or absent.
                if (ownsFolder)
                    foreach (string name in new[] { "en", "fr", "English", "French", "AssetEnglish", "Shared", "AssetShared", "Icon" })
                    {
                        string path = folder + "/" + name + ".asset";
                        if (!string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path)))
                            AssetDatabase.DeleteAsset(path);
                    }
            }
            finally
            {
                try
                {
                    RestoreAddressables();
                }
                finally
                {
                    try
                    {
                        if (ownsFolder)
                            AssetDatabase.DeleteAsset(folder);
                    }
                    finally
                    {
                        importScope?.Dispose();
                        importScope = null;
                    }
                }
            }
        }

        [Test]
        public void FixtureLocalesUseOwnedAddressablesWithoutCreatingDefaultFolder()
        {
            Assert.AreEqual(originalDefaultFolderGuid, AssetDatabase.AssetPathToGUID("Assets/AddressableAssetsData"));
            Assert.IsTrue(PathOf(addressableSettings).StartsWith(folder + "/", StringComparison.Ordinal));
            foreach (Object locale in new[] { en, fr })
            {
                object entry = Call(
                    addressableSettings,
                    "FindAssetEntry",
                    new[] { typeof(string), typeof(bool) },
                    AssetDatabase.AssetPathToGUID(PathOf(locale)),
                    false
                );
                Assert.IsNotNull(entry, "The imported locale must belong to the fixture's Addressables settings.");
                var group = (Object)Get(entry, "parentGroup");
                Assert.IsTrue(PathOf(group).StartsWith(folder + "/", StringComparison.Ordinal), PathOf(group));
            }
        }

        private static void BeforeSettingsImport(string[] imported, string[] deleted, string[] moved, string[] movedFrom) { }

        private static void AfterSettingsImport(string[] imported, string[] deleted, string[] moved, string[] movedFrom) { }

        [TestCase(false)]
        [TestCase(true)]
        public void ExistingSettingsAndImportRegistrationSurviveNestedFixture(bool registerSettings)
        {
            object handler = ImportHandler();
            MethodInfo register = handler.GetType().GetMethod("Register");
            MethodInfo unregister = handler.GetType().GetMethod("Unregister");
            Type callbackType = register.GetParameters()[0].ParameterType;
            var before = Delegate.CreateDelegate(
                callbackType,
                typeof(LocalizationIntegrityTests).GetMethod(nameof(BeforeSettingsImport), BindingFlags.Static | BindingFlags.NonPublic)
            );
            var after = Delegate.CreateDelegate(
                callbackType,
                typeof(LocalizationIntegrityTests).GetMethod(nameof(AfterSettingsImport), BindingFlags.Static | BindingFlags.NonPublic)
            );
            var settingsCallback = Delegate.CreateDelegate(
                callbackType,
                addressableSettings,
                addressableSettings.GetType().GetMethod("OnPostprocessAllAssets", BindingFlags.Instance | BindingFlags.NonPublic)
            );
            const int order = -137;
            var registrations = (IList)handler.GetType().GetField("m_RegisterQueue", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(handler);
            object queuedRegistration = null;
            var nested = new LocalizationIntegrityTests();
            try
            {
                register.Invoke(handler, new object[] { before, order });
                if (registerSettings)
                    register.Invoke(handler, new object[] { settingsCallback, order });
                register.Invoke(handler, new object[] { after, order });
                if (registerSettings)
                {
                    queuedRegistration = Activator.CreateInstance(registrations.GetType().GetGenericArguments()[0], order, settingsCallback);
                    registrations.Add(queuedRegistration);
                }
                AssetDatabase.SaveAssetIfDirty(addressableSettings);
                Object[] groups = ((IEnumerable)Get(addressableSettings, "groups")).Cast<Object>().ToArray();
                string[] entries = groups
                    .SelectMany(group => ((IEnumerable)Get(group, "entries")).Cast<object>())
                    .Select(entry => (string)Get(entry, "guid"))
                    .OrderBy(value => value)
                    .ToArray();
                int dirtyCount = EditorUtility.GetDirtyCount(addressableSettings);
                string serialized = EditorJsonUtility.ToJson(addressableSettings);
                byte[] bytes = System.IO.File.ReadAllBytes(PathOf(addressableSettings));
                var callbacks = (IDictionary)
                    handler.GetType().GetField("m_SortedInvocationList", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(handler);
                Delegate[] originalOrder = ((Delegate)callbacks[order]).GetInvocationList();
                object[] originalQueue = registrations.Cast<object>().ToArray();

                nested.Setup();
                nested.Cleanup();

                CollectionAssert.AreEqual(groups, ((IEnumerable)Get(addressableSettings, "groups")).Cast<Object>().ToArray());
                CollectionAssert.AreEqual(
                    entries,
                    groups
                        .SelectMany(group => ((IEnumerable)Get(group, "entries")).Cast<object>())
                        .Select(entry => (string)Get(entry, "guid"))
                        .OrderBy(value => value)
                        .ToArray()
                );
                Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(addressableSettings));
                Assert.AreEqual(serialized, EditorJsonUtility.ToJson(addressableSettings));
                CollectionAssert.AreEqual(bytes, System.IO.File.ReadAllBytes(PathOf(addressableSettings)));
                CollectionAssert.AreEqual(originalOrder, ((Delegate)callbacks[order]).GetInvocationList());
                CollectionAssert.AreEqual(originalQueue, registrations.Cast<object>().ToArray());
            }
            finally
            {
                try
                {
                    nested.Cleanup();
                }
                finally
                {
                    if (queuedRegistration != null)
                        registrations.Remove(queuedRegistration);
                    unregister.Invoke(handler, new object[] { before });
                    unregister.Invoke(handler, new object[] { settingsCallback });
                    unregister.Invoke(handler, new object[] { after });
                }
            }
        }

        [Test]
        public void CapabilitiesExposeInstalledVersion()
        {
            JObject result = Send("capabilities");
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("1.5.12", result["data"].Value<string>("package_version"));
        }

        [Test]
        public void TwoLocalesReportMissingAndSmartArgumentsWithoutChangingTranslations()
        {
            JObject data = Inspect(enTable, frTable);
            Assert.IsTrue(data["findings"].Any(f => f.Value<string>("code") == "missing_translation"));
            Assert.IsTrue(data["findings"].Any(f => f.Value<string>("code") == "unresolved_argument"));
            Assert.AreEqual("Bonjour", Get(Entry(frTable, 101), "Value"));
            Assert.IsTrue((bool)Get(Entry(enTable, 103), "IsSmart"));
        }

        [Test]
        public void NullStringEntryReportsMissingTranslationWithoutLosingOtherLocale()
        {
            object entry = Add(enTable, 101, null);
            Assert.IsNull(Get(entry, "Value"), "The package accepts an actual null localized value.");
            JObject data = Inspect(enTable, frTable);
            Assert.IsTrue(
                data["findings"]
                    .Any(f =>
                        f.Value<string>("code") == "missing_translation" && f.Value<string>("key_id") == "101" && f.Value<string>("path") == PathOf(enTable)
                    )
            );
            Assert.AreEqual(JTokenType.Null, data["tables"][0]["keys"].Single(k => k.Value<string>("key_id") == "101")["value"].Type);
            Assert.IsNull(Get(Entry(enTable, 101), "Value"));
            Assert.AreEqual("Bonjour", Get(Entry(frTable, 101), "Value"));
        }

        [Test]
        public void DuplicateSharedKeyNamesAreDiagnosed()
        {
            Call(shared, "AddKey", new[] { typeof(string), typeof(long) }, "Greeting", 104L);
            Assert.IsTrue(Inspect(enTable)["findings"].Any(f => f.Value<string>("code") == "duplicate_key_name"));
        }

        [Test]
        public void MissingAssetReferenceIsDiagnosedAndValidReferencePreserved()
        {
            Assert.IsTrue(Inspect(assetTable)["findings"].Any(f => f.Value<string>("code") == "missing_asset_reference"));
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(PathOf(icon)), Get(Entry(assetTable, 202), "Guid"));
        }

        [Test]
        public void MissingTranslationCorrectionPersistsWithoutChangingGuidsSharedIdsOrOtherLocale()
        {
            string tableGuid = AssetDatabase.AssetPathToGUID(PathOf(frTable));
            string sharedGuid = AssetDatabase.AssetPathToGUID(PathOf(shared));
            JObject result = Send(
                "set_string_entry",
                new JObject
                {
                    ["table_path"] = PathOf(frTable),
                    ["key_id"] = "102",
                    ["value"] = "Conserve",
                    ["expected_revision"] = Revision(frTable),
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            string path = PathOf(frTable);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            frTable = AssetDatabase.LoadMainAssetAtPath(path);
            Assert.AreEqual("Conserve", Get(Entry(frTable, 102), "Value"));
            Assert.AreEqual("Bonjour", Get(Entry(frTable, 101), "Value"));
            Assert.AreEqual("Retained", Get(Entry(enTable, 102), "Value"));
            Assert.AreEqual(tableGuid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(sharedGuid, AssetDatabase.AssetPathToGUID(PathOf((Object)Get(frTable, "SharedData"))));
            Assert.AreEqual(102L, Get(Call(shared, "GetEntry", new[] { typeof(long) }, 102L), "Id"));
        }

        [Test]
        public void StaleRevisionRefusesBeforeMutation()
        {
            string revision = Revision(frTable);
            Set(Entry(frTable, 101), "Value", "External writer");
            JObject result = Send(
                "set_string_entry",
                new JObject
                {
                    ["table_path"] = PathOf(frTable),
                    ["key_id"] = "101",
                    ["value"] = "Overwrite",
                    ["expected_revision"] = revision,
                }
            );
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("stale_revision", result.ToString());
            Assert.AreEqual("External writer", Get(Entry(frTable, 101), "Value"));
        }

        [Test]
        public void SmartFlagIsPreservedWhenOmitted()
        {
            JObject result = Send(
                "set_string_entry",
                new JObject
                {
                    ["table_path"] = PathOf(enTable),
                    ["key_id"] = "103",
                    ["value"] = "Hello {player}",
                    ["expected_revision"] = Revision(enTable),
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue((bool)Get(Entry(enTable, 103), "IsSmart"));
        }

        [Test]
        public void FallbackCorrectionPersistsAndCycleIsRefused()
        {
            JObject locales = Send("list_locales", new JObject { ["paths"] = new JArray(PathOf(en), PathOf(fr)) });
            Assert.IsTrue(locales.Value<bool>("success"), locales.ToString());
            string revision = locales["data"]["locales"].Single(l => l.Value<string>("path") == PathOf(fr)).Value<string>("revision");
            JObject result = Send(
                "set_fallbacks",
                new JObject
                {
                    ["locale_path"] = PathOf(fr),
                    ["fallback_locale_paths"] = new JArray(PathOf(en)),
                    ["expected_revision"] = revision,
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            AssetDatabase.ImportAsset(PathOf(fr), ImportAssetOptions.ForceUpdate);
            Assert.IsTrue((bool)result["data"]["verification"]["complete"], result.ToString());
            Assert.AreEqual(PathOf(en), (string)result["data"]["verification"]["observed_values"]["fallback_locale_paths"][0]);
            var entries = (IEnumerable)Get(Get(fr, "Metadata"), "MetadataEntries");
            Assert.AreSame(en, Get(entries.Cast<object>().Single(), "Locale"));
            locales = Send("list_locales", new JObject { ["paths"] = new JArray(PathOf(en)) });
            revision = locales["data"]["locales"][0].Value<string>("revision");
            result = Send(
                "set_fallbacks",
                new JObject
                {
                    ["locale_path"] = PathOf(en),
                    ["fallback_locale_paths"] = new JArray(PathOf(fr)),
                    ["expected_revision"] = revision,
                }
            );
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("fallback_cycle", result.ToString());
        }

        [Test]
        public void AssetCorrectionRefusesImplicitAddressablesChanges()
        {
            string original = (string)Get(Entry(assetTable, 202), "Guid");
            JObject result = Send(
                "set_asset_entry",
                new JObject
                {
                    ["table_path"] = PathOf(assetTable),
                    ["key_id"] = "202",
                    ["asset_path"] = PathOf(icon),
                    ["expected_revision"] = Revision(assetTable),
                }
            );
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("asset_authoring_requires_addressables", result.ToString());
            Assert.AreEqual(original, Get(Entry(assetTable, 202), "Guid"));
        }
    }
}
