using Xunit;

// Logger is static, and HeartbeatChannel and ThumbnailCache both write through
// it. With xUnit's default cross-class parallelism their lines land in
// LoggerTests' temporary file mid-assertion, and LoggerTests' redirection lands
// mid-write for them. Neither is a product bug; both make the suite flaky for
// reasons that have nothing to do with what is being tested.
//
// The whole suite runs in well under a second, so serialising it costs nothing
// worth measuring.
[assembly: CollectionBehavior(DisableTestParallelization = true)]
