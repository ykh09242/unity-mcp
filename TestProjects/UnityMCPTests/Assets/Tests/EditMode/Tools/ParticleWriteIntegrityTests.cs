using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Vfx;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    public class ParticleWriteIntegrityTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene ownedScene;
        private GameObject root;
        private ParticleSystem particles;
        private ParticleSystem child;
        private ParticleSystemRenderer renderer;
        private Material material;
        private string assetRoot;
        private bool ownsAssetFolder;
        private UnityEngine.Object[] previousSelection;
        private UnityEngine.Object previousActiveObject;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            ownsAssetFolder = false;
            material = null;
            previousSelection = Selection.objects;
            previousActiveObject = Selection.activeObject;
            string suffix = Guid.NewGuid().ToString("N");
            ownedScene = sceneFixture.Create("McpParticleWriteIntegrity_", suffix);
            assetRoot = "Assets/__McpParticleWriteIntegrity_" + suffix;
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            root = new GameObject("__McpParticleWriteIntegrity_" + suffix);
            particles = root.AddComponent<ParticleSystem>();
            renderer = root.GetComponent<ParticleSystemRenderer>();
            var childObject = new GameObject("Child");
            childObject.transform.SetParent(root.transform, false);
            child = childObject.AddComponent<ParticleSystem>();
            particles.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
            var main = particles.main;
            main.duration = 5;
            main.loop = true;
            main.prewarm = false;
            main.playOnAwake = false;
            main.startLifetime = 20;
            main.startSpeed = 6;
            main.maxParticles = 100;
            var childMain = child.main;
            childMain.playOnAwake = false;
            childMain.startLifetime = 20;
            var emission = particles.emission;
            emission.rateOverTime = 7;
            var size = particles.sizeOverLifetime;
            size.enabled = false;
            size.separateAxes = true;
            size.size = 2;
            var noise = particles.noise;
            noise.enabled = false;
            noise.frequency = 2;
            renderer.sharedMaterial = null;
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (root != null)
                    UnityEngine.Object.DestroyImmediate(root);
                if (material != null && !EditorUtility.IsPersistent(material))
                    UnityEngine.Object.DestroyImmediate(material);
                if (ownsAssetFolder)
                {
                    const string prefix = "Assets/__McpParticleWriteIntegrity_";
                    Assert.IsTrue(assetRoot.StartsWith(prefix, StringComparison.Ordinal));
                    Assert.AreEqual(32, assetRoot.Substring(prefix.Length).Length);
                    Assert.IsTrue(Guid.TryParseExact(assetRoot.Substring(prefix.Length), "N", out _));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Only the successfully created fixture folder may be removed.");
                }
            }
            finally
            {
                sceneFixture.Close();
                Selection.objects = previousSelection;
                Selection.activeObject = previousActiveObject;
            }
        }

        [TestCase("{position:[9,8,7],rotation:[0,'bad',0]}")]
        [TestCase("{position:[9,8,7],rotation:[0,20,0],scale:[1,'bad',1]}")]
        [TestCase("{position:[9,8,7],playOnAwake:'bad'}")]
        [TestCase("{position:[9,8,7],playOnAwake:true,looping:'bad'}")]
        public void InvalidCreatePreservesExistingObjectComponentsAndTransforms(string json)
        {
            sceneFixture.ClearDirtiness();
            Vector3 position = root.transform.position;
            Quaternion rotation = root.transform.rotation;
            Vector3 scale = root.transform.localScale;
            string before = Snapshot();
            int dirty = EditorUtility.GetDirtyCount(root);
            int particleDirty = EditorUtility.GetDirtyCount(particles);
            int rendererDirty = EditorUtility.GetDirtyCount(renderer);
            JObject response = Call("particle_create", JObject.Parse(json));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(particles, root.GetComponent<ParticleSystem>());
            Assert.AreSame(renderer, root.GetComponent<ParticleSystemRenderer>());
            Assert.AreEqual(position, root.transform.position);
            Assert.AreEqual(rotation, root.transform.rotation);
            Assert.AreEqual(scale, root.transform.localScale);
            Assert.AreEqual(before, Snapshot());
            Assert.IsNull(renderer.sharedMaterial);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(root));
            Assert.AreEqual(particleDirty, EditorUtility.GetDirtyCount(particles));
            Assert.AreEqual(rendererDirty, EditorUtility.GetDirtyCount(renderer));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase("{position:[9,8,7],looping:'bad'}")]
        [TestCase("{position:[9,8,7],rotation:[0,'bad',0]}")]
        public void InvalidCreateDoesNotAllocateNewObjectOrParticleComponent(string json)
        {
            sceneFixture.ClearDirtiness();
            string name = "McpRejectedParticle_" + Guid.NewGuid().ToString("N");
            int count = ownedScene.rootCount;
            JObject response = JObject.FromObject(
                ManageVFX.HandleCommand(
                    new JObject
                    {
                        ["action"] = "particle_create",
                        ["target"] = name,
                        ["properties"] = JObject.Parse(json),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(count, ownedScene.rootCount);
            Assert.IsNull(GameObject.Find(name));
            Assert.IsFalse(ownedScene.isDirty);
            var empty = new GameObject(name);
            sceneFixture.ClearDirtiness();
            int dirty = EditorUtility.GetDirtyCount(empty);
            response = JObject.FromObject(
                ManageVFX.HandleCommand(
                    new JObject
                    {
                        ["action"] = "particle_create",
                        ["target"] = empty.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["properties"] = JObject.Parse(json),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(empty.GetComponent<ParticleSystem>());
            Assert.IsNull(empty.GetComponent<ParticleSystemRenderer>());
            Assert.AreEqual(Vector3.zero, empty.transform.position);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(empty));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase("particle_play", "{withChildren:'bad'}")]
        [TestCase("particle_restart", "{withChildren:'bad'}")]
        [TestCase("particle_add_burst", "{time:'bad'}")]
        [TestCase("particle_add_burst", "{time:1,count:'bad'}")]
        [TestCase("particle_add_burst", "{time:1,minCount:2,maxCount:'bad'}")]
        [TestCase("particle_add_burst", "{time:1,count:2,cycles:'bad'}")]
        [TestCase("particle_add_burst", "{time:1,count:2,interval:'bad'}")]
        [TestCase("particle_add_burst", "{time:1,count:2,probability:'bad'}")]
        public void InvalidControlDoesNotAssignMaterialOrChangeParticles(string action, string json)
        {
            sceneFixture.ClearDirtiness();
            string before = Snapshot();
            int bursts = particles.emission.burstCount;
            int dirty = EditorUtility.GetDirtyCount(particles);
            int rendererDirty = EditorUtility.GetDirtyCount(renderer);
            JObject response = Call(action, JObject.Parse(json));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(before, Snapshot());
            Assert.AreEqual(bursts, particles.emission.burstCount);
            Assert.IsNull(renderer.sharedMaterial);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(particles));
            Assert.AreEqual(rendererDirty, EditorUtility.GetDirtyCount(renderer));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [Test]
        public void ValidCreateAndControlPreserveOverridesAndBurstConversions()
        {
            AssignUsableMaterial();
            JObject response = Call("particle_create", JObject.Parse("{position:[0,'-2',3],rotation:[0,20,0],scale:[1,2,1],playOnAwake:false,looping:false}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(response.Value<bool>("createdGameObject"));
            Assert.IsFalse(response.Value<bool>("addedParticleSystem"));
            Assert.AreEqual(new Vector3(0, -2, 3), root.transform.position);
            Assert.AreEqual(new Vector3(1, 2, 1), root.transform.localScale);
            Assert.IsFalse(particles.main.playOnAwake);
            Assert.IsFalse(particles.main.loop);
            Assert.IsTrue(Call("particle_play", JObject.Parse("{withChildren:false}")).Value<bool>("success"));
            Assert.IsTrue(particles.isPlaying);
            Assert.IsFalse(child.isPlaying);
            Assert.IsTrue(Call("particle_restart", JObject.Parse("{withChildren:false}")).Value<bool>("success"));
            response = Call("particle_add_burst", JObject.Parse("{time:'1',minCount:-2,maxCount:40000,cycles:'2',interval:'0.5',probability:'0.75'}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var burst = particles.emission.GetBurst(response.Value<int>("burstIndex"));
            Assert.AreEqual(1f, burst.time);
            Assert.AreEqual(0, burst.minCount);
            Assert.AreEqual(short.MaxValue, burst.maxCount);
            Assert.AreEqual(2, burst.cycleCount);
            Assert.AreEqual(0.5f, burst.repeatInterval);
            Assert.AreEqual(0.75f, burst.probability);
            Assert.AreSame(material, renderer.sharedMaterial);
        }

        [TestCase("particle_set_main", "{duration:'bad'}")]
        [TestCase("particle_set_main", "{duration:3,looping:'bad'}")]
        [TestCase("particle_set_main", "{duration:3,startSpeed:2,maxParticles:'bad'}")]
        [TestCase("particle_set_main", "{duration:3,startDelay:{value:'bad'}}")]
        [TestCase("particle_set_emission", "{enabled:false,rateOverTime:{value:'bad'}}")]
        [TestCase("particle_set_emission", "{rateOverTime:3,rateOverDistance:{mode:'two_constants',min:1,max:'bad'}}")]
        [TestCase("particle_set_shape", "{enabled:false,radius:3,angle:'bad'}")]
        [TestCase("particle_set_shape", "{enabled:'bad'}")]
        [TestCase("particle_set_color_over_lifetime", "{enabled:'bad',color:[1,0,0]}")]
        [TestCase("particle_set_size_over_lifetime", "{size:3,separateAxes:'bad'}")]
        [TestCase("particle_set_size_over_lifetime", "{size:{value:'bad'}}")]
        [TestCase("particle_set_size_over_lifetime", "{enabled:false,sizeX:3,sizeY:{value:'bad'}}")]
        [TestCase("particle_set_velocity_over_lifetime", "{enabled:true,x:3,y:{value:'bad'}}")]
        [TestCase("particle_set_velocity_over_lifetime", "{enabled:true,x:3,speedModifier:{mode:'curve',multiplier:'bad',keys:[]}}")]
        [TestCase("particle_set_noise", "{enabled:true,strength:3,frequency:'bad'}")]
        [TestCase("particle_set_noise", "{frequency:3,damping:'bad'}")]
        [TestCase("particle_set_renderer", "{renderMode:'Billboard',minParticleSize:0.1,maxParticleSize:'bad'}")]
        [TestCase("particle_set_renderer", "{minParticleSize:0.1,allowRoll:false,sortingOrder:'bad'}")]
        [TestCase("particle_set_renderer", "{renderMode:'Billboard',receiveShadows:false,renderingLayerMask:-1}")]
        public void InvalidConversionPreservesAllModuleAndRendererState(string action, string properties)
        {
            string before = Snapshot();
            int dirtyCount = EditorUtility.GetDirtyCount(particles);
            int rendererDirtyCount = EditorUtility.GetDirtyCount(renderer);
            JObject response = Call(action, JObject.Parse(properties));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(before, Snapshot());
            Assert.IsNull(renderer.sharedMaterial);
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(particles));
            Assert.AreEqual(rendererDirtyCount, EditorUtility.GetDirtyCount(renderer));
        }

        [TestCase("{duration:'bad'}")]
        [TestCase("{duration:3,looping:'bad'}")]
        [TestCase("{duration:3,startSpeed:2,maxParticles:'bad'}")]
        public void InvalidMainRequestPreservesPlayingRootAndChildParticles(string properties)
        {
            AssignUsableMaterial();
            particles.Play(true);
            particles.Emit(3);
            child.Emit(2);
            Assert.IsTrue(particles.isPlaying);
            Assert.IsTrue(child.isPlaying);
            Assert.Greater(particles.particleCount, 0);
            Assert.Greater(child.particleCount, 0);
            string before = Snapshot();
            Assert.IsFalse(Call("particle_set_main", JObject.Parse(properties)).Value<bool>("success"));
            Assert.AreEqual(before, Snapshot());
            Assert.IsTrue(particles.isPlaying);
            Assert.IsTrue(child.isPlaying);
        }

        [Test]
        public void ValidDurationChangeRestartsRootAndChild()
        {
            AssignUsableMaterial();
            particles.Play(true);
            particles.Emit(3);
            child.Emit(2);
            JObject response = Call("particle_set_main", JObject.Parse("{duration:'3',looping:false,maxParticles:0}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(3f, particles.main.duration);
            Assert.IsFalse(particles.main.loop);
            Assert.IsTrue(particles.isPlaying);
            Assert.IsTrue(child.isPlaying);
            Assert.AreEqual(0, particles.particleCount);
            Assert.AreEqual(0, child.particleCount);
            Assert.AreEqual("Updated: duration, looping, maxParticles, (restarted after duration change)", response.Value<string>("message"));
        }

        [Test]
        public void StoppedDurationChangeDoesNotStartPlayback()
        {
            AssignUsableMaterial();
            Assert.IsFalse(particles.isPlaying);
            Assert.IsTrue(Call("particle_set_main", JObject.Parse("{duration:3,looping:false}")).Value<bool>("success"));
            Assert.AreEqual(3f, particles.main.duration);
            Assert.IsFalse(particles.isPlaying);
            Assert.IsFalse(child.isPlaying);
        }

        [Test]
        public void NullDurationRetainsNativeZeroClamp()
        {
            AssignUsableMaterial();
            var main = particles.main;
            float duration = main.duration;
            main.duration = 0;
            float nativeZeroDuration = main.duration;
            main.duration = duration;
            JObject response = Call("particle_set_main", JObject.Parse("{duration:null}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(nativeZeroDuration, particles.main.duration);
            Assert.IsFalse(particles.isPlaying);
            Assert.AreSame(material, renderer.sharedMaterial);
        }

        [TestCase("{size:3,separateAxes:false}", true, 3f)]
        [TestCase("{enabled:false,size:3}", false, 3f)]
        [TestCase("{size:null}", true, 1f)]
        public void SizeAutoEnableAndExplicitDisableRemainCompatible(string properties, bool enabled, float size)
        {
            AssignUsableMaterial();
            Assert.IsTrue(Call("particle_set_size_over_lifetime", JObject.Parse(properties)).Value<bool>("success"));
            Assert.AreEqual(enabled, particles.sizeOverLifetime.enabled);
            Assert.AreEqual(size, particles.sizeOverLifetime.size.constant);
        }

        [Test]
        public void MainCurveNullStringAndUnknownModeFallbacksRemainCompatible()
        {
            AssignUsableMaterial();
            Assert.IsTrue(
                Call("particle_set_main", JObject.Parse("{startSpeed:'bad',startLifetime:null,startDelay:{mode:'unknown'},startColor:null}"))
                    .Value<bool>("success")
            );
            Assert.AreEqual(5f, particles.main.startSpeed.constant);
            Assert.AreEqual(5f, particles.main.startLifetime.constant);
            Assert.AreEqual(0f, particles.main.startDelay.constant);
            Assert.AreEqual(Color.white, particles.main.startColor.color);
        }

        [Test]
        public void RendererAcceptsOwnedExplicitMaterialAndPreservesLastCheckMetadata()
        {
            AssignUsableMaterial();
            string folderGuid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            Assert.IsFalse(string.IsNullOrEmpty(folderGuid));
            ownsAssetFolder = true;
            string path = assetRoot + "/Explicit.mat";
            AssetDatabase.CreateAsset(material, path);
            Material importedMaterial = AssetDatabase.LoadAssetAtPath<Material>(path);
            Assert.IsTrue(material == importedMaterial, "Import may return another managed wrapper for the same native material.");
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(importedMaterial));
            material = importedMaterial;
            renderer.sharedMaterial = null;
            JObject response = Call(
                "particle_set_renderer",
                new JObject
                {
                    ["materialPath"] = path,
                    ["allowRoll"] = false,
                    ["sortingOrder"] = 0,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(material == renderer.sharedMaterial);
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(renderer.sharedMaterial));
            Assert.IsFalse(response.Value<bool>("materialReplaced"));
            Assert.AreEqual(string.Empty, response.Value<string>("replacementReason"));
        }

        [Test]
        public void MissingExplicitMaterialKeepsUsableExistingMaterial()
        {
            AssignUsableMaterial();
            JObject response = Call("particle_set_renderer", new JObject { ["materialPath"] = assetRoot + "/Missing.mat", ["sortingOrder"] = -2 });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(material, renderer.sharedMaterial);
            Assert.AreEqual(-2, renderer.sortingOrder);
            Assert.IsFalse(response.Value<bool>("materialReplaced"));
        }

        [TestCase(null)]
        [TestCase("Missing.mat")]
        public void RendererReportsMissingMaterialRepair(string missingMaterialName)
        {
            var properties = new JObject { ["sortingOrder"] = -2, ["allowRoll"] = false };
            if (missingMaterialName != null)
                properties["materialPath"] = assetRoot + "/" + missingMaterialName;

            JObject response = Call("particle_set_renderer", properties);

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(renderer.sharedMaterial, "The default particle material must be assigned.");
            Assert.IsTrue(response.Value<bool>("materialReplaced"), response.ToString());
            Assert.AreEqual("missing_material", response.Value<string>("replacementReason"));
            Assert.AreEqual(-2, renderer.sortingOrder);
            Assert.IsFalse(renderer.allowRoll);
        }

        [Test]
        public void RendererReportsInvalidExistingMaterialRepair()
        {
            Shader shader = Shader.Find("Hidden/InternalErrorShader");
            if (shader == null)
                Assert.Ignore("The internal error shader is required for an invalid material.");

            material = new Material(shader);
            renderer.sharedMaterial = material;
            RenderPipelineUtility.IsMaterialInvalidForActivePipeline(material, out string reason);
            string expectedReason = !string.IsNullOrWhiteSpace(reason) ? reason : "invalid_material";

            JObject response = Call("particle_set_renderer", new JObject { ["sortingOrder"] = 2 });

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(renderer.sharedMaterial);
            Assert.IsTrue(material != renderer.sharedMaterial);
            Assert.IsTrue(response.Value<bool>("materialReplaced"), response.ToString());
            Assert.AreEqual(expectedReason, response.Value<string>("replacementReason"));
            Assert.AreEqual(2, renderer.sortingOrder);
        }

        private JObject Call(string action, JObject properties)
        {
            return JObject.FromObject(
                ManageVFX.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = root.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["properties"] = properties,
                    }
                )
            );
        }

        private void AssignUsableMaterial()
        {
            Shader shader = RenderPipelineUtility.ResolveShader("Standard");
            if (shader == null || !shader.isSupported)
                Assert.Ignore("An active-pipeline usable shader is required.");
            material = new Material(shader);
            if (RenderPipelineUtility.IsMaterialInvalidForActivePipeline(material, out string reason))
                Assert.Ignore("An active-pipeline usable material is required: " + reason);
            renderer.sharedMaterial = material;
        }

        private string Snapshot()
        {
            var main = particles.main;
            var emission = particles.emission;
            var shape = particles.shape;
            var color = particles.colorOverLifetime;
            var size = particles.sizeOverLifetime;
            var velocity = particles.velocityOverLifetime;
            var noise = particles.noise;
            return JObject
                .FromObject(
                    new
                    {
                        main = new
                        {
                            main.duration,
                            main.loop,
                            main.prewarm,
                            startDelay = CurveSnapshot(main.startDelay),
                            startLifetime = CurveSnapshot(main.startLifetime),
                            startSpeed = CurveSnapshot(main.startSpeed),
                            startSize = CurveSnapshot(main.startSize),
                            startRotation = CurveSnapshot(main.startRotation),
                            startColor = GradientSnapshot(main.startColor),
                            gravityModifier = CurveSnapshot(main.gravityModifier),
                            main.simulationSpace,
                            main.scalingMode,
                            main.playOnAwake,
                            main.maxParticles,
                        },
                        emission = new
                        {
                            emission.enabled,
                            rateOverTime = CurveSnapshot(emission.rateOverTime),
                            rateOverDistance = CurveSnapshot(emission.rateOverDistance),
                        },
                        shape = new
                        {
                            shape.enabled,
                            shape.shapeType,
                            shape.radius,
                            shape.radiusThickness,
                            shape.angle,
                            shape.arc,
                            position = VectorSnapshot(shape.position),
                            rotation = VectorSnapshot(shape.rotation),
                            scale = VectorSnapshot(shape.scale),
                        },
                        color = new { color.enabled, color = GradientSnapshot(color.color) },
                        size = new
                        {
                            size.enabled,
                            size.separateAxes,
                            size = CurveSnapshot(size.size),
                            x = CurveSnapshot(size.x),
                            y = CurveSnapshot(size.y),
                            z = CurveSnapshot(size.z),
                        },
                        velocity = new
                        {
                            velocity.enabled,
                            velocity.space,
                            x = CurveSnapshot(velocity.x),
                            y = CurveSnapshot(velocity.y),
                            z = CurveSnapshot(velocity.z),
                            speedModifier = CurveSnapshot(velocity.speedModifier),
                        },
                        noise = new
                        {
                            noise.enabled,
                            strength = CurveSnapshot(noise.strength),
                            noise.frequency,
                            scrollSpeed = CurveSnapshot(noise.scrollSpeed),
                            noise.damping,
                            noise.octaveCount,
                            noise.quality,
                        },
                        renderer = new
                        {
                            material = renderer.sharedMaterial != null ? renderer.sharedMaterial.GetInstanceIDCompat() : 0,
                            trailMaterial = renderer.trailMaterial != null ? renderer.trailMaterial.GetInstanceIDCompat() : 0,
                            renderer.renderMode,
                            renderer.sortMode,
                            renderer.minParticleSize,
                            renderer.maxParticleSize,
                            renderer.lengthScale,
                            renderer.velocityScale,
                            renderer.cameraVelocityScale,
                            renderer.normalDirection,
                            renderer.alignment,
                            pivot = VectorSnapshot(renderer.pivot),
                            flip = VectorSnapshot(renderer.flip),
                            renderer.allowRoll,
                            renderer.shadowBias,
                            renderer.receiveShadows,
                            renderer.shadowCastingMode,
                            renderer.lightProbeUsage,
                            renderer.reflectionProbeUsage,
                            renderer.motionVectorGenerationMode,
                            renderer.sortingOrder,
                            renderer.sortingLayerID,
                            renderer.sortingLayerName,
                            renderer.renderingLayerMask,
                        },
                        particles.isPlaying,
                        particles.particleCount,
                        childPlaying = child.isPlaying,
                        childParticleCount = child.particleCount,
                    }
                )
                .ToString(Formatting.None);
        }

        private static float[] VectorSnapshot(Vector3 value) => new[] { value.x, value.y, value.z };

        private static float[] ColorSnapshot(Color value) => new[] { value.r, value.g, value.b, value.a };

        private static object CurveSnapshot(ParticleSystem.MinMaxCurve curve) =>
            new
            {
                curve.mode,
                curve.constantMin,
                curve.constantMax,
                curve.curveMultiplier,
                min = AnimationSnapshot(curve.curveMin),
                max = AnimationSnapshot(curve.curveMax),
            };

        private static object AnimationSnapshot(AnimationCurve curve) =>
            curve
                ?.keys.Select(key => new
                {
                    key.time,
                    key.value,
                    key.inTangent,
                    key.outTangent,
                    key.inWeight,
                    key.outWeight,
                    key.weightedMode,
                })
                .ToArray();

        private static object GradientSnapshot(ParticleSystem.MinMaxGradient gradient) =>
            new
            {
                gradient.mode,
                // Inactive native color slots are unspecified and can contain changing garbage.
                minColor = gradient.mode == ParticleSystemGradientMode.TwoColors ? ColorSnapshot(gradient.colorMin) : null,
                maxColor = gradient.mode == ParticleSystemGradientMode.Color || gradient.mode == ParticleSystemGradientMode.TwoColors
                    ? ColorSnapshot(gradient.colorMax)
                    : null,
                minGradient = gradient.mode == ParticleSystemGradientMode.TwoGradients ? GradientKeys(gradient.gradientMin) : null,
                maxGradient = gradient.mode == ParticleSystemGradientMode.Gradient
                || gradient.mode == ParticleSystemGradientMode.TwoGradients
                || gradient.mode == ParticleSystemGradientMode.RandomColor
                    ? GradientKeys(gradient.gradientMax)
                    : null,
            };

        private static object GradientKeys(Gradient gradient) =>
            gradient == null
                ? null
                : new
                {
                    color = gradient.colorKeys.Select(key => new { key.time, color = ColorSnapshot(key.color) }).ToArray(),
                    alpha = gradient.alphaKeys.Select(key => new { key.time, key.alpha }).ToArray(),
                };
    }
}
