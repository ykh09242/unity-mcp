using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class UguiCreationAdmissionTests
    {
        private GameObject root;

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("UGUI_Creation_" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Canvas));
        }

        [TearDown]
        public void TearDown()
        {
            if (root == null)
                return;
            foreach (var component in root.GetComponentsInChildren<Component>(true))
                if (component != null)
                {
                    Undo.ClearUndo(component);
                    Undo.ClearUndo(component.gameObject);
                }
            UnityEngine.Object.DestroyImmediate(root);
        }

        private JObject Input() =>
            new JObject
            {
                ["action"] = "create",
                ["element_type"] = "panel",
                ["parent"] = root.GetInstanceIDCompat(),
            };

        private static JObject Call(JObject input) => JObject.FromObject(ManageUGUI.HandleCommand(input));

        [TestCase("false")]
        [TestCase("true")]
        [TestCase("0")]
        [TestCase("1.5")]
        [TestCase("{}")]
        [TestCase("[]")]
        [TestCase("[1]")]
        public void NonStringNameRejectsBeforeUndoAndChildCreation(string encoded)
        {
            var input = Input();
            input["name"] = JToken.Parse(encoded);
            int group = Undo.GetCurrentGroup();
            bool dirty = EditorUtility.IsDirty(root);
            var response = Call(input);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("name must be a string.", response.Value<string>("error"));
            Assert.AreEqual(0, root.transform.childCount);
            Assert.AreEqual(group, Undo.GetCurrentGroup());
            Assert.AreEqual(dirty, EditorUtility.IsDirty(root));
        }

        [TestCase("null")]
        [TestCase("\"\"")]
        [TestCase("\" \"")]
        [TestCase("\"a/b\"")]
        [TestCase("\"a\\nb\"")]
        public void ExistingInvalidStringAndNullNameRetainHierarchyError(string encoded)
        {
            var input = Input();
            input["name"] = JToken.Parse(encoded);
            int group = Undo.GetCurrentGroup();
            var response = Call(input);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("name must be a non-empty single hierarchy name.", response.Value<string>("error"));
            Assert.AreEqual(0, root.transform.childCount);
            Assert.AreEqual(group, Undo.GetCurrentGroup());
        }

        [Test]
        public void InvalidKindAndGlobalOptionsKeepTheirPriorityOverName()
        {
            var input = Input();
            input["name"] = false;
            input["element_type"] = "unknown";
            Assert.AreEqual("element_type must be canvas, panel, image, button or text.", Call(input).Value<string>("error"));
            input["include_inactive"] = 0;
            Assert.AreEqual("include_inactive must be a boolean.", Call(input).Value<string>("error"));
            Assert.AreEqual(0, root.transform.childCount);
        }

        [TestCase("False")]
        [TestCase("0")]
        [TestCase("{}")]
        [TestCase("[]")]
        [TestCase(" Name ")]
        public void StringScalarLookingNamesRemainAuthoredExactlyAndUndoable(string name)
        {
            if (UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image") == null)
                Assert.Ignore("This valid creation control requires the optional uGUI package.");
            var input = Input();
            input["name"] = name;
            var response = Call(input);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(1, root.transform.childCount);
            Assert.AreEqual(name, root.transform.GetChild(0).name);
            Undo.PerformUndo();
            Assert.AreEqual(0, root.transform.childCount);
        }

        [TestCase("ko-KR", "2026-10-09T12:34:56Z")]
        [TestCase("en-US", "2026-10-09T12:34:56Z")]
        [TestCase("de-DE", "2026-10-09T12:34:56+09:00")]
        [TestCase("fr-FR", "2026-10-09T12:34:56+09:00")]
        [TestCase("ko-KR", "2026-10-09")]
        [TestCase("en-US", "2026-10-09T12:34:56Z-label")]
        public void ActualCommandJsonDateAndOrdinaryStringsPreserveLegacyNameConversion(string culture, string wireName)
        {
            var previousCulture = System.Globalization.CultureInfo.CurrentCulture;
            var previousUiCulture = System.Globalization.CultureInfo.CurrentUICulture;
            try
            {
                System.Globalization.CultureInfo.CurrentCulture = System.Globalization.CultureInfo.GetCultureInfo(culture);
                System.Globalization.CultureInfo.CurrentUICulture = System.Globalization.CultureInfo.GetCultureInfo(culture);
                var input = Input();
                input["name"] = wireName;
                string commandJson = new JObject { ["type"] = "manage_ugui", ["params"] = input }.ToString(Newtonsoft.Json.Formatting.None);
                var command = Newtonsoft.Json.JsonConvert.DeserializeObject<MCPForUnity.Editor.Models.Command>(commandJson);
                string authored = new ToolParams(command.@params).Get("name");
                bool invalidName = authored.IndexOfAny(new[] { '/', '\\', '\0', '\r', '\n' }) >= 0;
                if (!invalidName && UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image") == null)
                    Assert.Ignore("This valid creation control requires the optional uGUI package.");
                int group = Undo.GetCurrentGroup();
                var response = Call(command.@params);
                if (invalidName)
                {
                    Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                    Assert.AreEqual("name must be a non-empty single hierarchy name.", response.Value<string>("error"));
                    Assert.AreEqual(0, root.transform.childCount);
                    Assert.AreEqual(group, Undo.GetCurrentGroup());
                }
                else
                {
                    Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                    Assert.AreEqual(1, root.transform.childCount);
                    Assert.AreEqual(authored, root.transform.GetChild(0).name);
                    Undo.PerformUndo();
                    Assert.AreEqual(0, root.transform.childCount);
                }
            }
            finally
            {
                System.Globalization.CultureInfo.CurrentCulture = previousCulture;
                System.Globalization.CultureInfo.CurrentUICulture = previousUiCulture;
            }
        }

        [Test]
        public void OmittedNameAndCamelElementTypeRetainPanelDefault()
        {
            if (UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image") == null)
                Assert.Ignore("This valid creation control requires the optional uGUI package.");
            var input = Input();
            input.Remove("element_type");
            input["elementType"] = "PaNeL";
            var response = Call(input);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(1, root.transform.childCount);
            Assert.AreEqual("Panel", root.transform.GetChild(0).name);
        }
    }
}
