using System;
using System.Collections.Generic;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEngine;
using UnityEngine.Networking;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Owns a native request and its callbacks for the current Editor domain.</summary>
    [InitializeOnLoad]
    internal sealed class EditorWebRequest : IDisposable
    {
        private static readonly HashSet<EditorWebRequest> Active = new();
        private static readonly int EditorThread = Thread.CurrentThread.ManagedThreadId;
        private static string shutdownReason;

        private readonly UnityWebRequest request;
        private readonly object cancellationGate = new();
        private UnityWebRequestAsyncOperation operation;
        private Action completed;
        private Action<Exception> interrupted;
        private CancellationToken cancellationToken;
        private CancellationTokenRegistration cancellationRegistration;
        private IDisposable scheduledCancellation;
        private Exception interruption;
        private int cancellationRequested;
        private int disposed;
        private bool sent;

        static EditorWebRequest()
        {
            AssemblyReloadEvents.beforeAssemblyReload += () => Shutdown("assembly_reload");
            EditorApplication.quitting += () => Shutdown("editor_quit");
        }

        internal EditorWebRequest(UnityWebRequest request)
        {
            ThrowIfUnavailable();
            this.request = request ?? throw new ArgumentNullException(nameof(request));
            Active.Add(this);
        }

        internal static void ThrowIfUnavailable()
        {
            RequireEditorThread();
            if (shutdownReason != null)
                throw new EditorWebRequestInterruptedException(shutdownReason);
        }

        internal bool IsDisposed => Volatile.Read(ref disposed) != 0;

        internal UnityWebRequest Request
        {
            get
            {
                RequireEditorThread();
                if (interruption != null)
                    throw interruption;
                if (IsDisposed)
                    throw new ObjectDisposedException(nameof(EditorWebRequest));
                return request;
            }
        }

        internal void Send(CancellationToken token, Action onCompleted = null, Action<Exception> onInterrupted = null)
        {
            ThrowIfUnavailable();
            if (IsDisposed)
                throw new ObjectDisposedException(nameof(EditorWebRequest));
            if (sent)
                throw new InvalidOperationException("An Editor web request can only be sent once.");
            sent = true;
            cancellationToken = token;
            completed = onCompleted;
            interrupted = onInterrupted;
            try
            {
                cancellationRegistration = token.Register(RequestCancellation);
                // Register can synchronously call us before its registration has been assigned.
                if (IsDisposed)
                {
                    cancellationRegistration.Dispose();
                    return;
                }
                if (Volatile.Read(ref cancellationRequested) != 0)
                {
                    CancelOnEditorThread();
                    return;
                }
                operation = request.SendWebRequest();
                operation.completed += OnCompleted;
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        private void OnCompleted(AsyncOperation _)
        {
            if (IsDisposed)
                return;
            if (Volatile.Read(ref cancellationRequested) != 0)
            {
                CancelOnEditorThread();
                return;
            }
            // Polling consumers retain the completed response until their own using scope ends.
            if (completed == null)
                return;
            try
            {
                completed();
            }
            finally
            {
                Dispose();
            }
        }

        private void RequestCancellation()
        {
            if (Interlocked.Exchange(ref cancellationRequested, 1) != 0 || IsDisposed)
                return;
            if (Thread.CurrentThread.ManagedThreadId == EditorThread)
            {
                CancelOnEditorThread();
                return;
            }
            // CancellationToken callbacks can run on timer/worker threads. No Unity API here.
            lock (cancellationGate)
            {
                if (IsDisposed)
                    return;
                try
                {
                    scheduledCancellation = EditorMainThreadWakeup.Schedule(CancelOnEditorThread);
                }
                catch (InvalidOperationException)
                {
                    // The scheduler can stop before our reload/quit hook. That hook drains
                    // Active synchronously, without depending on a queued cancellation.
                }
            }
        }

        private void CancelOnEditorThread() => Interrupt(new OperationCanceledException(cancellationToken));

        private void Interrupt(Exception reason)
        {
            if (IsDisposed)
                return;
            interruption = reason;
            Action<Exception> notify = interrupted;
            try
            {
                Dispose();
            }
            finally
            {
                notify?.Invoke(reason);
            }
        }

        public void Dispose()
        {
            RequireEditorThread();
            if (Interlocked.Exchange(ref disposed, 1) != 0)
                return;
            Active.Remove(this);
            completed = null;
            interrupted = null;
            if (operation != null)
                operation.completed -= OnCompleted;
            lock (cancellationGate)
            {
                scheduledCancellation?.Dispose();
                scheduledCancellation = null;
            }
            // Never wait for a token callback while holding the lock it uses to schedule work.
            cancellationRegistration.Dispose();
            try
            {
                if (operation != null && !operation.isDone)
                    request.Abort();
            }
            finally
            {
                request.Dispose();
            }
        }

        private static void Shutdown(string reason)
        {
            if (shutdownReason != null)
                return;
            shutdownReason = reason;
            var pending = new EditorWebRequest[Active.Count];
            Active.CopyTo(pending);
            foreach (EditorWebRequest owner in pending)
            {
                try
                {
                    owner.Interrupt(new EditorWebRequestInterruptedException(reason));
                }
                catch (Exception exception)
                {
                    // A failed consumer notification must not strand other native requests.
                    McpLog.Warn("Editor web request shutdown cleanup failed: " + exception.GetType().Name);
                }
            }
        }

        private static void RequireEditorThread()
        {
            if (Thread.CurrentThread.ManagedThreadId != EditorThread)
                throw new InvalidOperationException("Editor web requests must be used on the Unity main thread.");
        }
    }

    internal sealed class EditorWebRequestInterruptedException : InvalidOperationException
    {
        internal string Reason { get; }

        internal EditorWebRequestInterruptedException(string reason)
            : base(
                reason == "assembly_reload"
                    ? "The web request was interrupted by an Editor assembly reload."
                    : "The web request was interrupted because the Editor is quitting."
            )
        {
            Reason = reason;
        }
    }
}
