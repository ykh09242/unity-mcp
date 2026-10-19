using System;
using System.Collections.Generic;
using System.Globalization;
using MCPForUnity.Editor.Services.PlayScenarios;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Runtime.PlayScenarios.UIToolkit
{
    /// <summary>Direct Button submit activation. No elements, panels or event subscriptions survive a call.</summary>
    public static class UIToolkitScenarioInputAdapter
    {
        public static bool ValidateRequest(string backend, string elementName, string elementPath, string clickMode)
        {
            if (backend != null && backend != "uitoolkit")
                throw Capability("Omit ui_backend for uGUI or select uitoolkit explicitly.");
            if (backend != "uitoolkit")
            {
                if (elementName != null || elementPath != null)
                    throw new ArgumentException("Visual element selectors require ui_backend=uitoolkit.");
                return false;
            }
            ValidateSelector(elementName, elementPath);
            if (clickMode != null && clickMode != "direct")
                throw Capability("UI Toolkit scenarios support direct Button submit activation only; pointer/raycast backends are unavailable.");
            return true;
        }

        public static void ValidateSelector(string elementName, string elementPath)
        {
            if (elementName == null && elementPath == null)
                throw new ArgumentException("UI Toolkit activation requires element_name or element_path.");
            if (elementName != null)
            {
                if (elementName.Length == 0 || elementName.Length > 256)
                    throw new ArgumentException("element_name must contain 1..256 characters.");
                foreach (char value in elementName)
                    if (value < 32)
                        throw new ArgumentException("element_name must not contain control characters.");
            }
            if (elementPath == null)
                return;
            if (elementPath.Length > 4096 || (elementPath.Length > 0 && elementPath[0] != '/'))
                throw new ArgumentException("element_path must be empty for the root or a bounded canonical '/0/1' child path.");
            string[] parts = elementPath.Length == 0 ? Array.Empty<string>() : elementPath.Substring(1).Split('/');
            if (parts.Length >= 128)
                throw new ArgumentException("element_path exceeds the 128-level bound including the root.");
            foreach (string part in parts)
            {
                if (
                    part.Length == 0
                    || (part.Length > 1 && part[0] == '0')
                    || !int.TryParse(part, NumberStyles.None, CultureInfo.InvariantCulture, out int index)
                    || index < 0
                )
                    throw new ArgumentException("element_path requires canonical zero-based decimal child indices.");
                foreach (char value in part)
                    if (value < '0' || value > '9')
                        throw new ArgumentException("element_path requires canonical zero-based decimal child indices.");
            }
        }

        public static PlayScenarioObservation Evaluate(GameObject owner, string elementName, string elementPath, string clickMode)
        {
            var counts = new PlayScenarioQueryCounts();
            return EvaluateForScenario(owner, elementName, elementPath, clickMode, new PlayScenarioLogPolicy(), ref counts);
        }

        public static PlayScenarioObservation EvaluateForScenario(
            GameObject owner,
            string elementName,
            string elementPath,
            string clickMode,
            PlayScenarioLogPolicy logPolicy,
            ref PlayScenarioQueryCounts counts
        )
        {
            ValidateRequest("uitoolkit", elementName, elementPath, clickMode);
            counts.TargetSearches++;
            if (owner == null || !owner.activeInHierarchy)
                return Pending("Waiting for the selected document GameObject to be active.");
            UIDocument[] documents = owner.GetComponents<UIDocument>();
            if (documents.Length > 1)
                throw Ambiguous("The selected GameObject has more than one UIDocument.");
            if (documents.Length == 0 || !documents[0].isActiveAndEnabled)
                return Pending("Waiting for one enabled UIDocument on the selected GameObject.");
            // Borrow every identity anew. Disabling/recreating a document invalidates its old root.
            VisualElement root = documents[0].rootVisualElement;
            if (root == null || root.panel == null)
                return Pending("Waiting for the selected UIDocument root to attach to a panel.");
            VisualElement element = elementPath != null ? ResolvePath(root, elementPath, ref counts) : ResolveUniqueName(root, elementName, ref counts);
            if (element == null)
                return Pending("Waiting for the selected visual element in the current document root.", "target_missing");
            if (elementName != null && element.name != elementName)
                throw new ArgumentException("element_name does not match the element_path guard.");
            if (!(element is Button button))
                throw Capability("Direct UI Toolkit activation currently supports Button controls only.");
            if (button.clickable == null)
                throw Capability("The selected Button has no Clickable activation backend.");
            VisualElement current = button;
            bool owned = false;
            for (int depth = 0; current != null && depth < 128; depth++, current = current.hierarchy.parent)
            {
                counts.HierarchyVisits++;
                if (!current.enabledInHierarchy || !current.visible || current.resolvedStyle.display == DisplayStyle.None)
                    return Pending("Waiting for the selected Button and its ancestors to be enabled and visible.");
                if (ReferenceEquals(current, root))
                {
                    owned = true;
                    break;
                }
            }
            if (!owned || !ReferenceEquals(root.panel, button.panel))
                return Pending("The selected element detached from its current document root; waiting to resolve again.");
            Rect bounds = button.worldBound;
            if (!Finite(bounds.x) || !Finite(bounds.y) || !Finite(bounds.width) || !Finite(bounds.height) || bounds.width <= 0 || bounds.height <= 0)
                return Pending("Waiting for finite positive Button layout on the attached panel.");
            string loggedError = null;
            void CaptureError(string message, string stack, LogType type)
            {
                if (loggedError == null && (logPolicy ?? new PlayScenarioLogPolicy()).IsUnexpected(type.ToString(), message))
                    loggedError = type + ": " + PlayScenarioEngine.Bounded(message, 1024);
            }
            Application.logMessageReceived += CaptureError;
            try
            {
                using (NavigationSubmitEvent activation = NavigationSubmitEvent.GetPooled())
                {
                    activation.target = button;
                    button.SendEvent(activation);
                }
            }
            finally
            {
                Application.logMessageReceived -= CaptureError;
            }
            if (loggedError != null)
                throw new InvalidOperationException("UI Toolkit activation logged an error: " + loggedError);
            return new PlayScenarioObservation(
                true,
                "One direct UI Toolkit Button NavigationSubmitEvent was dispatched. Pointer/raycast routing and application outcomes are not proven."
            );
        }

        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        private static VisualElement ResolvePath(VisualElement root, string path, ref PlayScenarioQueryCounts counts)
        {
            VisualElement element = root;
            counts.HierarchyVisits++;
            foreach (string part in path.Length == 0 ? Array.Empty<string>() : path.Substring(1).Split('/'))
            {
                if (element.childCount > 1000)
                    throw new ArgumentException("Visual element path exceeds the 1000-sibling bound.");
                int index = int.Parse(part, CultureInfo.InvariantCulture);
                if (index >= element.childCount)
                    return null;
                element = element.ElementAt(index);
                counts.HierarchyVisits++;
            }
            return element;
        }

        private static VisualElement ResolveUniqueName(VisualElement root, string name, ref PlayScenarioQueryCounts counts)
        {
            VisualElement match = null;
            var pending = new Stack<(VisualElement Element, int NextChild)>();
            pending.Push((root, 0));
            int examined = 0;
            while (pending.Count > 0)
            {
                var frame = pending.Pop();
                if (frame.NextChild == 0)
                {
                    counts.HierarchyVisits++;
                    if (++examined > 10000 || pending.Count >= 128 || frame.Element.hierarchy.childCount > 1000)
                        throw new ArgumentException("Visual name selection exceeds the 10000-node, 128-level or 1000-sibling bound.");
                    if (frame.Element.name == name)
                    {
                        if (match != null)
                            throw Ambiguous("Visual element name is ambiguous within the current document root; use element_path.");
                        match = frame.Element;
                    }
                }
                if (frame.NextChild < frame.Element.hierarchy.childCount)
                {
                    VisualElement child = frame.Element.hierarchy[frame.NextChild++];
                    pending.Push(frame);
                    pending.Push((child, 0));
                }
            }
            return match;
        }

        private static PlayScenarioObservation Pending(string detail, string code = "input_blocked") =>
            new PlayScenarioObservation(
                false,
                detail,
                new PlayScenarioFailure
                {
                    Code = code,
                    Message = detail,
                    Expected = "one active attached eligible UI Toolkit Button",
                }
            );

        private static PlayScenarioException Capability(string message) =>
            new PlayScenarioException(
                new PlayScenarioFailure
                {
                    Code = "capability_unavailable",
                    Message = message,
                    Expected = "direct UI Toolkit Button submit activation",
                    Actual = "unsupported backend or control",
                }
            );

        private static PlayScenarioException Ambiguous(string message) =>
            new PlayScenarioException(new PlayScenarioFailure { Code = "target_ambiguous", Message = message });
    }
}
