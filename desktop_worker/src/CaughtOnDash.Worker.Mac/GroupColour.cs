using System;
using System.Globalization;
using Avalonia.Data.Converters;
using Avalonia.Media;
using CaughtOnDash.Worker.Services;

namespace CaughtOnDash.Worker.Mac
{
    /// <summary>
    /// The queue's colours, by group: green running, blue queued, amber needs
    /// review, red failed. The same values as the WPF window.
    /// </summary>
    /// <remarks>
    /// A converter because Avalonia has no data triggers, which is how the WPF
    /// window picks these. Bound to the row itself with a parameter naming the
    /// part: "chip" and "dot" and "ink" for the status chip, "band" and
    /// "bandInk" for a group's heading.
    /// </remarks>
    public class GroupColour : IValueConverter
    {
        private record Tone(string Band, string BandInk, string Chip, string Ink, string Dot);

        private static readonly Tone Running = new("#F0FDF4", "#14532D", "#DCFCE7", "#14532D", "#15803D");
        private static readonly Tone Queued = new("#EFF6FF", "#1E3A8A", "#DBEAFE", "#1E3A8A", "#1D4ED8");
        private static readonly Tone Review = new("#FFFBEB", "#78350F", "#FEF3C7", "#78350F", "#B45309");
        private static readonly Tone Failed = new("#FEF2F2", "#7F1D1D", "#FEE2E2", "#7F1D1D", "#B91C1C");

        public static IBrush For(QueueRow row, string part)
        {
            // Running with no heartbeat is not healthy, whatever group it is in.
            var tone = row.StatusLabel == "Stuck" ? Failed : row.Group switch
            {
                QueueGroup.Running => Running,
                QueueGroup.Queued => Queued,
                QueueGroup.Failed => Failed,
                _ => Review,
            };

            return SolidColorBrush.Parse(part switch
            {
                "band" => tone.Band,
                "bandInk" => tone.BandInk,
                "chip" => tone.Chip,
                "dot" => tone.Dot,
                _ => tone.Ink,
            });
        }

        /// <summary>A group heading's colour, by its first row -- stuck or not, the band is the group's.</summary>
        public static IBrush ForBand(QueueGroup group, string part)
            => For(new QueueRow(new Models.QueueEntry()) { Group = group }, part);

        public object? Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
            => value is QueueRow row ? For(row, parameter as string ?? "ink") : null;

        public object? ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
            => throw new NotSupportedException();
    }
}
