import QtQuick

// Async thumb slot — opacity fade when thumbOpacity goes 0→1 (plan §7.2)
Item {
    id: root
    property url source: ""
    property real thumbOpacity: 1.0
    property color placeholderColor: PipelineTheme.cardBorder

    Rectangle {
        anchors.fill: parent
        radius: 0
        color: root.placeholderColor
    }

    Image {
        id: img
        anchors.fill: parent
        source: root.source
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        opacity: root.thumbOpacity
        Behavior on opacity {
            NumberAnimation {
                duration: PipelineTheme.thumbFadeMs
                easing.type: Easing.OutCubic
            }
        }
    }
}
