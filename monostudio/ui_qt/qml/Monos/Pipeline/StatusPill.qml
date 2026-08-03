import QtQuick

Row {
    id: root
    property string line: "—"
    property color accent: PipelineTheme.waiting
    spacing: 6

    Rectangle {
        width: 6
        height: 6
        radius: 3
        anchors.verticalCenter: parent.verticalCenter
        color: root.accent
    }

    Text {
        text: root.line
        color: PipelineTheme.textLabel
        font.family: PipelineTheme.fontFamily
        font.pixelSize: PipelineTheme.statusSize
        font.weight: Font.Medium
        anchors.verticalCenter: parent.verticalCenter
    }
}
