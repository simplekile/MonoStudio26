pragma Singleton
import QtQuick

// Values mirror MONOS_COLORS (style.py). Python parity: pipeline_qml_theme.build_pipeline_theme_map()
QtObject {
    readonly property color appBg: "#09090b"
    readonly property color contentBg: "#151618"
    readonly property color panel: "#18181b"
    readonly property color chromeBg: "#181a1d"

    readonly property color cardBg: "#191b1e"
    readonly property color cardHover: "#1d1f23"
    readonly property color cardBorder: "#27272a"
    readonly property color cardSelectedBorder: "#2563eb"

    readonly property color textPrimary: "#cccccc"
    readonly property color textPrimarySelected: "#fafafa"
    readonly property color textLabel: "#a1a1aa"
    readonly property color textMeta: "#71717a"

    readonly property color blue600: "#2563eb"
    readonly property color blue500: "#3b82f6"
    readonly property color blue400: "#60a5fa"
    readonly property color emerald500: "#10b981"
    readonly property color amber500: "#f59e0b"
    readonly property color red500: "#ef4444"
    readonly property color waiting: "#71717a"

    readonly property int radiusCard: 12
    readonly property int radiusPill: 8
    readonly property int radiusChip: 4

    readonly property string fontFamily: "Inter"
    readonly property string fontMono: "JetBrains Mono"

    readonly property int nameSize: 13
    readonly property int metaSize: 11
    readonly property int statusSize: 10

    readonly property int thumbFadeMs: 120
    readonly property int hoverBorderMs: 150
}
