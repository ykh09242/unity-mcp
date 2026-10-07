using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Optional PanelRenderer support without raising the minimum Unity version.</summary>
    internal sealed class UIComponentAdapter
    {
        private const string PanelRendererName = "UnityEngine.UIElements.PanelRenderer";
        internal Component Component { get; }
        internal string Kind => Component is UIDocument ? "ui_document" : "panel_renderer";
        internal string DisplayName => Component is UIDocument ? "UIDocument" : "PanelRenderer";

        internal UIComponentAdapter(Component component)
        {
            Component =
                component != null ? component : throw new InvalidOperationException("The UI component could not be created or resolved in this Unity editor.");
        }

        internal static Type PanelRendererType =>
            AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType(PanelRendererName, false))
                .FirstOrDefault(type => type != null && typeof(Component).IsAssignableFrom(type));

        internal static Type ResolveType(GameObject go, string selection, bool creating)
        {
            string normalized = (selection ?? "auto").Replace("_", "").ToLowerInvariant();
            Type type;
            switch (normalized)
            {
                case "uidocument":
                    type = typeof(UIDocument);
                    break;
                case "panelrenderer":
                    type = PanelRendererType;
                    if (type == null)
                        throw new ArgumentException(
                            "PanelRenderer is unavailable in this Unity editor. Use component_type='ui_document' or Unity 6.5 or newer."
                        );
                    break;
                case "auto":
                    // Preserve existing documents; prefer the new renderer only for new attachments.
                    if (go != null && go.GetComponent<UIDocument>() != null)
                        return typeof(UIDocument);
                    type = PanelRendererType;
                    if (type != null && (creating || go.GetComponent(type) != null))
                        break;
                    type = typeof(UIDocument);
                    break;
                default:
                    throw new ArgumentException("component_type must be 'auto', 'ui_document', or 'panel_renderer'.");
            }
            if (!creating && go.GetComponent(type) == null)
                throw new ArgumentException($"GameObject '{go.name}' has no {type.Name} component.");
            if (type != typeof(UIDocument))
                ValidatePanelContract(type);
            return type;
        }

        internal static UIComponentAdapter Resolve(GameObject go, string selection) =>
            new UIComponentAdapter(go.GetComponent(ResolveType(go, selection, false)));

        internal static UIComponentAdapter Attach(GameObject go, Type type) => new UIComponentAdapter(go.GetComponent(type) ?? Undo.AddComponent(go, type));

        internal VisualTreeAsset SourceAsset =>
            Component is UIDocument document ? document.visualTreeAsset : (VisualTreeAsset)Property("visualTreeAsset").GetValue(Component);

        internal PanelSettings PanelSettings =>
            Component is UIDocument document ? document.panelSettings : (PanelSettings)Property("panelSettings").GetValue(Component);

        internal void Configure(VisualTreeAsset source, PanelSettings settings, int sortOrder)
        {
            if (Component is UIDocument document)
            {
                document.panelSettings = settings;
                document.visualTreeAsset = source;
                document.sortingOrder = sortOrder;
            }
            else
            {
                Property("panelSettings").SetValue(Component, settings);
                Property("visualTreeAsset").SetValue(Component, source);
                Property("sortingOrder").SetValue(Component, sortOrder);
            }
            EditorUtility.SetDirty(Component);
        }

        internal VisualElement Root
        {
            get
            {
                if (Component is UIDocument document)
                    return document.rootVisualElement;
                // Unity invokes this callback immediately when the root is initialized.
                // Unregister within this call: no stale root or callback survives a UI reload.
                var registration = FindReloadRegistration(Component.GetType());
                Type callbackType = registration.GetParameters()[0].ParameterType;
                var receiver = new RootReceiver();
                var receiveMethod = typeof(RootReceiver).GetMethod(nameof(RootReceiver.Receive)).MakeGenericMethod(Component.GetType());
                var callback = Delegate.CreateDelegate(callbackType, receiver, receiveMethod);
                var unregister = Component.GetType().GetMethod("UnregisterUIReloadCallback", new[] { callbackType });
                try
                {
                    registration.Invoke(Component, new object[] { callback });
                }
                finally
                {
                    unregister.Invoke(Component, new object[] { callback });
                }
                return receiver.Root;
            }
        }

        private PropertyInfo Property(string name) => Component.GetType().GetProperty(name);

        internal static void ValidatePanelContract(Type type)
        {
            RequireProperty(type, "visualTreeAsset", typeof(VisualTreeAsset));
            RequireProperty(type, "panelSettings", typeof(PanelSettings));
            RequireProperty(type, "sortingOrder", typeof(int));
            FindReloadRegistration(type);
        }

        private static void RequireProperty(Type type, string name, Type valueType)
        {
            var property = type.GetProperty(name);
            if (property == null || !property.CanRead || !property.CanWrite || property.PropertyType != valueType)
                throw new NotSupportedException($"PanelRenderer API is unsupported: expected readable/writable {name} ({valueType.Name}).");
        }

        private static MethodInfo FindReloadRegistration(Type type)
        {
            foreach (var method in type.GetMethods())
            {
                if (method.Name != "RegisterUIReloadCallback")
                    continue;
                var args = method.GetParameters();
                if (args.Length != 1 || !typeof(Delegate).IsAssignableFrom(args[0].ParameterType))
                    continue;
                var invoke = args[0].ParameterType.GetMethod("Invoke");
                var callbackArgs = invoke?.GetParameters();
                if (
                    callbackArgs == null
                    || callbackArgs.Length != 3
                    || invoke.ReturnType != typeof(void)
                    || callbackArgs[0].ParameterType != type
                    || callbackArgs[1].ParameterType != typeof(VisualElement)
                    || callbackArgs[2].ParameterType != typeof(int)
                )
                    continue;
                if (type.GetMethod("UnregisterUIReloadCallback", new[] { args[0].ParameterType }) != null)
                    return method;
            }
            throw new NotSupportedException("PanelRenderer API is unsupported: versioned UI reload registration/unregistration is required.");
        }

        private sealed class RootReceiver
        {
            internal VisualElement Root;

            public void Receive<T>(T renderer, VisualElement root, int version)
            {
                Root = root;
            }
        }
    }
}
