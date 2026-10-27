using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class AddressablesCompatibilityTests
    {
        private static object Policy(string name, params object[] args)
        {
            Type type = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesCompatibility, MCPForUnity.Editor", true);
            return type.GetMethod(name, BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, args);
        }

        [TestCase("1.0.0", true)]
        [TestCase("1.1.5", true)]
        [TestCase("1.28.2", true)]
        [TestCase("1.29.0", true)]
        [TestCase("1.28.3", true)]
        [TestCase("2.0.0", true)]
        [TestCase("2.7.6", true)]
        [TestCase("2.8.0", true)]
        [TestCase("2.11.2", true)]
        [TestCase("4.0.0", true)]
        [TestCase("0.9.0", false)]
        [TestCase("3.1.0", true)]
        [TestCase("3.2.0", true)]
        [TestCase("4.1.1", true)]
        [TestCase("4.1.2", true)]
        [TestCase("4.2.0", true)]
        [TestCase("3.0.9", true)]
        [TestCase("4.0.9", true)]
        [TestCase("4.1.0", true)]
        [TestCase("5.0.0", false)]
        [TestCase("3.1.0-pre.1", false)]
        [TestCase("4.1.1+metadata", false)]
        [TestCase("3.01.0", false)]
        [TestCase("4.1.01", false)]
        [TestCase("1.27.0", true)]
        [TestCase("3.0.0", true)]
        [TestCase("2.7.6-pre.1", false)]
        [TestCase("2.7", false)]
        [TestCase("2.7.6.1", false)]
        [TestCase(" 2.7.6", false)]
        [TestCase("2.-7.6", false)]
        [TestCase("02.7.6", false)]
        [TestCase("2.7.2147483648", false)]
        [TestCase(null, false)]
        public void StableFamiliesRequireStrictPackageVersions(string version, bool expected) => Assert.AreEqual(expected, Policy("SupportsVersion", version));

        [TestCase("1.28.2", true)]
        [TestCase("2.7.6", false)]
        [TestCase("3.1.0", true)]
        [TestCase("4.1.1", true)]
        [TestCase("3.0.0", true)]
        [TestCase("4.1.0", true)]
        [TestCase("5.0.0", false)]
        public void LookupCacheResetIsRequiredOnlyForCompatibleAffectedFamilies(string version, bool expected) =>
            Assert.AreEqual(expected, Policy("RequiresLookupCacheReset", version));

        private static void ReportShape(JObject report, bool modern)
        {
            Type type = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            type.GetMethod("RequireSupportedReportShape", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { report, modern });
        }

        [TestCase(null, false, true)]
        [TestCase(null, true, false)]
        [TestCase("[]", true, true)]
        [TestCase("[]", false, true)]
        [TestCase("null", true, false)]
        [TestCase("{}", true, false)]
        [TestCase("[{}]", true, false)]
        [TestCase("[{}]", false, false)]
        [TestCase("\"unsupported\"", true, false)]
        public void ContentDirectoryReportShapeRequiresEmptySupportedEvidence(string contentDirectories, bool modern, bool accepted)
        {
            var report = new JObject { ["DuplicatedAssets"] = new JArray(), ["Groups"] = new JArray() };
            if (contentDirectories != null)
                report["ContentDirectories"] = JToken.Parse(contentDirectories);
            if (accepted)
                Assert.DoesNotThrow(() => ReportShape(report, modern));
            else
            {
                var error = Assert.Throws<TargetInvocationException>(() => ReportShape(report, modern));
                Assert.AreEqual(
                    "addressables_layout_schema_unsupported",
                    error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
                );
            }
        }

        [TestCase(null, true)]
        [TestCase("\"\"", true)]
        [TestCase("\"owned-directory\"", false)]
        [TestCase("1", false)]
        [TestCase("{}", false)]
        [TestCase("false", false)]
        public void GroupContentDirectoryCoverageIsCheckedInlineAndInManagedReferences(string value, bool accepted)
        {
            foreach (bool referenced in new[] { false, true })
            {
                var group = new JObject { ["ContentDirectoryName"] = value == null ? JValue.CreateNull() : JToken.Parse(value) };
                var report = new JObject
                {
                    ["DuplicatedAssets"] = new JArray(),
                    ["Groups"] = new JArray(),
                    ["ContentDirectories"] = new JArray(),
                };
                if (referenced)
                    report["references"] = new JObject { ["RefIds"] = new JArray(new JObject { ["data"] = group }) };
                else
                    ((JArray)report["Groups"]).Add(group);
                if (accepted)
                    Assert.DoesNotThrow(() => ReportShape(report, true));
                else
                {
                    var error = Assert.Throws<TargetInvocationException>(() => ReportShape(report, true));
                    Assert.AreEqual(
                        "addressables_layout_schema_unsupported",
                        error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
                    );
                }
            }
        }

        private sealed class EditApi
        {
            internal int Calls;

            public void SetAddress(string address, bool postEvent) => Calls++;

            public void SetAddress(string address) => Calls++;

            public bool SetValue(string profile, string name, string value)
            {
                Calls++;
                return true;
            }

            public static void StaticEdit(string value) { }

            public void GenericEdit<T>(string value) => Calls++;
        }

        [Test]
        public void EditCapabilityResolutionChecksExactSignatureWithoutInvokingIt()
        {
            var target = new EditApi();
            var method = (MethodInfo)Policy("RequireMethod", target, "set_address", "SetAddress", typeof(void), new[] { typeof(string), typeof(bool) });
            Assert.AreEqual(2, method.GetParameters().Length);
            Assert.AreEqual(0, target.Calls, "Capability checks must never invoke native edits.");
        }

        [TestCase("SetAddress", "move_entry")]
        [TestCase("SetValue", "set_profile_value")]
        [TestCase("StaticEdit", "set_address")]
        [TestCase("Missing", "set_address")]
        public void MissingOrIncompatibleEditApisRefuseBeforeInvocation(string method, string action)
        {
            var target = new EditApi();
            var error = Assert.Throws<TargetInvocationException>(() =>
                Policy("RequireMethod", target, action, method, typeof(void), new[] { typeof(string), typeof(string), typeof(string) })
            );
            Assert.AreEqual(
                "addressables_api_unsupported",
                error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
            );
            StringAssert.Contains(action, error.InnerException.Message);
            Assert.AreEqual(0, target.Calls);
        }

        [TestCase("StaticEdit")]
        [TestCase("GenericEdit")]
        public void StaticAndOpenGenericMethodsAreNotNativeEditCapabilities(string method)
        {
            var target = new EditApi();
            var error = Assert.Throws<TargetInvocationException>(() =>
                Policy("RequireMethod", target, "set_address", method, typeof(void), new[] { typeof(string) })
            );
            Assert.AreEqual(
                "addressables_api_unsupported",
                error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
            );
            Assert.AreEqual(0, target.Calls);
        }

        [Test]
        public void MissingLayoutSchemaRefusesWithAnActionSpecificCapabilityError()
        {
            Type type = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            var error = Assert.Throws<TargetInvocationException>(() =>
                type.GetMethod("RequireSchema", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { typeof(object) })
            );
            Assert.AreEqual(
                "addressables_api_unsupported",
                error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
            );
            StringAssert.Contains("analyze_layout", error.InnerException.Message);
        }

#pragma warning disable CS0649, CS0169 // Deliberately incomplete native schema fixtures are inspected through reflection.
        private class LegacyLayout
        {
            public class Group { }
        }

        private class ModernLayout
        {
            public class Group
            {
                public string ContentDirectoryName;
            }

            public class ContentDirectory { }

            public List<ContentDirectory> ContentDirectories;
        }

        private class PartialLayout
        {
            public class Group
            {
                public string ContentDirectoryName;
            }
        }

        private class MalformedLayout
        {
            public class Group
            {
                public int ContentDirectoryName;
            }

            public class ContentDirectory { }

            public List<ContentDirectory> ContentDirectories;
        }

        private class PrivateExtensionLayout
        {
            public class Group { }

            private List<object> ContentDirectories;
        }

#pragma warning restore CS0649, CS0169

        [TestCase(typeof(LegacyLayout), false)]
        [TestCase(typeof(ModernLayout), true)]
        public void ContentDirectoryCapabilityUsesNativeSchemaRatherThanVersion(Type layout, bool expected)
        {
            Type adapter = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            Assert.AreEqual(
                expected,
                adapter.GetMethod("UsesContentDirectories", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { layout })
            );
        }

        [TestCase(typeof(PartialLayout))]
        [TestCase(typeof(MalformedLayout))]
        [TestCase(typeof(PrivateExtensionLayout))]
        public void IncompleteNativeContentDirectorySchemaRefusesBeforeReadingReports(Type layout)
        {
            Type adapter = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            var error = Assert.Throws<TargetInvocationException>(() =>
                adapter.GetMethod("UsesContentDirectories", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { layout })
            );
            Assert.AreEqual(
                "addressables_api_unsupported",
                error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
            );
            StringAssert.Contains("analyze_layout", error.InnerException.Message);
        }

        [Test]
        public void InstalledPackageHasTheCompleteConsumedLayoutSchema()
        {
            Type layout = Type.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout, Unity.Addressables.Editor");
            if (layout == null)
                Assert.Ignore("A supported real Addressables package is required.");
            Type adapter = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            Assert.DoesNotThrow(() => adapter.GetMethod("RequireSchema", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { layout }));
            Assert.DoesNotThrow(() =>
                adapter.GetMethod("UsesContentDirectories", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { layout })
            );
        }
    }

    [Parallelizable(ParallelScope.None)]
    public class AddressablesAuthoringTests
    {
        private readonly PrefabTestSceneFixture scenes = new PrefabTestSceneFixture();
        private string root,
            rootGuid,
            settingsPath,
            assetGuid;
        private object settings,
            first,
            second,
            entry,
            other,
            profiles;
        private string activeProfile;
        private Type settingsType;

        [OneTimeSetUp]
        public void Prepare() => scenes.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => scenes.RestoreRunnerBootstrap();

        internal static object Get(object value, string name)
        {
            var property = value.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return property != null
                ? property.GetValue(value)
                : value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)?.GetValue(value);
        }

        internal static object Invoke(object value, string name, params object[] args)
        {
            var method = value
                .GetType()
                .GetMethods(BindingFlags.Public | BindingFlags.Instance)
                .Single(m =>
                    m.Name == name
                    && m.GetParameters().Length == args.Length
                    && m.GetParameters().Select((p, i) => args[i] == null || p.ParameterType.IsInstanceOfType(args[i])).All(v => v)
                );
            return method.Invoke(value, args);
        }

        [SetUp]
        public void SetUp()
        {
            root = null;
            settingsType = Type.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings, Unity.Addressables.Editor");
            if (settingsType == null)
                Assert.Ignore("A supported real Addressables package is required.");
            scenes.Create("McpAddressables_", Guid.NewGuid().ToString("N"));
            root = "Assets/__McpAddressables_" + Guid.NewGuid().ToString("N");
            rootGuid = AssetDatabase.CreateFolder("Assets", root.Substring(7));
            // Persist only the owned temporary factory result. The persisted factory invokes global SaveAssets.
            UnityEngine.Object[] preservedDirty = UnityEngine
                .Resources.FindObjectsOfTypeAll<UnityEngine.Object>()
                .Where(v => v != null && EditorUtility.IsDirty(v) && AssetDatabase.Contains(v))
                .Take(16)
                .ToArray();
            foreach (UnityEngine.Object value in preservedDirty)
                Debug.Log("Addressables fixture preserves dirty asset " + AssetDatabase.GetAssetPath(value) + " nativeId=" + value.GetInstanceIDCompat());
            AssetDatabase.CreateFolder(root, "Config");
            var temporary = (UnityEngine.Object)
                settingsType
                    .GetMethod("Create", new[] { typeof(string), typeof(string), typeof(bool), typeof(bool) })
                    .Invoke(null, new object[] { root + "/Config", "Settings", false, false });
            Assert.IsNotNull(temporary);
            settingsPath = root + "/Config/Settings.asset";
            AssetDatabase.CreateAsset(temporary, settingsPath);
            AssetDatabase.SaveAssetIfDirty(temporary);
            // m_IsTemporary is not serialized in either baseline package. Reload establishes persisted native settings.
            UnityEngine.Resources.UnloadAsset(temporary);
            settings = AssetDatabase.LoadAssetAtPath(settingsPath, settingsType);
            Assert.IsNotNull(settings);
            Assert.IsTrue((bool)Get(settings, "IsPersisted"), "Owned settings must reload as a real persistent settings asset.");
            foreach (UnityEngine.Object value in preservedDirty)
                Assert.IsTrue(value != null && EditorUtility.IsDirty(value), "Unrelated dirty assets must not be saved or cleared.");
            first = Invoke(settings, "CreateGroup", "OwnedA", false, false, false, null, Array.Empty<Type>());
            second = Invoke(settings, "CreateGroup", "OwnedB", false, false, false, null, Array.Empty<Type>());
            var texture = new Texture2D(2, 2);
            AssetDatabase.CreateAsset(texture, root + "/First.asset");
            assetGuid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(texture));
            entry = Invoke(settings, "CreateOrMoveEntry", assetGuid, first, false, false);
            Assert.IsNotNull(entry, "The real package must create the owned asset entry.");
            Invoke(entry, "SetAddress", "owned/first", false);
            Invoke(settings, "AddLabel", "owned-label", false);
            Invoke(entry, "SetLabel", "owned-label", true, false, false);
            var reference = ScriptableObject.CreateInstance<AddressablesReferenceFixture>();
            Type assetReferenceType = Type.GetType("UnityEngine.AddressableAssets.AssetReference, Unity.Addressables", true);
            reference.Reference = Activator.CreateInstance(assetReferenceType, new object[] { new string('e', 32) });
            AssetDatabase.CreateAsset(reference, root + "/Reference.asset");
            string referenceGuid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(reference));
            other = Invoke(settings, "CreateOrMoveEntry", referenceGuid, first, false, false);
            Invoke(other, "SetAddress", "owned/reference", false);
            profiles = Get(settings, "profileSettings");
            activeProfile = (string)Get(settings, "activeProfileId");
            Assert.IsFalse(string.IsNullOrEmpty(activeProfile), "Native settings factory must establish its real default profile.");
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (root != null)
                {
                    foreach (string guid in AssetDatabase.FindAssets("", new[] { root }))
                    {
                        UnityEngine.Object value = AssetDatabase.LoadMainAssetAtPath(AssetDatabase.GUIDToAssetPath(guid));
                        if (value != null)
                            Undo.ClearUndo(value);
                    }
                    StringAssert.StartsWith("Assets/__McpAddressables_", root);
                    Assert.AreEqual(rootGuid, AssetDatabase.AssetPathToGUID(root));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(root), "Only the fixture-owned asset tree is removed.");
                }
            }
            finally
            {
                scenes.Close();
            }
        }

        private JObject Call(string action, JObject options = null)
        {
            Type handler = Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor");
            Assert.IsNotNull(handler, "SPEC15 dedicated Addressables handler is missing.");
            var args = options == null ? new JObject() : (JObject)options.DeepClone();
            args["action"] = action;
            args["settings_path"] = settingsPath;
            return JObject.FromObject(handler.GetMethod("HandleCommand", new[] { typeof(JObject) }).Invoke(null, new object[] { args }));
        }

        private JObject Read(string section = "entries", int page = 1, int size = 50, string revision = null)
        {
            var args = new JObject
            {
                ["section"] = section,
                ["page_number"] = page,
                ["page_size"] = size,
            };
            if (revision != null)
                args["expected_revision"] = revision;
            JObject response = Call("inspect", args);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        private static string[] Labels(object value) => ((IEnumerable)Get(value, "labels")).Cast<string>().OrderBy(x => x).ToArray();

        private static void SameAsset(UnityEngine.Object before, UnityEngine.Object after)
        {
            Assert.IsTrue(before == after, "Unity native object identity must be preserved.");
            Assert.AreEqual(before.GetInstanceIDCompat(), after.GetInstanceIDCompat());
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(before, out string guid, out long id));
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(after, out string nextGuid, out long nextId));
            Assert.AreEqual(guid, nextGuid);
            Assert.AreEqual(id, nextId);
        }

        [Test]
        public void PagedEntriesGroupsProfilesPreserveNativeIdentityAndLabels()
        {
            var bytes = Directory.GetFiles(root, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            bool dirty = EditorUtility.IsDirty((UnityEngine.Object)settings);
            JObject firstPage = Read("entries", 1, 1);
            Assert.AreEqual(2, firstPage.Value<int>("total"));
            JObject lastPage = Read("entries", 2, 1, firstPage.Value<string>("revision"));
            string[] guids = ((JArray)firstPage["items"]).Concat((JArray)lastPage["items"]).Select(t => t.Value<string>("guid")).ToArray();
            CollectionAssert.AreEquivalent(new[] { assetGuid, (string)Get(other, "guid") }, guids);
            Assert.AreEqual(2, guids.Distinct().Count());
            Assert.GreaterOrEqual(Read("groups").Value<int>("total"), 2);
            Assert.Greater(Read("profiles").Value<int>("total"), 0);
            CollectionAssert.AreEqual(new[] { "owned-label" }, Labels(entry));
            SameAsset((UnityEngine.Object)settings, AssetDatabase.LoadMainAssetAtPath(settingsPath));
            Assert.AreEqual(UnityEditor.PackageManager.PackageInfo.FindForAssembly(settingsType.Assembly).version, firstPage.Value<string>("package_version"));
            Assert.AreEqual(dirty, EditorUtility.IsDirty((UnityEngine.Object)settings), "Inspection must not dirty or migrate settings.");
            foreach (var pair in bytes)
                CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key), "Inspection must preserve existing owned asset bytes.");
        }

        [TestCase("inspect")]
        [TestCase("preflight")]
        [TestCase("set_address")]
        [TestCase("move_entry")]
        [TestCase("set_profile_value")]
        public void UnknownStoredProfileSchemaRefusesWithoutMigration(string action)
        {
            FieldInfo field = profiles.GetType().GetField("m_ProfileVersion", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(field);
            object original = field.GetValue(profiles);
            bool dirty = EditorUtility.IsDirty((UnityEngine.Object)settings);
            string address = (string)Get(entry, "address");
            int undo = Undo.GetCurrentGroup();
            try
            {
                field.SetValue(profiles, 2);
                JObject response = Call(action);
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual("addressables_profile_schema_unsupported", response.Value<string>("code"));
                StringAssert.Contains(action, response["data"].Value<string>("message"));
                Assert.AreEqual(2, field.GetValue(profiles), "The action must not invoke a native profile migration.");
                Assert.AreEqual(address, Get(entry, "address"));
                Assert.AreEqual(undo, Undo.GetCurrentGroup(), "Unsupported stored schemas must refuse before Undo registration.");
                Assert.AreEqual(dirty, EditorUtility.IsDirty((UnityEngine.Object)settings));
            }
            finally
            {
                field.SetValue(profiles, original);
            }
        }

        [Test]
        public void ExistingNativeLayoutUsesInstalledVersionAndRejectsMismatchedReportMetadata()
        {
            string version = UnityEditor.PackageManager.PackageInfo.FindForAssembly(settingsType.Assembly).version;
            Type layoutType = settingsType.Assembly.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout", true);
            object layout = Activator.CreateInstance(layoutType);
            layoutType.GetField("PackageVersion").SetValue(layout, "com.unity.addressables: " + version);
            layoutType.GetField("BuildTarget").SetValue(layout, EditorUserBuildSettings.activeBuildTarget);
            layoutType.GetField("UnityVersion").SetValue(layout, Application.unityVersion);
            string directory = "Library/MCPForUnity/Addressables/compat-" + Guid.NewGuid().ToString("N");
            string path = directory + "/layout.json";
            Assert.IsFalse(Directory.Exists(directory));
            Directory.CreateDirectory(directory);
            try
            {
                string json = EditorJsonUtility.ToJson(layout);
                File.WriteAllText(path, json);
                Type handler = Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor", true);
                var request = new JObject { ["action"] = "analyze_layout", ["layout_path"] = path };
                JObject response = JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { request }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(version, response["data"].Value<string>("package_version"));
                Assert.AreEqual(0, response["data"].Value<int>("total"));
                Assert.IsTrue(response["data"].Value<bool>("analysis_complete"), "An empty compatible native report has complete legacy duplication evidence.");
                Assert.IsFalse(response["data"].Value<bool>("content_build_started"));
                Assert.AreEqual(json, File.ReadAllText(path), "Existing reports are read-only.");

                layoutType.GetField("PackageVersion").SetValue(layout, "com.unity.addressables: 0.0.0");
                json = EditorJsonUtility.ToJson(layout);
                File.WriteAllText(path, json);
                response = JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { request }));
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual("addressables_layout_version_unsupported", response.Value<string>("code"));
                Assert.AreEqual(json, File.ReadAllText(path));
            }
            finally
            {
                File.Delete(path);
                Directory.Delete(directory);
            }
        }

        [Test]
        public void ContentDirectoryReportsRefuseWithoutCompleteCoverageAndPreserveStoredBytes()
        {
            string version = UnityEditor.PackageManager.PackageInfo.FindForAssembly(settingsType.Assembly).version;
            Type layoutType = settingsType.Assembly.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout", true);
            object layout = Activator.CreateInstance(layoutType);
            layoutType.GetField("PackageVersion").SetValue(layout, "com.unity.addressables: " + version);
            layoutType.GetField("BuildTarget").SetValue(layout, EditorUserBuildSettings.activeBuildTarget);
            layoutType.GetField("UnityVersion").SetValue(layout, Application.unityVersion);
            JObject baseline = JObject.Parse(EditorJsonUtility.ToJson(layout));
            var reports = new List<JObject>();
            foreach (JToken value in new JToken[] { new JArray(new JObject()), new JObject(), new JValue("unsupported"), JValue.CreateNull() })
            {
                var report = (JObject)baseline.DeepClone();
                report["ContentDirectories"] = value;
                reports.Add(report);
            }
            foreach (bool referenced in new[] { false, true })
            foreach (JToken value in new JToken[] { new JValue("owned-directory"), new JValue(1), new JObject() })
            {
                var report = (JObject)baseline.DeepClone();
                report["ContentDirectories"] = new JArray();
                var group = new JObject { ["ContentDirectoryName"] = value };
                if (referenced)
                    report["references"] = new JObject { ["RefIds"] = new JArray(new JObject { ["data"] = group }) };
                else
                    report["Groups"] = new JArray(group);
                reports.Add(report);
            }
            if (layoutType.GetField("ContentDirectories") != null)
            {
                var missing = (JObject)baseline.DeepClone();
                missing.Remove("ContentDirectories");
                reports.Add(missing);
            }
            string directory = "Library/MCPForUnity/Addressables/compat-content-" + Guid.NewGuid().ToString("N");
            string path = directory + "/layout.json";
            Assert.IsFalse(Directory.Exists(directory));
            Directory.CreateDirectory(directory);
            try
            {
                Type handler = Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor", true);
                foreach (JObject report in reports)
                {
                    File.WriteAllText(path, report.ToString());
                    byte[] before = File.ReadAllBytes(path);
                    JObject response = JObject.FromObject(
                        handler
                            .GetMethod("HandleCommand")
                            .Invoke(
                                null,
                                new object[]
                                {
                                    new JObject { ["action"] = "analyze_layout", ["layout_path"] = path },
                                }
                            )
                    );
                    Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                    Assert.AreEqual("addressables_layout_schema_unsupported", response.Value<string>("code"), response.ToString());
                    CollectionAssert.AreEqual(before, File.ReadAllBytes(path), "Unsupported reports must remain byte-for-byte unchanged.");
                }
            }
            finally
            {
                File.Delete(path);
                Directory.Delete(directory);
            }
        }

        [Test]
        public void ReadPreflightFindsDuplicateTypedMissingReferenceAndProfileErrorsWithoutAuthoring()
        {
            Invoke(other, "SetAddress", "owned/first", false);
            Invoke(profiles, "SetValue", activeProfile, "Local.BuildPath", "[MissingOwnedVariable]");
            Invoke(profiles, "CreateValue", "OwnedCycleA", "[OwnedCycleB]");
            Invoke(profiles, "CreateValue", "OwnedCycleB", "[OwnedCycleA]");
            string[] labels = Labels(entry);
            JObject before = Read();
            JObject response = Call("preflight");
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var codes = ((JArray)response["data"]["items"]).Select(i => i.Value<string>("code")).ToArray();
            CollectionAssert.Contains(codes, "duplicate_address");
            CollectionAssert.Contains(codes, "missing_asset_reference");
            CollectionAssert.Contains(codes, "unknown_profile_token");
            CollectionAssert.Contains(codes, "profile_cycle");
            Assert.IsFalse(response["data"].Value<bool>("content_build_started"), response.ToString());
            Assert.AreEqual(before.Value<string>("revision"), Read().Value<string>("revision"), "Read preflight must not author settings.");
            CollectionAssert.AreEqual(labels, Labels(entry));
            Assert.AreEqual("owned/first", Get(other, "address"));
        }

        [Test]
        public void GuardedAddressAndGroupMovePreserveGuidLabelsAssetsAndUndo()
        {
            var nativeSettings = (UnityEngine.Object)settings;
            var nativeFirst = (UnityEngine.Object)first;
            string[] labels = Labels(entry);
            JObject response = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["address"] = "owned/renamed",
                    ["expected_revision"] = Read().Value<string>("revision"),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("owned/renamed", Get(entry, "address"));
            JObject moved = Call(
                "move_entry",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["group_guid"] = (string)Get(second, "Guid"),
                    ["expected_revision"] = Read().Value<string>("revision"),
                }
            );
            Assert.IsTrue(moved.Value<bool>("success"), moved.ToString());
            object fresh = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(second, "Guid"), Get(Get(fresh, "parentGroup"), "Guid"));
            Assert.AreEqual(assetGuid, Get(fresh, "guid"));
            CollectionAssert.AreEqual(labels, Labels(fresh));
            SameAsset(nativeSettings, AssetDatabase.LoadMainAssetAtPath(settingsPath));
            SameAsset(nativeFirst, AssetDatabase.LoadMainAssetAtPath(AssetDatabase.GetAssetPath(nativeFirst)));
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            object sourceRestored = Invoke(first, "GetAssetEntry", assetGuid);
            object destinationRestored = Invoke(second, "GetAssetEntry", assetGuid);
            Assert.IsNotNull(sourceRestored, "Native Undo must restore the source group's entry collection.");
            Assert.IsNull(destinationRestored, "Native Undo must remove the entry from the destination group's collection.");
            object restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual(
                (string)Get(first, "Guid"),
                Get(Get(restored, "parentGroup"), "Guid"),
                "Native settings lookup must agree with restored group collections after Undo."
            );
            CollectionAssert.AreEqual(labels, Labels(restored));
            Assert.AreEqual("owned/renamed", Get(restored, "address"), "Undoing the move must preserve the preceding address edit.");
            Undo.PerformRedo();
            restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(second, "Guid"), Get(Get(restored, "parentGroup"), "Guid"), "Redo must refresh the native settings lookup too.");
            CollectionAssert.AreEqual(labels, Labels(restored));
            Undo.PerformUndo();
            Undo.PerformUndo();
            restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(first, "Guid"), Get(Get(restored, "parentGroup"), "Guid"));
            Assert.AreEqual("owned/first", Get(restored, "address"), "Undo must also restore the address through the settings lookup cache.");
            Undo.PerformRedo();
            Undo.PerformRedo();
            restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(second, "Guid"), Get(Get(restored, "parentGroup"), "Guid"));
            Assert.AreEqual("owned/renamed", Get(restored, "address"));

            var bytes = Directory.GetFiles(root, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            JObject metadata = Read();
            Texture2D unrelated = AssetDatabase.LoadAssetAtPath<Texture2D>(root + "/First.asset");
            string name = unrelated.name;
            Undo.IncrementCurrentGroup();
            Undo.RegisterCompleteObjectUndo(unrelated, "Unrelated texture name");
            unrelated.name = name + "Changed";
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.AreEqual(name, unrelated.name);
            Assert.AreEqual(metadata.Value<string>("revision"), Read().Value<string>("revision"), "Unrelated Undo must not change Addressables metadata.");
            restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(second, "Guid"), Get(Get(restored, "parentGroup"), "Guid"));
            foreach (var pair in bytes)
                CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key), "Undo/Redo cache refresh must not write owned asset files.");
        }

        [Test]
        public void GuardedProfileCorrectionPreservesExistingProfileIdentityAndOtherValues()
        {
            string name = "Local.BuildPath";
            string oldValue = (string)Invoke(profiles, "GetValueByName", activeProfile, "Local.LoadPath");
            string[] labels = Labels(entry);
            JObject response = Call(
                "set_profile_value",
                new JObject
                {
                    ["profile_id"] = activeProfile,
                    ["variable_name"] = name,
                    ["value"] = "Library/OwnedAddressables",
                    ["expected_revision"] = Read("profiles").Value<string>("revision"),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("Library/OwnedAddressables", Invoke(profiles, "GetValueByName", activeProfile, name));
            Assert.AreEqual(oldValue, Invoke(profiles, "GetValueByName", activeProfile, "Local.LoadPath"));
            Assert.AreEqual(activeProfile, Get(settings, "activeProfileId"));
            CollectionAssert.AreEqual(labels, Labels(entry));
            Assert.AreEqual(assetGuid, Get(entry, "guid"));
        }

        [Test]
        public void BracketedAddressRefusesBeforeNativeMutationAndPreservesIdentityLabelsAndBytes()
        {
            foreach (string guid in AssetDatabase.FindAssets("", new[] { root }))
            {
                UnityEngine.Object asset = AssetDatabase.LoadMainAssetAtPath(AssetDatabase.GUIDToAssetPath(guid));
                if (asset != null)
                    AssetDatabase.SaveAssetIfDirty(asset);
            }
            string[] labels = Labels(entry);
            string address = (string)Get(entry, "address");
            string guidBefore = (string)Get(entry, "guid");
            JObject before = Read();
            UnityEngine.Object nativeSettings = (UnityEngine.Object)settings;
            var bytes = Directory.GetFiles(root, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            JObject response = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["address"] = "[bad]",
                    ["expected_revision"] = before["revision"],
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), "Package-invalid addresses must be rejected before native SetAddress: " + response);
            Assert.AreEqual("invalid_addressables_request", response.Value<string>("code"));
            Assert.AreEqual(address, Get(entry, "address"), "Refusal must preserve the existing in-memory address.");
            Assert.AreEqual(guidBefore, Get(entry, "guid"));
            CollectionAssert.AreEqual(labels, Labels(entry));
            SameAsset(nativeSettings, AssetDatabase.LoadMainAssetAtPath(settingsPath));
            Assert.AreEqual(before.Value<string>("revision"), Read().Value<string>("revision"));
            CollectionAssert.AreEquivalent(bytes.Keys, Directory.GetFiles(root, "*", SearchOption.AllDirectories));
            foreach (var pair in bytes)
                CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key), "Refusal must preserve owned file bytes: " + pair.Key);
        }

        [Test]
        public void StaleContinuationDuplicateAddressAndUnknownProfileRefuseBeforeWrites()
        {
            JObject before = Read("entries", 1, 1);
            Invoke(entry, "SetAddress", "owned/external", false);
            JObject stale = Call(
                "inspect",
                new JObject
                {
                    ["section"] = "entries",
                    ["page_number"] = 2,
                    ["page_size"] = 1,
                    ["expected_revision"] = before["revision"],
                }
            );
            Assert.IsFalse(stale.Value<bool>("success"), stale.ToString());
            Assert.AreEqual("stale_addressables_revision", stale.Value<string>("code"));
            JObject current = Read();
            JObject duplicate = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["address"] = (string)Get(other, "address"),
                    ["expected_revision"] = current["revision"],
                }
            );
            Assert.IsFalse(duplicate.Value<bool>("success"), duplicate.ToString());
            JObject profile = Call(
                "set_profile_value",
                new JObject
                {
                    ["profile_id"] = new string('d', 32),
                    ["variable_name"] = "Local.BuildPath",
                    ["value"] = "Library/Owned",
                    ["expected_revision"] = current["revision"],
                }
            );
            Assert.IsFalse(profile.Value<bool>("success"), profile.ToString());
            Assert.AreEqual("owned/external", Get(entry, "address"));
            CollectionAssert.AreEqual(new[] { "owned-label" }, Labels(entry));
        }
    }
}
