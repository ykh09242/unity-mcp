using System;
using System.IO;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Services.AssetGen
{
    /// <summary>Owned, validated staging output. Workers never access Unity or publish into Assets.</summary>
    internal sealed class PreparedArtifact : IDisposable
    {
        private static readonly ArtifactPreparationQueue Workers = new();
        internal const int BackgroundThreshold = 256 * 1024;
        internal static Action<string> DeleteStageOverrideForTests = null;
        private readonly string root;
        private string path;
        private FileStream stream;
        private AssetDiskBudget.Lease lease;
        internal string Extension { get; private set; }
        internal string Hash { get; private set; }
        internal long Length { get; private set; }

        private PreparedArtifact(string root) => this.root = root;

        internal static async Task<PreparedArtifact> CreateAsync(
            HttpResult input,
            string root,
            string kind,
            string provider,
            string extension,
            string declaredExtension,
            CancellationToken token
        )
        {
            using (input)
            {
                long bytes = input.DownloadedFile?.Length ?? input.Body?.LongLength ?? long.MaxValue;
                if (bytes <= 0 || bytes > AssetDownloadTransport.MaxDownloadBytes)
                    throw new IOException("Provider artifact exceeds the 8 GiB limit or has changed length.");
                using (await Workers.AcquireAsync(bytes, token).ConfigureAwait(false))
                {
                    AssetDiskBudget.Lease reservation = await AssetDiskBudget.ReserveDownloadAsync(root, bytes, token).ConfigureAwait(false);
                    // Do not pass cancellation to Task.Run: the delegate owns cleanup even if canceled before dispatch.
                    return await Task.Run(() => CreateReserved(input, root, kind, provider, extension, declaredExtension, reservation, token))
                        .ConfigureAwait(false);
                }
            }
        }

        private static PreparedArtifact Create(
            HttpResult input,
            string root,
            string kind,
            string provider,
            string extension,
            string declaredExtension,
            CancellationToken token
        )
        {
            long bytes = input.DownloadedFile?.Length ?? input.Body?.LongLength ?? long.MaxValue;
            if (bytes <= 0 || bytes > AssetDownloadTransport.MaxDownloadBytes)
                throw new IOException("Provider artifact exceeds the 8 GiB limit or has changed length.");
            AssetDiskBudget.Lease reservation = AssetDiskBudget.ReserveDownloadAsync(root, bytes, token).GetAwaiter().GetResult();
            return CreateReserved(input, root, kind, provider, extension, declaredExtension, reservation, token);
        }

        private static PreparedArtifact CreateReserved(
            HttpResult input,
            string root,
            string kind,
            string provider,
            string extension,
            string declaredExtension,
            AssetDiskBudget.Lease reservation,
            CancellationToken token
        )
        {
            var result = new PreparedArtifact(root) { lease = reservation };
            try
            {
                token.ThrowIfCancellationRequested();
                using var memory = input.DownloadedFile == null ? new MemoryStream(input.Body, false) : null;
                Stream source = input.DownloadedFile?.Rewind() ?? memory;
                result.Length = source.Length;
                if (result.Length <= 0 || result.Length > AssetDownloadTransport.MaxDownloadBytes)
                    throw new IOException("Provider artifact exceeds the 8 GiB limit or has changed length.");
                result.Extension = DetectExtension(source, kind, provider, extension, declaredExtension);
                string directory = SafePathUtility.ResolveWithinRoot(root, "Temp/McpAssetPublication");
                Directory.CreateDirectory(directory);
                result.path = SafePathUtility.ResolveWithinRoot(root, Path.Combine(directory, Guid.NewGuid().ToString("N") + ".part"));
                result.stream = new FileStream(result.path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 64 * 1024);
                SafePathUtility.ResolveWithinRoot(root, result.path);
                var buffer = new byte[64 * 1024];
                // The download owns an exclusive handle and exposes only read access.
                // Carry its digest through the byte-for-byte copy; mutable Body inputs still hash here.
                string ownedHash = input.DownloadedFile?.Sha256;
                using var sha = ownedHash == null ? SHA256.Create() : null;
                long copied = 0;
                int read;
                source.Position = 0;
                while ((read = source.Read(buffer, 0, buffer.Length)) != 0)
                {
                    token.ThrowIfCancellationRequested();
                    if (read > result.Length - copied)
                        throw new IOException("Provider artifact changed length during preparation.");
                    result.stream.Write(buffer, 0, read);
                    reservation.RecordWritten(read);
                    sha?.TransformBlock(buffer, 0, read, null, 0);
                    copied += read;
                }
                if (copied != result.Length)
                    throw new IOException("Provider artifact ended during preparation.");
                sha?.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                result.Hash = ownedHash ?? BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
                result.stream.Flush();
                token.ThrowIfCancellationRequested();
                reservation.CompleteTransfer();
                return result;
            }
            catch
            {
                result.Dispose();
                throw;
            }
        }

        internal static string DetectExtension(Stream input, string kind, string provider, string extension, string declaredExtension)
        {
            input.Position = 0;
            var header = new byte[(int)Math.Min(16, input.Length)];
            int count = 0;
            while (count < header.Length)
            {
                int read = input.Read(header, count, header.Length - count);
                if (read == 0)
                    throw new EndOfStreamException();
                count += read;
            }
            string chosen = string.IsNullOrEmpty(declaredExtension) ? extension : declaredExtension;
            string ext = string.IsNullOrEmpty(chosen) ? "bin" : chosen.TrimStart('.').ToLowerInvariant();
            if (!AssetGenJobManager.IsAllowedResultExtension(kind, ext))
                throw new IOException($"provider returned a disallowed file type '.{ext}'");
            if (kind == "image")
            {
                string actual = ImageResultFormat.FromBytes(header);
                if (actual == "webp")
                    throw new IOException("Provider returned WebP, which this Unity image importer does not support. Choose a PNG/JPEG model.");
                if (actual != null)
                    ext = actual;
            }
            if (kind == "model" && string.Equals(provider, "tripo", StringComparison.OrdinalIgnoreCase))
                ext = TripoAdapter.ValidateModelStream(input, declaredExtension);
            input.Position = 0;
            return ext;
        }

        internal void Publish(string destination)
        {
            SafePathUtility.ResolveWithinRoot(root, path);
            SafePathUtility.ResolveWithinRoot(Path.Combine(root, "Assets"), destination);
            stream?.Dispose();
            stream = null;
            // The staging directory is on the project volume. Move never replaces a competing output.
            File.Move(path, destination);
            path = null; // Published output belongs to the checkpoint, including on later failure.
            lease?.Dispose();
            lease = null;
        }

        public void Dispose()
        {
            try
            {
                stream?.Dispose();
            }
            finally
            {
                stream = null;
                if (path != null)
                {
                    try
                    {
                        string stage = SafePathUtility.ResolveWithinRoot(root, path);
                        if (DeleteStageOverrideForTests == null)
                            File.Delete(stage);
                        else
                            DeleteStageOverrideForTests(stage);
                    }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                    catch (InvalidOperationException) { }
                }
                if (AssetDiskBudget.FileIsGone(path))
                {
                    lease?.Dispose();
                    lease = null;
                    path = null;
                }
                else
                    lease?.CompleteTransfer();
            }
        }
    }
}
