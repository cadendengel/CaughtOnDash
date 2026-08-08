using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// Fetches poster frames for the queue table, once each.
    ///
    /// Stops at raw bytes deliberately. Decoding needs a framework type --
    /// Avalonia's Bitmap, WPF's BitmapImage -- and pulling either into the
    /// shared core would drag a UI dependency across both hosts. Each host
    /// decodes what this returns.
    ///
    /// The caching is the point, not the download. The queue refreshes every ten
    /// seconds and rebuilds every row, so an uncached column would re-fetch
    /// every thumbnail six times a minute for as long as the window is open.
    /// </summary>
    public class ThumbnailCache
    {
        /// <summary>
        /// Poster frames are small; anything this large is not one, and
        /// decoding it would cost more than the column is worth.
        /// </summary>
        public const int MaxBytes = 4 * 1024 * 1024;

        /// <summary>
        /// Enough for a long queue without growing without bound. Eviction is
        /// oldest-first and approximate -- the cost of a wrong eviction is one
        /// re-fetch.
        /// </summary>
        public const int MaxEntries = 300;

        private readonly HttpClient _httpClient;
        private readonly object _gate = new();

        // Tasks rather than results, so N rows asking for the same URL at once
        // share one request instead of racing.
        private readonly Dictionary<string, Task<byte[]?>> _entries = new();
        private readonly Queue<string> _order = new();

        public ThumbnailCache(HttpClient? httpClient = null)
        {
            _httpClient = httpClient ?? new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        }

        /// <summary>
        /// The image bytes for a URL, or null if there is nothing usable there.
        /// Never throws: a missing thumbnail is a cosmetic problem.
        /// </summary>
        public Task<byte[]?> GetAsync(string? url, CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(url))
            {
                return Task.FromResult<byte[]?>(null);
            }

            lock (_gate)
            {
                if (_entries.TryGetValue(url, out var existing))
                {
                    return existing;
                }

                var task = FetchAsync(url, cancellationToken);
                _entries[url] = task;
                _order.Enqueue(url);
                Evict();
                return task;
            }
        }

        private void Evict()
        {
            while (_order.Count > MaxEntries)
            {
                _entries.Remove(_order.Dequeue());
            }
        }

        private async Task<byte[]?> FetchAsync(string url, CancellationToken cancellationToken)
        {
            try
            {
                using var response = await _httpClient.GetAsync(
                    url, HttpCompletionOption.ResponseHeadersRead, cancellationToken);

                if (!response.IsSuccessStatusCode)
                {
                    return null;
                }

                // Check the advertised length before reading, so an oversized or
                // mislabelled URL costs nothing.
                if (response.Content.Headers.ContentLength > MaxBytes)
                {
                    Logger.Log($"Thumbnail too large, skipping: {url}", Logger.LogLevel.Warning);
                    return null;
                }

                var bytes = await response.Content.ReadAsByteArrayAsync(cancellationToken);

                // And again after, because ContentLength can be absent or lie.
                if (bytes.Length == 0 || bytes.Length > MaxBytes)
                {
                    return null;
                }

                return bytes;
            }
            catch (Exception)
            {
                // Offline, DNS failure, timeout, a URL that has expired. None of
                // it is worth a log line per row per refresh.
                return null;
            }
        }
    }
}
