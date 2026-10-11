using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using UnityEditor;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    internal sealed class PlayScenarioImportRaceException : IOException
    {
        internal PlayScenarioImportRaceException(string message, Exception inner = null)
            : base(message, inner) { }
    }

    internal readonly struct PlayScenarioImportFileStamp
    {
        private readonly long length;
        private readonly long modifiedTicks;

        private PlayScenarioImportFileStamp(long length, long modifiedTicks)
        {
            this.length = length;
            this.modifiedTicks = modifiedTicks;
        }

        internal static PlayScenarioImportFileStamp Capture(string file)
        {
            FileAttributes attributes = File.GetAttributes(file);
            if ((attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
                throw new InvalidDataException("Import artifacts must be regular unlinked files.");
            try
            {
                var info = new FileInfo(file);
                return new PlayScenarioImportFileStamp(info.Length, info.LastWriteTimeUtc.Ticks);
            }
            catch (FileNotFoundException exception)
            {
                throw new PlayScenarioImportRaceException("Observed artifact disappeared before its metadata was read.", exception);
            }
            catch (DirectoryNotFoundException exception)
            {
                throw new PlayScenarioImportRaceException("Observed artifact directory disappeared before metadata was read.", exception);
            }
        }

        internal void Verify(string file)
        {
            PlayScenarioImportFileStamp current;
            try
            {
                current = Capture(file);
            }
            catch (FileNotFoundException exception)
            {
                throw new PlayScenarioImportRaceException("Previously observed artifact disappeared during import.", exception);
            }
            catch (DirectoryNotFoundException exception)
            {
                throw new PlayScenarioImportRaceException("Previously observed artifact directory disappeared during import.", exception);
            }
            if (current.length != length || current.modifiedTicks != modifiedTicks)
                throw new PlayScenarioImportRaceException("Observed artifact length or modification time changed during import.");
        }
    }

    /// <summary>One user-triggered read at a time; callbacks run only in the owning Editor update.</summary>
    internal sealed class PlayScenarioImportWorker : IDisposable
    {
        internal const int MaximumAttempts = 3;
        internal const int DeadlineMilliseconds = 5000;

        private sealed class Completion
        {
            internal object Value;
            internal Exception Error;
        }

        private sealed class Request
        {
            internal string Key;
            internal Func<CancellationToken, object> Read;
            internal Action<object, Exception> Apply;
            internal CancellationTokenSource Cancellation;
            internal Task<Completion> Task;
        }

        private Request active;
        private Request pending;
        private bool pumping;
        private bool disposed;
        private readonly int ownerThread = Thread.CurrentThread.ManagedThreadId;
        internal bool IsBusy => active != null || pending != null;
        internal bool IsPumping => pumping;
        internal bool IsDisposed => disposed;

        internal static string SourceKey(string path)
        {
            try
            {
                string full = Path.GetFullPath(path);
                return Path.DirectorySeparatorChar == '\\' ? full.ToUpperInvariant() : full;
            }
            catch (ArgumentException)
            {
                return path ?? "";
            }
            catch (NotSupportedException)
            {
                return path ?? "";
            }
            catch (PathTooLongException)
            {
                return path ?? "";
            }
        }

        internal bool RequestRead(string key, Func<CancellationToken, object> read, Action<object, Exception> apply)
        {
            EnsureOwnerThread();
            if (disposed)
                return false;
            if (active?.Apply != null && active.Key == key || pending?.Key == key)
                return false;
            CancelRequest(active);
            pending = new Request
            {
                Key = key,
                Read = read,
                Apply = apply,
            };
            if (!pumping)
            {
                pumping = true;
                EditorApplication.update += Pump;
                AssemblyReloadEvents.beforeAssemblyReload += Dispose;
            }
            if (active == null)
                StartPending();
            return true;
        }

        internal void Cancel()
        {
            EnsureOwnerThread();
            pending = null;
            CancelRequest(active);
        }

        private static void CancelRequest(Request request)
        {
            if (request == null)
                return;
            request.Apply = null;
            request.Read = null;
            request.Cancellation.Cancel();
        }

        private void StartPending()
        {
            active = pending;
            pending = null;
            active.Cancellation = new CancellationTokenSource();
            active.Task = StartRead(active.Read, active.Cancellation.Token);
            active.Read = null;
        }

        private static Task<Completion> StartRead(Func<CancellationToken, object> read, CancellationToken cancellation) =>
            Task.Run(() => ReadBounded(read, cancellation));

        private static Completion ReadBounded(Func<CancellationToken, object> read, CancellationToken cancellation)
        {
            var elapsed = Stopwatch.StartNew();
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
            {
                deadline.CancelAfter(DeadlineMilliseconds);
                for (int attempt = 1; attempt <= MaximumAttempts; attempt++)
                {
                    try
                    {
                        deadline.Token.ThrowIfCancellationRequested();
                        object value = read(deadline.Token);
                        deadline.Token.ThrowIfCancellationRequested();
                        return new Completion { Value = value };
                    }
                    catch (OperationCanceledException exception)
                    {
                        return new Completion
                        {
                            Error = cancellation.IsCancellationRequested
                                ? exception
                                : new TimeoutException("Import exceeded its bounded read deadline.", exception),
                        };
                    }
                    catch (Exception exception)
                    {
                        if (!IsTransient(exception) || attempt == MaximumAttempts || elapsed.ElapsedMilliseconds + 25 >= DeadlineMilliseconds)
                            return new Completion { Error = exception };
                        if (deadline.Token.WaitHandle.WaitOne(25))
                            return new Completion
                            {
                                Error = cancellation.IsCancellationRequested
                                    ? new OperationCanceledException(cancellation)
                                    : new TimeoutException("Import exceeded its bounded retry deadline."),
                            };
                    }
                }
            }
            throw new InvalidOperationException("Import attempt bound was not enforced.");
        }

        internal static bool IsTransient(Exception exception)
        {
            if (exception is PlayScenarioImportRaceException)
                return true;
            // Only the specific OS sharing/locking errors identify a transient access race.
            return exception is IOException
                && !(exception is InvalidDataException)
                && ((exception.HResult & 0xffff) == 32 || (exception.HResult & 0xffff) == 33);
        }

        internal void Pump()
        {
            EnsureOwnerThread();
            if (disposed || active == null || !active.Task.IsCompleted)
                return;
            Request finished = active;
            active = null;
            finished.Cancellation.Dispose();
            Action<object, Exception> apply = finished.Apply;
            finished.Apply = null;
            try
            {
                Completion result;
                try
                {
                    result = finished.Task.GetAwaiter().GetResult();
                }
                catch (Exception exception)
                {
                    result = new Completion { Error = exception };
                }
                apply?.Invoke(result.Value, result.Error);
            }
            finally
            {
                finished.Task = null;
                if (pending != null && !disposed)
                    StartPending();
                else if (active == null)
                    StopPump();
            }
        }

        private void StopPump()
        {
            EditorApplication.update -= Pump;
            AssemblyReloadEvents.beforeAssemblyReload -= Dispose;
            pumping = false;
        }

        public void Dispose()
        {
            EnsureOwnerThread();
            if (disposed)
                return;
            disposed = true;
            pending = null;
            CancelRequest(active);
            if (active != null)
            {
                CancellationTokenSource cancellation = active.Cancellation;
                active.Task.ContinueWith(
                    _ => cancellation.Dispose(),
                    CancellationToken.None,
                    TaskContinuationOptions.ExecuteSynchronously,
                    TaskScheduler.Default
                );
                active = null;
            }
            StopPump();
        }

        private void EnsureOwnerThread()
        {
            if (Thread.CurrentThread.ManagedThreadId != ownerThread)
                throw new InvalidOperationException("Importer coordination must run on its owning Editor thread.");
        }
    }

    internal sealed class PlayScenarioImportJsonReader : JsonTextReader
    {
        private readonly CancellationToken cancellation;

        internal PlayScenarioImportJsonReader(TextReader reader, CancellationToken cancellation)
            : base(reader) => this.cancellation = cancellation;

        public override bool Read()
        {
            cancellation.ThrowIfCancellationRequested();
            return base.Read();
        }
    }
}
