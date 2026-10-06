using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class EditorPrefsWindowSaveTests
    {
        private const BindingFlags InstanceFlags = BindingFlags.NonPublic | BindingFlags.Instance;
        private const BindingFlags StaticFlags = BindingFlags.NonPublic | BindingFlags.Static;
        private EditorPrefsWindow window;
        private readonly List<string> keys = new List<string>();
        private readonly List<string> errors = new List<string>();
        private object previousCache;

        [SetUp]
        public void SetUp()
        {
            keys.Clear();
            errors.Clear();
            var cacheField = typeof(EditorConfigurationCache).GetField("_instance", StaticFlags);
            previousCache = cacheField.GetValue(null);
            cacheField.SetValue(null, null);
            window = ScriptableObject.CreateInstance<EditorPrefsWindow>();
            var container = new VisualElement();
            SetField("prefsContainer", container);
            window.rootVisualElement.Add(container);
            SetField("resultCount", new Label());
            SetField("emptyState", new Label());
            var itemTemplate = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                AssetPathUtility.GetMcpPackageRootPath() + "/Editor/Windows/EditorPrefs/EditorPrefItem.uxml");
            Assert.IsNotNull(itemTemplate);
            SetField("itemTemplate", itemTemplate);
            typeof(EditorPrefsWindow).GetField("showSaveError", InstanceFlags)?.SetValue(window, (Action<string>)errors.Add);
        }

        [TearDown]
        public void TearDown()
        {
            foreach (string key in keys) EditorPrefs.DeleteKey(key);
            if (window != null) UnityEngine.Object.DestroyImmediate(window);
            typeof(EditorConfigurationCache).GetField("_instance", StaticFlags).SetValue(null, previousCache);
        }

        [TestCase("en-US", "00042")]
        [TestCase("en-US", "true")]
        [TestCase("de-DE", "1.234")]
        [TestCase("fr-FR", "1,234")]
        [TestCase("en-US", "")]
        public void UnknownString_PreservesRawValueAndType(string culture, string raw)
        {
            var previousCulture = CultureInfo.CurrentCulture;
            string key = NewKey();
            EditorPrefs.SetString(key, raw);
            try
            {
                CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo(culture);
                var item = (EditorPrefItem)Invoke("CreateEditorPrefItem", key);
                Assert.AreEqual(EditorPrefType.String, item.Type);
                Assert.AreEqual(raw, item.Value);
                Assert.AreEqual(raw, EditorPrefs.GetString(key));
            }
            finally
            {
                CultureInfo.CurrentCulture = previousCulture;
            }
        }

        [TestCase(EditorPrefType.Int)]
        [TestCase(EditorPrefType.Float)]
        [TestCase(EditorPrefType.Bool)]
        public void FailedSave_RetainsBothDraftsAndModels(EditorPrefType requestedType)
        {
            var first = AddItem("saved A", EditorPrefType.String);
            var second = AddItem("saved B", EditorPrefType.String);
            var firstRow = Rows[first.Key];
            var secondRow = Rows[second.Key];
            firstRow.Q<TextField>("value-field").value = "invalid input";
            firstRow.Q<DropdownField>("type-dropdown").index = (int)requestedType;
            secondRow.Q<TextField>("value-field").value = "other draft";
            secondRow.Q<DropdownField>("type-dropdown").index = (int)EditorPrefType.Float;

            Invoke("SavePref", first, "invalid input", requestedType);

            Assert.AreEqual(2, Rows.Count);
            Assert.AreSame(firstRow, Rows[first.Key]);
            Assert.AreSame(secondRow, Rows[second.Key]);
            Assert.AreEqual("invalid input", firstRow.Q<TextField>("value-field").value);
            Assert.AreEqual((int)requestedType, firstRow.Q<DropdownField>("type-dropdown").index);
            Assert.AreEqual("other draft", secondRow.Q<TextField>("value-field").value);
            Assert.AreEqual((int)EditorPrefType.Float, secondRow.Q<DropdownField>("type-dropdown").index);
            Assert.AreEqual("saved A", first.Value);
            Assert.AreEqual(EditorPrefType.String, first.Type);
            Assert.AreEqual("saved B", second.Value);
            Assert.AreEqual(EditorPrefType.String, second.Type);
            Assert.AreEqual("saved A", EditorPrefs.GetString(first.Key));
            Assert.AreEqual("saved B", EditorPrefs.GetString(second.Key));
            Assert.AreEqual(1, errors.Count);
        }

        [TestCase(EditorPrefKeys.ClientDetailsFoldoutOpen, EditorPrefType.Bool, "True")]
        [TestCase(EditorPrefKeys.AutoStartOnLoad, EditorPrefType.Bool, "True")]
        [TestCase(EditorPrefKeys.HttpServerLaunchConfirmed, EditorPrefType.Bool, "True")]
        [TestCase(EditorPrefKeys.LogRecordEnabled, EditorPrefType.Bool, "True")]
        [TestCase(EditorPrefKeys.AssetGenAutoNormalize, EditorPrefType.Bool, "True")]
        [TestCase(EditorPrefKeys.BatchExecuteMaxCommands, EditorPrefType.Int, "42")]
        [TestCase(EditorPrefKeys.BlenderPort, EditorPrefType.Int, "42")]
        public void KnownTypedPreference_ReadAndUnchangedSavePreserveStorageType(string key, EditorPrefType type, string value)
        {
            bool existed = EditorPrefs.HasKey(key);
            object previous = ReadStoredValue(key, type);
            try
            {
                WriteStoredValue(key, type, value);
                var item = (EditorPrefItem)Invoke("CreateEditorPrefItem", key);
                Assert.AreEqual(type, item.Type);
                Assert.AreEqual(value, item.Value);
                var row = (VisualElement)Invoke("CreateItemUI", item);
                Rows.Add(key, row);

                Invoke("SavePref", item, item.Value, item.Type);

                Assert.AreEqual(value, ReadStoredValue(key, type).ToString());
                Assert.AreEqual(0, errors.Count);
            }
            finally
            {
                if (existed) WriteStoredValue(key, type, previous.ToString());
                else EditorPrefs.DeleteKey(key);
            }
        }

        [TestCase(EditorPrefType.String, "00042")]
        [TestCase(EditorPrefType.Int, "42")]
        [TestCase(EditorPrefType.Float, "1.25")]
        [TestCase(EditorPrefType.Bool, "True")]
        public void SuccessfulSave_UpdatesOnlySavedRowAndModel(EditorPrefType requestedType, string savedValue)
        {
            var previousCulture = CultureInfo.CurrentCulture;
            try
            {
                CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
                var first = AddItem("saved A", EditorPrefType.String);
                var second = AddItem("saved B", EditorPrefType.String);
                var firstRow = Rows[first.Key];
                var secondRow = Rows[second.Key];
                secondRow.Q<TextField>("value-field").value = "other draft";
                secondRow.Q<DropdownField>("type-dropdown").index = (int)EditorPrefType.Int;
                SetField("searchFilter", "no visible rows");
                Invoke("ApplyFilter");

                Invoke("SavePref", first, savedValue, requestedType);

                Assert.AreEqual(2, Rows.Count);
                Assert.AreSame(firstRow, Rows[first.Key]);
                Assert.AreSame(secondRow, Rows[second.Key]);
                Assert.AreEqual(savedValue, first.Value);
                Assert.AreEqual(requestedType, first.Type);
                Assert.AreEqual(savedValue, firstRow.Q<TextField>("value-field").value);
                Assert.AreEqual((int)requestedType, firstRow.Q<DropdownField>("type-dropdown").index);
                Assert.AreEqual("other draft", secondRow.Q<TextField>("value-field").value);
                Assert.AreEqual((int)EditorPrefType.Int, secondRow.Q<DropdownField>("type-dropdown").index);
                Assert.AreEqual("saved B", second.Value);
                Assert.IsTrue(firstRow.ClassListContains("pref-hidden"));
                Assert.IsTrue(secondRow.ClassListContains("pref-hidden"));
                switch (requestedType)
                {
                    case EditorPrefType.String: Assert.AreEqual(savedValue, EditorPrefs.GetString(first.Key)); break;
                    case EditorPrefType.Int: Assert.AreEqual(42, EditorPrefs.GetInt(first.Key)); break;
                    case EditorPrefType.Float: Assert.AreEqual(1.25f, EditorPrefs.GetFloat(first.Key)); break;
                    case EditorPrefType.Bool: Assert.IsTrue(EditorPrefs.GetBool(first.Key)); break;
                }
                Assert.AreEqual("saved B", EditorPrefs.GetString(second.Key));
                Assert.AreEqual(0, errors.Count);
            }
            finally
            {
                CultureInfo.CurrentCulture = previousCulture;
            }
        }

        [TestCase(EditorPrefKeys.DebugLogs, nameof(EditorConfigurationCache.DebugLogs), EditorPrefType.Bool, "False", "True")]
        [TestCase(EditorPrefKeys.UseHttpTransport, nameof(EditorConfigurationCache.UseHttpTransport), EditorPrefType.Bool, "True", "False")]
        [TestCase(EditorPrefKeys.DevModeForceServerRefresh, nameof(EditorConfigurationCache.DevModeForceServerRefresh), EditorPrefType.Bool, "False", "True")]
        [TestCase(EditorPrefKeys.UnitySocketPort, nameof(EditorConfigurationCache.UnitySocketPort), EditorPrefType.Int, "1", "42")]
        [TestCase(EditorPrefKeys.UvxPathOverride, nameof(EditorConfigurationCache.UvxPathOverride), EditorPrefType.String, "old", "new")]
        [TestCase(EditorPrefKeys.GitUrlOverride, nameof(EditorConfigurationCache.GitUrlOverride), EditorPrefType.String, "old", "new")]
        [TestCase(EditorPrefKeys.HttpBaseUrl, nameof(EditorConfigurationCache.HttpBaseUrl), EditorPrefType.String, "old", "new")]
        [TestCase(EditorPrefKeys.HttpRemoteBaseUrl, nameof(EditorConfigurationCache.HttpRemoteBaseUrl), EditorPrefType.String, "old", "new")]
        [TestCase(EditorPrefKeys.ClaudeCliPathOverride, nameof(EditorConfigurationCache.ClaudeCliPathOverride), EditorPrefType.String, "old", "new")]
        [TestCase(EditorPrefKeys.HttpTransportScope, nameof(EditorConfigurationCache.HttpTransportScope), EditorPrefType.String, "old", "new")]
        public void CachedPreferenceSave_UpdatesCacheAndNotifiesOnce(string key, string propertyName, EditorPrefType type, string initial, string saved)
        {
            bool existed = EditorPrefs.HasKey(key);
            object previous = ReadStoredValue(key, type);
            try
            {
                WriteStoredValue(key, type, initial);
                var cache = EditorConfigurationCache.Instance;
                var notifications = new List<string>();
                cache.OnConfigurationChanged += notifications.Add;
                var item = AddItem(initial, type, key);

                Invoke("SavePref", item, saved, type);

                object expected = type == EditorPrefType.Bool ? (object)bool.Parse(saved) : type == EditorPrefType.Int ? int.Parse(saved) : saved;
                Assert.AreEqual(expected, ReadStoredValue(key, type));
                Assert.AreEqual(expected, typeof(EditorConfigurationCache).GetProperty(propertyName).GetValue(cache));
                CollectionAssert.AreEqual(new[] { propertyName }, notifications);
            }
            finally
            {
                if (existed) WriteStoredValue(key, type, previous.ToString());
                else EditorPrefs.DeleteKey(key);
            }
        }

        [Test]
        public void FailedCachedPreferenceSave_DoesNotNotifyOrChangeCache()
        {
            bool existed = EditorPrefs.HasKey(EditorPrefKeys.DebugLogs);
            bool previous = EditorPrefs.GetBool(EditorPrefKeys.DebugLogs);
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.DebugLogs, false);
                var cache = EditorConfigurationCache.Instance;
                var notifications = new List<string>();
                cache.OnConfigurationChanged += notifications.Add;
                var item = AddItem("False", EditorPrefType.Bool, EditorPrefKeys.DebugLogs);

                Invoke("SavePref", item, "invalid input", EditorPrefType.Bool);

                Assert.IsFalse(cache.DebugLogs);
                Assert.IsFalse(EditorPrefs.GetBool(EditorPrefKeys.DebugLogs));
                Assert.AreEqual(0, notifications.Count);
            }
            finally
            {
                if (existed) EditorPrefs.SetBool(EditorPrefKeys.DebugLogs, previous);
                else EditorPrefs.DeleteKey(EditorPrefKeys.DebugLogs);
            }
        }

        [TestCase(EditorPrefType.String)]
        [TestCase(EditorPrefType.Int)]
        [TestCase(EditorPrefType.Float)]
        [TestCase(EditorPrefType.Bool)]
        public void KnownUnsetPreference_DoesNotClaimApplicationDefault(EditorPrefType type)
        {
            string key = NewKey();
            ((Dictionary<string, EditorPrefType>)GetField("knownPrefTypes"))[key] = type;
            var item = (EditorPrefItem)Invoke("CreateEditorPrefItem", key);
            Assert.IsTrue(item.IsUnset);
            Assert.AreEqual(type, item.Type);
            Assert.AreEqual("Unset", item.Value);
            Assert.IsFalse(EditorPrefs.HasKey(key));
        }

        private Dictionary<string, VisualElement> Rows => (Dictionary<string, VisualElement>)GetField("prefRows");

        private static object ReadStoredValue(string key, EditorPrefType type)
        {
            switch (type)
            {
                case EditorPrefType.Bool: return EditorPrefs.GetBool(key);
                case EditorPrefType.Int: return EditorPrefs.GetInt(key);
                default: return EditorPrefs.GetString(key);
            }
        }

        private static void WriteStoredValue(string key, EditorPrefType type, string value)
        {
            switch (type)
            {
                case EditorPrefType.Bool: EditorPrefs.SetBool(key, bool.Parse(value)); break;
                case EditorPrefType.Int: EditorPrefs.SetInt(key, int.Parse(value)); break;
                default: EditorPrefs.SetString(key, value); break;
            }
        }

        private string NewKey()
        {
            string key = "MCPForUnity.SaveTests." + Guid.NewGuid().ToString("N");
            keys.Add(key);
            return key;
        }

        private EditorPrefItem AddItem(string value, EditorPrefType type, string key = null)
        {
            key = key ?? NewKey();
            if (type == EditorPrefType.String) EditorPrefs.SetString(key, value);
            var item = new EditorPrefItem { Key = key, Value = value, Type = type };
            var row = (VisualElement)Invoke("CreateItemUI", item);
            Rows.Add(key, row);
            ((List<EditorPrefItem>)GetField("currentPrefs")).Add(item);
            ((VisualElement)GetField("prefsContainer")).Add(row);
            return item;
        }

        private object GetField(string name) => typeof(EditorPrefsWindow).GetField(name, InstanceFlags).GetValue(window);
        private void SetField(string name, object value) => typeof(EditorPrefsWindow).GetField(name, InstanceFlags).SetValue(window, value);
        private object Invoke(string name, params object[] arguments) => typeof(EditorPrefsWindow).GetMethod(name, InstanceFlags).Invoke(window, arguments);
    }
}
