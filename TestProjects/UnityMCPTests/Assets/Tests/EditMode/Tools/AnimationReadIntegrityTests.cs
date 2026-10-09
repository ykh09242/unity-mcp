using System;
using System.Globalization;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Tools.Animation;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class AnimationReadIntegrityTests
    {
        private string _root;
        private string _rootGuid;
        private string _path;
        private AnimatorController _controller;
        private GameObject _object;
        private Animator _animator;
        private AnimatorOverrideController _override;

        [SetUp]
        public void SetUp()
        {
            if (Application.isPlaying)
                Assert.Ignore("These read-default controls require Edit mode.");
            _root = null;
            _rootGuid = null;
            string folder = "__McpAnimationReadIntegrity_" + Guid.NewGuid().ToString("N");
            string root = "Assets/" + folder;
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(root, AssetPathToGUIDOptions.OnlyExistingAssets));
            string guid = AssetDatabase.CreateFolder("Assets", folder);
            Assert.IsNotEmpty(guid);
            _root = root;
            _rootGuid = guid;
            _path = _root + "/Controller.controller";
            _controller = AnimatorController.CreateAnimatorControllerAtPath(_path);
            Assert.IsNotNull(_controller);
            _controller.parameters = new[]
            {
                new AnimatorControllerParameter
                {
                    name = "Float",
                    type = AnimatorControllerParameterType.Float,
                    defaultFloat = 3.5f,
                },
                new AnimatorControllerParameter
                {
                    name = "Int",
                    type = AnimatorControllerParameterType.Int,
                    defaultInt = -7,
                },
                new AnimatorControllerParameter
                {
                    name = "Bool",
                    type = AnimatorControllerParameterType.Bool,
                    defaultBool = true,
                },
            };
            AssetDatabase.SaveAssets();
            _object = new GameObject("AnimationReadIntegrity_" + Guid.NewGuid().ToString("N"));
            _animator = _object.AddComponent<Animator>();
            _animator.enabled = false;
            _animator.runtimeAnimatorController = _controller;
            // No Rebind, Update, Play or graph setup: defaults must be readable as asset metadata.
        }

        [TearDown]
        public void TearDown()
        {
            if (_object != null)
                UnityEngine.Object.DestroyImmediate(_object);
            if (_override != null)
                UnityEngine.Object.DestroyImmediate(_override);
            if (!string.IsNullOrEmpty(_rootGuid) && AssetDatabase.AssetPathToGUID(_root, AssetPathToGUIDOptions.OnlyExistingAssets) == _rootGuid)
                AssetDatabase.DeleteAsset(_root);
        }

        private JObject Send(string action, string parameter = null)
        {
            var request = new JObject
            {
                ["action"] = "animator_" + action,
                ["target"] = _object.GetInstanceIDCompat().ToString(CultureInfo.InvariantCulture),
                ["search_method"] = "by_id",
            };
            if (parameter != null)
                request["properties"] = new JObject { ["parameter_name"] = parameter };
            return JObject.FromObject(ManageAnimation.HandleCommand(request));
        }

        private void AssignOverride()
        {
            _override = new AnimatorOverrideController(_controller) { name = "OwnedOverride" };
            _animator.runtimeAnimatorController = _override;
        }

        private string Snapshot()
        {
            return new JObject
            {
                ["controller"] = EditorJsonUtility.ToJson(_controller),
                ["animator"] = EditorJsonUtility.ToJson(_animator),
                ["controllerDirty"] = EditorUtility.GetDirtyCount(_controller),
                ["animatorDirty"] = EditorUtility.GetDirtyCount(_animator),
                ["isInitialized"] = _animator.isInitialized,
                ["controllerGuid"] = AssetDatabase.AssetPathToGUID(_path),
                ["bytes"] = Convert.ToBase64String(File.ReadAllBytes(Path.Combine(Application.dataPath, _path.Substring("Assets/".Length)))),
            }.ToString(Newtonsoft.Json.Formatting.None);
        }

        private Command ReadRequest(JToken name, bool encoded)
        {
            var properties = new JObject { ["parameter_name"] = name };
            var request = new JObject
            {
                ["action"] = "animator_get_parameter",
                ["target"] = _object.GetInstanceIDCompat().ToString(CultureInfo.InvariantCulture),
                ["search_method"] = "by_id",
                ["properties"] = encoded ? (JToken)new JValue(properties.ToString(Formatting.None)) : properties,
            };
            return JsonConvert.DeserializeObject<Command>(JsonConvert.SerializeObject(new Command { type = "manage_animation", @params = request }));
        }

        private void AddNamedParameter(string name)
        {
            _controller.parameters = _controller
                .parameters.Concat(
                    new[]
                    {
                        new AnimatorControllerParameter
                        {
                            name = name,
                            type = AnimatorControllerParameterType.Float,
                            defaultFloat = 42f,
                        },
                    }
                )
                .ToArray();
            AssetDatabase.SaveAssets();
        }

        [TestCase("1", false)]
        [TestCase("true", false)]
        [TestCase("[]", false)]
        [TestCase("{}", false)]
        [TestCase("1", true)]
        [TestCase("true", true)]
        [TestCase("[]", true)]
        [TestCase("{}", true)]
        public void RawNameCannotReadAParameterWithItsStringifiedName(string raw, bool encoded)
        {
            JToken token = JToken.Parse(raw);
            AddNamedParameter(token.ToString());
            if (encoded)
                AssignOverride();
            string before = Snapshot();
            Command command = ReadRequest(token, encoded);

            JObject result = JObject.FromObject(ManageAnimation.HandleCommand(command.@params));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("'parameterName' must be a string", result.Value<string>("error"));
            Assert.AreEqual(before, Snapshot());
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("1")]
        [TestCase("True")]
        [TestCase("[]")]
        [TestCase("{}")]
        public void LiteralStringNamesStillReadTheirExactParameter(string name)
        {
            AddNamedParameter(name);
            string before = Snapshot();

            JObject result = Send("get_parameter", name);

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(name, result["data"].Value<string>("name"));
            Assert.AreEqual(42f, result["data"].Value<float>("value"));
            Assert.AreEqual(before, Snapshot());
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CommandDateStringRetainsItsExistingNameRepresentation(bool useOverride)
        {
            Command command = ReadRequest(new JValue("2026-10-09T12:00:00Z"), false);
            JToken name = command.@params["properties"]["parameter_name"];
            Assert.AreEqual(JTokenType.Date, name.Type);
            string legacyName = name.ToString();
            AddNamedParameter(legacyName);
            if (useOverride)
                AssignOverride();
            string before = Snapshot();

            JObject result = JObject.FromObject(ManageAnimation.HandleCommand(command.@params));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(legacyName, result["data"].Value<string>("name"));
            Assert.AreEqual(42f, result["data"].Value<float>("value"));
            Assert.AreEqual(before, Snapshot());
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("Float", false)]
        [TestCase("Int", false)]
        [TestCase("Bool", false)]
        [TestCase("Float", true)]
        [TestCase("Int", true)]
        [TestCase("Bool", true)]
        public void DisabledAnimatorReadsControllerDefaultsWithoutChangingGraphOrAsset(string parameter, bool useOverride)
        {
            if (useOverride)
                AssignOverride();
            string before = Snapshot();
            JObject response = Send("get_parameter", parameter);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(parameter, response["data"].Value<string>("name"));
            if (parameter == "Float")
                Assert.AreEqual(3.5f, response["data"].Value<float>("value"));
            if (parameter == "Int")
                Assert.AreEqual(-7, response["data"].Value<int>("value"));
            if (parameter == "Bool")
                Assert.IsTrue(response["data"].Value<bool>("value"));
            CollectionAssert.AreEqual(new[] { "name", "type", "value" }, ((JObject)response["data"]).Properties().Select(p => p.Name).ToArray());
            Assert.AreEqual(before, Snapshot(), "A read changed graph state, asset defaults, dirtiness or bytes.");
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("Float")]
        [TestCase("Bool")]
        public void ZeroAndFalseDefaultsRemainExact(string parameter)
        {
            var definitions = _controller.parameters;
            definitions.Single(p => p.name == "Float").defaultFloat = 0f;
            definitions.Single(p => p.name == "Bool").defaultBool = false;
            _controller.parameters = definitions;
            AssetDatabase.SaveAssets();
            string before = Snapshot();
            JObject response = Send("get_parameter", parameter);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            if (parameter == "Float")
                Assert.AreEqual(0f, response["data"].Value<float>("value"));
            else
                Assert.IsFalse(response["data"].Value<bool>("value"));
            Assert.AreEqual(before, Snapshot());
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InfoUsesAssetDefinitionsAndPreservesAssignedControllerIdentity(bool useOverride)
        {
            if (useOverride)
                AssignOverride();
            string before = Snapshot();
            JObject response = Send("get_info");
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(3, response["data"].Value<int>("parameterCount"));
            Assert.AreEqual(3, response["data"]["parameters"].Count());
            Assert.AreEqual(useOverride ? "OwnedOverride" : _controller.name, response["data"].Value<string>("controllerName"));
            Assert.IsTrue(response["data"]["parameters"][2].Value<bool>("defaultBool"));
            Assert.AreEqual(before, Snapshot());
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void MissingParameterRemainsReadOnlyFailure()
        {
            string before = Snapshot();
            JObject response = Send("get_parameter", "Missing");
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(before, Snapshot());
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void ControllerlessAnimatorHasNoParameterDefinitions()
        {
            _animator.runtimeAnimatorController = null;
            string before = Snapshot();
            JObject response = Send("get_parameter", "Float");
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(before, Snapshot());
            LogAssert.NoUnexpectedReceived();
        }
    }
}
