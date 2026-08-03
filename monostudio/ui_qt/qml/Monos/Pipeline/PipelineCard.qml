import QtQuick

// Grid card — layout contract plan_main_view_engine_v2 §6.1
Rectangle {
    id: card

    property string displayName: "Asset"
    property string statusLabel: "WAITING"
    property color statusColor: PipelineTheme.waiting
    property string alertLabel: ""
    property var dccLabels: []
    property url thumbSource: ""
    property real thumbOpacity: 1.0
    property bool selected: false
    property bool hovered: false

    color: hovered ? PipelineTheme.cardHover : PipelineTheme.cardBg
    radius: PipelineTheme.radiusCard
    border.width: selected ? 2 : 1
    border.color: selected ? PipelineTheme.cardSelectedBorder : PipelineTheme.cardBorder

    Behavior on border.color {
        ColorAnimation { duration: PipelineTheme.hoverBorderMs }
    }
    Behavior on color {
        ColorAnimation { duration: PipelineTheme.hoverBorderMs }
    }

    Item {
        id: thumbHost
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 1
        height: Math.round(width * 9 / 16)
        clip: true

        ThumbImage {
            anchors.fill: parent
            source: card.thumbSource
            thumbOpacity: card.thumbOpacity
        }

        BadgeChip {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.margins: 12
            label: "T"
            bg: Qt.rgba(16/255, 185/255, 129/255, 0.86)
            fg: "#ffffff"
        }

        BadgeChip {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 12
            visible: card.alertLabel.length > 0
            label: card.alertLabel
            bg: Qt.rgba(239/255, 68/255, 68/255, 0.35)
            fg: PipelineTheme.textPrimary
        }

        DccStack {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 12
            dccLabels: card.dccLabels
        }
    }

    Column {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: thumbHost.bottom
        anchors.margins: 16
        spacing: 8

        StatusPill {
            line: card.statusLabel
            accent: card.statusColor
        }

        Text {
            width: parent.width
            text: card.displayName
            color: card.selected ? PipelineTheme.textPrimarySelected : PipelineTheme.textPrimary
            font.family: PipelineTheme.fontFamily
            font.pixelSize: PipelineTheme.nameSize
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
    }
}
