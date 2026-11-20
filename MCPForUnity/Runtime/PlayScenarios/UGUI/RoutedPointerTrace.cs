using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UIElements;

namespace MCPForUnity.Runtime.PlayScenarios
{
    public enum RoutedPointerEvent
    {
        Down,
        Up,
        Click,
    }

    public readonly struct RoutedPointerSample
    {
        public string Backend { get; }
        public RoutedPointerEvent Event { get; }
        public string Phase { get; }
        public string Target { get; }
        public string Receiver { get; }
        public int PointerId { get; }
        public int Button { get; }
        public int Frame { get; }
        public Vector2 Position { get; }

        internal RoutedPointerSample(
            string backend,
            RoutedPointerEvent kind,
            string phase,
            string target,
            string receiver,
            int pointerId,
            int button,
            Vector2 position
        )
        {
            Backend = backend;
            Event = kind;
            Phase = phase;
            Target = Label(target);
            Receiver = Label(receiver);
            PointerId = pointerId;
            Button = button;
            Frame = Time.frameCount;
            Position = position;
        }

        private static string Label(string name) => name != null && name.Length > 128 ? name.Substring(0, 128) : name;
    }

    /// <summary>
    /// Main-thread capture of routed pointer callbacks. The caller owns the scope and disposes it while the observed UI is still live.
    /// </summary>
    public sealed class RoutedPointerTrace : IDisposable
    {
        public const int SampleLimit = 128;
        public const int ToolkitElementLimit = 16;

        private readonly List<RoutedPointerSample> samples = new List<RoutedPointerSample>();
        private readonly List<VisualElement> elements = new List<VisualElement>();
        private bool disposed;

        public IReadOnlyList<RoutedPointerSample> Samples { get; }
        public bool Truncated { get; private set; }

        public RoutedPointerTrace() => Samples = samples.AsReadOnly();

        /// <summary>Observe both directions on an ancestor, or the target callback on the control itself.</summary>
        public void WatchToolkit(VisualElement element)
        {
            if (disposed)
                throw new ObjectDisposedException(nameof(RoutedPointerTrace));
            if (element == null)
                throw new ArgumentNullException(nameof(element));
            if (elements.Contains(element))
                return;
            if (elements.Count >= ToolkitElementLimit)
                throw new InvalidOperationException("The routed pointer trace reached its UI Toolkit element limit.");
            elements.Add(element);
            element.RegisterCallback<PointerDownEvent>(DownCapture, TrickleDown.TrickleDown);
            element.RegisterCallback<PointerDownEvent>(Down);
            element.RegisterCallback<PointerUpEvent>(UpCapture, TrickleDown.TrickleDown);
            element.RegisterCallback<PointerUpEvent>(Up);
            element.RegisterCallback<ClickEvent>(ClickCapture, TrickleDown.TrickleDown);
            element.RegisterCallback<ClickEvent>(Click);
        }

        /// <summary>Call from the receiving uGUI pointer handler. No uGUI handler or raycast target is added.</summary>
        public void RecordUgui(RoutedPointerEvent kind, GameObject receiver, PointerEventData data)
        {
            if (disposed || receiver == null || data == null)
                return;
            GameObject hit = data.pointerCurrentRaycast.gameObject;
            Append(
                new RoutedPointerSample("ugui", kind, "handler", hit != null ? hit.name : null, receiver.name, data.pointerId, (int)data.button, data.position)
            );
        }

        public void Clear()
        {
            samples.Clear();
            Truncated = false;
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            foreach (VisualElement element in elements)
            {
                element.UnregisterCallback<PointerDownEvent>(DownCapture, TrickleDown.TrickleDown);
                element.UnregisterCallback<PointerDownEvent>(Down);
                element.UnregisterCallback<PointerUpEvent>(UpCapture, TrickleDown.TrickleDown);
                element.UnregisterCallback<PointerUpEvent>(Up);
                element.UnregisterCallback<ClickEvent>(ClickCapture, TrickleDown.TrickleDown);
                element.UnregisterCallback<ClickEvent>(Click);
            }
            elements.Clear();
        }

        private void DownCapture(PointerDownEvent evt) => ObserveToolkit(evt, evt, RoutedPointerEvent.Down, true);

        private void Down(PointerDownEvent evt) => ObserveToolkit(evt, evt, RoutedPointerEvent.Down, false);

        private void UpCapture(PointerUpEvent evt) => ObserveToolkit(evt, evt, RoutedPointerEvent.Up, true);

        private void Up(PointerUpEvent evt) => ObserveToolkit(evt, evt, RoutedPointerEvent.Up, false);

        private void ClickCapture(ClickEvent evt) => ObserveToolkit(evt, evt, RoutedPointerEvent.Click, true);

        private void Click(ClickEvent evt) => ObserveToolkit(evt, evt, RoutedPointerEvent.Click, false);

        private void ObserveToolkit(EventBase context, IPointerEvent pointer, RoutedPointerEvent kind, bool capture)
        {
            var target = context.target as VisualElement;
            var receiver = context.currentTarget as VisualElement;
            // Unity 6 includes the target in both phases. Report it once, from the non-trickle callback.
            if (disposed || (capture && ReferenceEquals(target, receiver)))
                return;
            string phase =
                ReferenceEquals(target, receiver) ? "target"
                : capture ? "trickle_down"
                : "bubble_up";
            Append(new RoutedPointerSample("uitoolkit", kind, phase, target?.name, receiver?.name, pointer.pointerId, pointer.button, pointer.position));
        }

        private void Append(RoutedPointerSample sample)
        {
            if (samples.Count < SampleLimit)
                samples.Add(sample);
            else
                Truncated = true;
        }
    }
}
