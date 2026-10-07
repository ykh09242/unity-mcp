using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using MCPForUnityTests.Editor.Tools.Fixtures;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageScriptableObjectContractTests
    {
        private string _root;
        private string _path;
        private ScriptableObjectContractDefinition _asset;

        [SetUp]
        public void SetUp()
        {
            string folder = "__scriptable_object_contract_" + Guid.NewGuid().ToString("N");
            _root = "Assets/" + folder;
            AssetDatabase.CreateFolder("Assets", folder);
            _path = _root + "/Existing.asset";
            _asset = ScriptableObject.CreateInstance<ScriptableObjectContractDefinition>();
            _asset.intValue = 99;
            AssetDatabase.CreateAsset(_asset, _path);
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(_root) && AssetDatabase.IsValidFolder(_root))
                AssetDatabase.DeleteAsset(_root);
        }

        private JObject Modify(JObject patch, bool dryRun = false) =>
            JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = new JObject { ["path"] = _path },
                        ["patches"] = new JArray(patch),
                        ["dryRun"] = dryRun,
                    }
                )
            );

        private JObject Create(JToken patches) =>
            JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["typeName"] = typeof(ScriptableObjectContractDefinition).FullName,
                        ["folderPath"] = _root,
                        ["assetName"] = "Existing",
                        ["overwrite"] = true,
                        ["patches"] = patches,
                    }
                )
            );

        [TestCase("sett", false)]
        [TestCase("delete", false)]
        [TestCase("sett", true)]
        public void UnknownOperation_DoesNotWrite(string op, bool dryRun)
        {
            JObject response = Modify(
                new JObject
                {
                    ["path"] = "intValue",
                    ["op"] = op,
                    ["value"] = 0,
                },
                dryRun
            );
            bool accepted = dryRun ? (bool)response["data"]["valid"] : (bool)response["data"]["results"][0]["ok"];
            Assert.IsFalse(accepted, response.ToString());
            Assert.AreEqual(99, _asset.intValue);
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("  ")]
        [TestCase("SeT")]
        public void DefaultAndCaseOperations_StillWriteZero(string op)
        {
            JObject response = Modify(
                new JObject
                {
                    ["path"] = "intValue",
                    ["op"] = op,
                    ["value"] = 0,
                }
            );
            Assert.IsTrue((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(0, _asset.intValue);
        }

        [TestCase("{}")]
        [TestCase("{broken")]
        [TestCase("5")]
        [TestCase("[false]")]
        [TestCase("[{\"path\":\"intValue\",\"op\":\"sett\",\"value\":0}]")]
        [TestCase("[{\"path\":\"intValue\"}]")]
        [TestCase("[{\"path\":\"items\",\"op\":\"array_resize\"}]")]
        public void MalformedCreatePatches_PreserveExistingAsset(string encoded)
        {
            string guid = AssetDatabase.AssetPathToGUID(_path);
            JObject response = Create(new JValue(encoded));
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.AreEqual(99, _asset.intValue);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(_path));
            Assert.AreSame(_asset, AssetDatabase.LoadAssetAtPath<ScriptableObjectContractDefinition>(_path));
        }

        [Test]
        public void EmptyCreatePatches_PreserveOverwriteAndGuidBehavior()
        {
            string guid = AssetDatabase.AssetPathToGUID(_path);
            JObject response = Create(new JArray());
            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.AreEqual(7, _asset.intValue);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(_path));
        }

        [TestCase("missing")]
        [TestCase("wrong")]
        [TestCase("abstract")]
        public void InvalidType_DoesNotCreateFolders(string selection)
        {
            string type =
                selection == "missing" ? "NoSuchScriptableObjectType_Contract"
                : selection == "wrong" ? typeof(Material).FullName
                : typeof(AbstractScriptableObjectContractDefinition).FullName;
            string folder = _root + "/Rejected/Nested";
            JObject response = JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["typeName"] = type,
                        ["folderPath"] = folder,
                        ["assetName"] = "New",
                    }
                )
            );
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.IsFalse(AssetDatabase.IsValidFolder(_root + "/Rejected"));
        }

        [Test]
        public void FolderPreparationFailurePreservesPreexistingContent()
        {
            string parent = _root + "/Existing";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(_root, "Existing"));
            string guid = AssetDatabase.AssetPathToGUID(parent);
            string occupied = parent + "/Occupied";
            string physical = Path.Combine(Application.dataPath, occupied.Substring("Assets/".Length));
            File.WriteAllText(physical, "retain existing content");
            AssetDatabase.ImportAsset(occupied, ImportAssetOptions.ForceSynchronousImport);
            string occupiedGuid = AssetDatabase.AssetPathToGUID(occupied);

            var response = JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["typeName"] = typeof(ScriptableObjectContractDefinition).FullName,
                        ["folderPath"] = occupied + "/Nested",
                        ["assetName"] = "Rejected",
                    }
                )
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(parent));
            Assert.AreEqual(occupiedGuid, AssetDatabase.AssetPathToGUID(occupied));
            Assert.AreEqual("retain existing content", File.ReadAllText(physical));
            Assert.IsFalse(Directory.Exists(physical + "/Nested"));
        }

        [Test]
        public void MissingSetValue_DoesNotGrowArray()
        {
            JObject response = Modify(new JObject { ["path"] = "items[3]" });
            Assert.IsFalse((bool)response["data"]["results"][0]["ok"]);
            CollectionAssert.AreEqual(new[] { 7, 8 }, _asset.items);
            JObject dryRun = Modify(new JObject { ["path"] = "items[3]" }, true);
            Assert.IsFalse((bool)dryRun["data"]["valid"]);
            CollectionAssert.AreEqual(new[] { 7, 8 }, _asset.items);
        }

        [Test]
        public void ValidSet_StillGrowsArray()
        {
            JObject response = Modify(new JObject { ["path"] = "items[3]", ["value"] = 0 });
            Assert.IsTrue((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(4, _asset.items.Length);
            Assert.AreEqual(0, _asset.items[3]);
        }

        [TestCase("2147483648")]
        [TestCase("-2147483649")]
        [TestCase("1e100")]
        public void Int32Overflow_IsRejectedInApplyAndDryRun(string number)
        {
            JObject patch = new JObject { ["path"] = "intValue", ["value"] = JToken.Parse(number) };
            JObject response = Modify(patch);
            Assert.IsFalse((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(99, _asset.intValue);
            JObject dryRun = Modify(patch, true);
            Assert.IsFalse((bool)dryRun["data"]["valid"], dryRun.ToString());
            Assert.AreEqual(99, _asset.intValue);
        }

        [TestCase("9223372036854775808")]
        [TestCase("-9223372036854775809")]
        public void LongOverflow_IsRejectedWithoutWrite(string number)
        {
            JObject response = Modify(new JObject { ["path"] = "longValue", ["value"] = JToken.Parse(number) });
            Assert.IsFalse((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(7, _asset.longValue);
        }

        [TestCase("2147483648")]
        [TestCase("9223372036854775807")]
        [TestCase("-9223372036854775808")]
        public void LongField_PreservesFullSignedRange(string number)
        {
            var serialized = new SerializedObject(_asset);
            var property = serialized.FindProperty("longValue");
            Assert.AreEqual(SerializedPropertyType.Integer, property.propertyType);
            Assert.AreEqual("long", property.type);
            JObject response = Modify(new JObject { ["path"] = "longValue", ["value"] = JToken.Parse(number) });
            Assert.IsTrue((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(long.Parse(number), _asset.longValue);
        }

        [Test]
        public void SupportedScalarControls_RetainNumericStringFloatFalseAndNull()
        {
            Assert.IsTrue((bool)Modify(new JObject { ["path"] = "intValue", ["value"] = "42" })["data"]["results"][0]["ok"]);
            Assert.AreEqual(42, _asset.intValue);
            Assert.IsTrue((bool)Modify(new JObject { ["path"] = "intValue", ["value"] = -2.75 })["data"]["results"][0]["ok"]);
            Assert.AreEqual(-2, _asset.intValue);
            Assert.IsTrue((bool)Modify(new JObject { ["path"] = "enabledValue", ["value"] = false })["data"]["results"][0]["ok"]);
            Assert.IsFalse(_asset.enabledValue);
            Assert.IsTrue((bool)Modify(new JObject { ["path"] = "textValue", ["value"] = JValue.CreateNull() })["data"]["results"][0]["ok"]);
            Assert.IsTrue(string.IsNullOrEmpty(_asset.textValue));
        }

        [TestCase("1.2345678901234567", false)]
        [TestCase("16777217", false)]
        [TestCase("1e100", false)]
        [TestCase("1.2345678901234567", true)]
        [TestCase("1e100", true)]
        public void DoubleField_PreservesPrecisionAndRange(string number, bool asString)
        {
            using var serialized = new SerializedObject(_asset);
            var property = serialized.FindProperty("doubleValue");
            Assert.AreEqual(SerializedPropertyType.Float, property.propertyType);
            Assert.AreEqual("double", property.type);
            JToken value = asString ? new JValue(number) : JToken.Parse(number);
            JObject response = Modify(new JObject { ["path"] = "doubleValue", ["value"] = value });
            Assert.IsTrue((bool)response["data"]["results"][0]["ok"], response.ToString());
            double expected = double.Parse(number, System.Globalization.CultureInfo.InvariantCulture);
            Assert.AreEqual(expected, _asset.doubleValue);
            serialized.Update();
            Assert.AreEqual(expected, serialized.FindProperty("doubleValue").doubleValue);
        }

        [TestCase("1.25", false)]
        [TestCase("0", false)]
        [TestCase("-2.5", false)]
        [TestCase("1.25", true)]
        [TestCase("0", true)]
        [TestCase("-2.5", true)]
        public void FloatField_PreservesExistingNumericAndStringInputs(string number, bool asString)
        {
            float expected = float.Parse(number, System.Globalization.CultureInfo.InvariantCulture);
            JToken value = asString ? new JValue(number) : JToken.Parse(number);
            JObject response = Modify(new JObject { ["path"] = "floatValue", ["value"] = value });
            Assert.IsTrue((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(expected, _asset.floatValue);
        }

        [TestCase("null")]
        [TestCase("\"invalid\"")]
        [TestCase("\"NaN\"")]
        [TestCase("true")]
        [TestCase("{}")]
        [TestCase("[]")]
        public void MalformedDoubleValues_DoNotWrite(string encoded)
        {
            JObject response = Modify(new JObject { ["path"] = "doubleValue", ["value"] = JToken.Parse(encoded) });
            Assert.IsFalse((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(7, _asset.doubleValue);
        }

        private JObject ModifyMany(JArray patches, bool dryRun = false) =>
            JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = new JObject { ["path"] = _path },
                        ["patches"] = patches,
                        ["dryRun"] = dryRun,
                    }
                )
            );

        [TestCase("4.75")]
        [TestCase("4e0")]
        [TestCase(" +4 ")]
        public void SupportedNumericStringResize_UsesCheckedLegacyTruncation(string value)
        {
            var response = Modify(
                new JObject
                {
                    ["path"] = "items",
                    ["op"] = "array_resize",
                    ["value"] = value,
                }
            );
            Assert.IsTrue((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(4, _asset.items.Length);
        }

        [TestCase("items", "array_resize", "1048577")]
        [TestCase("items.Array.size", "set", "1048577")]
        [TestCase("items", "array_resize", "2147483648")]
        [TestCase("items", "array_resize", "9223372036854775808")]
        [TestCase("items[2147483647]", "set", "0")]
        [TestCase("items[9999999999999999999999999]", "set", "0")]
        [TestCase("groups[2].numbers[1048576]", "set", "0")]
        public void ExcessiveGrowth_RejectsWholeRequestBeforeFirstPatch(string path, string op, string value)
        {
            var patches = new JArray(
                new JObject { ["path"] = "intValue", ["value"] = 0 },
                new JObject
                {
                    ["path"] = path,
                    ["op"] = op,
                    ["value"] = JToken.Parse(value),
                }
            );
            foreach (bool dryRun in new[] { false, true })
            {
                var response = ModifyMany(patches, dryRun);
                Assert.IsFalse((bool)response["success"], response.ToString());
                Assert.AreEqual(99, _asset.intValue);
                CollectionAssert.AreEqual(new[] { 7, 8 }, _asset.items);
                Assert.AreEqual(1, _asset.groups.Length);
            }
        }

        [Test]
        public void RepeatedShrinkRegrow_DoesNotRefundRequestBudget()
        {
            var patches = new JArray(new JObject { ["path"] = "intValue", ["value"] = 0 });
            for (int i = 0; i < 3; i++)
            {
                patches.Add(
                    new JObject
                    {
                        ["path"] = "items",
                        ["op"] = "array_resize",
                        ["value"] = 1048576,
                    }
                );
                patches.Add(
                    new JObject
                    {
                        ["path"] = "items",
                        ["op"] = "array_resize",
                        ["value"] = 0,
                    }
                );
            }
            Assert.IsFalse((bool)ModifyMany(patches)["success"]);
            Assert.AreEqual(99, _asset.intValue);
            Assert.AreEqual(2, _asset.items.Length);
        }

        [Test]
        public void PlannedNestedCopies_AreChargedBeforeAnyMutation()
        {
            var patches = new JArray(
                new JObject
                {
                    ["path"] = "groups[0].numbers",
                    ["op"] = "array_resize",
                    ["value"] = 200000,
                },
                new JObject
                {
                    ["path"] = "groups",
                    ["op"] = "array_resize",
                    ["value"] = 600,
                }
            );
            Assert.IsFalse((bool)ModifyMany(patches)["success"]);
            Assert.AreEqual(2, _asset.groups[0].numbers.Length);
            Assert.AreEqual(1, _asset.groups.Length);
        }

        [Test]
        public void CopiedStrings_AreBoundedWithoutLargeAllocation()
        {
            _asset.groups[0].text = new string('x', 2048);
            var response = Modify(
                new JObject
                {
                    ["path"] = "groups",
                    ["op"] = "array_resize",
                    ["value"] = 65536,
                }
            );
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.AreEqual(1, _asset.groups.Length);
        }

        [Test]
        public void ExpensiveFirstElement_IsBudgetedEvenWhenLastElementIsCheap()
        {
            _asset.groups = new[]
            {
                new ScriptableObjectContractNested { numbers = new int[1000], text = "first" },
                new ScriptableObjectContractNested { numbers = new int[0], text = "last" },
            };
            var response = Modify(
                new JObject
                {
                    ["path"] = "groups",
                    ["op"] = "array_resize",
                    ["value"] = 3000,
                }
            );
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.AreEqual(2, _asset.groups.Length);
        }

        [Test]
        public void ManagedReferenceDescendants_UseTheSameCopyBudget()
        {
            _asset.managed[0].numbers = new int[1000];
            var response = Modify(
                new JObject
                {
                    ["path"] = "managed",
                    ["op"] = "array_resize",
                    ["value"] = 3000,
                }
            );
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.AreEqual(1, _asset.managed.Length);
            response = Modify(new JObject { ["path"] = "managed[0].numbers[1000]", ["value"] = 9 });
            Assert.IsTrue((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.AreEqual(9, _asset.managed[0].numbers[1000]);
        }

        [Test]
        public void OversizedCreate_PreservesOverwriteAndDoesNotCreateFolder()
        {
            var patch = new JArray(
                new JObject
                {
                    ["path"] = "items",
                    ["op"] = "array_resize",
                    ["value"] = 1048577,
                }
            );
            string guid = AssetDatabase.AssetPathToGUID(_path);
            Assert.IsFalse((bool)Create(patch)["success"]);
            Assert.AreEqual(99, _asset.intValue);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(_path));
            string folder = _root + "/RejectedGrowth/Nested";
            var response = JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["typeName"] = typeof(ScriptableObjectContractDefinition).FullName,
                        ["folderPath"] = folder,
                        ["assetName"] = "New",
                        ["patches"] = patch,
                    }
                )
            );
            Assert.IsFalse((bool)response["success"]);
            Assert.IsFalse(AssetDatabase.IsValidFolder(_root + "/RejectedGrowth"));
        }

        [Test]
        public void SmallNestedMappingsAndMultipleIndexedPrefixes_StillApply()
        {
            var response = ModifyMany(
                new JArray(
                    new JObject
                    {
                        ["path"] = "nested",
                        ["value"] = new JObject { ["numbers"] = new JArray(3, 4, 5) },
                    },
                    new JObject { ["path"] = "groups[1].numbers[3]", ["value"] = 9 }
                )
            );
            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.IsTrue((bool)response["data"]["results"][0]["ok"], response.ToString());
            Assert.IsTrue((bool)response["data"]["results"][1]["ok"], response.ToString());
            CollectionAssert.AreEqual(new[] { 3, 4, 5 }, _asset.nested.numbers);
            Assert.AreEqual(2, _asset.groups.Length);
            Assert.AreEqual(9, _asset.groups[1].numbers[3]);
        }
    }
}
