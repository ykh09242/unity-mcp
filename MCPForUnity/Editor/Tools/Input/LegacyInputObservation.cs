using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    public readonly struct LegacyInputTouch
    {
        public int FingerId { get; }
        public Vector2 Position { get; }
        public TouchPhase Phase { get; }

        public LegacyInputTouch(int fingerId, Vector2 position, TouchPhase phase)
        {
            FingerId = fingerId;
            Position = position;
            Phase = phase;
        }
    }

    /// <summary>Read-only per-frame legacy API boundary. No setters, device injection or Input Manager axis guesses.</summary>
    public interface ILegacyInputRecordingSource
    {
        int FrameCount { get; }
        bool AnyKey { get; }
        bool AnyKeyDown { get; }
        bool GetKeyDown(KeyCode key);
        bool GetKeyUp(KeyCode key);
        bool MousePresent { get; }
        Vector2 MousePosition { get; }
        Vector2 MouseScrollDelta { get; }
        bool GetMouseButtonDown(int button);
        bool GetMouseButtonUp(int button);
        bool SimulateMouseWithTouches { get; }
        int TouchCount { get; }
        LegacyInputTouch GetTouch(int index);
    }

    /// <summary>Bounded observed edges, sampled once per game frame. Disposal synchronously ends all polling.</summary>
    public sealed class LegacyInputObservation : IDisposable
    {
        private static readonly Dictionary<KeyCode, string> Keys = KeyboardKeys();
        private readonly ILegacyInputRecordingSource source;
        private readonly Action<JObject, long> observed;
        private readonly Action<string> diagnose;
        private readonly HashSet<KeyCode> heldKeys = new HashSet<KeyCode>();
        private readonly List<KeyCode> releasedKeys = new List<KeyCode>();
        private readonly HashSet<int> heldMouse = new HashSet<int>();
        private readonly Dictionary<int, int> touchSlots = new Dictionary<int, int>();
        private readonly Dictionary<int, Vector2> touchPositions = new Dictionary<int, Vector2>();
        private Vector2? mousePosition;
        private int lastFrame;
        private bool disposed;
        public long UpdateCount { get; private set; }

        public LegacyInputObservation(ILegacyInputRecordingSource source, Action<JObject, long> observed, Action<string> diagnose)
        {
            this.source = source ?? throw new ArgumentNullException(nameof(source));
            this.observed = observed ?? throw new ArgumentNullException(nameof(observed));
            this.diagnose = diagnose ?? throw new ArgumentNullException(nameof(diagnose));
            lastFrame = source.FrameCount;
            if (source.MousePresent && ValidPosition(source.MousePosition))
                mousePosition = source.MousePosition;
        }

        public void Sample()
        {
            if (disposed || source.FrameCount <= lastFrame)
                return;
            UpdateCount += source.FrameCount - lastFrame;
            lastFrame = source.FrameCount;
            // Poll candidates only during keyboard/mouse activity. anyKeyDown alone can miss chords
            // on legacy backends that wait for all previous keys to be released before resetting it.
            if (source.AnyKey || source.AnyKeyDown)
                foreach (var key in Keys)
                {
                    if (disposed)
                        return;
                    if (source.GetKeyDown(key.Key) && heldKeys.Add(key.Key))
                        Emit(
                            new JObject
                            {
                                ["action"] = "key",
                                ["key"] = key.Value,
                                ["state"] = "press",
                            }
                        );
                }
            releasedKeys.Clear();
            foreach (KeyCode key in heldKeys)
                if (source.GetKeyUp(key))
                    releasedKeys.Add(key);
            foreach (KeyCode key in releasedKeys)
            {
                if (disposed)
                    return;
                heldKeys.Remove(key);
                Emit(
                    new JObject
                    {
                        ["action"] = "key",
                        ["key"] = Keys[key],
                        ["state"] = "release",
                    }
                );
            }
            if (disposed)
                return;
            int touchCount = source.TouchCount;
            if (touchCount > 0 && source.SimulateMouseWithTouches)
                diagnose("Legacy touch-to-mouse simulation is enabled; ambiguous mouse samples during touch were omitted without changing input settings.");
            else if (source.MousePresent)
                SampleMouse();
            for (int index = 0; index < Math.Min(touchCount, 128) && !disposed; index++)
                SampleTouch(source.GetTouch(index));
            if (touchCount > 128)
                diagnose("Legacy observation exceeded 128 reported touches in one game frame; additional samples were omitted.");
        }

        private void SampleMouse()
        {
            Vector2 position = source.MousePosition;
            Vector2? previous = mousePosition;
            bool valid = ValidPosition(position);
            if (valid)
                mousePosition = position;
            bool transition = false;
            for (int button = 0; button < 3 && !disposed; button++)
            {
                if (source.GetMouseButtonDown(button) && valid && heldMouse.Add(button))
                {
                    transition = true;
                    Mouse(button, "press", position);
                }
                if (!disposed && source.GetMouseButtonUp(button) && heldMouse.Remove(button))
                {
                    transition = true;
                    if (mousePosition.HasValue)
                        Mouse(button, "release", mousePosition.Value);
                }
            }
            if (disposed)
                return;
            if (valid && previous.HasValue && previous.Value != position && !transition)
                Mouse(0, "move", position);
            if (disposed)
                return;
            Vector2 wheel = source.MouseScrollDelta;
            if (wheel != Vector2.zero)
            {
                if (Finite(wheel.x, 10000) && Finite(wheel.y, 10000))
                    Emit(new JObject { ["action"] = "wheel", ["delta"] = Position(wheel) });
                else
                    diagnose("A legacy wheel delta exceeded the finite executable range and was omitted.");
            }
            if (!valid)
                diagnose("An invalid legacy mouse position was omitted; releases use the last valid observed position when available.");
        }

        private void Mouse(int button, string state, Vector2 position) =>
            Emit(
                new JObject
                {
                    ["action"] = "mouse",
                    ["button"] =
                        button == 0 ? "left"
                        : button == 1 ? "right"
                        : "middle",
                    ["state"] = state,
                    ["position"] = Position(position),
                }
            );

        private void SampleTouch(LegacyInputTouch touch)
        {
            bool end = touch.Phase == TouchPhase.Ended || touch.Phase == TouchPhase.Canceled;
            Vector2 position = touch.Position;
            if (!ValidPosition(position))
            {
                diagnose("An invalid legacy touch position was omitted; a terminal contact uses its last valid observed position when available.");
                if (!end || !touchPositions.TryGetValue(touch.FingerId, out position))
                    return;
            }
            if (touch.Phase == TouchPhase.Began && !touchSlots.ContainsKey(touch.FingerId))
            {
                int slot = 1;
                while (slot <= 10 && touchSlots.ContainsValue(slot))
                    slot++;
                if (slot > 10)
                {
                    diagnose("Legacy observation supports at most ten touch contacts; additional contacts were omitted without merging identities.");
                    return;
                }
                touchSlots.Add(touch.FingerId, slot);
            }
            if (!touchSlots.TryGetValue(touch.FingerId, out int selected))
                return;
            bool changed = !touchPositions.TryGetValue(touch.FingerId, out Vector2 previous) || previous != position;
            if (end)
            {
                touchSlots.Remove(touch.FingerId);
                touchPositions.Remove(touch.FingerId);
            }
            else
                touchPositions[touch.FingerId] = position;
            if (end || changed)
                Emit(
                    new JObject
                    {
                        ["action"] = "touch",
                        ["touch_id"] = selected,
                        ["state"] =
                            end ? "release"
                            : touch.Phase == TouchPhase.Began ? "press"
                            : "move",
                        ["position"] = Position(position),
                    }
                );
        }

        private void Emit(JObject value)
        {
            if (!disposed)
                observed(value, UpdateCount);
        }

        private static JArray Position(Vector2 value) => new JArray(value.x, value.y);

        private static bool Finite(float value, float maximum) => !float.IsNaN(value) && !float.IsInfinity(value) && Math.Abs(value) <= maximum;

        private static bool ValidPosition(Vector2 value) => Finite(value.x, 1000000) && Finite(value.y, 1000000) && value.x >= 0 && value.y >= 0;

        private static Dictionary<KeyCode, string> KeyboardKeys()
        {
            var keys = new Dictionary<KeyCode, string>();
            for (int index = 0; index < 26; index++)
                keys[(KeyCode)((int)KeyCode.A + index)] = ((char)('A' + index)).ToString();
            for (int index = 0; index < 10; index++)
            {
                keys[(KeyCode)((int)KeyCode.Alpha0 + index)] = "Digit" + index;
                keys[(KeyCode)((int)KeyCode.Keypad0 + index)] = "Numpad" + index;
            }
            for (int index = 0; index < 12; index++)
                keys[(KeyCode)((int)KeyCode.F1 + index)] = "F" + (index + 1);
            foreach (
                KeyCode key in new[]
                {
                    KeyCode.Space,
                    KeyCode.Tab,
                    KeyCode.Backspace,
                    KeyCode.Escape,
                    KeyCode.LeftShift,
                    KeyCode.RightShift,
                    KeyCode.LeftAlt,
                    KeyCode.RightAlt,
                    KeyCode.Home,
                    KeyCode.End,
                    KeyCode.PageUp,
                    KeyCode.PageDown,
                    KeyCode.Insert,
                    KeyCode.Delete,
                    KeyCode.CapsLock,
                    KeyCode.Numlock,
                    KeyCode.ScrollLock,
                    KeyCode.Pause,
                    KeyCode.Comma,
                    KeyCode.Period,
                    KeyCode.Slash,
                    KeyCode.Semicolon,
                    KeyCode.Minus,
                    KeyCode.Equals,
                    KeyCode.LeftBracket,
                    KeyCode.RightBracket,
                    KeyCode.Backslash,
                }
            )
                keys[key] = key == KeyCode.Numlock ? "NumLock" : key.ToString();
            keys[KeyCode.Return] = "Enter";
            keys[KeyCode.LeftControl] = "LeftCtrl";
            keys[KeyCode.RightControl] = "RightCtrl";
            keys[KeyCode.LeftCommand] = "LeftMeta";
            keys[KeyCode.RightCommand] = "RightMeta";
            keys[KeyCode.UpArrow] = "UpArrow";
            keys[KeyCode.DownArrow] = "DownArrow";
            keys[KeyCode.LeftArrow] = "LeftArrow";
            keys[KeyCode.RightArrow] = "RightArrow";
            keys[KeyCode.Quote] = "Quote";
            keys[KeyCode.BackQuote] = "Backquote";
            keys[KeyCode.KeypadPeriod] = "NumpadPeriod";
            keys[KeyCode.KeypadDivide] = "NumpadDivide";
            keys[KeyCode.KeypadMultiply] = "NumpadMultiply";
            keys[KeyCode.KeypadMinus] = "NumpadMinus";
            keys[KeyCode.KeypadPlus] = "NumpadPlus";
            keys[KeyCode.KeypadEnter] = "NumpadEnter";
            keys[KeyCode.KeypadEquals] = "NumpadEquals";
            return keys;
        }

        public void Dispose()
        {
            disposed = true;
            heldKeys.Clear();
            heldMouse.Clear();
            touchSlots.Clear();
            touchPositions.Clear();
            mousePosition = null;
        }
    }
}
