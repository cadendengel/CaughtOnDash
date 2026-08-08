using System;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Services;
using Xunit;

namespace CaughtOnDash.Worker.Core.Tests
{
    /// <summary>
    /// The caching matters more than the download. The queue refreshes every ten
    /// seconds and rebuilds every row, so without it the column would re-fetch
    /// every thumbnail six times a minute for as long as the window is open.
    /// </summary>
    public class ThumbnailCacheTests
    {
        private class StubHandler : HttpMessageHandler
        {
            private readonly Func<HttpRequestMessage, HttpResponseMessage> _respond;
            public int Calls;

            public StubHandler(Func<HttpRequestMessage, HttpResponseMessage> respond)
            {
                _respond = respond;
            }

            protected override Task<HttpResponseMessage> SendAsync(
                HttpRequestMessage request, CancellationToken cancellationToken)
            {
                Interlocked.Increment(ref Calls);
                return Task.FromResult(_respond(request));
            }
        }

        private static HttpResponseMessage Image(int bytes = 32, long? contentLength = null)
        {
            var response = new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new ByteArrayContent(new byte[bytes]),
            };
            if (contentLength.HasValue)
            {
                response.Content.Headers.ContentLength = contentLength;
            }
            return response;
        }

        private static (ThumbnailCache, StubHandler) Build(
            Func<HttpRequestMessage, HttpResponseMessage> respond)
        {
            var handler = new StubHandler(respond);
            return (new ThumbnailCache(new HttpClient(handler)), handler);
        }

        [Fact]
        public async Task FetchesAnImage()
        {
            var (cache, _) = Build(_ => Image(64));

            var bytes = await cache.GetAsync("https://example.com/a.jpg");

            Assert.NotNull(bytes);
            Assert.Equal(64, bytes!.Length);
        }

        [Fact]
        public async Task TheSameUrlIsFetchedOnce()
        {
            var (cache, handler) = Build(_ => Image());

            await cache.GetAsync("https://example.com/a.jpg");
            await cache.GetAsync("https://example.com/a.jpg");
            await cache.GetAsync("https://example.com/a.jpg");

            Assert.Equal(1, handler.Calls);
        }

        [Fact]
        public async Task ConcurrentAsksForOneUrlShareOneRequest()
        {
            // Every row asks at once when the table rebuilds. Caching the task
            // rather than the result is what stops them racing.
            var (cache, handler) = Build(_ => Image());

            var tasks = new Task<byte[]?>[20];
            for (var i = 0; i < tasks.Length; i++)
            {
                tasks[i] = cache.GetAsync("https://example.com/a.jpg");
            }
            await Task.WhenAll(tasks);

            Assert.Equal(1, handler.Calls);
        }

        [Fact]
        public async Task AnEmptyUrlIsNotARequest()
        {
            var (cache, handler) = Build(_ => Image());

            Assert.Null(await cache.GetAsync(null));
            Assert.Null(await cache.GetAsync(""));
            Assert.Null(await cache.GetAsync("   "));
            Assert.Equal(0, handler.Calls);
        }

        [Fact]
        public async Task AFailedResponseIsNull()
        {
            var (cache, _) = Build(_ => new HttpResponseMessage(HttpStatusCode.NotFound));

            Assert.Null(await cache.GetAsync("https://example.com/gone.jpg"));
        }

        [Fact]
        public async Task AThrowingRequestIsNull()
        {
            // Offline, DNS failure, an expired signed URL. A missing thumbnail
            // is cosmetic and must never surface as an error.
            var (cache, _) = Build(_ => throw new HttpRequestException("no network"));

            Assert.Null(await cache.GetAsync("https://example.com/a.jpg"));
        }

        [Fact]
        public async Task AnOversizedContentLengthIsRefusedBeforeReading()
        {
            var (cache, _) = Build(_ => Image(32, contentLength: ThumbnailCache.MaxBytes + 1));

            Assert.Null(await cache.GetAsync("https://example.com/huge.jpg"));
        }

        [Fact]
        public async Task AnOversizedBodyIsRefusedEvenWhenContentLengthLied()
        {
            // ContentLength can be absent or wrong, so the cap is checked twice.
            var (cache, _) = Build(_ => Image(ThumbnailCache.MaxBytes + 1, contentLength: 10));

            Assert.Null(await cache.GetAsync("https://example.com/liar.jpg"));
        }

        [Fact]
        public async Task AnEmptyBodyIsNull()
        {
            var (cache, _) = Build(_ => Image(0));

            Assert.Null(await cache.GetAsync("https://example.com/empty.jpg"));
        }

        [Fact]
        public async Task TheCacheDoesNotGrowWithoutBound()
        {
            var (cache, handler) = Build(_ => Image());

            for (var i = 0; i <= ThumbnailCache.MaxEntries; i++)
            {
                await cache.GetAsync($"https://example.com/{i}.jpg");
            }

            var beforeRefetch = handler.Calls;
            // The oldest entry has been evicted, so asking again re-fetches.
            await cache.GetAsync("https://example.com/0.jpg");

            Assert.Equal(beforeRefetch + 1, handler.Calls);
        }

        [Fact]
        public async Task AFailureIsRememberedRatherThanRetriedEveryRefresh()
        {
            // A row whose thumbnail 404s would otherwise re-request every ten
            // seconds forever.
            var (cache, handler) = Build(_ => new HttpResponseMessage(HttpStatusCode.NotFound));

            await cache.GetAsync("https://example.com/gone.jpg");
            await cache.GetAsync("https://example.com/gone.jpg");

            Assert.Equal(1, handler.Calls);
        }
    }
}
