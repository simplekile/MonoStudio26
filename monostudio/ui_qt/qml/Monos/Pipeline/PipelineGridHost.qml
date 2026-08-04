import QtQuick
import Monos.Pipeline 1.0

// Production grid host — context: pipelineModel, pipelineBridge
Item {
    id: root

    PipelineGridView {
        id: grid
        anchors.fill: parent
        anchors.margins: 24
        model: pipelineModel
        cardWidth: pipelineBridge.cardWidth

        onRowActivated: function(rowIndex) {
            pipelineBridge.activateRow(rowIndex)
        }
    }
}
