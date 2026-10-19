using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Tools.Physics;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Physics3D = UnityEngine.Physics;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class PhysicsPairDiagnosticsTests
    {
        private readonly PrefabTestSceneFixture fixture = new PrefabTestSceneFixture();
        private readonly List<GameObject> owned = new List<GameObject>();
        private readonly List<Scene> extraScenes = new List<Scene>();
        private GameObject first;
        private GameObject second;
        private Scene scene;
        private bool ignored3D;
        private bool ignored2D;

        [OneTimeSetUp]
        public void OneTimeSetUp() => fixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => fixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            scene = fixture.Create("McpPhysicsPair_", Guid.NewGuid().ToString("N"));
            ignored3D = Physics3D.GetIgnoreLayerCollision(30, 31);
            ignored2D = Physics2D.GetIgnoreLayerCollision(30, 31);
            Physics3D.IgnoreLayerCollision(30, 31, false);
            Physics2D.IgnoreLayerCollision(30, 31, false);
            first = Make("First", 30);
            second = Make("Second", 31);
            second.transform.position = Vector3.right * 0.75f;
        }

        private GameObject Make(string label, int layer)
        {
            var go = new GameObject("McpPhysicsPair_" + label + "_" + Guid.NewGuid().ToString("N"));
            go.layer = layer;
            owned.Add(go);
            return go;
        }

        private Scene CreateIsolatedPreview(string dimension)
        {
            var preview = EditorSceneManager.NewPreviewScene();
            extraScenes.Add(preview);
            Assert.IsTrue(EditorSceneManager.IsPreviewScene(preview));
            if (dimension == "2d")
            {
                Assert.IsTrue(preview.GetPhysicsScene2D().IsValid());
                Assert.AreNotEqual(Physics2D.defaultPhysicsScene, preview.GetPhysicsScene2D(), "Never simulate the default 2D world from this fixture.");
            }
            else
            {
                Assert.IsTrue(preview.GetPhysicsScene().IsValid());
                Assert.AreNotEqual(Physics3D.defaultPhysicsScene, preview.GetPhysicsScene(), "Never simulate the default 3D world from this fixture.");
            }
            return preview;
        }

        private void AddPair(string dimension, bool bodies = true)
        {
            foreach (var go in new[] { first, second })
            {
                if (dimension == "2d")
                {
                    go.AddComponent<BoxCollider2D>();
                    if (bodies)
                        go.AddComponent<Rigidbody2D>().gravityScale = 0;
                }
                else
                {
                    go.AddComponent<BoxCollider>();
                    if (bodies)
                        go.AddComponent<Rigidbody>().useGravity = false;
                }
            }
        }

        private JObject Request(string dimension = "3d") =>
            new JObject
            {
                ["action"] = "diagnose_pair",
                ["target"] = first.GetInstanceIDCompat(),
                ["other_target"] = second.GetInstanceIDCompat(),
                ["dimension"] = dimension,
            };

        private static JObject Call(JObject options) => JObject.FromObject(ManagePhysics.HandleCommand(options));

        private static void Success(JObject response) => Assert.IsTrue(response.Value<bool>("success"), response.ToString());

        private static string[] Blockers(JObject response) => response["data"]["blocking_reasons"].Select(row => row.Value<string>("code")).ToArray();

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (first != null && second != null && first.GetComponent<Collider>() != null && second.GetComponent<Collider>() != null)
                {
                    var options = Request();
                    options["observe_contacts"] = false;
                    Call(options);
                }
                foreach (var go in owned)
                    if (go != null)
                        UnityEngine.Object.DestroyImmediate(go);
                owned.Clear();
                foreach (Scene extra in extraScenes)
                    if (extra.IsValid() && extra.isLoaded)
                        EditorSceneManager.ClosePreviewScene(extra);
                extraScenes.Clear();
                fixture.Close();
            }
            finally
            {
                Physics3D.IgnoreLayerCollision(30, 31, ignored3D);
                Physics2D.IgnoreLayerCollision(30, 31, ignored2D);
            }
        }

        [TestCase("3d")]
        [TestCase("2d")]
        public void LayerIgnore_IsReportedWithoutRepairOrSimulation(string dimension)
        {
            AddPair(dimension);
            if (dimension == "2d")
                Physics2D.IgnoreLayerCollision(30, 31, true);
            else
                Physics3D.IgnoreLayerCollision(30, 31, true);
            fixture.ClearDirtiness();
            Vector3 before = second.transform.position;
            var response = Call(Request(dimension));
            Success(response);
            Assert.IsTrue(response["data"]["configuration"].Value<bool>("layer_ignored"));
            CollectionAssert.Contains(Blockers(response), "layer_collision_disabled");
            Assert.AreEqual(before, second.transform.position);
            Assert.IsFalse(scene.isDirty);
            Assert.IsTrue(dimension == "2d" ? Physics2D.GetIgnoreLayerCollision(30, 31) : Physics3D.GetIgnoreLayerCollision(30, 31));
        }

        [TestCase("3d")]
        [TestCase("2d")]
        public void PerColliderIgnore_IsIndependentOfLayerMatrix(string dimension)
        {
            AddPair(dimension);
            if (dimension == "2d")
                Physics2D.IgnoreCollision(first.GetComponent<Collider2D>(), second.GetComponent<Collider2D>(), true);
            else
                Physics3D.IgnoreCollision(first.GetComponent<Collider>(), second.GetComponent<Collider>(), true);
            var response = Call(Request(dimension));
            Success(response);
            Assert.IsTrue(response["data"]["configuration"].Value<bool>("pair_ignored"));
            Assert.IsFalse(response["data"]["configuration"].Value<bool>("layer_ignored"));
            CollectionAssert.Contains(Blockers(response), "pair_collision_ignored");
        }

        [TestCase("3d", false)]
        [TestCase("3d", true)]
        [TestCase("2d", false)]
        [TestCase("2d", true)]
        public void DisabledOrInactiveCollider_HasExplicitBlockingEvidence(string dimension, bool inactive)
        {
            AddPair(dimension);
            if (inactive)
                first.SetActive(false);
            else if (dimension == "2d")
                first.GetComponent<Collider2D>().enabled = false;
            else
                first.GetComponent<Collider>().enabled = false;
            var response = Call(Request(dimension));
            Success(response);
            CollectionAssert.Contains(Blockers(response), inactive ? "first_inactive" : "first_collider_disabled");
        }

        [TestCase("3d")]
        [TestCase("2d")]
        public void KinematicTriggers_AreNotMisdiagnosedAsRequiringDynamicBody(string dimension)
        {
            AddPair(dimension);
            foreach (var go in new[] { first, second })
            {
                if (dimension == "2d")
                {
                    go.GetComponent<Rigidbody2D>().bodyType = RigidbodyType2D.Kinematic;
                    go.GetComponent<Collider2D>().isTrigger = true;
                }
                else
                {
                    go.GetComponent<Rigidbody>().isKinematic = true;
                    go.GetComponent<Collider>().isTrigger = true;
                }
            }
            var response = Call(Request(dimension));
            Success(response);
            Assert.AreEqual("trigger", response["data"]["configuration"].Value<string>("interaction"));
            Assert.IsEmpty(Blockers(response));
            Assert.AreEqual("unavailable", response["data"]["contact_observation"].Value<string>("status"));
            Assert.IsNull(response["data"]["contact_observation"]["contact_count"]);
        }

        [TestCase("3d")]
        [TestCase("2d")]
        public void StaticBodies_AreDistinguishedFromDynamicColliderPairs(string dimension)
        {
            AddPair(dimension, false);
            var response = Call(Request(dimension));
            Success(response);
            CollectionAssert.Contains(Blockers(response), "static_static_pair");
            Assert.AreEqual("static", response["data"]["first"]["body"].Value<string>("motion_type"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FullKinematicContacts2D_IsReportedWithoutChangingSetting(bool fullContacts)
        {
            AddPair("2d");
            first.GetComponent<Rigidbody2D>().bodyType = RigidbodyType2D.Kinematic;
            first.GetComponent<Rigidbody2D>().useFullKinematicContacts = fullContacts;
            second.GetComponent<Rigidbody2D>().bodyType = RigidbodyType2D.Static;
            var response = Call(Request("2d"));
            Success(response);
            Assert.AreEqual(!fullContacts, Blockers(response).Contains("kinematic_contact_filter"));
            Assert.AreEqual(fullContacts, first.GetComponent<Rigidbody2D>().useFullKinematicContacts);
        }

        [TestCase("3d")]
        [TestCase("2d")]
        public void MultipleColliders_RequireExplicitSelection(string dimension)
        {
            AddPair(dimension);
            if (dimension == "2d")
                first.AddComponent<BoxCollider2D>();
            else
                first.AddComponent<BoxCollider>();
            var options = Request(dimension);
            var ambiguous = Call(options);
            Assert.IsFalse(ambiguous.Value<bool>("success"));
            Assert.AreEqual("collider_selection_required", ambiguous.Value<string>("code"));
            options["componentIndex"] = 1;
            var selected = Call(options);
            Success(selected);
            Component expected = dimension == "2d" ? (Component)first.GetComponents<Collider2D>()[1] : first.GetComponents<Collider>()[1];
            Assert.AreEqual(expected.GetInstanceIDCompat(), selected["data"]["first"].Value<int>("collider_id"));
        }

        [Test]
        public void DimensionSelection_IsExplicitWhenObjectsHaveBothSystems()
        {
            AddPair("2d", false);
            AddPair("3d", false);
            var response = Call(Request("2d"));
            Success(response);
            Assert.AreEqual(first.GetComponent<Collider2D>().GetInstanceIDCompat(), response["data"]["first"].Value<int>("collider_id"));
            var missing = Request("both");
            Assert.IsFalse(Call(missing).Value<bool>("success"));
        }

        [Test]
        public void DuplicateNames_RequireUniqueObjectSelection()
        {
            AddPair("3d");
            second.name = first.name;
            var options = Request();
            options["target"] = first.name;
            options["search_method"] = "by_name";
            var response = Call(options);
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("target_not_unique", response.Value<string>("code"));
            Success(Call(Request()));
        }

        [Test]
        public void CharacterController_DoesNotClaimOrdinaryStaticBodyEligibility()
        {
            first.AddComponent<CharacterController>();
            second.AddComponent<BoxCollider>();
            var response = Call(Request());
            Success(response);
            Assert.AreEqual("unavailable", response["data"]["first"]["body"].Value<string>("motion_type"));
            Assert.AreEqual(JTokenType.Null, response["data"]["configuration_allows_interaction"].Type);
            CollectionAssert.DoesNotContain(Blockers(response), "static_static_pair");
        }

#if UNITY_6000_0_OR_NEWER
        [TestCase("3d")]
        [TestCase("2d")]
        public void LayerOverrides_DoNotTreatGlobalIgnoreAsDefinitiveEligibility(string dimension)
        {
            AddPair(dimension);
            int mask = 1 << second.layer;
            if (dimension == "2d")
            {
                first.GetComponent<Collider2D>().includeLayers = mask;
                Physics2D.IgnoreLayerCollision(30, 31, true);
            }
            else
            {
                first.GetComponent<Collider>().includeLayers = mask;
                Physics3D.IgnoreLayerCollision(30, 31, true);
            }
            var response = Call(Request(dimension));
            Success(response);
            Assert.IsTrue(response["data"]["configuration"].Value<bool>("layer_ignored"));
            Assert.AreEqual("unavailable_with_layer_overrides", response["data"]["configuration"].Value<string>("effective_layer_policy"));
            Assert.AreEqual(mask, response["data"]["first"]["layer_overrides"].Value<int>("collider_include_mask"));
            Assert.AreEqual(JTokenType.Null, response["data"]["configuration_allows_interaction"].Type);
            CollectionAssert.DoesNotContain(Blockers(response), "layer_collision_disabled");
            Assert.AreEqual(
                mask,
                dimension == "2d" ? first.GetComponent<Collider2D>().includeLayers.value : first.GetComponent<Collider>().includeLayers.value
            );
        }
#endif

        [TestCase("3d")]
        [TestCase("2d")]
        public void SeparatePhysicsScenes_AreReportedAsStaticBlockingReason(string dimension)
        {
            AddPair(dimension);
            var extra = CreateIsolatedPreview(dimension);
            SceneManager.MoveGameObjectToScene(second, extra);
            var response = Call(Request(dimension));
            Success(response);
            Assert.IsFalse(response["data"]["configuration"].Value<bool>("same_physics_scene"));
            CollectionAssert.Contains(Blockers(response), "different_physics_scenes");
        }

        [Test]
        public void Contact2D_ReportsCachedSimulationEvidenceWithoutAdvancingWorld()
        {
            AddPair("2d");
            var extra = CreateIsolatedPreview("2d");
            SceneManager.MoveGameObjectToScene(first, extra);
            SceneManager.MoveGameObjectToScene(second, extra);
            first.GetComponent<Rigidbody2D>().constraints = RigidbodyConstraints2D.FreezeAll;
            second.GetComponent<Rigidbody2D>().bodyType = RigidbodyType2D.Static;
            Assert.IsTrue(extra.GetPhysicsScene2D().Simulate(0.02f));
            Vector3 before = first.transform.position;
            var response = Call(Request("2d"));
            Success(response);
            Assert.AreEqual("available", response["data"]["contact_observation"].Value<string>("status"));
            Assert.Greater(response["data"]["contact_observation"].Value<int>("contact_count"), 0);
            Assert.IsTrue(response["data"]["contact_observation"].Value<bool>("touching"));
            Assert.AreEqual(before, first.transform.position);
            Assert.IsFalse(response["data"]["observation_timing"].Value<bool>("simulation_advanced"));
        }

#if UNITY_6000_0_OR_NEWER
        [Test]
        public void Contact3D_IsExplicitlyOptInAndLastObservedRatherThanCurrentTruth()
        {
            AddPair("3d");
            first.GetComponent<Collider>().providesContacts = true;
            var extra = CreateIsolatedPreview("3d");
            SceneManager.MoveGameObjectToScene(first, extra);
            SceneManager.MoveGameObjectToScene(second, extra);
            first.GetComponent<Rigidbody>().constraints = RigidbodyConstraints.FreezeAll;
            UnityEngine.Object.DestroyImmediate(second.GetComponent<Rigidbody>());
            var options = Request();
            var before = Call(options);
            Success(before);
            Assert.AreEqual("unavailable", before["data"]["contact_observation"].Value<string>("status"));
            options["observe_contacts"] = true;
            Success(Call(options));
            extra.GetPhysicsScene().Simulate(0.02f);
            options.Remove("observe_contacts");
            var observed = Call(options);
            Success(observed);
            Assert.AreEqual("available", observed["data"]["contact_observation"].Value<string>("status"));
            Assert.AreEqual("last_observed_collision_event", observed["data"]["contact_observation"].Value<string>("basis"));
            Assert.Greater(observed["data"]["contact_observation"].Value<int>("contact_count"), 0);
            Assert.IsFalse(observed["data"]["contact_observation"].Value<bool>("current_state_proven"));
#if UNITY_6000_4_OR_NEWER
            Assert.AreEqual("entity_id_decimal_string", observed["data"]["contact_observation"].Value<string>("native_collider_id_kind"));
            CollectionAssert.AreEquivalent(
                new[]
                {
                    EntityId.ToULong(first.GetComponent<Collider>().GetEntityId()).ToString(System.Globalization.CultureInfo.InvariantCulture),
                    EntityId.ToULong(second.GetComponent<Collider>().GetEntityId()).ToString(System.Globalization.CultureInfo.InvariantCulture),
                },
                new[]
                {
                    observed["data"]["contact_observation"].Value<string>("native_first_collider_id"),
                    observed["data"]["contact_observation"].Value<string>("native_second_collider_id"),
                }
            );
#else
            Assert.AreEqual("instance_id", observed["data"]["contact_observation"].Value<string>("native_collider_id_kind"));
            CollectionAssert.AreEquivalent(
                new[] { first.GetComponent<Collider>().GetInstanceIDCompat(), second.GetComponent<Collider>().GetInstanceIDCompat() },
                new[]
                {
                    observed["data"]["contact_observation"].Value<int>("native_first_collider_id"),
                    observed["data"]["contact_observation"].Value<int>("native_second_collider_id"),
                }
            );
#endif
            options["observe_contacts"] = false;
            var stopped = Call(options);
            Success(stopped);
            Assert.IsFalse(stopped["data"]["contact_observation"].Value<bool>("watching"));
        }
#endif
    }
}
