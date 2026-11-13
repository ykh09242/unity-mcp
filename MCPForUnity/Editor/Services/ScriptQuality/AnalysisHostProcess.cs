using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.ScriptQuality
{
    // One owner per Editor. A queued/canceled job never releases a live child's slot.
    internal sealed class AnalysisHostProcess : IDisposable
    {
        private readonly SemaphoreSlim _slot = new SemaphoreSlim(1, 1);
        private readonly CancellationTokenSource _shutdown = new CancellationTokenSource();
        private readonly object _gate = new object();
        private Process _active;
        private string _activeJobId;
        private int _occupied;
        internal int Occupied => Volatile.Read(ref _occupied);
        internal int ActiveProcessId
        {
            get
            {
                lock (_gate)
                    return _active?.Id ?? 0;
            }
        }

        internal bool HasActiveChild(string jobId)
        {
            lock (_gate)
                return jobId != null && _active != null && _activeJobId == jobId;
        }

        internal async Task<byte[]> RunAsync(
            string executable,
            string[] arguments,
            string directory,
            Stream input,
            bool framed,
            CancellationToken cancellation,
            Func<Task> beforeStart = null,
            string jobId = null
        )
        {
            if (Interlocked.Increment(ref _occupied) > 3)
            {
                Interlocked.Decrement(ref _occupied);
                throw new InvalidOperationException("analysis_queue_full");
            }
            bool acquired = false,
                started = false,
                reaped = true;
            Process process = null;
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellation, _shutdown.Token);
            try
            {
                await _slot.WaitAsync(linked.Token).ConfigureAwait(false);
                acquired = true;
                if (beforeStart != null)
                    await beforeStart().ConfigureAwait(false);
                linked.Token.ThrowIfCancellationRequested();
                var info = new ProcessStartInfo
                {
                    FileName = executable,
                    WorkingDirectory = directory,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                    RedirectStandardInput = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                AddArguments(info, arguments);
                // Unity Mono's redirected StreamWriter can emit a BOM before BaseStream writes.
                // Framed binary input must begin with the four length bytes on every runtime.
                typeof(ProcessStartInfo).GetProperty("StandardInputEncoding")?.SetValue(info, new UTF8Encoding(false));
                // Only the shipped host project is built; no inherited MSBuild import directories.
                info.EnvironmentVariables["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1";
                info.EnvironmentVariables["MSBUILDDISABLENODEREUSE"] = "1";
                info.EnvironmentVariables["DOTNET_CLI_USE_MSBUILD_SERVER"] = "0";
                process = new Process { StartInfo = info };
                lock (_gate)
                {
                    linked.Token.ThrowIfCancellationRequested();
                    if (!process.Start())
                        throw new IOException("analysis_process_not_started");
                    started = true;
                    _active = process;
                    _activeJobId = jobId;
                }
                var canceled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                using var registration = linked.Token.Register(() => canceled.TrySetResult(true));
                var output = framed
                    ? ReadFrameAsync(process.StandardOutput.BaseStream, linked.Token)
                    : ReadBoundedAsync(process.StandardOutput.BaseStream, 1024 * 1024, linked.Token);
                var errors = ReadBoundedAsync(process.StandardError.BaseStream, 65536, linked.Token);
                var write = WriteAsync(process.StandardInput.BaseStream, input, linked.Token);
                var io = Task.WhenAll(output, errors, write);
                _ = io.ContinueWith(
                    task =>
                    {
                        var observed = task.Exception;
                    },
                    CancellationToken.None,
                    TaskContinuationOptions.OnlyOnFaulted,
                    TaskScheduler.Default
                );
                // Observe an early pipe failure without canceling away its bounded stderr evidence.
                var pipeFailure = new TaskCompletionSource<Exception>(TaskCreationOptions.RunContinuationsAsynchronously);
                foreach (var task in new Task[] { output, errors, write })
                    _ = task.ContinueWith(
                        failed => pipeFailure.TrySetResult(failed.Exception.GetBaseException()),
                        CancellationToken.None,
                        TaskContinuationOptions.OnlyOnFaulted,
                        TaskScheduler.Default
                    );
                await Task.WhenAny(io, canceled.Task, pipeFailure.Task).ConfigureAwait(false);
                cancellation.ThrowIfCancellationRequested();
                _shutdown.Token.ThrowIfCancellationRequested();
                if (pipeFailure.Task.IsCompleted || io.IsFaulted)
                {
                    var failure = pipeFailure.Task.IsCompleted ? pipeFailure.Task.Result : io.Exception.GetBaseException();
                    TerminateAndReap(process);
                    await Task.WhenAny(errors, Task.Delay(100)).ConfigureAwait(false);
                    string detail = errors.Status == TaskStatus.RanToCompletion ? Encoding.UTF8.GetString(errors.Result) : "";
                    throw new IOException(failure.Message + (detail.Length == 0 ? "" : ": " + detail), failure);
                }
                if (!io.IsCompleted)
                {
                    if (output.IsFaulted)
                        await output.ConfigureAwait(false);
                    if (errors.IsFaulted)
                        await errors.ConfigureAwait(false);
                    if (write.IsFaulted)
                        await write.ConfigureAwait(false);
                    throw new IOException("analysis_pipe_failed");
                }
                await io.ConfigureAwait(false);
                while (!process.HasExited)
                    await Task.Delay(20, linked.Token).ConfigureAwait(false);
                if (process.ExitCode != 0)
                    throw new IOException("analysis_process_failed: " + Encoding.UTF8.GetString(errors.Result));
                return output.Result;
            }
            finally
            {
                linked.Cancel();
                if (started)
                    reaped = TerminateAndReap(process);
                if (reaped)
                {
                    lock (_gate)
                        if (_active == process)
                        {
                            _active = null;
                            _activeJobId = null;
                        }
                    process?.Dispose();
                    if (acquired)
                        _slot.Release();
                    Interlocked.Decrement(ref _occupied);
                }
                // An unconfirmed exit deliberately retains capacity and the process handle.
            }
        }

        internal bool Stop()
        {
            _shutdown.Cancel();
            lock (_gate)
                return _active == null || TerminateAndReap(_active);
        }

        private static bool TerminateAndReap(Process process)
        {
            try
            {
                if (!process.HasExited)
                {
                    var treeKill = typeof(Process).GetMethod("Kill", new[] { typeof(bool) });
                    if (treeKill != null)
                        treeKill.Invoke(process, new object[] { true });
                    else
                        process.Kill();
                }
                return process.WaitForExit(5000) && process.HasExited;
            }
            catch (InvalidOperationException)
            {
                try
                {
                    return process.HasExited;
                }
                catch
                {
                    return false;
                }
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static void AddArguments(ProcessStartInfo info, IEnumerable<string> arguments)
        {
            var values = arguments.ToArray();
            if (values.Any(value => value == null || value.IndexOf('\0') >= 0))
                throw new ArgumentException("invalid_process_argument");
            var list = typeof(ProcessStartInfo).GetProperty("ArgumentList")?.GetValue(info) as ICollection<string>;
            if (list != null)
            {
                foreach (string value in values)
                    list.Add(value);
                return;
            }
            // Older Unity Mono exposes only Arguments. Encode literal argv; no shell is launched.
            info.Arguments = string.Join(" ", values.Select(QuoteArgument));
        }

        private static string QuoteArgument(string value)
        {
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char character in value)
            {
                if (character == '\\')
                {
                    slashes++;
                    continue;
                }
                result.Append('\\', character == '"' ? slashes * 2 + 1 : slashes).Append(character);
                slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }

        private static async Task<byte[]> WriteAsync(Stream output, Stream input, CancellationToken cancellation)
        {
            if (input != null)
                await input.CopyToAsync(output, 65536, cancellation).ConfigureAwait(false);
            output.Close();
            return Array.Empty<byte>();
        }

        internal static async Task<byte[]> ReadFrameAsync(Stream stream, CancellationToken cancellation)
        {
            byte[] prefix = new byte[4];
            await ReadExactAsync(stream, prefix, cancellation).ConfigureAwait(false);
            int length = prefix[0] | prefix[1] << 8 | prefix[2] << 16 | prefix[3] << 24;
            if (length <= 0 || length > 9 * 1024 * 1024)
                throw new InvalidDataException("analysis_response_frame_limit");
            byte[] result = new byte[length];
            await ReadExactAsync(stream, result, cancellation).ConfigureAwait(false);
            byte[] trailing = new byte[1];
            if (await stream.ReadAsync(trailing, 0, 1, cancellation).ConfigureAwait(false) != 0)
                throw new InvalidDataException("analysis_response_trailing_bytes");
            return result;
        }

        private static async Task ReadExactAsync(Stream stream, byte[] buffer, CancellationToken cancellation)
        {
            int offset = 0;
            while (offset < buffer.Length)
            {
                int read = await stream.ReadAsync(buffer, offset, buffer.Length - offset, cancellation).ConfigureAwait(false);
                if (read == 0)
                    throw new EndOfStreamException("analysis_response_incomplete");
                offset += read;
            }
        }

        private static async Task<byte[]> ReadBoundedAsync(Stream stream, int maximum, CancellationToken cancellation)
        {
            using var result = new MemoryStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = await stream.ReadAsync(buffer, 0, buffer.Length, cancellation).ConfigureAwait(false)) > 0)
            {
                if (result.Length + read > maximum)
                    throw new IOException("analysis_output_limit");
                result.Write(buffer, 0, read);
            }
            return result.ToArray();
        }

        public void Dispose() => Stop();
    }
}
