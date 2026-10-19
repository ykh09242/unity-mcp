using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.TimelineTools
{
    [InitializeOnLoad]
    internal static class TimelinePreview
    {
        private sealed class Value
        {
            internal Object Owner;
            internal string Property;
            internal Func<object> Read;
            internal Action<object> Write;
            internal object Original;
            internal object Last;
        }

        private sealed class Session
        {
            internal string Id;
            internal Component Director;
            internal TimelineAccess.Graph Timeline;
            internal string Revision;
            internal string Bindings;
            internal object Graph;
            internal double LastUse;
            internal readonly List<Value> Values = new List<Value>();
            internal readonly HashSet<Object> Captured = new HashSet<Object>();
        }

        private static Session active;

        static TimelinePreview()
        {
            EditorApplication.update += Expire;
            AssemblyReloadEvents.beforeAssemblyReload += Cleanup;
            EditorApplication.quitting += Cleanup;
            EditorApplication.playModeStateChanged += state =>
            {
                if (state == PlayModeStateChange.ExitingEditMode)
                    Cleanup();
            };
        }

        internal static bool Active => active != null;

        private static void Expire()
        {
            if (active != null && (active.Director == null || EditorApplication.timeSinceStartup - active.LastUse > 120))
                Cleanup();
        }

        private static void Cleanup()
        {
            if (active == null)
                return;
            try
            {
                StopSession(active);
            }
            catch (Exception error)
            {
                McpLog.Warn("[TimelinePreview] Cleanup could not fully restore owned state: " + error.GetBaseException().Message);
            }
            finally
            {
                active = null;
            }
        }

        private static void Capture(Session session, Object owner, string property, Func<object> read, Action<object> write)
        {
            object original = read();
            session.Values.Add(
                new Value
                {
                    Owner = owner,
                    Property = property,
                    Read = read,
                    Write = write,
                    Original = original,
                    Last = original,
                }
            );
        }

        private static void Transform(Session session, Transform transform)
        {
            if (!session.Captured.Add(transform))
                return;
            if (session.Captured.Count > 128)
                throw new ArgumentException("Preview exceeds 128 distinct bound transforms/components.");
            Capture(session, transform, "localPosition", () => transform.localPosition, value => transform.localPosition = (Vector3)value);
            Capture(session, transform, "localRotation", () => transform.localRotation, value => transform.localRotation = (Quaternion)value);
            Capture(session, transform, "localScale", () => transform.localScale, value => transform.localScale = (Vector3)value);
        }

        private static void Audio(Session session, AudioSource audio)
        {
            if (!session.Captured.Add(audio))
                return;
            if (session.Captured.Count > 128)
                throw new ArgumentException("Preview exceeds 128 distinct bound transforms/components.");
            if (audio.isPlaying)
                throw new ArgumentException("Preview refuses an already playing AudioSource.");
            Capture(session, audio, "mute", () => audio.mute, value => audio.mute = (bool)value);
            Capture(session, audio, "volume", () => audio.volume, value => audio.volume = (float)value);
            Capture(session, audio, "pitch", () => audio.pitch, value => audio.pitch = (float)value);
            Capture(session, audio, "loop", () => audio.loop, value => audio.loop = (bool)value);
            Capture(session, audio, "clip", () => audio.clip, value => audio.clip = (AudioClip)value);
        }

        private static string Bindings(Component director) => TimelineAccess.DirectorRead(director)["bindings"].ToString(Newtonsoft.Json.Formatting.None);

        private static void Validate(Session session)
        {
            var marker = TimelineAccess.Get(session.Timeline.Asset, "markerTrack") as Object;
            if (marker != null && (int)TimelineAccess.Call(marker, "GetMarkerCount") != 0)
                throw new ArgumentException("Preview refuses Timeline markers/notifications.");
            foreach (var track in session.Timeline.Tracks)
            {
                if ((int)TimelineAccess.Call(track, "GetMarkerCount") != 0)
                    throw new ArgumentException("Preview refuses track markers/notifications.");
                string kind = TimelineAccess.Kind(track);
                if (kind == "unsupported")
                    throw new ArgumentException("Preview supports only built-in animation/audio tracks; custom/group/control tracks are refused.");
                var binding = TimelineAccess.Call(session.Director, "GetGenericBinding", track) as Component;
                if (binding == null || binding.gameObject.scene != session.Director.gameObject.scene || !binding.gameObject.activeInHierarchy)
                    throw new ArgumentException("Preview requires active exact component bindings in the selected director scene.");
                if (kind == "animation")
                {
                    var animator = binding as Animator;
                    if (animator == null || !animator.enabled || animator.isHuman)
                        throw new ArgumentException("Animation preview requires an enabled nonhumanoid Animator binding.");
                    // Reflected Unity objects may be managed wrappers for destroyed native objects; retain Unity null semantics.
                    if (!(bool)TimelineAccess.Get(track, "inClipMode") || (TimelineAccess.Get(track, "infiniteClip") as AnimationClip) != null)
                        throw new ArgumentException("Animation preview requires explicit Timeline clips, without an active infinite clip.");
                    foreach (Transform transform in animator.GetComponentsInChildren<Transform>(true))
                        Transform(session, transform);
                    Capture(session, animator, "applyRootMotion", () => animator.applyRootMotion, value => animator.applyRootMotion = (bool)value);
                    Capture(session, animator, "fireEvents", () => animator.fireEvents, value => animator.fireEvents = (bool)value);
                }
                else
                {
                    if (!(binding is AudioSource source))
                        throw new ArgumentException("Audio preview binding must be an AudioSource.");
                    Audio(session, source);
                }
                foreach (object clip in TimelineAccess.Clips(track))
                {
                    Object source = TimelineAccess.Source(clip);
                    if (kind == "animation")
                    {
                        if (
                            !(source is AnimationClip animation)
                            || animation.legacy
                            || AnimationUtility.GetAnimationEvents(animation).Length != 0
                            || AnimationUtility.GetObjectReferenceCurveBindings(animation).Length != 0
                        )
                            throw new ArgumentException("Animation preview requires nonlegacy clips without events/object-reference curves.");
                        EditorCurveBinding[] curves = AnimationUtility.GetCurveBindings(animation);
                        if (curves.Length > 256 || curves.Any(curve => curve.type != typeof(Transform) || !AllowedCurve(curve.propertyName)))
                            throw new ArgumentException("Preview animation supports at most 256 Transform position/rotation/scale curves per source clip.");
                        foreach (var curve in curves)
                            if (string.IsNullOrEmpty(curve.path) ? binding.transform == null : binding.transform.Find(curve.path) == null)
                                throw new ArgumentException("Animation curve path does not resolve beneath its exact Animator binding.");
                    }
                    else if (!(source is AudioClip))
                        throw new ArgumentException("Audio preview requires existing AudioClip sources.");
                }
            }
            if (session.Timeline.ClipCount < 1)
                throw new ArgumentException("Preview requires at least one supported clip.");
        }

        private static bool AllowedCurve(string property)
        {
            string[] roots = { "m_LocalPosition", "m_LocalRotation", "m_LocalScale", "localEulerAnglesRaw" };
            return roots.Any(root =>
                property == root + ".x" || property == root + ".y" || property == root + ".z" || (root == "m_LocalRotation" && property == root + ".w")
            );
        }

        internal static object Start(JObject request, TimelineAccess.Graph graph, Component director)
        {
            Expire();
            if (
                active != null
                || AnimationMode.InAnimationMode()
                || TimelineAccess.GraphValid(director)
                || TimelineAccess.Get(director, "state").ToString() == "Playing"
            )
                throw new ArgumentException(
                    "Preview requires an idle director without an existing graph, another tool session or an external AnimationMode owner."
                );
            if (TimelineAccess.Get(director, "playableAsset") as Object != graph.Asset)
                throw new ArgumentException("Assign the exact Timeline to this director before preview.");
            double duration = Convert.ToDouble(TimelineAccess.Get(graph.Asset, "duration"));
            if (duration <= 0 || duration > 86400 || double.IsNaN(duration) || double.IsInfinity(duration))
                throw new ArgumentException("Preview duration must be finite, positive and at most 86400 seconds.");
            var session = new Session
            {
                Id = Guid.NewGuid().ToString("N"),
                Director = director,
                Timeline = graph,
                Revision = graph.Revision(),
                Bindings = Bindings(director),
                LastUse = EditorApplication.timeSinceStartup,
            };
            Capture(session, director, "time", () => TimelineAccess.Get(director, "time"), value => TimelineAccess.Set(director, "time", value));
            Validate(session);
            graph.Guard(request);
            TimelineAccess.DirectorGuard(request, director);
            active = session;
            try
            {
                foreach (AudioSource audio in session.Captured.OfType<AudioSource>())
                    audio.mute = true;
                // Record our explicit mute write before a native call can fail.
                Remember(session);
                TimelineAccess.Call(director, "RebuildGraph");
                session.Graph = TimelineAccess.Get(director, "playableGraph");
                if (!(bool)TimelineAccess.Call(session.Graph, "IsValid"))
                    throw new InvalidOperationException("The native director did not create a valid preview graph.");
                Remember(session);
                return new SuccessResponse(
                    "Owned manual Timeline preview prepared; seek explicitly.",
                    new
                    {
                        preview_id = session.Id,
                        expires_after_idle_seconds = 120,
                        audio_muted = true,
                        director = TimelineAccess.SceneReference(director),
                        duration,
                    }
                );
            }
            catch
            {
                // Entry required an invalid graph: a graph now present was created by this call.
                session.Graph = TimelineAccess.Get(director, "playableGraph");
                Remember(session);
                Cleanup();
                throw;
            }
        }

        private static void Remember(Session session)
        {
            foreach (Value value in session.Values)
                if (value.Owner != null)
                    value.Last = value.Read();
            session.LastUse = EditorApplication.timeSinceStartup;
        }

        private static Session Select(JObject request)
        {
            Expire();
            if (active == null || TimelineAccess.Text(request, "preview_id") != active.Id)
                throw new ArgumentException("Preview token is missing, expired or belongs to another editor session.");
            return active;
        }

        internal static object Seek(JObject request)
        {
            Session session = Select(request);
            double time;
            try
            {
                time = TimelineAccess.Number(request["time"], "time", 0, Convert.ToDouble(TimelineAccess.Get(session.Timeline.Asset, "duration")));
                bool drift =
                    session.Director == null
                    || session.Revision != session.Timeline.Revision()
                    || session.Bindings != Bindings(session.Director)
                    || (TimelineAccess.Get(session.Director, "playableAsset") as Object) != session.Timeline.Asset
                    || !TimelineAccess.Get(session.Director, "playableGraph").Equals(session.Graph)
                    || session.Values.Any(value => value.Owner == null || !Equals(value.Read(), value.Last));
                if (drift)
                {
                    JObject stopped = StopSession(session);
                    active = null;
                    return new ErrorResponse("Preview target/source/binding/state drifted; stopped owned preview before evaluation.", stopped);
                }
            }
            catch
            {
                // Guard reads may fail after an external asset move/delete. Preserve Last so cleanup can distinguish external writes.
                Cleanup();
                throw;
            }
            try
            {
                TimelineAccess.Set(session.Director, "time", time);
                Remember(session);
                TimelineAccess.Call(session.Director, "Evaluate");
                Remember(session);
                return new SuccessResponse(
                    "Native Timeline graph evaluated once; no realtime playback/audibility claim.",
                    new
                    {
                        preview_id = session.Id,
                        time = (double)TimelineAccess.Get(session.Director, "time"),
                        graph_valid = TimelineAccess.GraphValid(session.Director),
                        director = TimelineAccess.SceneReference(session.Director),
                    }
                );
            }
            catch
            {
                // Only the validated built-in graph was evaluated synchronously; retain its last sampled values for cleanup.
                Remember(session);
                Cleanup();
                throw;
            }
        }

        internal static object Stop(JObject request)
        {
            Session session = Select(request);
            JObject result = StopSession(session);
            active = null;
            return new SuccessResponse("Owned Timeline preview stopped; conditional restoration recorded.", result);
        }

        private static JObject StopSession(Session session)
        {
            var current = session.Values.Select(value => value.Owner != null ? value.Read() : null).ToArray();
            var conflicts = new JArray();
            if (session.Graph != null && (bool)TimelineAccess.Call(session.Graph, "IsValid"))
            {
                if (session.Director != null && TimelineAccess.Get(session.Director, "playableGraph").Equals(session.Graph))
                    TimelineAccess.Call(session.Director, "Stop");
                else
                {
                    TimelineAccess.Call(session.Graph, "Destroy");
                    conflicts.Add(
                        new JObject
                        {
                            ["property"] = "playableGraph",
                            ["reason"] = "Director graph ownership changed; only the cached owned graph was destroyed.",
                        }
                    );
                }
            }
            for (int i = 0; i < session.Values.Count; i++)
            {
                Value value = session.Values[i];
                if (value.Owner == null)
                {
                    conflicts.Add(new JObject { ["property"] = value.Property, ["reason"] = "Target was destroyed; no replacement object was modified." });
                    continue;
                }
                bool external = !Equals(current[i], value.Last);
                object now = value.Read();
                // Stop may restore an engine baseline; external values are re-applied only over known tool/baseline values.
                if (
                    Equals(now, current[i])
                    || Equals(now, value.Last)
                    || Equals(now, value.Original)
                    || (value.Owner == session.Director && value.Property == "time" && Equals(now, 0d))
                )
                    value.Write(external ? current[i] : value.Original);
                else
                    external = true;
                if (external)
                    conflicts.Add(
                        new JObject
                        {
                            ["instance_id"] = value.Owner.GetInstanceIDCompat(),
                            ["property"] = value.Property,
                            ["reason"] = "External state preserved; original value was not blindly restored.",
                        }
                    );
            }
            return new JObject
            {
                ["preview_id"] = session.Id,
                ["stopped"] = true,
                ["restored"] = conflicts.Count == 0,
                ["conflicts"] = conflicts,
            };
        }
    }
}
