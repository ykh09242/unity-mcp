using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Security
{
    /// <summary>Owns a key-store CLI process and its redirected streams until completion or timeout.</summary>
    internal static class KeyStoreProcess
    {
        internal static (int code, string stdout, string stderr) Run(ProcessStartInfo startInfo, string input = null, int timeoutMs = 5000)
        {
            using var process = new Process { StartInfo = startInfo };
            bool started = false;
            StreamReader outputReader = null;
            StreamReader errorReader = null;
            StreamWriter inputWriter = null;
            Stream inputStream = null;
            Task<string> stdout = null;
            Task<string> stderr = null;
            Task stdin = null;
            Task streams = null;
            try
            {
                if (!(started = process.Start()))
                    return (-1, null, "Could not start key-store process.");

                var elapsed = Stopwatch.StartNew();
                outputReader = process.StandardOutput;
                errorReader = process.StandardError;
                if (startInfo.RedirectStandardInput)
                {
                    inputWriter = process.StandardInput;
                    inputStream = inputWriter.BaseStream;
                }
                // Drain both pipes concurrently before waiting, including commands whose output is ignored.
                stdout = outputReader.ReadToEndAsync();
                stderr = errorReader.ReadToEndAsync();
                stdin = inputWriter != null ? WriteInputAsync(inputWriter, input ?? string.Empty) : Task.CompletedTask;
                streams = Task.WhenAll(stdout, stderr, stdin);
                if (!process.WaitForExit(Remaining(timeoutMs, elapsed)) || !streams.Wait(Remaining(timeoutMs, elapsed)))
                    return (-1, null, "Key-store process timed out.");

                return (process.ExitCode, stdout.Result, stderr.Result);
            }
            catch (Exception error)
            {
                return (-1, null, error.Message);
            }
            finally
            {
                // Disposing Process releases handles; it does not terminate an owned child.
                if (started)
                {
                    try
                    {
                        if (!process.HasExited)
                        {
                            process.Kill();
                            process.WaitForExit(1000);
                        }
                    }
                    catch { /* The child may already have exited or termination may be denied. */ }
                }
                // Accessed Process streams belong to the caller. Close the pipe first so a
                // pending stdin write cannot make StreamWriter disposal flush into a full pipe.
                TryDispose(inputStream);
                TryDispose(inputWriter);
                TryDispose(outputReader);
                TryDispose(errorReader);
                ObserveFailure(stdout);
                ObserveFailure(stderr);
                ObserveFailure(stdin);
                ObserveFailure(streams);
            }
        }

        private static int Remaining(int timeoutMs, Stopwatch elapsed) => (int)Math.Max(0L, timeoutMs - elapsed.ElapsedMilliseconds);

        private static async Task WriteInputAsync(TextWriter writer, string input)
        {
            await writer.WriteAsync(input).ConfigureAwait(false);
            await writer.FlushAsync().ConfigureAwait(false);
            writer.Close();
        }

        private static void TryDispose(IDisposable resource)
        {
            try
            {
                resource?.Dispose();
            }
            catch { /* Release the remaining pipes even when a pending operation fails on close. */ }
        }

        private static void ObserveFailure(Task task)
        {
            task?.ContinueWith(
                completed =>
                {
                    _ = completed.Exception;
                },
                CancellationToken.None,
                TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously,
                TaskScheduler.Default
            );
        }
    }
}
