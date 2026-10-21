using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
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

        [SetUp]
        public void Setup()
        {
            if (Kind("Locale") == null)
                Assert.Ignore("Optional Localization package absent; positive fixture requires com.unity.localization 1.5.12.");
            Assert.AreEqual("1.5.12", UnityEditor.PackageManager.PackageInfo.FindForAssembly(Kind("Locale").Assembly).version);
            folder = "Assets/LocalizationIntegrity_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            Assert.IsNotEmpty(folderGuid);
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
            if (folder != null && AssetDatabase.AssetPathToGUID(folder) == folderGuid)
                AssetDatabase.DeleteAsset(folder);
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
