import QtQuick

Row {
    id: root
    property var dccLabels: []
    property int maxVisible: 3
    spacing: 4

    Repeater {
        model: Math.min(root.dccLabels.length, root.maxVisible)
        delegate: Rectangle {
            width: 22
            height: 22
            radius: 11
            color: PipelineTheme.blue600
            Text {
                anchors.centerIn: parent
                text: root.dccLabels[index].substring(0, 1).toUpperCase()
                color: "#ffffff"
                font.pixelSize: 9
                font.weight: Font.Bold
            }
        }
    }

    Text {
        visible: root.dccLabels.length > root.maxVisible
        text: "+" + (root.dccLabels.length - root.maxVisible)
        color: PipelineTheme.textMeta
        font.pixelSize: 10
        anchors.verticalCenter: parent.verticalCenter
    }
}
