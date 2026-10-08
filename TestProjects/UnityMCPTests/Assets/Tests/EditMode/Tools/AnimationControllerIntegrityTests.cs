using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Animation;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class AnimationControllerIntegrityTests
    {
        private string _root;
        private string _rootGuid;
        private string _path;
        private AnimatorController _controller;

        [SetUp]
        public void SetUp()
        {
            _root = null;
            _rootGuid = null;
            string folder = "__McpAnimationControllerIntegrity_" + Guid.NewGuid().ToString("N");
            string root = "Assets/" + folder;
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(root, AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.IsFalse(AssetDatabase.IsValidFolder(root));
            string guid = AssetDatabase.CreateFolder("Assets", folder);
            Assert.IsNotEmpty(guid);
            _root = root;
            _rootGuid = guid;
            Assert.AreEqual(_rootGuid, AssetDatabase.AssetPathToGUID(_root, AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.IsTrue(AssetDatabase.IsValidFolder(_root));
            _path = _root + "/Controller.controller";
            _controller = AnimatorController.CreateAnimatorControllerAtPath(_path);
            Assert.IsNotNull(_controller);
            var machine = _controller.layers[0].stateMachine;
            machine.defaultState = machine.AddState("From");
            machine.AddState("To");
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(_rootGuid) && AssetDatabase.AssetPathToGUID(_root, AssetPathToGUIDOptions.OnlyExistingAssets) == _rootGuid)
                AssetDatabase.DeleteAsset(_root);
        }

        private JObject Send(string action, JObject properties)
        {
            properties["controller_path"] = _path;
            return JObject.FromObject(ManageAnimation.HandleCommand(new JObject { ["action"] = "controller_" + action, ["properties"] = properties }));
        }

        private string Snapshot()
        {
            // Include every serialized subasset, including AnyState transitions that GetInfo omits.
            return new JObject
            {
                ["info"] = Send("get_info", new JObject()),
                ["guid"] = AssetDatabase.AssetPathToGUID(_path),
                ["dirtyCount"] = EditorUtility.GetDirtyCount(_controller),
                ["file"] = Convert.ToBase64String(File.ReadAllBytes(Path.Combine(Application.dataPath, _path.Substring("Assets/".Length)))),
                ["assets"] = new JArray(
                    AssetDatabase
                        .LoadAllAssetsAtPath(_path)
                        .OrderBy(asset => asset.GetInstanceIDCompat())
                        .Select(asset => new JObject { ["id"] = asset.GetInstanceIDCompat(), ["serialized"] = EditorJsonUtility.ToJson(asset) })
                ),
            }.ToString(Newtonsoft.Json.Formatting.None);
        }

        private void RejectWithoutMutation(string action, JObject properties, bool expectsErrorLog)
        {
            string before = Snapshot();
            if (expectsErrorLog)
                LogAssert.Expect(LogType.Error, new Regex(@"\[ManageAnimation\] Action 'controller_" + action + @"' failed:"));
            JObject response = Send(action, properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(before, Snapshot(), "Rejected input changed controller state, subassets, GUID or file bytes.");
        }

        [TestCase("speed")]
        [TestCase("is_default")]
        public void InvalidLateStateValueDoesNotCreateStateOrSubasset(string property)
        {
            RejectWithoutMutation("add_state", new JObject { ["state_name"] = "New", [property] = "bad" }, true);
        }

        [TestCase("Missing.anim")]
        [TestCase("../Missing.anim")]
        public void ExplicitUnresolvedClipDoesNotCreateState(string clip)
        {
            RejectWithoutMutation(
                "add_state",
                new JObject { ["state_name"] = "New", ["clip_path"] = clip.StartsWith("..") ? clip : _root + "/" + clip },
                false
            );
        }

        [TestCase("From")]
        [TestCase("AnyState")]
        public void InvalidTransitionValueDoesNotCreateTransitionSubasset(string from)
        {
            RejectWithoutMutation(
                "add_transition",
                new JObject
                {
                    ["from_state"] = from,
                    ["to_state"] = "To",
                    ["duration"] = "bad",
                },
                true
            );
        }

        [TestCase("From", "object")]
        [TestCase("From", "string")]
        [TestCase("From", "integer")]
        [TestCase("From", "boolean")]
        [TestCase("AnyState", "object")]
        [TestCase("AnyState", "string")]
        [TestCase("AnyState", "integer")]
        [TestCase("AnyState", "boolean")]
        public void InvalidConditionsContainerDoesNotCreateTransitionSubasset(string from, string shape)
        {
            JToken conditions = shape switch
            {
                "object" => new JObject(),
                "string" => new JValue("invalid"),
                "integer" => new JValue(1),
                "boolean" => new JValue(true),
                _ => throw new ArgumentOutOfRangeException(nameof(shape)),
            };
            RejectWithoutMutation(
                "add_transition",
                new JObject
                {
                    ["from_state"] = from,
                    ["to_state"] = "To",
                    ["conditions"] = conditions,
                },
                false
            );
        }

        [TestCase("From", "omitted")]
        [TestCase("From", "null")]
        [TestCase("From", "empty")]
        [TestCase("AnyState", "omitted")]
        [TestCase("AnyState", "null")]
        [TestCase("AnyState", "empty")]
        public void OptionalConditionsContainersKeepUnconditionalTransitionDefaults(string from, string shape)
        {
            var properties = new JObject { ["from_state"] = from, ["to_state"] = "To" };
            if (shape == "null")
                properties["conditions"] = JValue.CreateNull();
            else if (shape == "empty")
                properties["conditions"] = new JArray();
            JObject response = Send("add_transition", properties);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0, response["data"].Value<int>("conditionCount"));
            var machine = _controller.layers[0].stateMachine;
            var transition =
                from == "AnyState" ? machine.anyStateTransitions.Single() : machine.states.Single(s => s.state.name == from).state.transitions.Single();
            Assert.IsEmpty(transition.conditions);
            Assert.IsTrue(transition.hasExitTime);
            Assert.AreEqual(0.25f, transition.duration);
            Assert.AreEqual(0.75f, transition.exitTime);
        }

        [TestCase("From")]
        [TestCase("AnyState")]
        public void InvalidLaterConditionDoesNotCreatePartialTransition(string from)
        {
            RejectWithoutMutation(
                "add_transition",
                new JObject
                {
                    ["from_state"] = from,
                    ["to_state"] = "To",
                    ["conditions"] = new JArray(
                        new JObject { ["parameter"] = "First", ["threshold"] = 0 },
                        new JObject { ["parameter"] = "Later", ["threshold"] = "bad" }
                    ),
                },
                true
            );
        }

        [TestCase("float")]
        [TestCase("int")]
        [TestCase("bool")]
        public void InvalidParameterDefaultDoesNotAddParameter(string type)
        {
            RejectWithoutMutation(
                "add_parameter",
                new JObject
                {
                    ["parameter_name"] = "New",
                    ["parameter_type"] = type,
                    ["default_value"] = "bad",
                },
                true
            );
        }

        [Test]
        public void NullNumericParameterDefaultRemainsRejectedWithoutAddingParameter()
        {
            RejectWithoutMutation("add_parameter", new JObject { ["parameter_name"] = "New", ["default_value"] = JValue.CreateNull() }, true);
        }

        [Test]
        public void ExistingNonControllerDestinationPreservesObjectGuidAndContents()
        {
            string collision = _root + "/Collision.controller";
            AssetDatabase.CreateFolder(_root, "Collision.controller");
            var existing = AssetDatabase.LoadMainAssetAtPath(collision);
            string guid = AssetDatabase.AssetPathToGUID(collision);
            Assert.IsNotNull(existing);
            Assert.IsFalse(existing is AnimatorController);
            JObject response = JObject.FromObject(
                ManageAnimation.HandleCommand(new JObject { ["action"] = "controller_create", ["controller_path"] = collision })
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(existing, AssetDatabase.LoadMainAssetAtPath(collision));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(collision));
            Assert.IsTrue(AssetDatabase.IsValidFolder(collision));
        }

        [Test]
        public void NewControllerPersistsAtRequestedPathWithBaseLayer()
        {
            string path = _root + "/New.controller";
            JObject response = JObject.FromObject(ManageAnimation.HandleCommand(new JObject { ["action"] = "controller_create", ["controller_path"] = path }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(path);
            Assert.IsNotNull(controller);
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(controller));
            Assert.AreEqual(1, controller.layers.Length);
            Assert.IsTrue(AssetDatabase.Contains(controller.layers[0].stateMachine));
        }

        [Test]
        public void SerializedPropertiesRetainClipZeroSpeedAndFalseDefault()
        {
            string clipPath = _root + "/Motion.anim";
            var clip = new AnimationClip { name = "Motion" };
            AssetDatabase.CreateAsset(clip, clipPath);
            var originalDefault = _controller.layers[0].stateMachine.defaultState;
            var properties = new JObject
            {
                ["controller_path"] = _path,
                ["state_name"] = "New",
                ["clip_path"] = clipPath,
                ["speed"] = "0",
                ["is_default"] = false,
            };
            JObject response = JObject.FromObject(
                ManageAnimation.HandleCommand(new JObject { ["action"] = "controller_add_state", ["properties"] = properties.ToString() })
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var state = _controller.layers[0].stateMachine.states.Single(s => s.state.name == "New").state;
            Assert.AreSame(clip, state.motion);
            Assert.AreEqual(0f, state.speed);
            Assert.AreSame(originalDefault, _controller.layers[0].stateMachine.defaultState);
        }

        [Test]
        public void DeletedControllerDestinationCanBeRecreated()
        {
            string path = _root + "/Recreated.controller";
            Assert.IsNotNull(AnimatorController.CreateAnimatorControllerAtPath(path));
            AssetDatabase.SaveAssets();
            Assert.IsTrue(AssetDatabase.DeleteAsset(path));
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(path));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
            JObject response = JObject.FromObject(ManageAnimation.HandleCommand(new JObject { ["action"] = "controller_create", ["controller_path"] = path }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<AnimatorController>(path));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OmittedAndNullClipKeepOptionalStateDefaults(bool explicitNull)
        {
            var properties = new JObject { ["state_name"] = "New" };
            if (explicitNull)
                properties["clip_path"] = JValue.CreateNull();
            JObject response = Send("add_state", properties);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var state = _controller.layers[0].stateMachine.states.Single(s => s.state.name == "New").state;
            Assert.IsNull(state.motion);
            Assert.AreEqual(1f, state.speed);
        }

        [TestCase("float")]
        [TestCase("integer")]
        [TestCase("boolean")]
        public void ValidZeroAndFalseParameterDefaultsArePreserved(string type)
        {
            JObject response = Send(
                "add_parameter",
                new JObject
                {
                    ["parameter_name"] = "New",
                    ["parameter_type"] = type,
                    ["default_value"] = type == "boolean" ? (JToken)false : (JToken)"0",
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var parameter = _controller.parameters.Single();
            Assert.AreEqual(0f, parameter.defaultFloat);
            Assert.AreEqual(0, parameter.defaultInt);
            Assert.IsFalse(parameter.defaultBool);
        }

        [Test]
        public void TransitionDefaultsAndIgnoredConditionEntriesRemainCompatible()
        {
            JObject response = Send(
                "add_transition",
                new JObject
                {
                    ["from_state"] = "Any",
                    ["to_state"] = "To",
                    ["has_exit_time"] = false,
                    ["duration"] = "0",
                    ["conditions"] = new JArray(
                        1,
                        new JObject(),
                        new JObject
                        {
                            ["parameter"] = "Speed",
                            ["mode"] = "unknown",
                            ["threshold"] = "-2",
                        }
                    ),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var transition = _controller.layers[0].stateMachine.anyStateTransitions.Single();
            Assert.IsFalse(transition.hasExitTime);
            Assert.AreEqual(0f, transition.duration);
            Assert.AreEqual(0.75f, transition.exitTime);
            Assert.AreEqual(AnimatorConditionMode.Greater, transition.conditions.Single().mode);
            Assert.AreEqual(-2f, transition.conditions[0].threshold);
        }

        [TestCase("Additive", AnimatorLayerBlendingMode.Additive)]
        [TestCase("unknown", AnimatorLayerBlendingMode.Override)]
        [TestCase("", AnimatorLayerBlendingMode.Override)]
        public void LayerModeCompatibilityAndZeroWeightRemainUnchanged(string mode, AnimatorLayerBlendingMode expected)
        {
            JObject response = Send(
                "add_layer",
                new JObject
                {
                    ["layer_name"] = "Extra",
                    ["blending_mode"] = mode,
                    ["weight"] = 0,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var layer = _controller.layers.Last();
            Assert.AreEqual(expected, layer.blendingMode);
            Assert.AreEqual(0f, layer.defaultWeight);
        }

        [Test]
        public void RemovingBaseLayerRemainsRejectedWithoutMutations()
        {
            RejectWithoutMutation("remove_layer", new JObject { ["layer_index"] = 0 }, false);
        }

        [TestCase("create_blend_tree_1d")]
        [TestCase("create_blend_tree_2d")]
        public void BlendTreeCreationWithExistingOrdinaryStateNameDoesNotMutateController(string action)
        {
            RejectWithoutMutation(
                action,
                new JObject
                {
                    ["state_name"] = "From",
                    ["blend_parameter"] = "Speed",
                    ["blend_parameter_x"] = "Speed",
                    ["blend_parameter_y"] = "Turn",
                },
                false
            );
        }

        [TestCase("create_blend_tree_1d", "Simple1D")]
        [TestCase("create_blend_tree_2d", "SimpleDirectional2D")]
        public void RepeatedBlendTreeCreationPreservesOriginalStateAndMotion(string action, string blendType)
        {
            var properties = new JObject
            {
                ["state_name"] = "Move",
                ["blend_parameter"] = "Speed",
                ["blend_parameter_x"] = "Speed",
                ["blend_parameter_y"] = "Turn",
            };
            JObject response = Send(action, properties);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var state = _controller.layers[0].stateMachine.states.Single(s => s.state.name == "Move").state;
            var motion = state.motion as BlendTree;
            Assert.IsNotNull(motion);
            Assert.AreEqual(state.name, response["data"].Value<string>("stateName"));
            Assert.AreEqual(blendType, motion.blendType.ToString());
            Assert.AreEqual("Speed", motion.blendParameter);
            if (action == "create_blend_tree_2d")
                Assert.AreEqual("Turn", motion.blendParameterY);

            RejectWithoutMutation(action, properties, false);
            Assert.AreSame(motion, state.motion);
            Assert.AreEqual(3, _controller.layers[0].stateMachine.states.Length);
        }

        [TestCase(1)]
        [TestCase(4)]
        [TestCase(16)]
        public void ControllerInfoPreservesLayerAndGraphCountsAndReadsChangesOnNextCall(int layerCount)
        {
            for (int i = 1; i < layerCount; i++)
            {
                _controller.AddLayer("Layer" + i);
                _controller.layers[i].stateMachine.AddState("Aux");
            }
            _controller.AddParameter("Speed", AnimatorControllerParameterType.Float);
            var machine = _controller.layers[0].stateMachine;
            var transition = machine.defaultState.AddTransition(machine.states.Single(s => s.state.name == "To").state);
            transition.AddCondition(AnimatorConditionMode.Greater, 2f, "Speed");
            AssetDatabase.SaveAssets();

            string before = Snapshot();
            JObject response = Send("get_info", new JObject());
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(layerCount, response["data"].Value<int>("layerCount"));
            Assert.AreEqual(1, response["data"].Value<int>("parameterCount"));
            Assert.AreEqual(layerCount, response["data"]["layers"].Count());
            var firstLayer = response["data"]["layers"][0];
            Assert.AreEqual(2, firstLayer.Value<int>("stateCount"));
            var from = firstLayer["states"].Single(s => s.Value<string>("name") == "From");
            Assert.AreEqual(1, from.Value<int>("transitionCount"));
            Assert.AreEqual(1, from["transitions"][0].Value<int>("conditionCount"));
            Assert.AreEqual(2f, from["transitions"][0]["conditions"][0].Value<float>("threshold"));
            for (int i = 1; i < layerCount; i++)
            {
                Assert.AreEqual("Layer" + i, response["data"]["layers"][i].Value<string>("name"));
                Assert.AreEqual(1, response["data"]["layers"][i].Value<int>("stateCount"));
            }
            Assert.AreEqual(before, Snapshot());

            machine.AddState("Later");
            _controller.AddParameter("Later", AnimatorControllerParameterType.Bool);
            AssetDatabase.SaveAssets();
            before = Snapshot();
            JObject next = Send("get_info", new JObject());
            Assert.AreEqual(3, next["data"]["layers"][0].Value<int>("stateCount"));
            Assert.AreEqual(2, next["data"].Value<int>("parameterCount"));
            Assert.AreEqual(before, Snapshot(), "Controller info must remain a read after graph changes.");
        }
    }
}
