using CaughtOnDash.Worker.Services;
using Xunit;

namespace CaughtOnDash.Worker.Core.Tests
{
    /// <summary>
    /// The analyzer speaks JSON Lines over stdout. These pin the parsing rules
    /// without spawning a process, so they run anywhere -- no Python needed.
    /// </summary>
    public class AnalyzerProtocolTests
    {
        [Fact]
        public void ParsesProgressLines()
        {
            var line = AnalyzerProtocol.Parse("{\"type\":\"progress\",\"stage\":\"analyzing\",\"progress\":45}");

            var progress = Assert.IsType<AnalyzerProtocol.ProgressLine>(line);
            Assert.Equal("analyzing", progress.Stage);
            Assert.Equal(45, progress.Progress);
        }

        [Theory]
        [InlineData(-10, 0)]
        [InlineData(150, 100)]
        public void ClampsProgressToRange(int reported, int expected)
        {
            var line = AnalyzerProtocol.Parse(
                $"{{\"type\":\"progress\",\"stage\":\"analyzing\",\"progress\":{reported}}}");

            Assert.Equal(expected, Assert.IsType<AnalyzerProtocol.ProgressLine>(line).Progress);
        }

        [Fact]
        public void ParsesResultLines()
        {
            const string json = "{\"type\":\"result\",\"summary\":\"640x360 video\"," +
                                "\"tags\":[\"car\",\"person\"],\"events\":[]," +
                                "\"metadata\":{\"width\":640,\"fps\":25.0}}";

            var result = Assert.IsType<AnalyzerProtocol.ResultLine>(AnalyzerProtocol.Parse(json)).Result;

            Assert.Equal("640x360 video", result.Summary);
            Assert.Equal(new[] { "car", "person" }, result.Tags);
            Assert.Empty(result.Events);
            Assert.True(result.Metadata.ContainsKey("width"));
        }

        [Fact]
        public void ParsesResultLinesWithMissingOptionalFields()
        {
            // A result carrying only a summary must not throw; tags and events
            // are legitimately empty until milestone 3.
            var result = Assert.IsType<AnalyzerProtocol.ResultLine>(
                AnalyzerProtocol.Parse("{\"type\":\"result\",\"summary\":\"ok\"}")).Result;

            Assert.Equal("ok", result.Summary);
            Assert.Empty(result.Tags);
            Assert.Empty(result.Events);
            Assert.Empty(result.Metadata);
        }

        [Theory]
        [InlineData(null)]
        [InlineData("")]
        [InlineData("   ")]
        [InlineData("Loading model weights...")]          // a library printing to stdout
        [InlineData("{\"type\":\"debug\",\"msg\":\"hi\"}")] // unknown message type
        [InlineData("{not valid json")]
        [InlineData("[1,2,3]")]
        public void IgnoresAnythingThatIsNotAProtocolMessage(string? line)
        {
            // Stray output must not fail the job -- Python libraries print.
            Assert.Null(AnalyzerProtocol.Parse(line));
        }

        [Theory]
        [InlineData(0, "never produced a result")]
        [InlineData(2, "could not find the video")]
        [InlineData(3, "missing a Python dependency")]
        [InlineData(4, "could not read the video")]
        [InlineData(99, "exit code 99")]
        public void ExitCodesGetSpecificExplanations(int exitCode, string expectedFragment)
        {
            var message = AnalyzerProtocol.DescribeExitCode(exitCode, "");
            Assert.Contains(expectedFragment, message);
        }

        [Fact]
        public void ExitCodeExplanationIncludesStderr()
        {
            var message = AnalyzerProtocol.DescribeExitCode(4, "moov atom not found");
            Assert.Contains("moov atom not found", message);
        }
    }
}
