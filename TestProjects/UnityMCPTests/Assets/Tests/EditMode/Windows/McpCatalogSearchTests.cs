using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Windows.Components.Resources;
using MCPForUnity.Editor.Windows.Components.Tools;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class McpCatalogSearchTests
    {
        private readonly Dictionary<string, object> previousServices = new();
        private FakeToolDiscovery tools;
        private FakeResourceDiscovery resources;

        [SetUp]
        public void SetUp()
        {
            foreach (string fieldName in new[] { "_toolDiscoveryService", "_resourceDiscoveryService", "_transportManager" })
                previousServices[fieldName] = ServiceField(fieldName).GetValue(null);
            tools = new FakeToolDiscovery();
            resources = new FakeResourceDiscovery();
            MCPServiceLocator.Register<IToolDiscoveryService>(tools);
            MCPServiceLocator.Register<IResourceDiscoveryService>(resources);
            MCPServiceLocator.Register(new TransportManager());
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var previous in previousServices)
                ServiceField(previous.Key).SetValue(null, previous.Value);
            previousServices.Clear();
        }

        [TestCase(true, true, true, "Off by default")]
        [TestCase(true, true, false, "On by default")]
        [TestCase(true, false, true, "Off by default")]
        [TestCase(true, false, false, "On by default")]
        [TestCase(false, true, true, "Off by default")]
        [TestCase(false, true, false, "On by default")]
        [TestCase(false, false, true, "Off by default")]
        [TestCase(false, false, false, "Off by default")]
        public void ToolDefaultTag_RespectsExplicitConsent(bool builtIn, bool autoRegister, bool requiresConsent, string expected)
        {
            var tool = new ToolMetadata
            {
                Name = "default_tag_probe",
                IsBuiltIn = builtIn,
                AutoRegister = autoRegister,
                RequiresExplicitConsent = requiresConsent,
            };
            var section = new McpToolsSection(CreateRoot(true));
            var row = (VisualElement)
                typeof(McpToolsSection).GetMethod("CreateToolRow", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(section, new object[] { tool });

            Assert.That(row.Query<Label>(className: "tool-tag").ToList()[0].text, Is.EqualTo(expected));
            Assert.That(tools.WriteCalls, Is.Zero);
        }

        [TestCase("  CAMERA ", true)]
        [TestCase("render a scene", true)]
        [TestCase(" VFX ", true)]
        [TestCase("shaders", true)]
        [TestCase(" ", true)]
        [TestCase("missing", false)]
        public void ToolSearch_MatchesTrimmedNameDescriptionAndGroup(string query, bool expected)
        {
            var tool = new ToolMetadata
            {
                Name = "manage_camera",
                Description = "Render a scene",
                Group = "vfx",
                IsBuiltIn = true,
            };
            Assert.That(Matches(typeof(McpToolsSection), tool, query), Is.EqualTo(expected));
        }

        [Test]
        public void ToolSearch_HandlesNullMetadataFieldsAndCustomCategory()
        {
            Assert.That(Matches(typeof(McpToolsSection), new ToolMetadata { Group = null, IsBuiltIn = true }, "core"), Is.True);
            Assert.That(Matches(typeof(McpToolsSection), new ToolMetadata(), "custom"), Is.True);
            Assert.That(Matches(typeof(McpToolsSection), null, null), Is.False);
        }

        [TestCase(" PROJECT ", true)]
        [TestCase("loaded assemblies", true)]
        [TestCase(" BUILT-IN ", true)]
        [TestCase(" ", true)]
        [TestCase("missing", false)]
        public void ResourceSearch_MatchesTrimmedNameDescriptionAndCategory(string query, bool expected)
        {
            var resource = new ResourceMetadata
            {
                Name = "project_info",
                Description = "Read loaded assemblies",
                IsBuiltIn = true,
            };
            Assert.That(Matches(typeof(McpResourcesSection), resource, query), Is.EqualTo(expected));
        }

        [Test]
        public void ResourceSearch_HandlesNullMetadataFieldsAndCustomCategory()
        {
            Assert.That(Matches(typeof(McpResourcesSection), new ResourceMetadata(), "custom"), Is.True);
            Assert.That(Matches(typeof(McpResourcesSection), new ResourceMetadata(), "missing"), Is.False);
            Assert.That(Matches(typeof(McpResourcesSection), null, null), Is.False);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void Search_UpdatesVisibilityCountAndEmptyState_WithoutChangingEnablement(bool toolCatalog)
        {
            var (section, root) = CreateCatalog(toolCatalog);
            string prefix = toolCatalog ? "tools" : "resources";
            var originalRows = root.Query<VisualElement>(className: "tool-item").ToList();
            var toggles = originalRows.Select(row => row.Q<Toggle>()).ToList();
            bool[] originalStates = toggles.Select(toggle => toggle.value).ToArray();
            var foldouts = root.Query<Foldout>().ToList();
            foldouts[0].SetValueWithoutNotify(false);
            foldouts[1].SetValueWithoutNotify(true);

            Search(root, prefix, "  ALPHA ");
            Assert.That(root.Q<Label>(prefix + "-visible-count").text, Is.EqualTo("1 / 2 shown"));
            Assert.That(originalRows.Count(row => !row.ClassListContains("catalog-hidden")), Is.EqualTo(1));
            Assert.That(foldouts[0].value, Is.True);
            Assert.That(foldouts[1].ClassListContains("catalog-hidden"), Is.True);

            Search(root, prefix, "no-match");
            Assert.That(root.Q<Label>(prefix + "-visible-count").text, Is.EqualTo("0 / 2 shown"));
            Assert.That(root.Q<Label>(prefix + "-empty-state").ClassListContains("catalog-hidden"), Is.False);
            Assert.That(foldouts.All(foldout => foldout.ClassListContains("catalog-hidden")), Is.True);

            Search(root, prefix, "  ");
            Assert.That(root.Q<Label>(prefix + "-visible-count").text, Is.EqualTo("2 / 2 shown"));
            Assert.That(root.Q<Label>(prefix + "-empty-state").ClassListContains("catalog-hidden"), Is.True);
            Assert.That(foldouts[0].value, Is.False);
            Assert.That(foldouts[1].value, Is.True);
            Assert.That(originalRows.All(row => !row.ClassListContains("catalog-hidden")), Is.True);
            Assert.That(root.Query<VisualElement>(className: "tool-item").ToList(), Is.EqualTo(originalRows));
            Assert.That(toggles.Select(toggle => toggle.value), Is.EqualTo(originalStates));
            Assert.That(toolCatalog ? tools.DiscoverCalls : resources.DiscoverCalls, Is.EqualTo(1));
            Assert.That(toolCatalog ? tools.WriteCalls : resources.WriteCalls, Is.Zero);
            Assert.That(toolCatalog ? tools.InvalidateCalls : resources.InvalidateCalls, Is.Zero);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void BulkActions_IncludeItemsHiddenBySearch(bool toolCatalog)
        {
            var (section, root) = CreateCatalog(toolCatalog);
            Search(root, toolCatalog ? "tools" : "resources", "alpha");
            string method = toolCatalog ? "SetAllToolsState" : "SetAllResourcesState";
            Invoke(section, method, true);
            Assert.That(root.Query<VisualElement>(className: "tool-item").ToList().All(row => row.Q<Toggle>().value), Is.True);
            Assert.That(toolCatalog ? tools.IsToolEnabled("beta") : resources.IsResourceEnabled("beta"), Is.True);
            Invoke(section, method, false);
            Assert.That(root.Query<VisualElement>(className: "tool-item").ToList().All(row => !row.Q<Toggle>().value), Is.True);
            Assert.That(toolCatalog ? tools.IsToolEnabled("beta") : resources.IsResourceEnabled("beta"), Is.False);
        }

        [TestCase(true, true, true)]
        [TestCase(true, true, false)]
        [TestCase(true, false, true)]
        [TestCase(true, false, false)]
        [TestCase(false, true, true)]
        [TestCase(false, true, false)]
        [TestCase(false, false, true)]
        [TestCase(false, false, false)]
        public void BulkActions_UseAuthoritativeStateAndSynchronizeCachedUi(bool toolCatalog, bool requested, bool needsChange)
        {
            var (section, root) = CreateCatalog(toolCatalog);
            var rows = root.Query<VisualElement>(className: "tool-item").ToList();
            var alphaToggle = rows.Single(row => row.Q<Toggle>().label == "alpha").Q<Toggle>();
            alphaToggle.SetValueWithoutNotify(needsChange ? requested : !requested);
            if (toolCatalog)
            {
                tools.SetToolEnabled("alpha", needsChange ? !requested : requested);
                tools.SetToolEnabled("beta", requested);
            }
            else
            {
                resources.SetResourceEnabled("alpha", needsChange ? !requested : requested);
                resources.SetResourceEnabled("beta", requested);
            }
            int previousWrites = toolCatalog ? tools.WriteCalls : resources.WriteCalls;
            Search(root, toolCatalog ? "tools" : "resources", "beta");

            Invoke(section, toolCatalog ? "SetAllToolsState" : "SetAllResourcesState", requested);

            Assert.That(toolCatalog ? tools.IsToolEnabled("alpha") : resources.IsResourceEnabled("alpha"), Is.EqualTo(requested));
            Assert.That(rows.All(row => row.Q<Toggle>().value == requested), Is.True);
            Assert.That((toolCatalog ? tools.WriteCalls : resources.WriteCalls) - previousWrites, Is.EqualTo(needsChange ? 1 : 0));
        }

        [TestCase(true, true)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(false, false)]
        public void GroupAction_UsesAuthoritativeStateAndSynchronizesCachedUi(bool requested, bool needsChange)
        {
            var (section, root) = CreateCatalog(true);
            var alphaToggle = root.Query<VisualElement>(className: "tool-item").ToList().Single(row => row.Q<Toggle>().label == "alpha").Q<Toggle>();
            alphaToggle.SetValueWithoutNotify(needsChange ? requested : !requested);
            tools.SetToolEnabled("alpha", needsChange ? !requested : requested);
            int previousWrites = tools.WriteCalls;
            Search(root, "tools", "beta");
            var foldout = root.Query<Foldout>().ToList()[0];

            Invoke(section, "SetGroupToolsState", tools.Items.Where(tool => tool.IsBuiltIn).ToList(), requested, foldout, "Core Tools");

            Assert.That(tools.IsToolEnabled("alpha"), Is.EqualTo(requested));
            Assert.That(alphaToggle.value, Is.EqualTo(requested));
            Assert.That(tools.WriteCalls - previousWrites, Is.EqualTo(needsChange ? 1 : 0));
        }

        [Test]
        public void GroupAction_IncludesHiddenTools()
        {
            tools.Items.Add(
                new ToolMetadata
                {
                    Name = "alpha_hidden",
                    Group = "core",
                    IsBuiltIn = true,
                }
            );
            var (section, root) = CreateCatalog(true);
            Search(root, "tools", "First item");
            var groupTools = tools.Items.Where(tool => tool.IsBuiltIn).ToList();
            var foldout = root.Query<Foldout>().ToList()[0];
            Invoke(section, "SetGroupToolsState", groupTools, true, foldout, "Core Tools");
            Assert.That(tools.IsToolEnabled("alpha_hidden"), Is.True);
            Assert.That(
                root.Query<VisualElement>(className: "tool-item").ToList().Single(row => row.Q<Toggle>().label == "alpha_hidden").Q<Toggle>().value,
                Is.True
            );
        }

        [TestCase(true)]
        [TestCase(false)]
        public void Refresh_RetainsSearchQueryAndReappliesVisibility(bool toolCatalog)
        {
            var (section, root) = CreateCatalog(toolCatalog);
            string prefix = toolCatalog ? "tools" : "resources";
            Search(root, prefix, "alpha");
            Invoke(section, "Refresh");
            Assert.That(root.Q<TextField>(prefix + "-search").value, Is.EqualTo("alpha"));
            Assert.That(root.Q<Label>(prefix + "-visible-count").text, Is.EqualTo("1 / 2 shown"));
        }

        [TestCase(true, true)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(false, false)]
        public void SearchFoldoutChanges_AreTemporaryAcrossClearAndRefresh(bool toolCatalog, bool initialExpanded)
        {
            string key = toolCatalog ? EditorPrefKeys.ToolFoldoutStatePrefix + "group-core" : EditorPrefKeys.ResourceFoldoutStatePrefix + "built-in";
            bool hadKey = EditorPrefs.HasKey(key);
            bool previous = EditorPrefs.GetBool(key);
            try
            {
                EditorPrefs.SetBool(key, initialExpanded);
                var (section, root) = CreateCatalog(toolCatalog);
                string prefix = toolCatalog ? "tools" : "resources";
                var foldout = root.Query<Foldout>().ToList()[0];
                Search(root, prefix, "alpha");

                ChangeFoldout(foldout, !initialExpanded);
                ChangeFoldout(foldout, initialExpanded);
                ChangeFoldout(foldout, !initialExpanded);
                Assert.That(EditorPrefs.GetBool(key), Is.EqualTo(initialExpanded));

                Search(root, prefix, string.Empty);
                Assert.That(foldout.value, Is.EqualTo(initialExpanded));
                Invoke(section, "Refresh");
                foldout = root.Query<Foldout>().ToList()[0];
                Assert.That(foldout.value, Is.EqualTo(initialExpanded));
                Assert.That(EditorPrefs.GetBool(key), Is.EqualTo(initialExpanded));

                Search(root, prefix, "   ");
                ChangeFoldout(foldout, !initialExpanded);
                Assert.That(EditorPrefs.GetBool(key), Is.EqualTo(!initialExpanded));
            }
            finally
            {
                if (hadKey)
                    EditorPrefs.SetBool(key, previous);
                else
                    EditorPrefs.DeleteKey(key);
            }
        }

        [TestCase(true)]
        [TestCase(false)]
        public void EmptyCatalog_ShowsZeroAndDiscoveryEmptyState(bool toolCatalog)
        {
            VisualElement root = CreateRoot(toolCatalog);
            object section = toolCatalog ? (object)new McpToolsSection(root) : new McpResourcesSection(root);
            Invoke(section, "Refresh");
            string prefix = toolCatalog ? "tools" : "resources";
            Assert.That(root.Q<Label>(prefix + "-visible-count").text, Is.EqualTo("0 / 0 shown"));
            Assert.That(root.Q<Label>(prefix + "-empty-state").ClassListContains("catalog-hidden"), Is.False);
            StringAssert.Contains("discovered", root.Q<Label>(prefix + "-empty-state").text);
        }

        [Test]
        public void ResourceToggle_DoesNotOverwriteCategoryExpansionPreference()
        {
            string key = EditorPrefKeys.ResourceFoldoutStatePrefix + "built-in";
            bool hadKey = EditorPrefs.HasKey(key);
            bool previous = EditorPrefs.GetBool(key);
            try
            {
                EditorPrefs.SetBool(key, false);
                var (_, root) = CreateCatalog(false);
                var toggle = root.Query<VisualElement>(className: "tool-item").ToList()[0].Q<Toggle>();
                toggle.SetValueWithoutNotify(true);
                using (var evt = ChangeEvent<bool>.GetPooled(false, true))
                {
                    evt.target = toggle;
                    toggle.SendEvent(evt);
                }
                Assert.That(resources.IsResourceEnabled("alpha"), Is.True);
                Assert.That(EditorPrefs.GetBool(key), Is.False);
            }
            finally
            {
                if (hadKey)
                    EditorPrefs.SetBool(key, previous);
                else
                    EditorPrefs.DeleteKey(key);
            }
        }

        private (object section, VisualElement root) CreateCatalog(bool toolCatalog)
        {
            tools.Items.Add(
                new ToolMetadata
                {
                    Name = "alpha",
                    Description = "First item",
                    Group = "core",
                    IsBuiltIn = true,
                }
            );
            tools.Items.Add(
                new ToolMetadata
                {
                    Name = "beta",
                    Description = "Second item",
                    IsBuiltIn = false,
                }
            );
            resources.Items.Add(
                new ResourceMetadata
                {
                    Name = "alpha",
                    Description = "First item",
                    IsBuiltIn = true,
                }
            );
            resources.Items.Add(
                new ResourceMetadata
                {
                    Name = "beta",
                    Description = "Second item",
                    IsBuiltIn = false,
                }
            );
            VisualElement root = CreateRoot(toolCatalog);
            object section = toolCatalog ? (object)new McpToolsSection(root) : new McpResourcesSection(root);
            Invoke(section, "Refresh");
            return (section, root);
        }

        private static VisualElement CreateRoot(bool toolCatalog)
        {
            string prefix = toolCatalog ? "tools" : "resources";
            var root = new VisualElement();
            root.Add(new TextField { name = prefix + "-search" });
            root.Add(new Label { name = prefix + "-visible-count" });
            root.Add(new Label { name = prefix + "-empty-state" });
            root.Add(new VisualElement { name = toolCatalog ? "tool-category-container" : "resource-category-container" });
            return root;
        }

        private static void Search(VisualElement root, string prefix, string query)
        {
            var field = root.Q<TextField>(prefix + "-search");
            string previous = field.value;
            field.SetValueWithoutNotify(query);
            using (var evt = ChangeEvent<string>.GetPooled(previous, query))
            {
                evt.target = field;
                field.SendEvent(evt);
            }
        }

        private static void ChangeFoldout(Foldout foldout, bool expanded)
        {
            bool previous = foldout.value;
            if (previous == expanded)
                return;
            foldout.SetValueWithoutNotify(expanded);
            using (var evt = ChangeEvent<bool>.GetPooled(previous, expanded))
            {
                evt.target = foldout;
                foldout.SendEvent(evt);
            }
        }

        private static FieldInfo ServiceField(string name) => typeof(MCPServiceLocator).GetField(name, BindingFlags.Static | BindingFlags.NonPublic);

        private static bool Matches(Type type, object item, string query) =>
            (bool)type.GetMethod("MatchesSearch", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new[] { item, query });

        private static void Invoke(object target, string method, params object[] args) =>
            target.GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public).Invoke(target, args);

        private sealed class FakeToolDiscovery : IToolDiscoveryService
        {
            public List<ToolMetadata> Items { get; } = new();
            private readonly Dictionary<string, bool> enabled = new();
            public int DiscoverCalls { get; private set; }
            public int WriteCalls { get; private set; }
            public int InvalidateCalls { get; private set; }

            public List<ToolMetadata> DiscoverAllTools()
            {
                DiscoverCalls++;
                return Items.ToList();
            }

            public ToolMetadata GetToolMetadata(string name) => Items.FirstOrDefault(item => item.Name == name);

            public List<ToolMetadata> GetEnabledTools() => Items.Where(item => IsToolEnabled(item.Name)).ToList();

            public bool IsToolEnabled(string name) => enabled.TryGetValue(name, out bool value) && value;

            public void SetToolEnabled(string name, bool value)
            {
                WriteCalls++;
                enabled[name] = value;
            }

            public void InvalidateCache() => InvalidateCalls++;
        }

        private sealed class FakeResourceDiscovery : IResourceDiscoveryService
        {
            public List<ResourceMetadata> Items { get; } = new();
            private readonly Dictionary<string, bool> enabled = new();
            public int DiscoverCalls { get; private set; }
            public int WriteCalls { get; private set; }
            public int InvalidateCalls { get; private set; }

            public List<ResourceMetadata> DiscoverAllResources()
            {
                DiscoverCalls++;
                return Items.ToList();
            }

            public ResourceMetadata GetResourceMetadata(string name) => Items.FirstOrDefault(item => item.Name == name);

            public List<ResourceMetadata> GetEnabledResources() => Items.Where(item => IsResourceEnabled(item.Name)).ToList();

            public bool IsResourceEnabled(string name) => enabled.TryGetValue(name, out bool value) && value;

            public void SetResourceEnabled(string name, bool value)
            {
                WriteCalls++;
                enabled[name] = value;
            }

            public void InvalidateCache() => InvalidateCalls++;
        }
    }
}
