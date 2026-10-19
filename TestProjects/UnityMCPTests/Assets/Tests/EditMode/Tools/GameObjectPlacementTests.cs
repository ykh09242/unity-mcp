using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    public class GameObjectPlacementTests
    {
        private readonly List<GameObject> owned = new List<GameObject>();
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene scene;
        private GameObject parent;
        private GameObject target;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            scene = sceneFixture.Create("McpPlacement_", Guid.NewGuid().ToString("N"));
            parent = Make("Parent");
            parent.transform.SetPositionAndRotation(new Vector3(10, 20, 30), Quaternion.Euler(25, 60, 15));
            parent.transform.localScale = new Vector3(2, 3, 4);
            target = Make("Target");
            target.transform.localPosition = new Vector3(2, 3, 4);
            target.transform.localRotation = Quaternion.Euler(10, 20, 30);
        }

        private GameObject Make(string name)
        {
            var go = new GameObject("Placement_" + name + "_" + Guid.NewGuid().ToString("N"));
            owned.Add(go);
            return go;
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var go in owned)
            {
                if (go == null)
                    continue;
                Undo.ClearUndo(go.transform);
                Undo.ClearUndo(go);
                UnityEngine.Object.DestroyImmediate(go);
            }
            owned.Clear();
            sceneFixture.Close();
        }

        private JObject Modify(JObject options)
        {
            options["action"] = "modify";
            options["target"] = target.GetInstanceIDCompat();
            return Send(options);
        }

        private static JObject Send(JObject options) => JObject.FromObject(ManageGameObject.HandleCommand(options));

        private static void Success(JObject response) => Assert.IsTrue(response.Value<bool>("success"), response.ToString());

        private static void Near(Vector3 expected, Vector3 actual) => Assert.Less(Vector3.Distance(expected, actual), 0.0001f);

        [TestCase("local")]
        [TestCase("world")]
        public void ExplicitSpace_ModifyUnderRotatedNonuniformParent(string space)
        {
            // Given a transformed parent.
            target.transform.SetParent(parent.transform, false);
            var requested = new Vector3(4, 5, 6);
            // When position and rotation explicitly select their coordinate system.
            Success(
                Modify(
                    new JObject
                    {
                        ["space"] = space,
                        ["position"] = new JArray(4, 5, 6),
                        ["rotation"] = new JArray(0, 90, 0),
                    }
                )
            );
            // Then Unity's exact local/world properties carry the requested values.
            Near(requested, space == "world" ? target.transform.position : target.transform.localPosition);
            Assert.Less(Quaternion.Angle(Quaternion.Euler(0, 90, 0), space == "world" ? target.transform.rotation : target.transform.localRotation), 0.001f);
        }

        [TestCase("local")]
        [TestCase("world")]
        public void ExplicitSpace_CreateUnderRotatedNonuniformParent(string space)
        {
            string name = "Placement_Create_" + Guid.NewGuid().ToString("N");
            var response = Send(
                new JObject
                {
                    ["action"] = "create",
                    ["name"] = name,
                    ["parent"] = parent.GetInstanceIDCompat(),
                    ["space"] = space,
                    ["position"] = new JArray(4, 5, 6),
                    ["rotation"] = new JArray(0, 90, 0),
                }
            );
            var created = GameObject.Find(name);
            if (created != null)
                owned.Add(created);
            Success(response);
            Near(new Vector3(4, 5, 6), space == "world" ? created.transform.position : created.transform.localPosition);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void Reparent_PreservesRequestedCoordinatesAndUndo(bool preserveWorld)
        {
            Vector3 before = preserveWorld ? target.transform.position : target.transform.localPosition;
            Quaternion rotation = preserveWorld ? target.transform.rotation : target.transform.localRotation;
            Vector3 scale = target.transform.localScale;
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Success(Modify(new JObject { ["parent"] = parent.GetInstanceIDCompat(), ["world_position_stays"] = preserveWorld }));
            Near(before, preserveWorld ? target.transform.position : target.transform.localPosition);
            Assert.Less(Quaternion.Angle(rotation, preserveWorld ? target.transform.rotation : target.transform.localRotation), 0.001f);
            if (!preserveWorld)
                Near(scale, target.transform.localScale);
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(group);
            Undo.PerformUndo();
            Assert.IsNull(target.transform.parent);
            Near(new Vector3(2, 3, 4), target.transform.localPosition);
        }

        [TestCase("detach")]
        [TestCase("null")]
        [TestCase("empty")]
        public void Detach_PreservesWorldByDefault(string form)
        {
            target.transform.SetParent(parent.transform, false);
            Vector3 before = target.transform.position;
            var options = new JObject();
            if (form == "detach")
                options["detach"] = true;
            else
                options["parent"] = form == "null" ? JValue.CreateNull() : new JValue("");
            Success(Modify(options));
            Assert.IsNull(target.transform.parent);
            Near(before, target.transform.position);
        }

        [TestCase("world_scale")]
        [TestCase("sibling")]
        [TestCase("detach_conflict")]
        [TestCase("space")]
        public void InvalidTransform_RejectsBeforeRenameOrReparent(string invalid)
        {
            string before = target.name;
            var options = new JObject { ["name"] = "MustNotChange", ["parent"] = parent.GetInstanceIDCompat() };
            if (invalid == "world_scale")
            {
                options["space"] = "world";
                options["scale"] = new JArray(1, 1, 1);
            }
            if (invalid == "sibling")
                options["sibling_index"] = 100;
            if (invalid == "detach_conflict")
                options["detach"] = true;
            if (invalid == "space")
                options["space"] = "screen";
            Assert.IsFalse(Modify(options).Value<bool>("success"));
            Assert.AreEqual(before, target.name);
            Assert.IsNull(target.transform.parent);
        }

        [Test]
        public void SiblingOrder_UsesFinalDestinationIndexAndUndo()
        {
            var first = Make("First");
            first.transform.SetParent(parent.transform, false);
            target.transform.SetParent(parent.transform, false);
            Undo.IncrementCurrentGroup();
            Success(Modify(new JObject { ["sibling_index"] = 0 }));
            Assert.AreEqual(0, target.transform.GetSiblingIndex());
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.AreEqual(1, target.transform.GetSiblingIndex());
        }

        private JObject Placement(GameObject middle, GameObject last, string mode = "distribute", string space = "world") =>
            new JObject
            {
                ["action"] = "placement",
                ["targets"] = new JArray(target.GetInstanceIDCompat(), middle.GetInstanceIDCompat(), last.GetInstanceIDCompat()),
                ["placement_mode"] = mode,
                ["axis"] = "x",
                ["space"] = space,
            };

        [TestCase("align", 2)]
        [TestCase("distribute", 6)]
        public void OrderedPlacement_PreviewsWithoutMutationThenAppliesExactValues(string mode, float expectedMiddle)
        {
            var middle = Make("Middle");
            var last = Make("Last");
            middle.transform.position = new Vector3(99, 4, 5);
            last.transform.position = new Vector3(10, 7, 8);
            var options = Placement(middle, last, mode);
            var preview = Send(options);
            Success(preview);
            Near(new Vector3(99, 4, 5), middle.transform.position);
            Assert.AreEqual(expectedMiddle, preview["data"]["placements"][1]["position"][0].Value<float>());
            options["preview"] = false;
            options["preview_token"] = preview["data"]["preview_token"];
            Success(Send(options));
            Near(new Vector3(expectedMiddle, 4, 5), middle.transform.position);
            Assert.IsFalse(Send(options).Value<bool>("success"), "A consumed preview cannot be applied again.");
        }

        [TestCase("target")]
        [TestCase("parent")]
        [TestCase("deleted")]
        [TestCase("order")]
        public void Placement_RefusesStaleStateBeforeAnyMutation(string change)
        {
            var middle = Make("Middle");
            var last = Make("Last");
            middle.transform.SetParent(parent.transform, false);
            middle.transform.localPosition = new Vector3(99, 4, 5);
            last.transform.position = new Vector3(10, 7, 8);
            var options = Placement(middle, last);
            var preview = Send(options);
            Success(preview);
            if (change == "target")
                last.transform.localScale = Vector3.one * 2;
            if (change == "parent")
                parent.transform.Rotate(0, 20, 0);
            if (change == "deleted")
                UnityEngine.Object.DestroyImmediate(last);
            if (change == "order")
                options["targets"] = new JArray(middle.GetInstanceIDCompat(), target.GetInstanceIDCompat(), last.GetInstanceIDCompat());
            Vector3 before = target.transform.position;
            Vector3 middleBefore = middle.transform.position;
            options["preview"] = false;
            options["preview_token"] = preview["data"]["preview_token"];
            Assert.IsFalse(Send(options).Value<bool>("success"));
            Near(before, target.transform.position);
            Near(middleBefore, middle.transform.position);
        }

        [Test]
        public void RelativeOffset_UsesRotatedNonuniformReferenceTransform()
        {
            Success(
                Send(
                    new JObject
                    {
                        ["action"] = "move_relative",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["reference_object"] = parent.GetInstanceIDCompat(),
                        ["offset"] = new JArray(1, 2, 3),
                        ["world_space"] = false,
                    }
                )
            );
            Near(parent.transform.TransformPoint(new Vector3(1, 2, 3)), target.transform.position);
        }

        [Test]
        public void LocalPlacement_PreservesOtherCoordinatesAndUndoesTogether()
        {
            var middle = Make("Middle");
            var last = Make("Last");
            foreach (var go in new[] { target, middle, last })
                go.transform.SetParent(parent.transform, false);
            target.transform.localPosition = new Vector3(10, 2, 3);
            middle.transform.localPosition = new Vector3(99, 4, 5);
            last.transform.localPosition = new Vector3(2, 7, 8);
            var options = Placement(middle, last, "distribute", "local");
            sceneFixture.ClearDirtiness();
            var preview = Send(options);
            Success(preview);
            Assert.IsFalse(scene.isDirty);
            Near(new Vector3(99, 4, 5), middle.transform.localPosition);
            options["preview"] = false;
            options["preview_token"] = preview["data"]["preview_token"];
            Success(Send(options));
            Near(new Vector3(6, 4, 5), middle.transform.localPosition);
            Undo.PerformUndo();
            Near(new Vector3(99, 4, 5), middle.transform.localPosition);
        }

        [TestCase("duplicate_alias")]
        [TestCase("ancestor")]
        [TestCase("ambiguous_name")]
        [TestCase("different_local_parents")]
        public void Placement_InvalidTargetSetDoesNotProducePreview(string invalid)
        {
            var middle = Make("Middle");
            var last = Make("Last");
            var options = Placement(middle, last);
            if (invalid == "duplicate_alias")
                options["targets"] = new JArray(target.name, target.GetInstanceIDCompat());
            if (invalid == "ancestor")
            {
                middle.transform.SetParent(target.transform);
                options["targets"] = new JArray(target.GetInstanceIDCompat(), middle.GetInstanceIDCompat());
            }
            if (invalid == "ambiguous_name")
            {
                last.name = middle.name;
                options["targets"] = new JArray(target.GetInstanceIDCompat(), middle.name);
            }
            if (invalid == "different_local_parents")
            {
                middle.transform.SetParent(parent.transform);
                options["space"] = "local";
            }
            Vector3 before = target.transform.position;
            var response = Send(options);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(response["data"]?["preview_token"]);
            Near(before, target.transform.position);
        }

        [TestCase(" true ")]
        [TestCase("1")]
        [TestCase("[]")]
        [TestCase("{}")]
        public void Placement_MalformedPreviewFlagRejectsWithoutMutation(string invalid)
        {
            var middle = Make("Middle");
            var last = Make("Last");
            var options = Placement(middle, last);
            options["preview"] = invalid == " true " ? new JValue(invalid) : JToken.Parse(invalid);
            var before = middle.transform.position;
            Assert.IsFalse(Send(options).Value<bool>("success"));
            Near(before, middle.transform.position);
        }

        [TestCase("null")]
        [TestCase("\"TrUe\"")]
        public void Placement_PreviewBooleanMatchesPolicyContract(string json)
        {
            var middle = Make("Middle");
            var last = Make("Last");
            var options = Placement(middle, last);
            options["preview"] = JToken.Parse(json);
            var response = Send(options);
            Success(response);
            Assert.IsTrue(response["data"].Value<bool>("preview"));
        }

        [TestCase("axis")]
        [TestCase("space")]
        [TestCase("placement_mode")]
        public void Placement_TokenBindsRequestedScope(string field)
        {
            var middle = Make("Middle");
            var last = Make("Last");
            var options = Placement(middle, last);
            var preview = Send(options);
            Success(preview);
            options[field] =
                field == "axis" ? "y"
                : field == "space" ? "local"
                : "align";
            options["preview"] = false;
            options["preview_token"] = preview["data"]["preview_token"];
            Assert.IsFalse(Send(options).Value<bool>("success"));
        }

        [Test]
        public void Placement_CacheEvictsOldestPreviewAtBound()
        {
            var middle = Make("Middle");
            var last = Make("Last");
            var options = Placement(middle, last);
            var first = Send(options);
            Success(first);
            for (int i = 0; i < 32; i++)
                Success(Send(options));
            options["preview"] = false;
            options["preview_token"] = first["data"]["preview_token"];
            Assert.IsFalse(Send(options).Value<bool>("success"));
        }

        [Test]
        public void Create_InvalidWorldScaleDoesNotAllocateObject()
        {
            string name = "Placement_Rejected_" + Guid.NewGuid().ToString("N");
            var response = Send(
                new JObject
                {
                    ["action"] = "create",
                    ["name"] = name,
                    ["space"] = "world",
                    ["scale"] = new JArray(1, 1, 1),
                }
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.IsNull(GameObject.Find(name));
        }

        [Test]
        public void Placement_UndoFlushCallbackCannotTurnPartialOutcomeIntoSuccess()
        {
            var middle = Make("Middle");
            var last = Make("Last");
            middle.transform.position = new Vector3(99, 4, 5);
            last.transform.position = new Vector3(10, 7, 8);
            var options = Placement(middle, last, "align");
            var preview = Send(options);
            Success(preview);
            options["preview"] = false;
            options["preview_token"] = preview["data"]["preview_token"];
            bool invoked = false;
            Undo.WillFlushUndoRecord callback = () =>
            {
                if (middle != null && last != null && Mathf.Abs(middle.transform.position.x - 2) < 0.001f && Mathf.Abs(last.transform.position.x - 2) < 0.001f)
                {
                    invoked = true;
                    middle.transform.position = new Vector3(77, 4, 5);
                }
            };
            Undo.willFlushUndoRecord += callback;
            JObject result;
            try
            {
                result = Send(options);
            }
            finally
            {
                Undo.willFlushUndoRecord -= callback;
            }
            Assert.IsTrue(invoked, "The native Undo flush callback must have changed a written target.");
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("changed_after_apply", result["data"]["outcomes"][1].Value<string>("status"));
            Assert.AreEqual(2, result["data"].Value<int>("applied_count"));
        }
    }
}
