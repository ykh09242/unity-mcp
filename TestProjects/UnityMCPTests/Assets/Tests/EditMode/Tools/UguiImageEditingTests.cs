using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class UguiImageEditingTests
    {
        private const string EmptySceneTemplateGuid = "d8f5ae9136f356e190abdc5c9be58fb6";
        private Type imageType;
        private SceneSnapshot[] preservedScenes;
        private string ownedFolderGuid;
        private Scene scene,
            previousScene;
        private string prefix,
            folder;
        private int undoGroup;
        private bool ownsFolder,
            ownsScene,
            ownsUndoGroup;

        [SetUp]
        public void SetUp()
        {
            ownsFolder = ownsScene = ownsUndoGroup = false;
            scene = previousScene = default;
            preservedScenes = null;
            ownedFolderGuid = null;
            try
            {
                imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
                if (imageType == null)
                    Assert.Ignore("This fixture requires the optional uGUI package; absence is not a passing Image test.");
                prefix = "UI15_" + Guid.NewGuid().ToString("N");
                folder = "Assets/" + prefix;
                Assert.That(PrefabStageUtility.GetCurrentPrefabStage(), Is.Null, "Preserve an existing Prefab Stage; this fixture requires the main stage.");
                previousScene = SceneManager.GetActiveScene();
                preservedScenes = Enumerable
                    .Range(0, SceneManager.sceneCount)
                    .Select(index => SceneManager.GetSceneAt(index))
                    .Select(original => new SceneSnapshot
                    {
                        Scene = original,
                        Path = original.path,
                        Dirty = original.isDirty,
                        Loaded = original.isLoaded,
                        RootIds = original.isLoaded ? RootIds(original) : Array.Empty<int>(),
                    })
                    .ToArray();
                string template = AssetDatabase.GUIDToAssetPath(EmptySceneTemplateGuid);
                Assert.That(AssetDatabase.LoadAssetAtPath<SceneAsset>(template), Is.Not.Null, "The shared empty test scene template must be imported.");
                Folder();
                string ownedPath = folder + "/" + prefix + ".unity";
                Assert.That(AssetDatabase.AssetPathToGUID(ownedPath), Is.Empty);
                Assert.That(AssetDatabase.CopyAsset(template, ownedPath), Is.True);
                AssertPreservedScenes(false);
                scene = EditorSceneManager.OpenScene(ownedPath, OpenSceneMode.Additive);
                ownsScene = true;
                Assert.That(scene.path, Is.EqualTo(ownedPath));
                Assert.That(scene.rootCount, Is.Zero, "The owned copy must be an empty normal scene.");
                Assert.That(EditorSceneManager.IsPreviewScene(scene), Is.False);
                Assert.That(SceneManager.SetActiveScene(scene), Is.True, "Create owned targets only in the owned scene.");
                AssertPreservedScenes(true);
                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                ownsUndoGroup = true;
            }
            catch (Exception setupError)
            {
                try
                {
                    TearDown();
                }
                catch (Exception cleanupError)
                {
                    throw new AggregateException("Owned Image fixture setup and cleanup failed.", setupError, cleanupError);
                }
                throw;
            }
        }

        [TearDown]
        public void TearDown()
        {
            var errors = new List<Exception>();
            if (ownsUndoGroup)
                Cleanup(() => Undo.RevertAllDownToGroup(undoGroup), errors);
            ownsUndoGroup = false;
            Cleanup(
                () =>
                {
                    if (preservedScenes != null && previousScene.IsValid() && previousScene.isLoaded)
                        SceneManager.SetActiveScene(previousScene);
                },
                errors
            );
            Cleanup(
                () =>
                {
                    if (ownsScene && scene.IsValid() && scene.isLoaded)
                        Assert.That(EditorSceneManager.CloseScene(scene, true), Is.True);
                },
                errors
            );
            ownsScene = false;
            Cleanup(
                () =>
                {
                    if (ownsFolder && AssetDatabase.IsValidFolder(folder))
                    {
                        Assert.That(
                            AssetDatabase.AssetPathToGUID(folder),
                            Is.EqualTo(ownedFolderGuid),
                            "Delete only the folder GUID acquired by this fixture."
                        );
                        Assert.That(AssetDatabase.DeleteAsset(folder), Is.True);
                    }
                },
                errors
            );
            ownsFolder = false;
            Cleanup(() => AssertPreservedScenes(false), errors);
            preservedScenes = null;
            ownedFolderGuid = null;
            scene = previousScene = default;
            if (errors.Count > 0)
                throw new AggregateException("Owned Image fixture cleanup failed.", errors);
        }

        private sealed class SceneSnapshot
        {
            public Scene Scene;
            public string Path;
            public bool Dirty;
            public bool Loaded;
            public int[] RootIds;
        }

        private static int[] RootIds(Scene target) => target.GetRootGameObjects().Select(root => root.GetInstanceIDCompat()).OrderBy(id => id).ToArray();

        private void AssertPreservedScenes(bool expectOwnedScene)
        {
            if (preservedScenes == null)
                return;
            Scene[] current = Enumerable.Range(0, SceneManager.sceneCount).Select(index => SceneManager.GetSceneAt(index)).ToArray();
            Scene[] expected = preservedScenes.Select(original => original.Scene).Concat(expectOwnedScene ? new[] { scene } : Array.Empty<Scene>()).ToArray();
            Assert.That(current, Is.EquivalentTo(expected), "Preserve every previously open scene, including untitled scenes.");
            foreach (SceneSnapshot original in preservedScenes)
            {
                Scene retained = current.Single(item => item.handle == original.Scene.handle);
                Assert.That(retained.IsValid(), Is.True);
                Assert.That(retained.isLoaded, Is.EqualTo(original.Loaded));
                Assert.That(retained.path, Is.EqualTo(original.Path));
                Assert.That(retained.isDirty, Is.EqualTo(original.Dirty));
                if (original.Loaded)
                    Assert.That(RootIds(retained), Is.EqualTo(original.RootIds));
            }
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(expectOwnedScene ? scene : previousScene));
        }

        private static void Cleanup(Action action, List<Exception> errors)
        {
            try
            {
                action();
            }
            catch (Exception error)
            {
                errors.Add(error);
            }
        }

        private GameObject Target(bool withImage = true)
        {
            var go = new GameObject(prefix + "_Image", typeof(RectTransform));
            SceneManager.MoveGameObjectToScene(go, scene);
            if (withImage)
                go.AddComponent(imageType);
            return go;
        }

        private object Get(Component image, string name) => imageType.GetProperty(name).GetValue(image);

        private void Set(Component image, string name, object value) => imageType.GetProperty(name).SetValue(image, value);

        private void EnumValue(Component image, string name, string value) => Set(image, name, Enum.Parse(imageType.GetProperty(name).PropertyType, value));

        private void Folder()
        {
            if (ownsFolder)
                return;
            Assert.That(AssetDatabase.IsValidFolder(folder), Is.False, "Never reuse or delete pre-existing assets.");
            Assert.That(AssetDatabase.AssetPathToGUID(folder), Is.Empty);
            string guid = AssetDatabase.CreateFolder("Assets", prefix);
            ownsFolder = !string.IsNullOrEmpty(guid);
            ownedFolderGuid = guid;
            Assert.That(guid, Is.Not.Empty);
            Assert.That(AssetDatabase.GUIDToAssetPath(guid), Is.EqualTo(folder));
        }

        private string Sprites(bool bordered = true)
        {
            Folder();
            string path = folder + "/sprites.asset";
            var texture = new Texture2D(8, 8);
            AssetDatabase.CreateAsset(texture, path);
            foreach (string name in new[] { "Primary", "Secondary" })
            {
                var sprite = Sprite.Create(
                    texture,
                    new Rect(0, 0, 8, 8),
                    Vector2.one * .5f,
                    100,
                    0,
                    SpriteMeshType.FullRect,
                    bordered ? Vector4.one : Vector4.zero
                );
                sprite.name = name;
                AssetDatabase.AddObjectToAsset(sprite, texture);
            }
            AssetDatabase.SaveAssetIfDirty(texture);
            Assert.That(AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().Count(), Is.EqualTo(2));
            return path;
        }

        private static JObject Call(GameObject go, JObject properties)
        {
            object raw = ManageUGUI.HandleCommand(
                new JObject
                {
                    ["action"] = "set_image",
                    ["target"] = go.GetInstanceIDCompat(),
                    ["properties"] = properties,
                }
            );
            // Assert the public JSON wire, including semantic null, rather than JValue's in-memory token kind.
            return JObject.Parse(JsonConvert.SerializeObject(raw));
        }

        private static void Success(JObject response) => Assert.That(response.Value<bool>("success"), Is.True, response.ToString());

        private static void Failure(JObject response) => Assert.That(response.Value<bool>("success"), Is.False, response.ToString());

        [TestCase("Simple")]
        [TestCase("Sliced")]
        [TestCase("Tiled")]
        [TestCase("Filled")]
        public void TypeEditsReadBackAndUndoRedoWithoutChangingSpriteAssets(string type)
        {
            string path = Sprites();
            byte[] assetBefore = System.IO.File.ReadAllBytes(path);
            byte[] metaBefore = System.IO.File.ReadAllBytes(path + ".meta");
            var go = Target();
            var image = go.GetComponent(imageType);
            var properties = new JObject
            {
                ["sprite"] = new JObject { ["path"] = path, ["name"] = "Primary" },
                ["type"] = type,
                ["color"] = new JArray(.2, .3, .4, .8),
                ["raycastTarget"] = false,
            };
            if (type == "Simple" || type == "Filled")
                properties["preserveAspect"] = true;
            else
                properties["fillCenter"] = false;
            var response = Call(go, properties);
            Success(response);
            Assert.That(Get(image, "type").ToString(), Is.EqualTo(type));
            Assert.That((Color)Get(image, "color"), Is.EqualTo(new Color(.2f, .3f, .4f, .8f)));
            Assert.That(Get(image, "raycastTarget"), Is.False);
            string flag = type == "Simple" || type == "Filled" ? "preserveAspect" : "fillCenter";
            bool expectedFlag = flag == "preserveAspect";
            Assert.That(Get(image, flag), Is.EqualTo(expectedFlag));
            Assert.That(response["data"]["image"][flag].Value<bool>(), Is.EqualTo(expectedFlag));
            Assert.That(response["data"]["image"]["raycastTarget"].Value<bool>(), Is.False);
            Assert.That(response["data"]["image"]["type"].Value<string>(), Is.EqualTo(type));
            Assert.That(response["data"]["image"]["sprite"]["name"].Value<string>(), Is.EqualTo("Primary"));
            Assert.That(response["data"]["image"]["sprite"]["guid"].Value<string>(), Is.EqualTo(AssetDatabase.AssetPathToGUID(path)));
            Undo.PerformUndo();
            Assert.That(Get(image, "sprite"), Is.Null);
            Assert.That(Get(image, "type").ToString(), Is.EqualTo("Simple"));
            Assert.That((Color)Get(image, "color"), Is.EqualTo(Color.white));
            Undo.PerformRedo();
            Assert.That(Get(image, "type").ToString(), Is.EqualTo(type));
            Assert.That(((Sprite)Get(image, "sprite")).name, Is.EqualTo("Primary"));
            Assert.That(System.IO.File.ReadAllBytes(path), Is.EqualTo(assetBefore));
            Assert.That(System.IO.File.ReadAllBytes(path + ".meta"), Is.EqualTo(metaBefore));
        }

        [TestCase("Primary")]
        [TestCase("Secondary")]
        public void ExactNamedSubSpriteIsSelectedAndIdentityIsActual(string name)
        {
            string path = Sprites();
            var go = Target();
            var response = Call(
                go,
                new JObject
                {
                    ["sprite"] = new JObject { ["path"] = path, ["name"] = name },
                }
            );
            Success(response);
            var sprite = (Sprite)Get(go.GetComponent(imageType), "sprite");
            Assert.That(sprite.name, Is.EqualTo(name));
            Assert.That(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sprite, out string guid, out long localId), Is.True);
            Assert.That(response["data"]["image"]["sprite"]["guid"].Value<string>(), Is.EqualTo(guid));
            Assert.That(
                response["data"]["image"]["sprite"]["local_id"].Value<string>(),
                Is.EqualTo(localId.ToString(System.Globalization.CultureInfo.InvariantCulture))
            );
        }

        [Test]
        public void ExplicitNullClearsOnlyTheBaseSprite()
        {
            string path = Sprites();
            var sprites = AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().ToArray();
            var go = Target();
            var image = go.GetComponent(imageType);
            Set(image, "sprite", sprites[0]);
            Set(image, "overrideSprite", sprites[1]);
            var response = Call(go, new JObject { ["sprite"] = JValue.CreateNull() });
            Success(response);
            Assert.That(Get(image, "sprite"), Is.Null);
            Assert.That(Get(image, "overrideSprite"), Is.SameAs(sprites[1]));
            Assert.That(response["data"]["image"]["sprite"].Type, Is.EqualTo(JTokenType.Null));
            Assert.That(response["data"]["image"]["overrideSprite"]["name"].Value<string>(), Is.EqualTo(sprites[1].name));
        }

        [TestCase("{\"type\":\"Unknown\"}")]
        [TestCase("{\"fillAmount\":1.1}")]
        [TestCase("{\"fillAmount\":true}")]
        [TestCase("{\"fillOrigin\":4}")]
        [TestCase("{\"type\":\"Simple\",\"fillAmount\":0.5}")]
        [TestCase("{\"type\":\"Filled\",\"fillMethod\":\"Horizontal\",\"fillOrigin\":2}")]
        [TestCase("{\"type\":\"Sliced\",\"sprite\":null}")]
        [TestCase("{\"preserveAspect\":\"true\"}")]
        [TestCase("{\"unknown\":1}")]
        public void InvalidFinalCombinationsDoNotApplyEarlierColorOrOpenUndo(string json)
        {
            var go = Target();
            var image = go.GetComponent(imageType);
            var props = new JObject { ["color"] = new JArray(.1, .2, .3, .4) };
            props.Merge(JObject.Parse(json));
            int group = Undo.GetCurrentGroup();
            bool dirty = scene.isDirty;
            Failure(Call(go, props));
            Assert.That((Color)Get(image, "color"), Is.EqualTo(Color.white));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(scene.isDirty, Is.EqualTo(dirty));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FillMethodCannotSilentlyResetAnUnrequestedValidOrigin(bool explicitOrigin)
        {
            var go = Target();
            var image = go.GetComponent(imageType);
            EnumValue(image, "type", "Filled");
            EnumValue(image, "fillMethod", "Radial360");
            Set(image, "fillOrigin", 1);
            var props = new JObject();
            if (explicitOrigin)
                props["fillOrigin"] = 1; // Deliberately precedes fillMethod.
            props["fillMethod"] = "Horizontal";
            props["fillClockwise"] = false;
            var response = Call(go, props);
            Success(response);
            Assert.That(Get(image, "fillOrigin"), Is.EqualTo(1));
            Assert.That(response["data"]["image"]["fillOrigin"].Value<int>(), Is.EqualTo(1));
            Assert.That(Get(image, "fillClockwise"), Is.False);
            Assert.That(response["data"]["image"]["fillClockwise"].Value<bool>(), Is.False);
        }

        [Test]
        public void IncompatibleRetainedOriginRequiresExplicitReplacementBeforeMutation()
        {
            var go = Target();
            var image = go.GetComponent(imageType);
            EnumValue(image, "type", "Filled");
            EnumValue(image, "fillMethod", "Radial360");
            Set(image, "fillOrigin", 3);
            Failure(Call(go, new JObject { ["fillMethod"] = "Vertical", ["fillAmount"] = .4 }));
            Assert.That(Get(image, "fillMethod").ToString(), Is.EqualTo("Radial360"));
            Assert.That(Get(image, "fillOrigin"), Is.EqualTo(3));
            Assert.That(Get(image, "fillAmount"), Is.EqualTo(1f));
        }

        [Test]
        public void AmbiguousPathRefusesWithoutPickingTheFirstSprite()
        {
            string path = Sprites();
            var go = Target();
            Failure(Call(go, new JObject { ["color"] = new JArray(0, 0, 0, 0), ["sprite"] = path }));
            Assert.That(Get(go.GetComponent(imageType), "sprite"), Is.Null);
            Assert.That((Color)Get(go.GetComponent(imageType), "color"), Is.EqualTo(Color.white));
        }

        [TestCase("missing")]
        [TestCase("wrong_name")]
        [TestCase("invalid_shape")]
        public void InvalidSpriteSelectorsCannotApplyEarlierFieldsOrChangeAssetBytes(string failure)
        {
            string path = Sprites();
            byte[] before = System.IO.File.ReadAllBytes(path);
            byte[] meta = System.IO.File.ReadAllBytes(path + ".meta");
            JToken selector = new JObject { ["path"] = path, ["name"] = "Missing" };
            if (failure == "missing")
                selector = folder + "/missing.asset";
            if (failure == "invalid_shape")
                selector = new JArray(path);
            var go = Target();
            int group = Undo.GetCurrentGroup();
            Failure(Call(go, new JObject { ["color"] = new JArray(0, 0, 0, 0), ["sprite"] = selector }));
            Assert.That((Color)Get(go.GetComponent(imageType), "color"), Is.EqualTo(Color.white));
            Assert.That(Get(go.GetComponent(imageType), "sprite"), Is.Null);
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(System.IO.File.ReadAllBytes(path), Is.EqualTo(before));
            Assert.That(System.IO.File.ReadAllBytes(path + ".meta"), Is.EqualTo(meta));
        }

        [Test]
        public void SlicedTypeRequiresAnActuallyBorderedBaseSprite()
        {
            string path = Sprites(false);
            var go = Target();
            Failure(
                Call(
                    go,
                    new JObject
                    {
                        ["color"] = new JArray(0, 0, 0, 0),
                        ["type"] = "Sliced",
                        ["sprite"] = new JObject { ["path"] = path, ["name"] = "Primary" },
                    }
                )
            );
            Assert.That((Color)Get(go.GetComponent(imageType), "color"), Is.EqualTo(Color.white));
            Assert.That(Get(go.GetComponent(imageType), "type").ToString(), Is.EqualTo("Simple"));
            Assert.That(Get(go.GetComponent(imageType), "sprite"), Is.Null);
        }

        [Test]
        public void MissingImageDoesNotAddAComponent()
        {
            var go = Target(false);
            int before = go.GetComponents<Component>().Length;
            Failure(Call(go, new JObject { ["preserveAspect"] = true }));
            Assert.That(go.GetComponents<Component>().Length, Is.EqualTo(before));
        }

        [Test]
        public void PrefabOverrideSurvivesOwnedSceneSaveReloadAndLeavesSourceUnchanged()
        {
            Folder();
            var source = Target();
            string prefab = folder + "/image.prefab";
            var asset = PrefabUtility.SaveAsPrefabAsset(source, prefab);
            byte[] prefabBefore = System.IO.File.ReadAllBytes(prefab);
            byte[] metaBefore = System.IO.File.ReadAllBytes(prefab + ".meta");
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset, scene);
            string name = prefix + "_Instance";
            instance.name = name;
            PrefabUtility.RecordPrefabInstancePropertyModifications(instance);
            var correspondingSource = PrefabUtility.GetCorrespondingObjectFromSource(instance);
            Assert.That(correspondingSource, Is.EqualTo(asset));
            Assert.That(
                PrefabUtility.GetPropertyModifications(instance).Any(m => m.target == correspondingSource && m.propertyPath == "m_Name" && m.value == name),
                Is.True
            );
            var image = instance.GetComponent(imageType);
            var response = Call(
                instance,
                new JObject
                {
                    ["type"] = "Filled",
                    ["fillAmount"] = .35,
                    ["fillMethod"] = "Vertical",
                    ["fillOrigin"] = 1,
                }
            );
            Success(response);
            Assert.That(Get(image, "fillAmount"), Is.EqualTo(.35f));
            Assert.That(response["data"]["image"]["fillAmount"].Value<float>(), Is.EqualTo(.35f));
            Assert.That(PrefabUtility.GetPropertyModifications(instance).Any(m => m.propertyPath == "m_FillAmount"), Is.True);
            Assert.That(Get(asset.GetComponent(imageType), "fillAmount"), Is.EqualTo(1f));
            Undo.PerformUndo();
            Assert.That(Get(image, "fillAmount"), Is.EqualTo(1f));
            Assert.That(Get(image, "type").ToString(), Is.EqualTo("Simple"));
            Undo.PerformRedo();
            Assert.That(Get(image, "fillAmount"), Is.EqualTo(.35f));
            Assert.That(Get(asset.GetComponent(imageType), "fillAmount"), Is.EqualTo(1f));
            string path = folder + "/image.unity";
            Assert.That(EditorSceneManager.SaveScene(scene, path), Is.True);
            Assert.That(EditorSceneManager.CloseScene(scene, true), Is.True);
            scene = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
            var reopened = scene.GetRootGameObjects().Single(go => go.name == name).GetComponent(imageType);
            Assert.That(Get(reopened, "fillAmount"), Is.EqualTo(.35f));
            Assert.That(Get(reopened, "fillOrigin"), Is.EqualTo(1));
            Assert.That(Get(asset.GetComponent(imageType), "fillAmount"), Is.EqualTo(1f));
            Failure(Call(asset, new JObject { ["color"] = new JArray(0, 0, 0, 0) }));
            Assert.That((Color)Get(asset.GetComponent(imageType), "color"), Is.EqualTo(Color.white));
            Assert.That(System.IO.File.ReadAllBytes(prefab), Is.EqualTo(prefabBefore));
            Assert.That(System.IO.File.ReadAllBytes(prefab + ".meta"), Is.EqualTo(metaBefore));
        }
    }
}
