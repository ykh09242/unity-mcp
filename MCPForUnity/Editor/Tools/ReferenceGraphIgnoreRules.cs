using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Read-only ignore evidence. Matching and user policy belong to the server.</summary>
    internal sealed class ReferenceGraphIgnoreRules : IDisposable
    {
        private sealed class Entry
        {
            internal string Path,
                Physical,
                Stamp,
                Fingerprint;
            internal long Size;
        }

        private readonly string projectRoot;
        private const string IgnoreFilter = "*.gitignore";
        private readonly Func<string, FileSystemWatcher> watcherFactory;
        private readonly List<FileSystemWatcher> watchers = new List<FileSystemWatcher>();
        private string[] watchedRoots = Array.Empty<string>();
        private Dictionary<string, string> candidates;
        private Entry[] files;
        private string snapshot,
            epoch,
            rootStamp,
            error;
        private long journalRevision,
            revision = 1,
            eventSerial;
        private int pending,
            observerFailed;
        private bool disposed;
        private CancellationTokenSource hashing;

        // Captured by a worker before it starts; tests gate actual reads, never a substitute hash.
        internal Action<string, long> FingerprintReadForTests { get; set; }

        internal long Revision => revision;

        internal ReferenceGraphIgnoreRules(string projectRoot, long initialRevision = 1, Func<string, FileSystemWatcher> watcherFactory = null)
        {
            if (initialRevision < 1)
                throw new ArgumentOutOfRangeException(nameof(initialRevision));
            revision = initialRevision;
            this.projectRoot = SafePathUtility.ResolveWithinRoot(projectRoot, ".");
            this.watcherFactory = watcherFactory ?? (root => new FileSystemWatcher(root, IgnoreFilter));
            SetWatchers(BaseRoots());
        }

        // Callbacks do not resolve paths, read files, or call Unity APIs. Even a late
        // callback from a disposed watcher invalidates instead of publishing old proof.
        private void Changed(object sender, FileSystemEventArgs args)
        {
            if (IsRuleName(args.Name))
                MarkChanged();
        }

        private void Renamed(object sender, RenamedEventArgs args)
        {
            if (IsRuleName(args.Name) || IsRuleName(args.OldName))
                MarkChanged();
        }

        private static bool IsRuleName(string path) => string.Equals(Path.GetFileName(path), ".gitignore", StringComparison.OrdinalIgnoreCase);

        internal static bool CanObserveWithoutPolling(bool monoRuntime, Func<string> inspectBackend)
        {
            if (!monoRuntime)
                return true;
            try
            {
                switch (inspectBackend())
                {
                    case "System.IO.CoreFXFileSystemWatcherProxy":
                    case "System.IO.FAMWatcher":
                    case "System.IO.KeventWatcher":
                        return true;
                    default:
                        return false;
                }
            }
            catch (Exception)
            {
                return false;
            }
        }

        private void Failed(object sender, ErrorEventArgs args)
        {
            Interlocked.Exchange(ref observerFailed, 1);
            MarkChanged();
        }

        private void MarkChanged()
        {
            Interlocked.Increment(ref eventSerial);
            Interlocked.Exchange(ref pending, 1);
        }

        internal void Invalidate()
        {
            hashing?.Cancel();
            snapshot = null;
            files = null;
            candidates = null;
            revision++;
        }

        internal JObject Status(string currentEpoch, long currentRevision)
        {
            Observe(currentEpoch, currentRevision);
            return new JObject
            {
                ["ignore_revision"] = revision,
                ["ignore_state"] = error == null ? "available" : "unavailable",
                ["ignore_watchers"] = Volatile.Read(ref observerFailed) == 0,
                ["ignore_error"] = error ?? (Volatile.Read(ref observerFailed) != 0 ? "IgnoreWatcherUnavailable" : null),
            };
        }

        private void Observe(string currentEpoch, long currentRevision)
        {
            if (disposed)
                throw new ObjectDisposedException(nameof(ReferenceGraphIgnoreRules));
            bool changed = Interlocked.Exchange(ref pending, 0) != 0;
            try
            {
                string stamp = Stamp(SafePathUtility.ResolveWithinRoot(projectRoot, ".gitignore"));
                changed |= rootStamp != stamp;
                rootStamp = stamp;
            }
            catch (Exception exception)
            {
                error = exception.GetType().Name;
                changed = true;
            }
            if ((snapshot != null || hashing != null) && epoch != currentEpoch)
                changed = true;
            if (changed)
                Invalidate();
        }

        internal string ValidateCurrent(string currentEpoch, long currentRevision)
        {
            Observe(currentEpoch, currentRevision);
            if (snapshot == null || error != null)
                return null;
            string expected = snapshot;
            CheckSnapshot(expected, currentEpoch, currentRevision);
            Validate();
            CheckSnapshot(expected, currentEpoch, currentRevision);
            using (SHA256 hash = SHA256.Create())
                return string.Concat(hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes(expected)).Select(value => value.ToString("x2")));
        }

        internal async Task<string> ValidateCurrentAsync(string currentEpoch, long currentRevision, Func<bool> current, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            Observe(currentEpoch, currentRevision);
            if (snapshot == null || error != null)
                return null;
            string expected = snapshot;
            CheckSnapshot(expected, currentEpoch, currentRevision);
            await ValidateAsync(currentEpoch, currentRevision, current, token);
            CheckSnapshot(expected, currentEpoch, currentRevision);
            using (SHA256 hash = SHA256.Create())
                return string.Concat(hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes(expected)).Select(value => value.ToString("x2")));
        }

        internal async Task<JObject> RulesAsync(
            JObject request,
            int count,
            string currentEpoch,
            long currentRevision,
            Func<Dictionary<string, string>> discover,
            Func<bool> current,
            CancellationToken token
        )
        {
            token.ThrowIfCancellationRequested();
            Observe(currentEpoch, currentRevision);
            int offset = Offset(request);
            string expected = request.Value<string>("snapshot");
            bool acquired = false;
            if (expected == null && snapshot == null)
            {
                if (offset != 0)
                    throw new ArgumentException("Ignore continuation requires its snapshot.");
                EnsureHashingIdle();
                Dictionary<string, string> discovered = discover();
                PrepareCapture(discovered);
                string[] roots = watchedRoots;
                Action<string, long> read = FingerprintReadForTests;
                await HashAsync(
                    cancellation =>
                    {
                        Entry[] entries = CaptureEntries(discovered, roots, cancellation, read);
                        ValidateEntries(discovered, entries, roots, cancellation, read);
                        return entries;
                    },
                    currentEpoch,
                    currentRevision,
                    current,
                    token,
                    captured => PublishCapture(discovered, captured, currentEpoch, currentRevision)
                );
                acquired = true;
            }
            CheckSnapshot(expected ?? snapshot, currentEpoch, currentRevision);
            if (!acquired && request.Value<bool?>("validate") == true)
                await ValidateAsync(currentEpoch, currentRevision, current, token);
            token.ThrowIfCancellationRequested();
            CheckSnapshot(expected ?? snapshot, currentEpoch, currentRevision);
            var page = (JObject)request.DeepClone();
            page["validate"] = false;
            return Rules(page, count, currentEpoch, currentRevision, () => throw new InvalidOperationException("Ignore capture was not published."));
        }

        private Task<bool> ValidateAsync(string currentEpoch, long currentRevision, Func<bool> current, CancellationToken token)
        {
            Dictionary<string, string> observedCandidates = candidates;
            Entry[] observedFiles = files;
            string[] roots = watchedRoots;
            Action<string, long> read = FingerprintReadForTests;
            return HashAsync(
                cancellation =>
                {
                    ValidateEntries(observedCandidates, observedFiles, roots, cancellation, read);
                    return true;
                },
                currentEpoch,
                currentRevision,
                current,
                token
            );
        }

        private void EnsureHashingIdle()
        {
            if (hashing != null)
                throw new InvalidOperationException("Ignore hashing capacity is occupied; retry after the outstanding operation settles.");
        }

        private async Task<T> HashAsync<T>(
            Func<CancellationToken, T> work,
            string currentEpoch,
            long currentRevision,
            Func<bool> current,
            CancellationToken token,
            Action<T> publish = null
        )
        {
            EnsureHashingIdle();
            token.ThrowIfCancellationRequested();
            using (var owner = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                hashing = owner;
                epoch = currentEpoch;
                long generation = revision;
                long serial = Interlocked.Read(ref eventSerial);
                try
                {
                    // No Unity API or shared snapshot mutation occurs on this worker.
                    // Await the real worker even after cancellation: its open stream and capacity
                    // remain owned until the in-flight read exits and using scopes have disposed.
                    T result = await Task.Run(() => work(owner.Token), owner.Token);
                    owner.Token.ThrowIfCancellationRequested();
                    if (disposed || revision != generation || !current() || serial != Interlocked.Read(ref eventSerial) || Volatile.Read(ref pending) != 0)
                        throw new InvalidOperationException("Ignore source changed during hashing; restart collection.");
                    Observe(currentEpoch, currentRevision);
                    if (revision != generation)
                        throw new InvalidOperationException("Ignore source changed during hashing; restart collection.");
                    owner.Token.ThrowIfCancellationRequested();
                    publish?.Invoke(result);
                    return result;
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch (Exception exception)
                {
                    if (!disposed && revision == generation)
                    {
                        error = exception.GetType().Name;
                        Invalidate();
                    }
                    throw new InvalidOperationException("Ignore evidence changed or is unavailable; restart collection.", exception);
                }
                finally
                {
                    hashing = null;
                }
            }
        }

        internal JObject Rules(JObject request, int count, string currentEpoch, long currentRevision, Func<Dictionary<string, string>> discover)
        {
            Observe(currentEpoch, currentRevision);
            int offset = Offset(request);
            string expected = request.Value<string>("snapshot");
            bool acquired = false;
            if (expected == null)
            {
                if (offset != 0)
                    throw new ArgumentException("Ignore continuation requires its snapshot.");
                if (snapshot == null)
                {
                    Capture(discover(), currentEpoch, currentRevision);
                    acquired = true;
                }
            }
            CheckSnapshot(expected ?? snapshot, currentEpoch, currentRevision);
            if (!acquired && request.Value<bool?>("validate") == true)
                Validate();
            CheckSnapshot(expected ?? snapshot, currentEpoch, currentRevision);
            if (offset > files.Length)
                throw new ArgumentException("Ignore offset is past the snapshot.");
            int end = offset + Math.Min(count, files.Length - offset);
            var rows = new JArray();
            for (int index = offset; index < end; index++)
            {
                Entry file = files[index];
                rows.Add(
                    new JObject
                    {
                        ["path"] = file.Path,
                        ["fingerprint"] = file.Fingerprint,
                        ["size_bytes"] = file.Size,
                    }
                );
            }
            JObject result = Envelope();
            result["files"] = rows;
            result["next_offset"] = end < files.Length ? (JToken)end : JValue.CreateNull();
            return result;
        }

        private static int Offset(JObject request)
        {
            JToken token = request["offset"];
            if (token == null)
                return 0;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int offset) || offset < 0)
                throw new ArgumentException("Ignore offset is outside the supported page range.");
            return offset;
        }

        private void Capture(Dictionary<string, string> discovered, string currentEpoch, long currentRevision)
        {
            EnsureHashingIdle();
            try
            {
                // Watch exactly the project and registered package roots admitted by
                // the census. No directory enumeration is used to discover rule files.
                PrepareCapture(discovered);
                long serial = Interlocked.Read(ref eventSerial);
                Entry[] captured = CaptureEntries(discovered, watchedRoots, CancellationToken.None, FingerprintReadForTests);
                ValidateEntries(discovered, captured, watchedRoots, CancellationToken.None, FingerprintReadForTests);
                if (Interlocked.Read(ref eventSerial) != serial || Volatile.Read(ref pending) != 0)
                    throw new IOException("Ignore rules changed during capture.");
                PublishCapture(discovered, captured, currentEpoch, currentRevision);
            }
            catch (Exception exception)
            {
                error = exception.GetType().Name;
                Invalidate();
                throw new InvalidOperationException("Ignore evidence is unavailable (" + error + "); retry collection.");
            }
        }

        private void PrepareCapture(Dictionary<string, string> discovered)
        {
            var roots = new List<string>(BaseRoots());
            string assetsRoot = Path.Combine(projectRoot, "Assets");
            foreach (var candidate in discovered)
                if (candidate.Key.StartsWith("Packages/", StringComparison.Ordinal) && candidate.Key.Count(value => value == '/') == 2)
                {
                    string root = Path.GetDirectoryName(candidate.Value);
                    if (!root.StartsWith(assetsRoot + Path.DirectorySeparatorChar, PathComparison) && !root.Equals(assetsRoot, PathComparison))
                        roots.Add(root);
                }
            string[] observers = roots.Distinct(PathComparer).OrderBy(value => value, PathComparer).ToArray();
            SetWatchers(observers);
            error = null;
            Interlocked.Exchange(ref pending, 0);
        }

        private static Entry[] CaptureEntries(Dictionary<string, string> discovered, string[] roots, CancellationToken token, Action<string, long> read)
        {
            var present = new List<Entry>();
            foreach (var candidate in discovered.OrderBy(pair => pair.Key, StringComparer.Ordinal))
            {
                token.ThrowIfCancellationRequested();
                string physical = Checked(candidate.Value, roots);
                string stamp = Stamp(physical);
                if (stamp == null)
                    continue;
                string fingerprint = Fingerprint(physical, token, read);
                if (stamp != Stamp(Checked(physical, roots)))
                    throw new IOException("Ignore rules changed during capture.");
                present.Add(
                    new Entry
                    {
                        Path = candidate.Key,
                        Physical = physical,
                        Stamp = stamp,
                        Fingerprint = fingerprint,
                        Size = new FileInfo(physical).Length,
                    }
                );
            }
            return present.ToArray();
        }

        private void PublishCapture(Dictionary<string, string> discovered, Entry[] captured, string currentEpoch, long currentRevision)
        {
            candidates = discovered;
            files = captured;
            epoch = currentEpoch;
            journalRevision = currentRevision;
            snapshot = Guid.NewGuid().ToString("N");
            error = null;
        }

        private static StringComparison PathComparison => Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        private static StringComparer PathComparer => Path.DirectorySeparatorChar == '\\' ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal;

        private string[] BaseRoots()
        {
            var roots = new List<string> { projectRoot };
            foreach (string directory in new[] { "Assets", "Packages" })
            {
                string root = SafePathUtility.ResolveWithinRoot(projectRoot, directory);
                if (Directory.Exists(root))
                    roots.Add(root);
            }
            return roots.OrderBy(value => value, PathComparer).ToArray();
        }

        private string Checked(string physical) => Checked(physical, watchedRoots);

        private static string Checked(string physical, string[] roots)
        {
            string root = roots.FirstOrDefault(value =>
                physical.StartsWith(value.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar, PathComparison)
            );
            if (root == null)
                throw new InvalidOperationException("Ignore file is outside the admitted roots.");
            return SafePathUtility.ResolveWithinRoot(
                root,
                physical.Substring(root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Length + 1)
            );
        }

        private void SetWatchers(string[] roots)
        {
            if (Volatile.Read(ref observerFailed) == 0 && roots.SequenceEqual(watchedRoots, PathComparer))
                return;
            ReleaseWatchers();
            foreach (string root in roots)
                SafePathUtility.ResolveWithinRoot(root, ".");
            watchedRoots = roots;
            try
            {
                foreach (string root in roots)
                {
                    var watcher = watcherFactory(root);
                    watchers.Add(watcher);
                    // Mono selects its backend in the constructor. Inspect before
                    // enabling: DefaultWatcher otherwise scans the tree every 750ms.
                    if (
                        !CanObserveWithoutPolling(Type.GetType("Mono.Runtime") != null, () =>
                            typeof(FileSystemWatcher)
                                .GetField("watcher", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                                ?.GetValue(watcher)
                                ?.GetType()
                                .FullName
                        )
                    )
                        throw new NotSupportedException("The watcher backend cannot provide event-only invalidation.");
                    // Mono's DefaultWatcher silently misses nested literal-name
                    // filters. Keep the suffix filter narrow and check exact names
                    // in the callbacks, including both sides of a rename.
                    watcher.Filter = IgnoreFilter;
                    watcher.IncludeSubdirectories =
                        !root.Equals(projectRoot, PathComparison) && !root.Equals(Path.Combine(projectRoot, "Packages"), PathComparison);
                    watcher.NotifyFilter = NotifyFilters.FileName | NotifyFilters.DirectoryName | NotifyFilters.LastWrite | NotifyFilters.Size;
                    watcher.Changed += Changed;
                    watcher.Created += Changed;
                    watcher.Deleted += Changed;
                    watcher.Renamed += Renamed;
                    watcher.Error += Failed;
                    watcher.EnableRaisingEvents = true;
                }
                watchedRoots = roots;
                Interlocked.Exchange(ref observerFailed, 0);
            }
            catch (Exception)
            {
                ReleaseWatchers(false);
                Interlocked.Exchange(ref observerFailed, 1);
            }
        }

        private void Validate()
        {
            EnsureHashingIdle();
            long serial = Interlocked.Read(ref eventSerial);
            try
            {
                ValidateEntries(candidates, files, watchedRoots, CancellationToken.None, FingerprintReadForTests);
                if (serial != Interlocked.Read(ref eventSerial) || Volatile.Read(ref pending) != 0)
                    throw new IOException("Ignore observer changed during validation.");
            }
            catch (Exception exception)
            {
                error = exception.GetType().Name;
                Invalidate();
                throw new InvalidOperationException("Ignore snapshot changed; restart collection.");
            }
        }

        private static void ValidateEntries(
            Dictionary<string, string> candidates,
            Entry[] files,
            string[] roots,
            CancellationToken token,
            Action<string, long> read
        )
        {
            var known = files.ToDictionary(value => value.Path, StringComparer.Ordinal);
            foreach (var candidate in candidates)
            {
                token.ThrowIfCancellationRequested();
                string physical = Checked(candidate.Value, roots);
                string stamp = Stamp(physical);
                if (known.TryGetValue(candidate.Key, out Entry present))
                {
                    if (stamp != present.Stamp || Fingerprint(physical, token, read) != present.Fingerprint || stamp != Stamp(Checked(physical, roots)))
                        throw new IOException("Ignore rules changed.");
                }
                else if (stamp != null)
                    throw new IOException("Ignore rules were created.");
            }
        }

        internal JObject Source(JObject request, string currentEpoch, long currentRevision)
        {
            Observe(currentEpoch, currentRevision);
            CheckSnapshot(request.Value<string>("snapshot"), currentEpoch, currentRevision);
            string path = request.Value<string>("path");
            Entry file = files.FirstOrDefault(value => value.Path == path);
            if (file == null)
                throw new ArgumentException("Ignore source requires an exact file from the current ignore snapshot.");
            JToken token = request["offset"];
            long offset =
                token == null ? 0
                : token.Type == JTokenType.Integer && long.TryParse(token.ToString(), out long number) ? number
                : -1;
            if (offset < 0 || offset > file.Size || request.Value<string>("fingerprint") != file.Fingerprint)
                throw new ArgumentException("Ignore source continuation is invalid.");
            try
            {
                long serial = Interlocked.Read(ref eventSerial);
                string physical = Checked(file.Physical);
                if (Stamp(physical) != file.Stamp)
                    throw new InvalidOperationException("Ignore source changed; restart collection.");
                byte[] bytes = new byte[(int)Math.Min(65536, file.Size - offset)];
                using (var stream = new FileStream(physical, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    stream.Position = offset;
                    int length = 0;
                    while (length < bytes.Length)
                    {
                        int read = stream.Read(bytes, length, bytes.Length - length);
                        if (read == 0)
                            throw new IOException("Ignore source changed while reading.");
                        length += read;
                    }
                }
                if (Stamp(Checked(physical)) != file.Stamp || serial != Interlocked.Read(ref eventSerial) || Volatile.Read(ref pending) != 0)
                    throw new InvalidOperationException("Ignore source changed; restart collection.");
                JObject result = Envelope();
                result["path"] = path;
                result["fingerprint"] = file.Fingerprint;
                result["bytes_base64"] = Convert.ToBase64String(bytes);
                result["next_offset"] = offset + bytes.Length < file.Size ? (JToken)(offset + bytes.Length) : JValue.CreateNull();
                return result;
            }
            catch (Exception exception)
            {
                error = exception.GetType().Name;
                Invalidate();
                throw;
            }
        }

        private void CheckSnapshot(string expected, string currentEpoch, long currentRevision)
        {
            if (snapshot == null || expected != snapshot || epoch != currentEpoch || error != null)
                throw new InvalidOperationException("Ignore snapshot expired; restart collection.");
        }

        private JObject Envelope() =>
            new JObject
            {
                ["snapshot"] = snapshot,
                ["epoch"] = epoch,
                ["revision"] = journalRevision,
                ["ignore_revision"] = revision,
                ["ignore_state"] = "available",
                ["ignore_watchers"] = Volatile.Read(ref observerFailed) == 0,
            };

        private static string Stamp(string path)
        {
            var info = new FileInfo(path);
            return info.Exists && (info.Attributes & FileAttributes.Directory) == 0 ? info.Length + ":" + info.LastWriteTimeUtc.Ticks : null;
        }

        private static string Fingerprint(string path, CancellationToken token, Action<string, long> read)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (SHA256 hash = SHA256.Create())
            {
                var buffer = new byte[65536];
                long total = 0;
                for (; ; )
                {
                    token.ThrowIfCancellationRequested();
                    int length = stream.Read(buffer, 0, buffer.Length);
                    token.ThrowIfCancellationRequested();
                    if (length == 0)
                        break;
                    hash.TransformBlock(buffer, 0, length, buffer, 0);
                    total += length;
                    read?.Invoke(path, total);
                }
                hash.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                token.ThrowIfCancellationRequested();
                return string.Concat(hash.Hash.Select(value => value.ToString("x2")));
            }
        }

        private void ReleaseWatchers(bool clearRoots = true)
        {
            foreach (FileSystemWatcher watcher in watchers)
            {
                watcher.Changed -= Changed;
                watcher.Created -= Changed;
                watcher.Deleted -= Changed;
                watcher.Renamed -= Renamed;
                watcher.Error -= Failed;
                watcher.Dispose();
            }
            watchers.Clear();
            if (clearRoots)
                watchedRoots = Array.Empty<string>();
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            ReleaseWatchers();
            Invalidate();
        }
    }
}
