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

    private static Task Send(JObject state, CancellationToken token) => Task.CompletedTask;

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
        Case(
            "cancel before synchronous initial callback",
            () =>
            {
                using var cts = new CancellationTokenSource();
                EditorStateCache.BeforeInitial = _ => cts.Cancel();
                var task = Start(cts.Token);
                Require(task.IsCanceled, "dispatcher must remain canceled");
                Require(EditorStateCache.Count == 0, $"orphan observers={EditorStateCache.Count}");
            }
        );
        Case(
            "cancel inside subscription before Start returns",
            () =>
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
            }
        );
        Case(
            "cancel after Start before dispatcher result delivery",
            () =>
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
            }
        );
        Case(
            "caller receives and disposes ownership",
            () =>
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
            }
        );
        Case(
            "caller receives then connection cancels",
            () =>
            {
                using var cts = new CancellationTokenSource();
                var publisher = Start(cts.Token).Result;
                Idle(publisher);
                cts.Cancel();
                Require(EditorStateCache.Count == 0, $"canceled observers={EditorStateCache.Count}");
                publisher.Dispose();
            }
        );
        Case(
            "pre-canceled never subscribes",
            () =>
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
            }
        );
        Case(
            "subscription failure propagates and cleans up",
            () =>
            {
                using var cts = new CancellationTokenSource();
                EditorStateCache.FailSubscribe = true;
                var task = Start(cts.Token);
                Require(task.IsFaulted && task.Exception.InnerException is InvalidOperationException, "exception contract lost");
                cts.Cancel();
                Require(EditorStateCache.Count == 0, "failed start leaked");
            }
        );
        Case(
            "concurrent owner dispose and cancellation",
            () =>
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
            }
        );
        Case(
            "100 interrupted registrations retain zero observers",
            () =>
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
            }
        );
        Case(
            "successful sender retains only latest observation",
            () =>
            {
                using var cts = new CancellationTokenSource();
                using var entered = new ManualResetEventSlim();
                var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                var sequences = new List<int>();
                var publisher = EditorStatePublisher.Start(
                    (message, token) =>
                    {
                        lock (sequences)
                            sequences.Add((int)message["sequence"]);
                        if ((int)message["sequence"] != 1)
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
            }
        );
        Case(
            "cancel in-flight sender unregisters before drain settles",
            () =>
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
            }
        );
        Case(
            "send failure unregisters exactly once",
            () =>
            {
                using var cts = new CancellationTokenSource();
                var publisher = EditorStatePublisher.Start((message, token) => Task.FromException(new InvalidOperationException("send failed")), cts.Token);
                Idle(publisher);
                publisher.Dispose();
                cts.Cancel();
                Require(EditorStateCache.Count == 0 && EditorStateCache.Removed == 1, "send exception retained observer");
            }
        );
        Case(
            "explicit dispose unregisters cancellation callback capture",
            () =>
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
            }
        );
        Case(
            "owner disposes during synchronous subscription",
            () =>
            {
                using var cts = new CancellationTokenSource();
                EditorStateCache.BeforeInitial = publisher => ((EditorStatePublisher)publisher).Dispose();
                var publisher = Start(cts.Token).Result;
                Require(EditorStateCache.Count == 0 && EditorStateCache.Removed == 1, "unadopted subscription leaked");
                publisher.Dispose();
                cts.Cancel();
            }
        );
        Console.WriteLine($"TOTAL passed={_passed} failed={_failed}");
        return _failed == 0 ? 0 : 1;
    }
}
