using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Editor-owned shared preparation. Canceling one waiter never retires another waiter's census.</summary>
    [InitializeOnLoad]
    internal sealed class ReferenceGraphPreparation
    {
        internal sealed class Snapshot
        {
            internal readonly string[] Items;
            internal readonly string Token = Guid.NewGuid().ToString("N");

            internal Snapshot(string[] items) => Items = items;
        }

        private static readonly Dictionary<string, ReferenceGraphPreparation> active = new(StringComparer.Ordinal);
        private static readonly Dictionary<string, JObject> recent = new(StringComparer.Ordinal);
        private readonly string kind;
        private readonly Func<string[]> census;
        private readonly Func<string, string> convert;
        private readonly Func<bool> current;
        private readonly TaskCompletionSource<Snapshot> completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        private readonly Stopwatch elapsed = Stopwatch.StartNew();
        private readonly List<CancellationToken> callers = new();
        private ReferenceGraphSnapshotPreparation preparation;
        private string key;
        private string state = "queued";
        private int consumers;
        private int updates;
        private double nativeMilliseconds;
        private double preparationMilliseconds;
        private double maximumCallbackMilliseconds;
        private int inputItems;
        private long operations;

        static ReferenceGraphPreparation()
        {
            AssemblyReloadEvents.beforeAssemblyReload += CancelAll;
            EditorApplication.quitting += CancelAll;
        }

        internal ReferenceGraphPreparation(string kind, Func<string[]> census, Func<string, string> convert, Func<bool> current)
        {
            this.kind = kind;
            this.census = census;
            this.convert = convert;
            this.current = current;
        }

        internal static Task<Snapshot> Acquire(string key, Func<ReferenceGraphPreparation> create, CancellationToken caller)
        {
            caller.ThrowIfCancellationRequested();
            if (!ToolConsentThread.IsMainThread)
                throw new InvalidOperationException("Reference preparation requires the Editor thread.");
            if (!active.TryGetValue(key, out ReferenceGraphPreparation job))
            {
                if (active.Count >= 2)
                    throw new InvalidOperationException(
                        "Reference preparation capacity is occupied; observe preparation status before requesting another census."
                    );
                job = create();
                job.key = key;
                active.Add(key, job);
                EditorApplication.update += job.Tick;
            }
            job.consumers++;
            job.callers.Add(caller);
            return job.Wait(caller);
        }

        private async Task<Snapshot> Wait(CancellationToken caller)
        {
            try
            {
                var canceled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                using (caller.Register(() => canceled.TrySetResult(true)))
                {
                    await Task.WhenAny(completion.Task, canceled.Task);
                    caller.ThrowIfCancellationRequested();
                    return await completion.Task;
                }
            }
            finally
            {
                consumers--;
                callers.Remove(caller);
                if (consumers == 0 && !completion.Task.IsCompleted)
                    Finish("canceled", new OperationCanceledException());
                else if (consumers == 0)
                    active.Remove(key);
            }
        }

        private void Tick()
        {
            // An update delegate may already be in Unity's invocation list when its owner retires.
            if (completion.Task.IsCompleted)
                return;
            var watch = Stopwatch.StartNew();
            try
            {
                if (callers.TrueForAll(caller => caller.IsCancellationRequested))
                    throw new OperationCanceledException();
                if (!current())
                    throw new InvalidOperationException("Reference source revision changed during preparation; restart collection when idle.");
                updates++;
                if (preparation == null)
                {
                    state = "native_census";
                    var native = Stopwatch.StartNew();
                    string[] input;
                    try
                    {
                        input = census();
                    }
                    finally
                    {
                        nativeMilliseconds = native.Elapsed.TotalMilliseconds;
                    }
                    preparation = new ReferenceGraphSnapshotPreparation(input, convert);
                    state = preparation.Stage;
                    return;
                }
                var slice = Stopwatch.StartNew();
                preparation.Step(8192, 4, CancellationToken.None);
                preparationMilliseconds += slice.Elapsed.TotalMilliseconds;
                state = preparation.Stage;
                if (!current())
                    throw new InvalidOperationException("Reference source revision changed during preparation; restart collection when idle.");
                if (preparation.Complete)
                    Finish("complete", null);
            }
            catch (Exception error)
            {
                Finish(error is OperationCanceledException ? "canceled" : "failed", error);
            }
            finally
            {
                maximumCallbackMilliseconds = Math.Max(maximumCallbackMilliseconds, watch.Elapsed.TotalMilliseconds);
                if (completion.Task.IsCompleted)
                    recent[kind] = Describe();
            }
        }

        private void Finish(string finalState, Exception error)
        {
            if (completion.Task.IsCompleted)
                return;
            state = finalState;
            inputItems = preparation?.InputCount ?? 0;
            operations = preparation?.Operations ?? 0;
            EditorApplication.update -= Tick;
            if (consumers == 0)
                active.Remove(key);
            recent[kind] = Describe();
            string[] result = error == null ? preparation.Result : null;
            preparation = null;
            if (error is OperationCanceledException)
                completion.TrySetCanceled();
            else if (error != null)
                completion.TrySetException(error);
            else
                completion.TrySetResult(new Snapshot(result));
        }

        private JObject Describe() =>
            new JObject
            {
                ["kind"] = kind,
                ["state"] = state,
                ["consumers"] = consumers,
                ["input_items"] = preparation?.InputCount ?? inputItems,
                ["operations"] = preparation?.Operations ?? operations,
                ["editor_updates"] = updates,
                ["native_call_milliseconds"] = nativeMilliseconds,
                ["preparation_milliseconds"] = preparationMilliseconds,
                ["maximum_callback_milliseconds"] = maximumCallbackMilliseconds,
                ["elapsed_milliseconds"] = elapsed.Elapsed.TotalMilliseconds,
                ["slice_operations_limit"] = 8192,
                ["slice_milliseconds_target"] = 4,
                ["native_call_interruptible"] = false,
            };

        internal static JObject Status()
        {
            var running = new JArray();
            foreach (ReferenceGraphPreparation job in active.Values)
                running.Add(job.Describe());
            var completed = new JArray();
            foreach (JObject value in recent.Values)
                completed.Add(value.DeepClone());
            return new JObject
            {
                ["active"] = running,
                ["recent"] = completed,
                ["maximum_active"] = 2,
            };
        }

        private static void CancelAll()
        {
            foreach (ReferenceGraphPreparation job in new List<ReferenceGraphPreparation>(active.Values))
                job.Finish("canceled", new OperationCanceledException());
        }
    }
}
