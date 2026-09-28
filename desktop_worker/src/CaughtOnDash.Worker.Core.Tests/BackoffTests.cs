using System;
using System.Net;
using System.Net.Http.Headers;
using CaughtOnDash.Worker.Services;
using Xunit;

namespace CaughtOnDash.Worker.Core.Tests
{
    public class BackoffTests
    {
        private static readonly DateTimeOffset Now = new(2026, 9, 27, 23, 20, 57, TimeSpan.Zero);

        [Theory]
        [InlineData(HttpStatusCode.TooManyRequests, true)]
        [InlineData(HttpStatusCode.ServiceUnavailable, true)]
        [InlineData(HttpStatusCode.BadGateway, false)]
        [InlineData(HttpStatusCode.BadRequest, false)]
        public void OnlyRateLimitsAndOverloadThrottle(HttpStatusCode status, bool throttles)
        {
            Assert.Equal(throttles, Backoff.IsThrottle(status));
        }

        [Fact]
        public void DoublesFromFifteenSecondsToFiveMinutes()
        {
            var backoff = new Backoff();
            var waits = new[] { 15, 30, 60, 120, 240, 300, 300 };
            foreach (var expected in waits)
            {
                Assert.Equal(TimeSpan.FromSeconds(expected), backoff.OnThrottled(null, Now));
            }
        }

        [Fact]
        public void HonoursRetryAfterButNeverBeyondTheCap()
        {
            var backoff = new Backoff();
            Assert.Equal(TimeSpan.FromSeconds(7), backoff.OnThrottled(TimeSpan.FromSeconds(7), Now));
            Assert.Equal(Backoff.Maximum, backoff.OnThrottled(TimeSpan.FromHours(1), Now));
        }

        [Fact]
        public void EveryRequestWaitsUntilTheBackoffEnds()
        {
            var backoff = new Backoff();
            backoff.OnThrottled(null, Now);
            Assert.Equal(TimeSpan.FromSeconds(15), backoff.Remaining(Now));
            Assert.Equal(TimeSpan.FromSeconds(5), backoff.Remaining(Now.AddSeconds(10)));
            Assert.Equal(TimeSpan.Zero, backoff.Remaining(Now.AddSeconds(20)));
        }

        [Fact]
        public void SuccessClearsTheWaitAndTheEscalation()
        {
            var backoff = new Backoff();
            backoff.OnThrottled(null, Now);
            backoff.OnThrottled(null, Now);
            backoff.OnSuccess();
            Assert.Equal(TimeSpan.Zero, backoff.Remaining(Now));
            Assert.Equal(Backoff.Initial, backoff.OnThrottled(null, Now));
        }

        [Fact]
        public void RetryAfterIsReadAsSecondsOrAsADate()
        {
            Assert.Equal(TimeSpan.FromSeconds(30), Backoff.ParseRetryAfter(new RetryConditionHeaderValue(TimeSpan.FromSeconds(30)), Now));
            Assert.Equal(TimeSpan.FromSeconds(45), Backoff.ParseRetryAfter(new RetryConditionHeaderValue(Now.AddSeconds(45)), Now));
            Assert.Null(Backoff.ParseRetryAfter(null, Now));
        }

        [Fact]
        public void AnHtmlChallengePageIsSummarizedNotDumped()
        {
            var page = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>" + new string('x', 3000) + "</body></html>";
            var summary = Backoff.Summarize(page);
            Assert.StartsWith("HTML page \"Just a moment...\"", summary);
            Assert.True(summary.Length < 80);
            Assert.Equal("{\"success\": false}", Backoff.Summarize("{\"success\": false}"));
        }
    }
}
