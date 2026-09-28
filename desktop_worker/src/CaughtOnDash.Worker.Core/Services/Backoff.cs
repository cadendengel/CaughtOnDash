using System;
using System.Net;
using System.Net.Http.Headers;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// How long to hold off after the backend says it is overloaded.
    /// </summary>
    /// <remarks>
    /// In the first production run a photo upload restarted the web service; the
    /// worker kept polling and heartbeating at full rate through the 503s, and
    /// Cloudflare in front of Render answered with 429s for this machine's IP.
    /// Retrying immediately is what a rate limiter punishes. So a 429 or 503
    /// sets a pause -- the server's Retry-After when it gives one, otherwise
    /// doubling from 15 seconds to 5 minutes -- that every request waits out,
    /// and the first success clears it.
    /// </remarks>
    public sealed class Backoff
    {
        public static readonly TimeSpan Initial = TimeSpan.FromSeconds(15);
        public static readonly TimeSpan Maximum = TimeSpan.FromMinutes(5);

        private readonly object _lock = new();
        private DateTimeOffset _until = DateTimeOffset.MinValue;
        private TimeSpan _next = Initial;

        public static bool IsThrottle(HttpStatusCode status) =>
            status == HttpStatusCode.TooManyRequests || status == HttpStatusCode.ServiceUnavailable;

        /// <summary>Time still to wait before the next request, or zero.</summary>
        public TimeSpan Remaining(DateTimeOffset now)
        {
            lock (_lock)
            {
                var remaining = _until - now;
                return remaining > TimeSpan.Zero ? remaining : TimeSpan.Zero;
            }
        }

        /// <summary>Record a throttling response and return how long to wait.</summary>
        public TimeSpan OnThrottled(TimeSpan? retryAfter, DateTimeOffset now)
        {
            lock (_lock)
            {
                var delay = retryAfter is { } asked && asked > TimeSpan.Zero ? asked : _next;
                if (delay > Maximum)
                {
                    delay = Maximum;
                }
                _next = _next + _next > Maximum ? Maximum : _next + _next;
                var until = now + delay;
                if (until > _until)
                {
                    _until = until;
                }
                return delay;
            }
        }

        public void OnSuccess()
        {
            lock (_lock)
            {
                _until = DateTimeOffset.MinValue;
                _next = Initial;
            }
        }

        /// <summary>Retry-After as a duration, whether sent as seconds or as a date.</summary>
        public static TimeSpan? ParseRetryAfter(RetryConditionHeaderValue? header, DateTimeOffset now)
        {
            if (header?.Delta is { } delta)
            {
                return delta;
            }
            if (header?.Date is { } date)
            {
                var wait = date - now;
                return wait > TimeSpan.Zero ? wait : TimeSpan.Zero;
            }
            return null;
        }

        /// <summary>
        /// An error body short enough to log. A Cloudflare challenge is a whole
        /// HTML page; the log said so in three kilobytes per request.
        /// </summary>
        public static string Summarize(string body, int limit = 300)
        {
            var trimmed = (body ?? "").Trim();
            if (trimmed.StartsWith("<", StringComparison.Ordinal))
            {
                var titleStart = trimmed.IndexOf("<title>", StringComparison.OrdinalIgnoreCase);
                var titleEnd = trimmed.IndexOf("</title>", StringComparison.OrdinalIgnoreCase);
                var title = titleStart >= 0 && titleEnd > titleStart
                    ? trimmed.Substring(titleStart + 7, titleEnd - titleStart - 7).Trim()
                    : "";
                return $"HTML page{(title.Length > 0 ? $" \"{title}\"" : "")} ({trimmed.Length:N0} chars)";
            }
            return trimmed.Length <= limit ? trimmed : trimmed.Substring(0, limit) + "…";
        }
    }
}
