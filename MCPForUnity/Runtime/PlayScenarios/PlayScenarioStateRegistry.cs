using System;
using System.Collections.Generic;
using System.Globalization;
using System.Threading;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Explicit read-only state access. TryRead false means the scalar is not ready yet.</summary>
    public interface IPlayScenarioStateProvider
    {
        bool TryRead(out PlayScenarioStateValue value);
    }

    public enum PlayScenarioStateValueKind
    {
        Invalid,
        Boolean,
        Integer,
        Number,
        String,
    }

    /// <summary>A bounded scalar union. Default values are invalid; factories reject nonfinite numbers and oversized strings.</summary>
    public readonly struct PlayScenarioStateValue
    {
        public PlayScenarioStateValueKind Kind { get; }
        private readonly bool _boolean;
        private readonly long _integer;
        private readonly double _number;
        private readonly string _string;

        private PlayScenarioStateValue(PlayScenarioStateValueKind kind, bool boolean = false, long integer = 0, double number = 0, string text = null)
        {
            Kind = kind;
            _boolean = boolean;
            _integer = integer;
            _number = number;
            _string = text;
        }

        public bool BooleanValue => Kind == PlayScenarioStateValueKind.Boolean ? _boolean : throw new InvalidOperationException("State value is not boolean.");
        public long IntegerValue => Kind == PlayScenarioStateValueKind.Integer ? _integer : throw new InvalidOperationException("State value is not integer.");
        public double NumberValue => Kind == PlayScenarioStateValueKind.Number ? _number : throw new InvalidOperationException("State value is not number.");
        public string StringValue => Kind == PlayScenarioStateValueKind.String ? _string : throw new InvalidOperationException("State value is not string.");
        public bool IsValid =>
            Kind == PlayScenarioStateValueKind.Boolean
            || Kind == PlayScenarioStateValueKind.Integer
            || (Kind == PlayScenarioStateValueKind.Number && !double.IsNaN(_number) && !double.IsInfinity(_number))
            || (Kind == PlayScenarioStateValueKind.String && _string != null && _string.Length <= 1024);

        public static PlayScenarioStateValue FromBoolean(bool value) => new PlayScenarioStateValue(PlayScenarioStateValueKind.Boolean, boolean: value);

        public static PlayScenarioStateValue FromInteger(long value) => new PlayScenarioStateValue(PlayScenarioStateValueKind.Integer, integer: value);

        public static PlayScenarioStateValue FromNumber(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value))
                throw new ArgumentException("State numbers must be finite.", nameof(value));
            return new PlayScenarioStateValue(PlayScenarioStateValueKind.Number, number: value);
        }

        public static PlayScenarioStateValue FromString(string value)
        {
            if (value == null || value.Length > 1024)
                throw new ArgumentException("State strings must be nonnull and contain at most 1024 UTF-16 code units.", nameof(value));
            return new PlayScenarioStateValue(PlayScenarioStateValueKind.String, text: value);
        }

        /// <summary>Booleans match only booleans; strings use ordinal equality. Integers and finite numbers compare exact mathematical values without rounding a long into a double.</summary>
        public bool Matches(PlayScenarioStateValue other)
        {
            if (!IsValid || !other.IsValid)
                return false;
            if (Kind == other.Kind)
            {
                switch (Kind)
                {
                    case PlayScenarioStateValueKind.Boolean:
                        return _boolean == other._boolean;
                    case PlayScenarioStateValueKind.Integer:
                        return _integer == other._integer;
                    case PlayScenarioStateValueKind.Number:
                        return _number == other._number;
                    case PlayScenarioStateValueKind.String:
                        return string.Equals(_string, other._string, StringComparison.Ordinal);
                }
            }
            if (Kind == PlayScenarioStateValueKind.Integer && other.Kind == PlayScenarioStateValueKind.Number)
                return IntegerMatchesNumber(_integer, other._number);
            if (Kind == PlayScenarioStateValueKind.Number && other.Kind == PlayScenarioStateValueKind.Integer)
                return IntegerMatchesNumber(other._integer, _number);
            return false;
        }

        private static bool IntegerMatchesNumber(long integer, double number) =>
            number >= -9223372036854775808d && number < 9223372036854775808d && Math.Truncate(number) == number && (long)number == integer;

        internal static PlayScenarioStateValue FromJson(JToken value)
        {
            if (!PlayScenarioDefinition.Scalar(value))
                throw new ArgumentException(
                    "state_equals must be a boolean, signed 64-bit integer, finite number or string of at most 1024 UTF-16 code units."
                );
            switch (value.Type)
            {
                case JTokenType.Boolean:
                    return FromBoolean(value.Value<bool>());
                case JTokenType.Integer:
                    return FromInteger(value.Value<long>());
                case JTokenType.Float:
                    return FromNumber(value.Value<double>());
                case JTokenType.String:
                    return FromString(value.Value<string>());
                default:
                    throw new ArgumentException("Unsupported state scalar.");
            }
        }

        internal string JsonText()
        {
            switch (Kind)
            {
                case PlayScenarioStateValueKind.Boolean:
                    return _boolean ? "true" : "false";
                case PlayScenarioStateValueKind.Integer:
                    return _integer.ToString(CultureInfo.InvariantCulture);
                case PlayScenarioStateValueKind.Number:
                    return JsonConvert.SerializeObject(_number);
                case PlayScenarioStateValueKind.String:
                    return JsonConvert.ToString(_string);
                default:
                    return "invalid scalar";
            }
        }
    }

    /// <summary>Explicit weak scalar providers, available in ordinary Players too. Registration never starts a scenario runner.</summary>
    public static class PlayScenarioStateRegistry
    {
        public const int RegistrationLimit = 128;
        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Entry> Entries = new Dictionary<string, Entry>(StringComparer.Ordinal);
        private static int _mainThreadId;

        private sealed class Entry
        {
            internal readonly WeakReference Provider;

            internal Entry(IPlayScenarioStateProvider provider) => Provider = new WeakReference(provider);
        }

        private sealed class Registration : IDisposable
        {
            private string _id;
            private readonly Entry _entry;

            internal Registration(string id, Entry entry)
            {
                _id = id;
                _entry = entry;
            }

            public void Dispose()
            {
                lock (Gate)
                {
                    if (_id != null && Entries.TryGetValue(_id, out Entry current) && ReferenceEquals(current, _entry))
                        Entries.Remove(_id);
                    _id = null;
                }
            }
        }

        /// <summary>Register on the Unity main thread. Dispose the ownership token on any thread; it never invokes provider code.</summary>
        public static IDisposable Register(string id, IPlayScenarioStateProvider provider)
        {
            EnsureMainThread();
            if (!PlayScenarioTarget.IsValidTargetId(id))
                throw new ArgumentException("State ID must match [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}.", nameof(id));
            if (!Alive(provider))
                throw new ArgumentNullException(nameof(provider));
            lock (Gate)
            {
                var expired = new List<string>();
                foreach (var pair in Entries)
                    if (!Alive(pair.Value.Provider.Target))
                        expired.Add(pair.Key);
                foreach (string key in expired)
                    Entries.Remove(key);
                if (Entries.ContainsKey(id))
                    throw new InvalidOperationException("A state provider is already registered for ID: " + id);
                if (Entries.Count >= RegistrationLimit)
                    throw new InvalidOperationException("State provider registration exceeds its bounded capacity.");
                var entry = new Entry(provider);
                Entries.Add(id, entry);
                return new Registration(id, entry);
            }
        }

        private static void EnsureMainThread()
        {
            if (Thread.CurrentThread.ManagedThreadId != Volatile.Read(ref _mainThreadId))
                throw new InvalidOperationException("State provider registration and reads require the Unity main thread.");
        }

        private static bool Alive(object provider) => provider != null && (!(provider is UnityEngine.Object native) || native != null);

        private static PlayScenarioException Unavailable(string id) =>
            new PlayScenarioException(
                new PlayScenarioFailure
                {
                    Code = "state_provider_unavailable",
                    Target = id,
                    Expected = "the original live registered state provider",
                    Actual = "missing, destroyed, released or replaced",
                    Message = "Bound state provider is unavailable: " + id,
                }
            );

        internal sealed class Snapshot
        {
            private readonly string _id;
            private Entry _bound;

            internal Snapshot(string id)
            {
                if (!PlayScenarioTarget.IsValidTargetId(id))
                    throw new ArgumentException("state_id must contain 1-128 permitted identifier characters.");
                _id = id;
            }

            private IPlayScenarioStateProvider Borrow()
            {
                lock (Gate)
                {
                    if (_bound == null)
                    {
                        if (!Entries.TryGetValue(_id, out Entry available) || !Alive(available.Provider.Target))
                            return null;
                        _bound = available;
                    }
                    if (!Entries.TryGetValue(_id, out Entry current) || !ReferenceEquals(current, _bound))
                        throw Unavailable(_id);
                    object provider = current.Provider.Target;
                    if (!Alive(provider))
                        throw Unavailable(_id);
                    return (IPlayScenarioStateProvider)provider;
                }
            }

            internal bool TryRead(out PlayScenarioStateValue value, out bool providerMissing)
            {
                EnsureMainThread();
                value = default;
                IPlayScenarioStateProvider provider = Borrow();
                providerMissing = provider == null;
                if (providerMissing)
                    return false;
                bool ready;
                try
                {
                    ready = provider.TryRead(out value);
                }
                catch (Exception error)
                {
                    throw new PlayScenarioException(
                        new PlayScenarioFailure
                        {
                            Code = "state_provider_error",
                            Target = _id,
                            Message = PlayScenarioEngine.Bounded("State provider read failed: " + error.GetType().Name + ": " + error.Message, 4096),
                        }
                    );
                }
                if (!ReferenceEquals(Borrow(), provider))
                    throw Unavailable(_id);
                if (ready && !value.IsValid)
                    throw new PlayScenarioException(
                        new PlayScenarioFailure
                        {
                            Code = "state_provider_error",
                            Target = _id,
                            Expected = "a valid bounded boolean, integer, finite number or string",
                            Actual = "invalid scalar",
                            Message = "State provider returned an invalid scalar value: " + _id,
                        }
                    );
                return ready;
            }
        }

        internal static Snapshot Resolve(string id) => new Snapshot(id);

#if UNITY_EDITOR
        [InitializeOnLoadMethod]
        private static void InitializeEditorMainThread() => Volatile.Write(ref _mainThreadId, Thread.CurrentThread.ManagedThreadId);
#endif

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        internal static void ResetOnPlayEntry()
        {
            lock (Gate)
            {
                Volatile.Write(ref _mainThreadId, Thread.CurrentThread.ManagedThreadId);
                Entries.Clear();
            }
        }
    }
}
