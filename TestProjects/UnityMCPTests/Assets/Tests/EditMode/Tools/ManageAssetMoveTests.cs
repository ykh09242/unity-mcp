using System;
using System.IO;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageAssetMoveTests
    {
        private string _folder;
        private string _source;
        private string _destination;

        [SetUp]
        public void SetUp()
        {
            _folder = "Assets/MoveContractTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
            _source = _folder + "/source.txt";
            _destination = _folder + "/destination.txt";
            File.WriteAllText(_source, "move fixture");
            AssetDatabase.ImportAsset(_source, ImportAssetOptions.ForceSynchronousImport);
        }

        [TearDown]
        public void TearDown()
        {
            AssetDatabase.DeleteAsset(_folder);
        }

        private static JObject Contract() =>
            JObject.Parse(File.ReadAllText(Path.Combine(Application.dataPath, "Tests/EditMode/Tools/asset-move-contract.json")));

        private static void AssertResult(JObject result, JToken contract)
        {
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            foreach (var field in ((JObject)contract["result"]).Properties())
                Assert.That(result["data"]?[field.Name]?.Type.ToString().ToLowerInvariant(), Is.EqualTo((string)field.Value), field.Name);
        }

        [Test]
        public async Task FocusedPreviewThenMove_PreservesThePreviewedGuid()
        {
            using var tools = MCPForUnityTests.Editor.Services.NativeProgressProbe.EnableTools("asset_reference_diagnostics", "asset_edit");
            CommandRegistry.Initialize();
            var contract = Contract();
            string guid = AssetDatabase.AssetPathToGUID(_source);
            var arguments = new JObject
            {
                ["action"] = contract["preview"]["action"],
                ["path"] = _source,
                ["destination"] = _destination,
                ["scanRoot"] = _folder,
            };
            var preview = JObject.FromObject(await CommandRegistry.InvokeCommandAsync((string)contract["preview"]["tool"], arguments));
            AssertResult(preview, contract["preview"]);
            Assert.IsTrue((bool)preview["data"]["scanComplete"], preview.ToString());
            Assert.AreEqual(guid, (string)preview["data"]["guid"]);
            Assert.AreEqual(_source, (string)preview["data"]["path"]);
            Assert.AreEqual(_destination, (string)preview["data"]["destination"]);
            Assert.IsTrue(File.Exists(_source));
            Assert.IsFalse(File.Exists(_destination));

            arguments["action"] = contract["move"]["action"];
            arguments["referenceRevision"] = preview["data"]["referenceRevision"];
            var moved = JObject.FromObject(await CommandRegistry.InvokeCommandAsync((string)contract["move"]["tool"], arguments));
            AssertResult(moved, contract["move"]);
            Assert.AreEqual(_destination, (string)moved["data"]["path"]);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(_destination));
            Assert.IsFalse(File.Exists(_source));
        }

        [Test]
        public async Task FocusedMove_AgreesWithPublicInputCases()
        {
            using var tools = MCPForUnityTests.Editor.Services.NativeProgressProbe.EnableTools("asset_edit");
            CommandRegistry.Initialize();
            var contract = Contract();
            foreach (var row in contract["inputs"])
            {
                string id = (string)row["id"];
                // The public adapter drops omitted and explicit-null optional parameters.
                bool valid = (bool?)row["nativeValid"] ?? (bool)row["valid"];
                var arguments = (JObject)row["arguments"].DeepClone();
                if (arguments["path"]?.Type == JTokenType.String && (string)arguments["path"] == "SRC")
                    arguments["path"] = _source;
                if (arguments["destination"]?.Type == JTokenType.String && (string)arguments["destination"] == "DST")
                    arguments["destination"] = _destination;
                if (arguments.TryGetValue("reference_revision", out var revision))
                {
                    arguments.Remove("reference_revision");
                    arguments["referenceRevision"] = revision;
                }
                var result = JObject.FromObject(await CommandRegistry.InvokeCommandAsync((string)contract["move"]["tool"], arguments));
                Assert.AreEqual(valid, result.Value<bool>("success"), id + ": " + result);
                Assert.AreEqual(!valid, File.Exists(_source), id);
                Assert.AreEqual(valid, File.Exists(_destination), id);
                if (valid)
                {
                    AssertResult(result, contract["move"]);
                    Assert.AreEqual(string.Empty, AssetDatabase.MoveAsset(_destination, _source), id);
                }
            }
        }

        [Test]
        public void DecodeMove_DistinguishesWrongTypeFromAbsentAndNullRequiredValues()
        {
            Assert.IsNull(AssetMoveRequest.Decode(new JObject { ["path"] = JValue.CreateNull() }, out var move));
            Assert.IsNull(move.Path);
            Assert.IsNull(move.Destination);
            Assert.IsFalse(AssetMoveRequest.Decode(new JObject { ["path"] = new JArray() }, out _).Success);
        }

        [TestCase("move")]
        [TestCase("rename")]
        public void MoveOrRename_ReportsSuccessAndPreservesGuid(string action)
        {
            string originalGuid = AssetDatabase.AssetPathToGUID(_source);

            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["path"] = _source,
                        ["destination"] = _destination,
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response["error"]?.ToString());
            Assert.IsFalse(File.Exists(_source));
            Assert.AreEqual("move fixture", File.ReadAllText(_destination));
            Assert.AreEqual(originalGuid, AssetDatabase.AssetPathToGUID(_destination));
        }

        [Test]
        public void ExistingDestination_ReturnsErrorAndPreservesBothAssets()
        {
            File.WriteAllText(_destination, "destination fixture");
            AssetDatabase.ImportAsset(_destination, ImportAssetOptions.ForceSynchronousImport);

            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "move",
                        ["path"] = _source,
                        ["destination"] = _destination,
                    }
                )
            );

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("move fixture", File.ReadAllText(_source));
            Assert.AreEqual("destination fixture", File.ReadAllText(_destination));
        }
    }
}
