import QtQuick

Item {
    id: root
    property string label: ""
    property color fg: PipelineTheme.textPrimary
    property color bg: Qt.rgba(1, 1, 1, 0.08)
    property int implicitSize: 24

    implicitWidth: Math.max(implicitSize, labelRow.implicitWidth + 10)
    implicitHeight: implicitSize

    Rectangle {
        anchors.fill: parent
        radius: PipelineTheme.radiusChip
        color: root.bg
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, 0.06)
    }

    Row {
        id: labelRow
        anchors.centerIn: parent
        spacing: 4
        Text {
            visible: root.label.length > 0
            text: root.label
            color: root.fg
            font.family: PipelineTheme.fontFamily
            font.pixelSize: 10
            font.weight: Font.DemiBold
        }
    }
}
