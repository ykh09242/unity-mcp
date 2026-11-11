using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
#if LEGACY_EDITOR_STATE
using Observation = Newtonsoft.Json.Linq.JObject;
#else
using Observation = MCPForUnity.Editor.Services.EditorStateFrame;
#endif

internal static class Program
{
    private static int _passed,
        _failed;
    private static readonly FieldInfo Gate = typeof(EditorStatePublisher).GetField("_gate", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo Sending = typeof(EditorStatePublisher).GetField("_sending", BindingFlags.Instance | BindingFlags.NonPublic);

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }

    private static void Idle(object publisher)
    {
        Require(
            SpinWait.SpinUntil(
                () =>
                {
                    lock (Gate.GetValue(publisher))
                        return !(bool)Sending.GetValue(publisher);
                },
                5000
            ),
            "sender did not settle"
        );
    }

    private static int ReadSequence(Observation message)
    {
#if LEGACY_EDITOR_STATE
        return (int)message["sequence"];
#else
        return (int)message.Sequence;
#endif
    }

    private static Task Send(Observation state, CancellationToken token) => Task.CompletedTask;

    private static Task Completion(EditorStatePublisher publisher)
    {
        var property = typeof(EditorStatePublisher).GetProperty("Completion", BindingFlags.Instance | BindingFlags.NonPublic);
        Require(property != null, "publisher must expose actual Completion");
        return (Task)property.GetValue(publisher);
    }

    private static void Case(string name, Action test)
    {
        EditorStateCache.Reset();
        try
        {
            test();
            _passed++;
            Console.WriteLine("PASS " + name);
        }
        catch (Exception ex)
        {
            _failed++;
            Console.WriteLine("FAIL " + name + ": " + ex.Message);
        }
        finally
        {
            EditorStateCache.Reset();
        }
    }

    private static Task<EditorStatePublisher> Start(CancellationToken token) =>
        TransportCommandDispatcher.RunOnMainThreadAsync(() => EditorStatePublisher.Start(Send, token), token);

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static WeakReference DisposedPublisher(CancellationToken token)
    {
        var publisher = EditorStatePublisher.Start(Send, token);
        Idle(publisher);
        publisher.Dispose();
        return new WeakReference(publisher);
    }

    public static int Main()
    {
        Case("cancel before synchronous initial callback", () =>
        {
            using var cts = new CancellationTokenSource();
            EditorStateCache.BeforeInitial = _ => cts.Cancel();
            var task = Start(cts.Token);
            Require(task.IsCanceled, "dispatcher must remain canceled");
            Require(EditorStateCache.Count == 0, $"orphan observers={EditorStateCache.Count}");
        });
        Case("cancel inside subscription before Start returns", () =>
        {
            using var cts = new CancellationTokenSource();
            EditorStateCache.BeforeReturn = publisher =>
            {
                Idle(publisher);
                cts.Cancel();
            };
            var task = Start(cts.Token);
            Require(task.IsCanceled, "dispatcher must remain canceled");
            Require(EditorStateCache.Count == 0, $"orphan observers={EditorStateCache.Count}");
        });
        Case("cancel after Start before dispatcher result delivery", () =>
        {
            using var cts = new CancellationTokenSource();
            var task = TransportCommandDispatcher.RunOnMainThreadAsync(
                () =>
                {
                    var publisher = EditorStatePublisher.Start(Send, cts.Token);
                    Idle(publisher);
                    cts.Cancel();
                    return publisher;
                },
                cts.Token
            );
            Require(task.IsCanceled, "cancellation must win result delivery");
            Require(EditorStateCache.Count == 0, $"lost-result observers={EditorStateCache.Count}");
        });
        Case("caller receives and disposes ownership", () =>
        {
            using var cts = new CancellationTokenSource();
            var task = Start(cts.Token);
            Require(task.IsCompletedSuccessfully, "success result missing");
            var publisher = task.Result;
            Idle(publisher);
            Require(EditorStateCache.Count == 1, "live subscription missing");
            publisher.Dispose();
            publisher.Dispose();
            cts.Cancel();
            Require(EditorStateCache.Count == 0 && EditorStateCache.Removed == 1, "dispose must remove exactly once");
        });
        Case("caller receives then connection cancels", () =>
        {
            using var cts = new CancellationTokenSource();
            var publisher = Start(cts.Token).Result;
            Idle(publisher);
            cts.Cancel();
            Require(EditorStateCache.Count == 0, $"canceled observers={EditorStateCache.Count}");
            publisher.Dispose();
        });
        Case("pre-canceled never subscribes", () =>
        {
            using var cts = new CancellationTokenSource();
            cts.Cancel();
            Require(Start(cts.Token).IsCanceled && EditorStateCache.Count == 0, "pre-canceled started");
            try
            {
                EditorStatePublisher.Start(Send, cts.Token);
                throw new Exception("expected cancellation");
            }
            catch (OperationCanceledException) { }
        });
        Case("subscription failure propagates and cleans up", () =>
        {
            using var cts = new CancellationTokenSource();
            EditorStateCache.FailSubscribe = true;
            var task = Start(cts.Token);
            Require(task.IsFaulted && task.Exception.InnerException is InvalidOperationException, "exception contract lost");
            cts.Cancel();
            Require(EditorStateCache.Count == 0, "failed start leaked");
        });
        Case("concurrent owner dispose and cancellation", () =>
        {
            for (int i = 0; i < 100; i++)
            {
                using var cts = new CancellationTokenSource();
                var publisher = EditorStatePublisher.Start(Send, cts.Token);
                Idle(publisher);
                var cancel = Task.Run(cts.Cancel);
                var dispose = Task.Run(publisher.Dispose);
                Require(Task.WaitAll(new[] { cancel, dispose }, 5000), "dispose/cancel deadlock");
                Require(EditorStateCache.Count == 0, "race retained subscription");
            }
        });
        Case("100 interrupted registrations retain zero observers", () =>
        {
            for (int i = 0; i < 100; i++)
            {
                using var cts = new CancellationTokenSource();
                EditorStateCache.BeforeInitial = _ => cts.Cancel();
                Require(Start(cts.Token).IsCanceled, "interrupted task not canceled");
                EditorStateCache.BeforeInitial = null;
            }
            Console.WriteLine($"METRIC interrupted=100 observers={EditorStateCache.Count}");
            Require(EditorStateCache.Count == 0, "observer count grew");
        });
        Case("successful sender retains only latest observation", () =>
        {
            using var cts = new CancellationTokenSource();
            using var entered = new ManualResetEventSlim();
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var sequences = new List<int>();
            var publisher = EditorStatePublisher.Start(
                (message, token) =>
                {
                    lock (sequences)
                        sequences.Add(ReadSequence(message));
                    if (ReadSequence(message) != 1)
                        return Task.CompletedTask;
                    entered.Set();
                    return release.Task;
                },
                cts.Token
            );
            Require(entered.Wait(5000), "initial send missing");
            EditorStateCache.Emit(2);
            EditorStateCache.Emit(3);
            release.SetResult(true);
            Idle(publisher);
            lock (sequences)
                Require(sequences.Count == 2 && sequences[0] == 1 && sequences[1] == 3, "latest-slot coalescing changed");
            publisher.Dispose();
            Require(EditorStateCache.Count == 0, "success cleanup failed");
        });
        Case("cancel in-flight sender unregisters before drain settles", () =>
        {
            using var cts = new CancellationTokenSource();
            using var entered = new ManualResetEventSlim();
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            CancellationToken sendToken = default;
            var publisher = EditorStatePublisher.Start(
                (message, token) =>
                {
                    sendToken = token;
                    entered.Set();
                    return release.Task;
                },
                cts.Token
            );
            Require(entered.Wait(5000), "send did not start");
            cts.Cancel();
            Require(sendToken.IsCancellationRequested, "send cancellation not propagated");
            Require(EditorStateCache.Count == 0, "in-flight cancellation retained observer");
            release.SetResult(true);
            Idle(publisher);
            publisher.Dispose();
            Require(EditorStateCache.Removed == 1, "multiple unsubscribe");
        });
        Case("send failure unregisters exactly once", () =>
        {
            using var cts = new CancellationTokenSource();
            var publisher = EditorStatePublisher.Start((message, token) => Task.FromException(new InvalidOperationException("send failed")), cts.Token);
            Idle(publisher);
            publisher.Dispose();
            cts.Cancel();
            Require(EditorStateCache.Count == 0 && EditorStateCache.Removed == 1, "send exception retained observer");
        });
        Case("explicit dispose unregisters cancellation callback capture", () =>
        {
            using var cts = new CancellationTokenSource();
            var weak = DisposedPublisher(cts.Token);
            Require(
                SpinWait.SpinUntil(
                    () =>
                    {
                        GC.Collect();
                        GC.WaitForPendingFinalizers();
                        GC.Collect();
                        return !weak.IsAlive;
                    },
                    5000
                ),
                "connection token retained disposed publisher"
            );
            GC.KeepAlive(cts);
        });
        Case("owner disposes during synchronous subscription", () =>
        {
            using var cts = new CancellationTokenSource();
            EditorStateCache.BeforeInitial = publisher => ((EditorStatePublisher)publisher).Dispose();
            var publisher = Start(cts.Token).Result;
            Require(EditorStateCache.Count == 0 && EditorStateCache.Removed == 1, "unadopted subscription leaked");
            publisher.Dispose();
            cts.Cancel();
        });
        Case("idle publisher completes only after disposal", () =>
        {
            using var publisher = EditorStatePublisher.Start(Send, CancellationToken.None);
            Idle(publisher);
            var completion = Completion(publisher);
            Require(!completion.IsCompleted, "idle publisher is still active");
            publisher.Dispose();
            Require(completion.Wait(5000), "idle publisher completion missing");
            Require(ReferenceEquals(completion, Completion(publisher)), "completion identity changed");
        });
        Case("completion waits for ignored cancellation and drops pending observations", () =>
        {
            using var cts = new CancellationTokenSource();
            using var entered = new ManualResetEventSlim();
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int sends = 0;
            using var publisher = EditorStatePublisher.Start(
                (message, token) =>
                {
                    Interlocked.Increment(ref sends);
                    entered.Set();
                    return release.Task;
                },
                cts.Token
            );
            try
            {
                Require(entered.Wait(5000), "held send did not start");
                var completion = Completion(publisher);
                EditorStateCache.Emit(2);
                cts.Cancel();
                publisher.Dispose();
                Require(!completion.IsCompleted, "canceling an active send claimed completion");
                Require(EditorStateCache.Count == 0, "dispose retained subscription");
                release.SetResult(true);
                Require(completion.Wait(5000), "released send did not complete publisher");
                Require(sends == 1, "pending observation sent after disposal");
            }
            finally
            {
                release.TrySetResult(true);
                Idle(publisher);
            }
        });
        Case("completion observes cancellation-aware send cleanup", () =>
        {
            using var entered = new ManualResetEventSlim();
            using var cleanup = new ManualResetEventSlim();
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var publisher = EditorStatePublisher.Start(
                async (message, token) =>
                {
                    entered.Set();
                    try
                    {
                        await Task.Delay(Timeout.Infinite, token).ConfigureAwait(false);
                    }
                    finally
                    {
                        cleanup.Set();
                        await release.Task.ConfigureAwait(false);
                    }
                },
                CancellationToken.None
            );
            try
            {
                Require(entered.Wait(5000), "send did not start");
                var completion = Completion(publisher);
                publisher.Dispose();
                Require(cleanup.Wait(5000), "send cancellation cleanup missing");
                Require(!completion.IsCompleted, "publisher completed before send cleanup");
                release.SetResult(true);
                Require(completion.Wait(5000), "cleanup did not complete publisher");
            }
            finally
            {
                publisher.Dispose();
                release.TrySetResult(true);
                Idle(publisher);
            }
        });
        Case("completion waits for synchronous subscription adoption", () =>
        {
            EditorStateCache.BeforeReturn = value =>
            {
                var publisher = (EditorStatePublisher)value;
                Idle(publisher);
                publisher.Dispose();
                Require(!Completion(publisher).IsCompleted, "unadopted subscription claimed completion");
            };
            using var publisher = EditorStatePublisher.Start(Send, CancellationToken.None);
            Require(Completion(publisher).Wait(5000), "completed Start did not settle disposal");
            Require(EditorStateCache.Count == 0, "synchronous subscription retained observer");
        });
        Case("cancel callbacks run outside publisher gate and completion waits for them", () =>
        {
            using var entered = new ManualResetEventSlim();
            using var cancelEntered = new ManualResetEventSlim();
            using var cancelRelease = new ManualResetEventSlim();
            CancellationTokenRegistration registration = default;
            using var publisher = EditorStatePublisher.Start(
                (message, token) =>
                {
                    registration = token.Register(() =>
                    {
                        cancelEntered.Set();
                        cancelRelease.Wait(5000);
                    });
                    entered.Set();
                    return Task.CompletedTask;
                },
                CancellationToken.None
            );
            Task disposal = null;
            try
            {
                Require(entered.Wait(5000), "send did not start");
                Idle(publisher);
                var completion = Completion(publisher);
                disposal = Task.Run(publisher.Dispose);
                Require(cancelEntered.Wait(5000), "cancel callback missing");
                Require(!completion.IsCompleted, "callback still owns lifetime resources");
                Require(Task.Run(() => EditorStateCache.Emit(2)).Wait(1000), "cancel callback held publisher gate");
                cancelRelease.Set();
                Require(disposal.Wait(5000) && completion.Wait(5000), "cancellation did not settle");
            }
            finally
            {
                cancelRelease.Set();
                disposal?.Wait(5000);
                registration.Dispose();
            }
        });
        Case("captured transport drain retains cancellation-ignoring send", () =>
        {
            using var cts = new CancellationTokenSource();
            using var entered = new ManualResetEventSlim();
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var publisher = EditorStatePublisher.Start(
                (message, token) =>
                {
                    entered.Set();
                    return release.Task;
                },
                cts.Token
            );
            var transport = new TransportLifetimeBoundary(publisher, new TransportLifetimeBoundary.ClientWebSocket(), cts);
            Task drain = null;
            try
            {
                Require(entered.Wait(5000), "send did not start");
                drain = transport.CaptureDrain()();
                Require(SpinWait.SpinUntil(() => EditorStateCache.Count == 0, 5000), "drain did not dispose publisher");
                Require(!drain.Wait(100), "transport released connection before send settled");
                Require(ReferenceEquals(transport.Publisher, publisher), "transport released publisher owner early");
                release.SetResult(true);
                Require(drain.Wait(5000), "transport did not observe send settlement");
                Require(transport.Publisher == null, "settled captured publisher still owned");
            }
            finally
            {
                release.TrySetResult(true);
                drain?.Wait(5000);
                Idle(publisher);
            }
        });
        Case("captured transport abort unblocks send without touching replacement owners", () =>
        {
            using var cts = new CancellationTokenSource();
            using var entered = new ManualResetEventSlim();
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var publisher = EditorStatePublisher.Start(
                (message, token) =>
                {
                    entered.Set();
                    return release.Task;
                },
                cts.Token
            );
            var capturedSocket = new TransportLifetimeBoundary.ClientWebSocket { OnAbort = () => release.TrySetResult(true) };
            var replacementSocket = new TransportLifetimeBoundary.ClientWebSocket();
            var transport = new TransportLifetimeBoundary(publisher, capturedSocket, cts);
            Task drain = null;
            try
            {
                Require(entered.Wait(5000), "send did not start");
                var capturedDrain = transport.CaptureDrain();
                using var replacement = EditorStatePublisher.Start(Send, CancellationToken.None);
                using var replacementCts = new CancellationTokenSource();
                Idle(replacement);
                transport.Replace(replacement, replacementSocket, replacementCts);
                drain = capturedDrain();
                Require(drain.Wait(5000), "socket abort was delayed behind active send");
                Require(capturedSocket.Aborted, "captured socket not aborted to settle send");
                Require(!replacementSocket.Aborted, "replacement socket was aborted");
                Require(ReferenceEquals(transport.Publisher, replacement), "replacement publisher cleared by old drain");
                Require(ReferenceEquals(transport.PublisherSettlement, Completion(replacement)), "replacement publisher completion cleared by old drain");
            }
            finally
            {
                release.TrySetResult(true);
                drain?.Wait(5000);
                Idle(publisher);
            }
        });
        Case("repeated registration retains replaced publisher completion", () =>
        {
            using var cts = new CancellationTokenSource();
            using var entered = new ManualResetEventSlim();
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int sends = 0;
            var transport = new TransportLifetimeBoundary(null, new TransportLifetimeBoundary.ClientWebSocket(), cts)
            {
                Send = (message, token) =>
                {
                    if (Interlocked.Increment(ref sends) != 1)
                        return Task.CompletedTask;
                    entered.Set();
                    return release.Task;
                },
            };
            Task drain = null;
            try
            {
                Require(transport.StartRegistration().Wait(5000), "first registration did not settle");
                Require(entered.Wait(5000), "first publisher send missing");
                var first = transport.Publisher;
                Require(transport.StartRegistration().Wait(5000), "repeated registration did not settle");
                Require(!ReferenceEquals(first, transport.Publisher), "repeated registration did not replace publisher");
                drain = transport.CaptureDrain()();
                Require(SpinWait.SpinUntil(() => EditorStateCache.Count == 0, 5000), "drain did not dispose publishers");
                Require(!drain.Wait(100), "replaced publisher send lost its completion owner");
                release.SetResult(true);
                Require(drain.Wait(5000), "replaced publisher settlement not observed");
            }
            finally
            {
                release.TrySetResult(true);
                if (drain == null)
                    cts.Cancel();
                drain?.Wait(5000);
            }
        });
        foreach (bool captureBeforeRegistration in new[] { false, true })
            Case(
                captureBeforeRegistration
                    ? "capture before registration still observes its active publisher"
                    : "registration retains publisher before cancellable dispatcher returns",
                () =>
                {
                    using var cts = new CancellationTokenSource();
                    using var entered = new ManualResetEventSlim();
                    var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    var transport = new TransportLifetimeBoundary(null, new TransportLifetimeBoundary.ClientWebSocket(), cts)
                    {
                        Send = (message, token) =>
                        {
                            entered.Set();
                            return release.Task;
                        },
                    };
                    Task drain = null;
                    EditorStatePublisher started = null;
                    var earlyCapture = captureBeforeRegistration ? transport.CaptureDrain() : null;
                    EditorStateCache.BeforeReturn = value =>
                    {
                        started = (EditorStatePublisher)value;
                        Require(entered.Wait(5000), "initial send missing");
                        var capturedDrain = earlyCapture ?? transport.CaptureDrain();
                        cts.Cancel();
                        drain = capturedDrain();
                        Require(!drain.Wait(100), "lost dispatcher result released active publisher send");
                    };
                    var registration = Task.Run(transport.StartRegistration);
                    try
                    {
                        Require(SpinWait.SpinUntil(() => TransportCommandDispatcher.PendingCount == 1, 5000), "registration callback not queued");
                        TransportCommandDispatcher.Pump();
                        Require(registration.Wait(5000), "canceled dispatcher result did not settle registration");
                        Require(drain != null && !drain.IsCompleted, "connection drain missed the unreturned publisher");
                        Require(ReferenceEquals(transport.Publisher, started), "publisher not adopted before first observation");
                        release.SetResult(true);
                        Require(drain.Wait(5000), "lost-result publisher did not settle");
                        Require(transport.Publisher == null && EditorStateCache.Count == 0, "lost-result publisher ownership retained");
                    }
                    finally
                    {
                        release.TrySetResult(true);
                        started?.Dispose();
                        drain?.Wait(5000);
                        if (started != null)
                            Idle(started);
                    }
                }
            );
        Console.WriteLine($"TOTAL passed={_passed} failed={_failed}");
        return _failed == 0 ? 0 : 1;
    }
}
