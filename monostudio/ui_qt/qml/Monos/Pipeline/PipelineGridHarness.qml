import QtQuick
import QtQuick.Controls
import Monos.Pipeline 1.0

// Golden harness root — scripts/test_pipeline_qml.py
Rectangle {
    color: PipelineTheme.contentBg

    Column {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 12

        Text {
            text: "MONOS Pipeline QML — Grid harness"
            color: PipelineTheme.textLabel
            font.family: PipelineTheme.fontFamily
            font.pixelSize: 11
            font.weight: Font.DemiBold
        }

        PipelineGridView {
            width: parent.width
            height: parent.height - 32
            model: ListModel {
                ListElement {
                    displayName: "Aya"
                    statusLabel: "IN PROGRESS"
                    statusColor: "#f59e0b"
                    alertLabel: "!"
                    dccNames: "maya,zbrush"
                    thumbSource: ""
                    thumbOpacity: 1.0
                }
                ListElement {
                    displayName: "Forest Spirit"
                    statusLabel: "APPROVED"
                    statusColor: "#10b981"
                    alertLabel: ""
                    dccNames: "blender"
                    thumbSource: ""
                    thumbOpacity: 0.35
                }
                ListElement {
                    displayName: "char_hero_v2"
                    statusLabel: "BLOCKED"
                    statusColor: "#ef4444"
                    alertLabel: "3"
                    dccNames: "maya,houdini,nuke,substance"
                    thumbSource: ""
                    thumbOpacity: 1.0
                }
            }
            currentIndex: 0
        }
    }
}
