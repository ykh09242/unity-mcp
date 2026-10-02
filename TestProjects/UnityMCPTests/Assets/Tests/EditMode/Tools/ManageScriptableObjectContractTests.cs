using System;
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

        private JObject Modify(JObject patch, bool dryRun = false)
            => JObject.FromObject(ManageScriptableObject.HandleCommand(new JObject
            {
                ["action"] = "modify", ["target"] = new JObject { ["path"] = _path },
                ["patches"] = new JArray(patch), ["dryRun"] = dryRun
            }));

        private JObject Create(JToken patches)
            => JObject.FromObject(ManageScriptableObject.HandleCommand(new JObject
            {
                ["action"] = "create", ["typeName"] = typeof(ScriptableObjectContractDefinition).FullName,
                ["folderPath"] = _root, ["assetName"] = "Existing", ["overwrite"] = true,
                ["patches"] = patches
            }));

        [TestCase("sett", false)]
        [TestCase("delete", false)]
        [TestCase("sett", true)]
        public void UnknownOperation_DoesNotWrite(string op, bool dryRun)
        {
            JObject response = Modify(new JObject { ["path"] = "intValue", ["op"] = op, ["value"] = 0 }, dryRun);
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
            JObject response = Modify(new JObject { ["path"] = "intValue", ["op"] = op, ["value"] = 0 });
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
            string type = selection == "missing" ? "NoSuchScriptableObjectType_Contract"
                : selection == "wrong" ? typeof(Material).FullName
                : typeof(AbstractScriptableObjectContractDefinition).FullName;
            string folder = _root + "/Rejected/Nested";
            JObject response = JObject.FromObject(ManageScriptableObject.HandleCommand(new JObject
            {
                ["action"] = "create", ["typeName"] = type, ["folderPath"] = folder, ["assetName"] = "New"
            }));
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.IsFalse(AssetDatabase.IsValidFolder(_root + "/Rejected"));
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
    }
}
